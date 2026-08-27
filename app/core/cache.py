import redis.asyncio as redis # 非同期版Redisクライアント
import json
import asyncio
import time
from typing import Optional, Any
from redis.exceptions import BusyLoadingError, ConnectionError as RedisConnectionError
from app.core.config import settings
from app.core.logger import logger

# Redis接続プールを保持するグローバル変数
redis_pool: Optional[redis.Redis] = None # 型ヒントを修正

# unattended-upgrades 等で Redis が再起動し、2.7GB 級の RDB をロードする観測値（停止~50秒+ロード~20秒）に余裕を持たせる
_CONNECT_RETRY_SECONDS = 90.0
_CONNECT_RETRY_INTERVAL = 2.0
_TRANSIENT_LOG_INTERVAL = 10.0
_last_transient_log_at = 0.0
_suppressed_transient_logs = 0


def _is_transient_redis_error(e: Exception) -> bool:
    """再起動・RDBロード中など、短時間で解消する Redis エラーかどうか。"""
    if isinstance(e, (BusyLoadingError, RedisConnectionError)):
        return True
    msg = str(e)
    return (
        "Buffer is closed" in msg
        or "Connection closed" in msg
        or "loading the dataset in memory" in msg
        or "Connection reset by peer" in msg
        or "Connection refused" in msg
    )


def _log_transient_redis_warning(message: str) -> None:
    """高頻度の一時エラーを 10 秒に1回へ間引く。"""
    global _last_transient_log_at, _suppressed_transient_logs
    now = time.monotonic()
    elapsed = now - _last_transient_log_at
    if elapsed >= _TRANSIENT_LOG_INTERVAL:
        omitted = ""
        if _suppressed_transient_logs:
            omitted = f"（直前 {elapsed:.0f}秒で {_suppressed_transient_logs}件を省略）"
            _suppressed_transient_logs = 0
        logger.warning(f"{message}{omitted}")
        _last_transient_log_at = now
        return
    _suppressed_transient_logs += 1


async def _discard_redis_client(client: redis.Redis | None) -> None:
    if client is None:
        return
    try:
        await asyncio.wait_for(client.close(), timeout=2.0)
    except Exception:
        pass


async def connect_redis():
    """Redis接続プールを初期化する"""
    global redis_pool
    if redis_pool is not None:
        return

    deadline = time.monotonic() + _CONNECT_RETRY_SECONDS
    attempt = 0
    while True:
        attempt += 1
        client: redis.Redis | None = None
        try:
            client = redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                password=settings.REDIS_PASSWORD,
                db=settings.REDIS_DB,
                decode_responses=False # バイト列で取得・設定するためFalseに（JSONシリアライズのため）
            )
            await client.ping()
            redis_pool = client
            suffix = f"（{attempt}回目で成功）" if attempt > 1 else ""
            logger.info(f"Redis接続プールを確立しました: {settings.REDIS_HOST}:{settings.REDIS_PORT}{suffix}")
            return
        except Exception as e:
            await _discard_redis_client(client)
            redis_pool = None
            remaining = deadline - time.monotonic()
            if _is_transient_redis_error(e) and remaining > 0:
                logger.warning(
                    f"Redis接続を待機します ({attempt}回目, 残り約{remaining:.0f}秒): {e}"
                )
                await asyncio.sleep(min(_CONNECT_RETRY_INTERVAL, remaining))
                continue
            logger.error(f"Redis接続プールの確立に失敗しました: {e}", exc_info=True)
            raise RuntimeError(f"Failed to connect to Redis: {e}") from e

async def close_redis():
    """Redis接続プールを閉じる"""
    global redis_pool
    if redis_pool:
        try:
            await asyncio.wait_for(redis_pool.close(), timeout=5.0)
            logger.info("Redis接続プールを閉じました。")
        except asyncio.TimeoutError:
            logger.warning("Redis接続プールのクローズがタイムアウトしました。")
        except Exception as e:
            logger.error(f"Redis接続プールのクローズ中にエラーが発生しました: {e}", exc_info=True)
        finally:
            redis_pool = None

