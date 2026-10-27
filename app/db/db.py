import asyncpg
import asyncio
import time
from typing import Any, AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
import json

from starlette.requests import Request

from app.core.logger import logger
from app.core.config import settings

# unattended-upgrades / needrestart 等で PostgreSQL が再起動する観測値（停止〜起動が十数秒）に余裕を持たせる。
# Redis の RDB ロード（数十秒）より短い。プール枯渇の TimeoutError は再試行しない。
_CONNECT_RETRY_SECONDS = 30.0
_CONNECT_RETRY_INTERVAL = 1.0
_TRANSIENT_LOG_INTERVAL = 10.0
_last_transient_log_at = 0.0
_suppressed_transient_logs = 0


# asyncpgがJSON/JSONBを自動的にPythonのdictに変換するための設定関数
async def setup_jsonb_codec(conn: asyncpg.Connection):
    """
    JSON/JSONB型を自動的にデコード/エンコードするためのコーデックを設定します。
    """
    await conn.set_type_codec(
        'jsonb',
        encoder=json.dumps,
        decoder=json.loads,
        schema='pg_catalog'
    )
    await conn.set_type_codec(
        'json',
        encoder=json.dumps,
        decoder=json.loads,
        schema='pg_catalog'
    )


class PoolAcquireTimeoutError(Exception):
    """プールの空き接続を待つ時間が上限を超えた（アクセス集中でプールが使い切られている）。"""


_LOST_CONNECTION_MARKERS = (
    "released back to the pool",
    "the underlying connection is closed",
    "connection was closed",
    "connection does not exist",
    "remaining connection slots",
)


def _is_pg_restarting_error(e: Exception) -> bool:
    """接続確立時に再試行する、PostgreSQL 再起動中のエラーかどうか。"""
    if isinstance(e, (asyncpg.CannotConnectNowError, ConnectionRefusedError, ConnectionResetError, BrokenPipeError)):
        return True
    if isinstance(e, OSError) and getattr(e, "errno", None) in {111, 104}:  # ECONNREFUSED / ECONNRESET
        return True
    msg = str(e).lower()
    return (
        "connection refused" in msg
        or "connect call failed" in msg
        or "the database system is starting up" in msg
        or "the database system is shutting down" in msg
        or "connection was closed" in msg
    )


def is_transient_pg_error(e: Exception) -> bool:
    """リクエスト経路・ワーカーで WARNING に落とす一時的な PostgreSQL エラー。

    再起動直後の接続枯渇や、切断済みコネクションの再利用も含む。
    プール取得の再試行には使わない（TooManyConnections の再試行は嵐を悪化させる）。
    """
    if _is_pg_restarting_error(e):
        return True
    if isinstance(e, (asyncpg.TooManyConnectionsError, asyncpg.ConnectionDoesNotExistError, PoolAcquireTimeoutError)):
        return True
    msg = str(e).lower()
    return any(marker in msg for marker in _LOST_CONNECTION_MARKERS)


def log_transient_pg_warning(message: str, *, extra: dict[str, Any] | None = None) -> None:
    """高頻度の一時エラーを 10 秒に1回へ間引く。"""
    global _last_transient_log_at, _suppressed_transient_logs
    now = time.monotonic()
    elapsed = now - _last_transient_log_at
    if elapsed >= _TRANSIENT_LOG_INTERVAL:
        omitted = ""
        if _suppressed_transient_logs:
            omitted = f"（直前 {elapsed:.0f}秒で {_suppressed_transient_logs}件を省略）"
            _suppressed_transient_logs = 0
        logger.warning(f"{message}{omitted}", extra=extra)
        _last_transient_log_at = now
        return
    _suppressed_transient_logs += 1


async def _create_pool(min_size: int, max_size: int) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        database=settings.DB_NAME,
        min_size=min_size,
        max_size=max_size,
        timeout=30.0,
        command_timeout=60.0,
        max_inactive_connection_lifetime=300.0,
        init=setup_jsonb_codec
    )


def _require_pool() -> asyncpg.Pool:
    pool = _pool
    if pool is None:
        raise RuntimeError("データベースのコネクションプールが初期化されていません。")
    return pool


def expire_stale_pool_connections() -> None:
    """切断済みのプール接続を破棄する。PostgreSQL 再起動後の再試行前に呼ぶ。"""
    pool = _pool
    if pool is None:
        return
    try:
        pool.expire_connections()
    except Exception:
        pass


async def _acquire_from_pool(timeout: float | None = None) -> tuple[asyncpg.Pool, asyncpg.Connection]:
    """プールから接続を取得する。PostgreSQL 再起動中は短時間リトライする。

    timeout を指定した場合、プールの空き待ちがその秒数を超えると PoolAcquireTimeoutError を送出する。
    """
    pool = _require_pool()
    deadline = time.monotonic() + _CONNECT_RETRY_SECONDS
    attempt = 0
    while True:
        attempt += 1
        try:
            return pool, await pool.acquire(timeout=timeout)
        except TimeoutError as e:
            if timeout is None:
                raise
            raise PoolAcquireTimeoutError(f"DBコネクションプールの空き待ちが {timeout:.0f}秒を超えました") from e
        except Exception as e:
            remaining = deadline - time.monotonic()
            if _is_pg_restarting_error(e) and remaining > 0:
                log_transient_pg_warning(
                    f"PostgreSQL接続を待機します ({attempt}回目, 残り約{remaining:.0f}秒): {e}"
                )
                await asyncio.sleep(min(_CONNECT_RETRY_INTERVAL, remaining))
                continue
            raise


