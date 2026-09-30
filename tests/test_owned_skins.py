import datetime

import pytest

from app.services import bsinfoapi
from app.services import brawl_service
from app.services.brawl_service import Player, PlayerBrawler

TAG = "#TESTTAG"
SYNCED_AT = datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc)


@pytest.fixture
def fake_cache(monkeypatch):
    """bsinfoapi が使う Redis キャッシュを dict で置き換える。"""
    store: dict[str, tuple[object, int | None]] = {}

    async def fake_get_cache(key):
        entry = store.get(key)
        return entry[0] if entry else None

    async def fake_set_cache(key, value, ttl=3600):
        store[key] = (value, ttl)

    monkeypatch.setattr(bsinfoapi, "get_cache", fake_get_cache)
    monkeypatch.setattr(bsinfoapi, "set_cache", fake_set_cache)
    return store


def _mock_api(monkeypatch, response):
    calls = []

    async def fake_get(endpoint, params=None, timeout=None):
        calls.append(endpoint)
        return response

    monkeypatch.setattr(bsinfoapi._api_client, "get", fake_get)
    return calls


async def test_owned_skins_success_is_cached_with_ttl(fake_cache, monkeypatch):
    _mock_api(monkeypatch, {"brawlers": [{"id": 16000000, "owned": [{"id": 29000000}, {"id": 29000001}]}]})

    result = await bsinfoapi.get_player_owned_skins(TAG)

    assert result == {16000000: [29000000, 29000001]}
    # 所持スキンはDBに永続化しているため、Redisには有効期限付きでのみ保存する
    assert fake_cache[f"bsinfo:skins:{TAG}"][1] == bsinfoapi.BSINFO_SKINS_UPDATE_LOCK_TTL
    assert fake_cache[f"bsinfo_update_lock:skins:{TAG}"][1] == bsinfoapi.BSINFO_SKINS_UPDATE_LOCK_TTL


@pytest.mark.parametrize("response", [None, {"error": "x"}, {"brawlers": []}])
async def test_owned_skins_failure_returns_none_and_suppresses_retry(fake_cache, monkeypatch, response):
    calls = _mock_api(monkeypatch, response)

    assert await bsinfoapi.get_player_owned_skins(TAG) is None
    assert f"bsinfo:skins:{TAG}" not in fake_cache
    assert fake_cache[f"bsinfo_update_lock:skins:{TAG}"][1] == bsinfoapi.BSINFO_SKINS_FAILURE_LOCK_TTL

    # 失敗ロック中は再問い合わせしない
    assert await bsinfoapi.get_player_owned_skins(TAG) is None
    assert len(calls) == 1


async def test_owned_skins_returns_cache_while_locked(fake_cache, monkeypatch):
    calls = _mock_api(monkeypatch, None)
    fake_cache[f"bsinfo_update_lock:skins:{TAG}"] = (True, 3600)
    fake_cache[f"bsinfo:skins:{TAG}"] = ({"16000000": [29000000]}, 3600)

    assert await bsinfoapi.get_player_owned_skins(TAG) == {16000000: [29000000]}
    assert calls == []


class FakeDB:
    def __init__(self, rows):
        self.rows = rows
        self.fetch_calls = 0

    async def fetch(self, query, *args):
        self.fetch_calls += 1
        return self.rows


def _brawler(brawler_id, skin_id):
    b = PlayerBrawler()
    b.id = brawler_id
    b.skin_id = skin_id
    return b


def _player(monkeypatch, *, bsinfo_result, synced_at, level=20, saved_rows=None, owned_skin_count=500):
    async def fake_owned_skins(tag):
        return bsinfo_result

    async def fake_all_skins(db):
        return {}

    monkeypatch.setattr(brawl_service.bsinfoapi, "get_player_owned_skins", fake_owned_skins)
    monkeypatch.setattr(brawl_service, "get_all_skins", fake_all_skins)
    player = Player(TAG, FakeDB(saved_rows or []))
    player.level = level
    player.owned_skins_synced_at = synced_at
    player.owned_skin_count = owned_skin_count
    # キャラ1は非デフォルトスキン(29000011)を装備中
    player.brawlers = [_brawler(1, 29000011), _brawler(2, 29000020)]
    return player


async def test_apply_owned_skins_uses_bsinfo_and_marks_synced(monkeypatch):
    player = _player(monkeypatch, bsinfo_result={1: [29000010, 29000011], 2: [29000020, 29000021]}, synced_at=None)

    await player._apply_owned_skins()

    assert player.brawlers[0].owned_skin_ids == [29000010, 29000011]
    assert player.owned_skin_count == 4
    assert player.owned_skins_synced_at is not None


async def test_apply_owned_skins_failure_falls_back_to_saved(monkeypatch):
    saved = [{"brawler_id": 1, "owned_skin_ids": [29000010, 29000011]}, {"brawler_id": 2, "owned_skin_ids": [29000020, 29000021]}]
    player = _player(monkeypatch, bsinfo_result=None, synced_at=SYNCED_AT, saved_rows=saved)

    await player._apply_owned_skins()

    # 装備中スキンのみで上書きせず、保存済みのデフォルトスキンを引き継ぐ
    assert player.brawlers[0].owned_skin_ids == [29000010, 29000011]
    assert player.owned_skin_count == 4
    assert player.owned_skins_synced_at == SYNCED_AT


async def test_apply_owned_skins_failure_without_sync_is_unknown(monkeypatch):
    player = _player(monkeypatch, bsinfo_result=None, synced_at=None)

    await player._apply_owned_skins()

    assert player.brawlers[0].owned_skin_ids == [29000011]
    assert player.owned_skin_count is None
    assert player.db.fetch_calls == 0


async def test_apply_owned_skins_failure_without_saved_rows_is_unknown(monkeypatch):
    player = _player(monkeypatch, bsinfo_result=None, synced_at=SYNCED_AT, saved_rows=[])

    await player._apply_owned_skins()

    assert player.owned_skin_count is None
    assert player.owned_skins_synced_at is None


async def test_apply_owned_skins_failure_below_level20_keeps_db_count(monkeypatch):
    player = _player(monkeypatch, bsinfo_result=None, synced_at=SYNCED_AT, level=10, owned_skin_count=123)

    await player._apply_owned_skins()

    assert player.owned_skin_count == 123
    assert player.owned_skins_synced_at == SYNCED_AT
    assert player.db.fetch_calls == 0


def test_player_dict_roundtrip_keeps_unknown_and_synced_at():
    player = Player(TAG, None)
    player.owned_skin_count = None
    player.owned_skins_synced_at = SYNCED_AT
    data = player.to_dict()
    assert data["owned_skin_count"] is None
    assert data["owned_skins_synced_at"]