def get_redis() -> Optional[redis.Redis]: # 型ヒントを修正
    """Redisクライアントインスタンスを取得する（依存性注入用など）"""
    if redis_pool is None:
        # アプリケーション起動時に connect_redis が呼ばれているはずなので、
        # ここで None の場合は問題がある
        _log_transient_redis_warning(
            "Redis接続が利用できません。connect_redisが呼び出されていないか、再起動待ちです。"
        )
    return redis_pool


def _log_cache_error(operation: str, e: Exception, *, key: str | None = None, prefix: str | None = None) -> None:
    ident = f"key: {key}" if key is not None else f"prefix: {prefix}"
    if _is_transient_redis_error(e):
        _log_transient_redis_warning(f"Redis{operation}中に一時的なエラー ({ident}): {e}")
        return
    logger.error(f"Redis{operation}中にエラー ({ident}): {e}", exc_info=True)

# --- キャッシュ操作関数 ---

async def get_cache(key: str) -> Optional[Any]:
    """Redisからキャッシュを取得する。存在しないキーを指定した場合はNoneが返ってくる。"""
    r = get_redis()
    if not r:
        return None
    try:
        cached_data_bytes = await r.get(key)
        if cached_data_bytes:
            # バイト列をデコードしてからJSONパース
            return json.loads(cached_data_bytes.decode('utf-8'))
        return None
    except Exception as e:
        _log_cache_error("からのキャッシュ取得", e, key=key)
        return None

async def set_cache(key: str, value: Any, ttl: int | None = 3600): # ttlのデフォルトは1時間
    """Redisにキャッシュを設定する"""
    r = get_redis()
    if not r:
        return
    try:
        # valueをJSON文字列にシリアライズしてからバイト列にエンコード
        value_json_bytes = json.dumps(value).encode('utf-8')
        if ttl:
            await r.setex(key, ttl, value_json_bytes)
        else: # ttlがNoneの場合は永続化
            await r.set(key, value_json_bytes)
    except Exception as e:
        _log_cache_error("へのキャッシュ設定", e, key=key)

async def delete_cache(key: str):
    """Redisからキャッシュを削除する。存在しないキーを指定してもエラーにはならない。"""
    r = get_redis()
    if not r:
        return
    try:
        await r.delete(key)
    except Exception as e:
        _log_cache_error("からのキャッシュ削除", e, key=key)

async def adjust_cache_counter_if_exists(key: str, delta: int = 1, ttl: int | None = None) -> bool:
    """既存の整数カウンタキャッシュを増減する。キーが無い場合は何もしない。

    set_cache(JSON) と redis INCR/DECR が共存するキー用。
    存在しないキーへの INCR は Redis が 0 から始めて 1 になってしまうため、事前に exists を確認する。
    """
    r = get_redis()
    if not r:
        return False
    try:
        if not await r.exists(key):
            return False
        if delta == 1:
            await r.incr(key)
        elif delta == -1:
            await r.decr(key)
        else:
            await r.incrby(key, delta)
        if ttl is not None:
            await r.expire(key, ttl)
        return True
    except Exception as e:
        _log_cache_error("カウンタ更新", e, key=key)
        return False

async def clear_cache_by_prefix(prefix: str):
    """指定されたプレフィックスを持つ全てのキャッシュを削除する（慎重に使用）"""
    r = get_redis()
    if not r:
        return
    try:
        # SCANを使用してキーをイテレートし、マッチするものを削除
        # 大量のキーがある場合はパフォーマンスに影響する可能性があるので注意
        async for key_bytes in r.scan_iter(match=f"{prefix}*"):
            await r.delete(key_bytes)
        logger.info(f"プレフィックス '{prefix}' を持つキャッシュをクリアしました。")
    except Exception as e:
        _log_cache_error("のプレフィックスによるキャッシュクリア", e, prefix=prefix)