# コネクションプールを保持するためのグローバル変数
# アプリケーション全体で一つのプールを共有します。
_pool: asyncpg.Pool | None = None

async def connect_to_db(*, min_size: int | None = None, max_size: int | None = None):
    """
    アプリケーション起動時に呼ばれ、コネクションプールを作成する関数。

    全プロセスの max_size の合計は PostgreSQL の max_connections を下回るようにすること。
    超えると、アクセス集中時に各プロセスのプールが一斉に広がり、DB の接続枠を使い切る。

    再試行しても作成できなければ例外を送出する。プールなしで起動を続けると全リクエストが失敗し続けるため、
    プロセスを落として systemd（Restart=always）や Uvicorn のワーカー再起動に任せる。
    VPS 再起動直後など、PostgreSQL の起動（クラッシュリカバリを含む）が再試行時間を超えた場合もこれで復帰する。
    """
    global _pool
    if _pool is not None:
        return

    max_size = max_size if max_size is not None else settings.DB_POOL_MAX_SIZE
    min_size = min(min_size if min_size is not None else settings.DB_POOL_MIN_SIZE, max_size)
    deadline = time.monotonic() + _CONNECT_RETRY_SECONDS
    attempt = 0
    while True:
        attempt += 1
        try:
            _pool = await _create_pool(min_size, max_size)
            suffix = f"（{attempt}回目で成功）" if attempt > 1 else ""
            logger.info(f"PostgreSQLのコネクションプールを正常に作成しました (min={min_size}, max={max_size})。{suffix}")
            return
        except Exception as e:
            _pool = None
            remaining = deadline - time.monotonic()
            if _is_pg_restarting_error(e) and remaining > 0:
                log_transient_pg_warning(
                    f"PostgreSQL接続を待機します ({attempt}回目, 残り約{remaining:.0f}秒): {e}"
                )
                await asyncio.sleep(min(_CONNECT_RETRY_INTERVAL, remaining))
                continue
            logger.error(f"データベース接続エラー。PostgreSQLのコネクションプール作成に失敗しました。: {e}", exc_info=True)
            raise RuntimeError(f"Failed to connect to PostgreSQL: {e}") from e

async def close_db_connection():
    """
    アプリケーション終了時に呼ばれ、コネクションプールを閉じる関数。
    """
    global _pool
    if _pool:
        try:
            await asyncio.wait_for(_pool.close(), timeout=10.0)
            logger.info("PostgreSQLのコネクションプールを正常に閉じました。")
        except asyncio.TimeoutError:
            logger.warning("PostgreSQLのコネクションプールのクローズがタイムアウトしました。")
        except Exception as e:
            logger.error(f"PostgreSQLのコネクションプールのクローズ中にエラーが発生しました: {e}", exc_info=True)
        finally:
            _pool = None

async def get_shared_db() -> AsyncGenerator[asyncpg.Connection, None]:
    """
    FastAPIのDependsで利用する、DBコネクションを取得するための依存性注入関数。
    """
    pool, connection = await _acquire_from_pool(timeout=settings.DB_POOL_ACQUIRE_TIMEOUT)
    try:
        yield connection
    finally:
        await pool.release(connection)


async def get_shared_db_if_logged_in(request: Request) -> AsyncGenerator[asyncpg.Connection | None, None]:
    """ログイン中のリクエストにだけDBコネクションを渡す。未ログインなら None で、接続を確保しない。

    UserToStateMiddleware が request.state.current_user を設定した後に解決される。
    """
    if getattr(request.state, "current_user", None) is None:
        yield None
        return
    pool, connection = await _acquire_from_pool(timeout=settings.DB_POOL_ACQUIRE_TIMEOUT)
    try:
        yield connection
    finally:
        await pool.release(connection)
        
        
# バックグラウンドタスク用のコンテキストマネージャ
@asynccontextmanager
async def get_db_connection_for_bg_task() -> AsyncGenerator[asyncpg.Connection, None]:
    """
    バックグラウンドタスク用のDB接続を取得・解放するコンテキストマネージャ。
    """
    pool: asyncpg.Pool | None = None
    conn: asyncpg.Connection | None = None
    try:
        pool, conn = await _acquire_from_pool()
        yield conn
    finally:
        if pool is not None and conn is not None:
            await pool.release(conn)


async def load_serialized_within_request_timeout[T](lock: asyncio.Lock, load: Callable[[], Awaitable[T]]) -> T:
    """キャッシュ再生成などを lock で1件ずつ実行する。ロック待ちを含めて DB_POOL_ACQUIRE_TIMEOUT を超えたら打ち切る。

    Raises:
        PoolAcquireTimeoutError: 待ち時間が上限を超えた場合
    """
    try:
        async with asyncio.timeout(settings.DB_POOL_ACQUIRE_TIMEOUT):
            async with lock:
                return await load()
    except TimeoutError as e:
        raise PoolAcquireTimeoutError(f"DB処理の待ち時間が {settings.DB_POOL_ACQUIRE_TIMEOUT:.0f}秒を超えました") from e


# [この部分は公開用リポジトリでは非公開にされています]
