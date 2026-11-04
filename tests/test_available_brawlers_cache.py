"""キャラクター数(calc_num_of_available_brawlers)の計算が、キャッシュの切れ目に同時に何本も走らないことを、DB・Redisを使わずに検証する。"""

import asyncio

import pytest

from app.services import brawl_service


class _FakeRedis:
    """ロックの取得(SET NX)と解放(eval)だけを模す。"""

    def __init__(self):
        self.store = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def eval(self, script, numkeys, key, token, *args):
        if self.store.get(key) == token:
            del self.store[key]
            return 1
        return 0


class _SlowMaxDB:
    def __init__(self, value=108, delay=0.1):
        self.value, self.delay, self.calls = value, delay, 0

    async def fetchval(self, query):
        self.calls += 1
        await asyncio.sleep(self.delay)
        return self.value


@pytest.fixture
def cache(monkeypatch):
    store = {}
    redis = _FakeRedis()

    async def fake_set_cache(key, value, ttl=3600):
        store[key] = (value, ttl)

    async def get(key):
        entry = store.get(key)
        return entry[0] if isinstance(entry, tuple) else entry

    monkeypatch.setattr(brawl_service, "get_cache", get)
    monkeypatch.setattr(brawl_service, "set_cache", fake_set_cache)
    monkeypatch.setattr(brawl_service, "get_redis", lambda: redis)
    return store, redis


@pytest.mark.asyncio
async def test_only_one_request_scans_when_cache_expires(cache):
    store, redis = cache
    store[brawl_service.NUM_OF_AVAILABLE_BRAWLERS_STALE_KEY] = (107, None)
    db = _SlowMaxDB(value=108)
    results = await asyncio.gather(*(brawl_service.calc_num_of_available_brawlers(db) for _ in range(10)))
    # 全件走査は1本だけ。ほかは控えの値を返し、待たされない
    assert db.calls == 1
    assert sorted(results) == [107] * 9 + [108]
    assert store[brawl_service.NUM_OF_AVAILABLE_BRAWLERS_CACHE_KEY] == (108, 3600)
    assert store[brawl_service.NUM_OF_AVAILABLE_BRAWLERS_STALE_KEY] == (108, brawl_service.NUM_OF_AVAILABLE_BRAWLERS_STALE_TTL)
    # ロックは解放されている
    assert brawl_service.NUM_OF_AVAILABLE_BRAWLERS_LOCK_KEY not in redis.store
    # 次からはキャッシュを返す
    assert await brawl_service.calc_num_of_available_brawlers(db) == 108
    assert db.calls == 1


@pytest.mark.asyncio
async def test_computes_itself_without_stale_value(cache):
    # 控えがまだない(デプロイ直後など)場合は、これまでどおりそれぞれ計算する
    db = _SlowMaxDB(value=108)
    results = await asyncio.gather(*(brawl_service.calc_num_of_available_brawlers(db) for _ in range(3)))
    assert results == [108, 108, 108]
    assert db.calls == 3


@pytest.mark.asyncio
async def test_lock_is_released_when_query_fails(cache):
    store, redis = cache

    class _FailingDB:
        async def fetchval(self, query):
            raise RuntimeError("db down")

    with pytest.raises(RuntimeError):
        await brawl_service.calc_num_of_available_brawlers(_FailingDB())
    assert brawl_service.NUM_OF_AVAILABLE_BRAWLERS_LOCK_KEY not in redis.store


@pytest.mark.asyncio
async def test_works_without_redis(monkeypatch):
    async def no_cache(key):
        return None

    async def no_set(key, value, ttl=3600):
        pass
    monkeypatch.setattr(brawl_service, "get_cache", no_cache)
    monkeypatch.setattr(brawl_service, "set_cache", no_set)
    monkeypatch.setattr(brawl_service, "get_redis", lambda: None)
    db = _SlowMaxDB(value=108, delay=0)
    assert await brawl_service.calc_num_of_available_brawlers(db) == 108
