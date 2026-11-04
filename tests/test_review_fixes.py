"""レビューで指摘された点(キャッシュの競合・シェルのDB参照の時間制限・ゲートの403の見分け)を検証する。"""

import asyncio
import json
import time

import pytest
from httpx import AsyncClient

from app.core import cache, page_integrity
from app.db import db as db_module


class _SlowPool:
    """acquire はすぐ返り、release の呼び出しを記録する疑似プール。"""

    def __init__(self):
        self.released = asyncio.Event()

    async def acquire(self, timeout=None):
        return object()

    async def release(self, conn, timeout=None):
        self.released.set()


@pytest.mark.asyncio
async def test_quick_lookup_gives_up_on_slow_query(monkeypatch):
    pool = _SlowPool()
    monkeypatch.setattr(db_module, "_pool", pool)

    async def slow_query(conn):
        await asyncio.sleep(5)
        return {"name": "x"}

    started = time.perf_counter()
    result = await db_module.run_quick_db_lookup(slow_query, timeout=0.05, label="テスト")
    # 接続の取得後のクエリも含めて打ち切り、外枠だけを返せるようにする
    assert result is None
    assert time.perf_counter() - started < 0.5
    await asyncio.wait_for(pool.released.wait(), timeout=1)


@pytest.mark.asyncio
async def test_quick_lookup_returns_result_and_releases(monkeypatch):
    pool = _SlowPool()
    monkeypatch.setattr(db_module, "_pool", pool)

    async def query(conn):
        return {"name": "x"}

    assert await db_module.run_quick_db_lookup(query, timeout=0.5, label="テスト") == {"name": "x"}
    assert pool.released.is_set()


@pytest.mark.asyncio
async def test_integrity_gate_marks_its_403(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(page_integrity.settings, "ADBLOCK_GATE_ENABLED", True)
    client.cookies.clear()
    response = await client.get("/api/ja/player/PCJJPRP/profile")
    assert response.status_code == 403
    assert response.headers.get(page_integrity.BLOCKED_HEADER) == "blocked"


@pytest.mark.asyncio
async def test_partial_update_does_not_roll_back_concurrent_write(monkeypatch):
    """読み取りの直後に別の処理がキャッシュを作り直しても、その値を古い値で上書きしない(Redisがある場合のみ)。"""
    import redis.asyncio as redis_asyncio
    from app.core.config import settings
    r = redis_asyncio.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, password=settings.REDIS_PASSWORD, db=settings.REDIS_DB)
    try:
        await r.ping()
    except Exception:
        await r.aclose()
        pytest.skip("Redisに接続できない環境")
    monkeypatch.setattr(cache, "get_redis", lambda: r)
    key = "test:partial_update:player"
    try:
        await r.set(key, json.dumps({"hide_name_history": False, "level": 20}), ex=120)
        original_pipeline = r.pipeline
        injected = []

        def pipeline_with_race(*args, **kwargs):
            pipe = original_pipeline(*args, **kwargs)
            real_get = pipe.get

            async def get_then_concurrent_write(name):
                value = await real_get(name)
                if not injected:
                    injected.append(True)
                    # 本人が履歴を非公開にし、別のリクエストがキャッシュを作り直した
                    await r.set(key, json.dumps({"hide_name_history": True, "level": 20}), ex=120)
                return value
            pipe.get = get_then_concurrent_write
            return pipe
        monkeypatch.setattr(r, "pipeline", pipeline_with_race)

        assert await cache.update_cache_fields_keep_ttl(key, {"level": 30}) is True
        assert json.loads(await r.get(key)) == {"hide_name_history": True, "level": 30}
        assert 100 < await r.ttl(key) <= 120
        # キーがない場合は作らない
        await r.delete(key)
        assert await cache.update_cache_fields_keep_ttl(key, {"level": 30}) is True
        assert not await r.exists(key)
    finally:
        await r.delete(key)
        await r.aclose()
