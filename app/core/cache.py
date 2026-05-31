import redis.asyncio as redis # 非同期版Redisクライアント
import json
import asyncio
from typing import Optional, Any
from app.core.config import settings
from app.core.logger import logger

# Redis接続プールを保持するグローバル変数
redis_pool: Optional[redis.Redis] = None # 型ヒントを修正

async def connect_redis():
    """Redis接続プールを初期化する"""
    global redis_pool
    if redis_pool is None:
        try:
            # redis.asyncio.from_url を使うとURL形式で設定できる
            # または個別にhost, portなどを指定
            redis_pool = redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                password=settings.REDIS_PASSWORD,
                db=settings.REDIS_DB,
                decode_responses=False # バイト列で取得・設定するためFalseに（JSONシリアライズのため）
            )
            await redis_pool.ping() # 接続確認
            logger.info(f"Redis接続プールを確立しました: {settings.REDIS_HOST}:{settings.REDIS_PORT}")
        except Exception as e:
            logger.error(f"Redis接続プールの確立に失敗しました: {e}", exc_info=True)
            redis_pool = None
            raise RuntimeError(f"Failed to connect to Redis: {e}") # 接続失敗時はNoneのまま
            # 必要に応じてここでアプリケーションの起動を中止するなどの処理も検討

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
        logger.warning("Redis接続が利用できません。connect_redisが呼び出されていません。")
    return redis_pool

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
        logger.error(f"Redisからのキャッシュ取得中にエラー (key: {key}): {e}", exc_info=True)
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
        logger.error(f"Redisへのキャッシュ設定中にエラー (key: {key}): {e}", exc_info=True)

async def delete_cache(key: str):
    """Redisからキャッシュを削除する。存在しないキーを指定してもエラーにはならない。"""
    r = get_redis()
    if not r:
        return
    try:
        await r.delete(key)
    except Exception as e:
        logger.error(f"Redisからのキャッシュ削除中にエラー (key: {key}): {e}", exc_info=True)

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
        logger.error(f"Redisのプレフィックスによるキャッシュクリア中にエラー (prefix: {prefix}): {e}", exc_info=True)
