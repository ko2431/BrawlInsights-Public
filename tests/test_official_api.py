"""公式APIの非同期クライアントと、プレイヤー取得で外部APIを同時に呼ぶ処理を、DBを使わずに検証する。"""

import asyncio
import datetime
import time

import httpx
import pytest

from app.exceptions.custom_exceptions import PlayerNotFoundError
from app.services import brawl_service, official_api

TAG = "#PCJJPRP"


@pytest.fixture
def mock_official_api(monkeypatch):
    """公式APIへの通信を差し替え、受け取ったリクエストを記録する。"""
    calls: list[httpx.Request] = []

    def install(handler):
        def wrapped(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return handler(request)
        client = httpx.AsyncClient(base_url=official_api.BASE_URL, transport=httpx.MockTransport(wrapped))
        monkeypatch.setattr(official_api._api_client, "_client", client)
        return calls
    return install


@pytest.mark.asyncio
async def test_get_player_returns_json_and_encodes_tag(mock_official_api):
    calls = mock_official_api(lambda request: httpx.Response(200, json={"tag": TAG, "name": "a"}))
    assert await official_api.get_player(TAG) == {"tag": TAG, "name": "a"}
    assert await official_api.get_battlelog(TAG) == {"tag": TAG, "name": "a"}
    assert [c.url.raw_path.decode() for c in calls] == ["/v1/players/%23PCJJPRP", "/v1/players/%23PCJJPRP/battlelog"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 404, 429, 503])
async def test_non_200_raises_with_status(mock_official_api, status):
    mock_official_api(lambda request: httpx.Response(status, json={"reason": "x"}))
    with pytest.raises(official_api.OfficialAPIError) as exc_info:
        await official_api.get_club(TAG)
    assert exc_info.value.status == status
    assert exc_info.value.label == str(status)


@pytest.mark.asyncio
async def test_timeout_and_connection_error_have_no_status(mock_official_api):
    def timeout(request):
        raise httpx.ReadTimeout("timed out", request=request)
    mock_official_api(timeout)
    with pytest.raises(official_api.OfficialAPIError) as exc_info:
        await official_api.get_player(TAG)
    assert exc_info.value.status is None and exc_info.value.label == "timeout"

    def refused(request):
        raise httpx.ConnectError("refused", request=request)
    mock_official_api(refused)
    with pytest.raises(official_api.OfficialAPIError) as exc_info:
        await official_api.get_player(TAG)
    assert exc_info.value.status is None and exc_info.value.label == "ConnectError"


@pytest.mark.asyncio
async def test_invalid_json_raises(mock_official_api):
    mock_official_api(lambda request: httpx.Response(200, content=b"<html>"))
    with pytest.raises(official_api.OfficialAPIError) as exc_info:
        await official_api.get_player(TAG)
    assert exc_info.value.detail == "invalid json"


def _patch_requests(monkeypatch, *, player, delay=0.2):
    """プレイヤー取得で使う4つの外部APIを差し替える。player 以外は delay 秒かかり、止められたかを記録する。"""
    state = {"cancelled": set(), "started": {}}

    def slow(name, value):
        async def call(*args, **kwargs):
            state["started"][name] = time.perf_counter()
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                state["cancelled"].add(name)
                raise
            return value
        return call

    monkeypatch.setattr(brawl_service.official_api, "get_player", player)
    monkeypatch.setattr(brawl_service.official_api, "get_battlelog", slow("battlelog", {"items": []}))
    monkeypatch.setattr(brawl_service.meowapi, "get_player", slow("meow", None))
    monkeypatch.setattr(brawl_service.bsinfoapi, "get_player_owned_skins", slow("skins", None))
    return state


class _NoRowDB:
    async def fetchrow(self, query, *args):
        return None


@pytest.mark.asyncio
async def test_not_found_player_cancels_other_requests(monkeypatch):
    async def not_found(tag):
        raise official_api.OfficialAPIError(404, "Not Found")
    state = _patch_requests(monkeypatch, player=not_found, delay=5)
    player = brawl_service.Player(TAG, _NoRowDB())
    started = time.perf_counter()
    with pytest.raises(PlayerNotFoundError):
        await player.get_player_data()
    # 残りの呼び出しを待たずに終わり、止められている
    assert time.perf_counter() - started < 1
    assert state["cancelled"] == {"battlelog", "meow", "skins"}


@pytest.mark.asyncio
async def test_requests_start_at_the_same_time(monkeypatch):
    async def player_info(tag):
        await asyncio.sleep(0.2)
        return {}
    state = _patch_requests(monkeypatch, player=player_info, delay=0.2)
    started = time.perf_counter()
    requests = brawl_service._PlayerAPIRequests(TAG)
    await asyncio.gather(requests.player, requests.battlelog, requests.meow, requests.owned_skins)
    await requests.discard()
    # 4つとも0.2秒かかるが、同時に呼ぶため合計でも0.2秒程度
    assert time.perf_counter() - started < 0.5
    assert max(state["started"].values()) - min(state["started"].values()) < 0.05


class _ExecuteDB:
    def __init__(self):
        self.executed = []

    async def execute(self, query, *args):
        self.executed.append(args)


@pytest.mark.asyncio
async def test_auto_activation_for_page_view_keeps_cache(monkeypatch):
    cache = {"player:" + TAG: {"tag": TAG, "level": 20, "auto_track_expiration": None, "remaining_time": None, "name": "a"}}
    deleted, kept = [], []

    async def fake_get_cache(key):
        return cache.get(key)

    async def fake_set_cache_keep_ttl(key, value):
        kept.append(key)
        cache[key] = value

    async def fake_delete_cache(key):
        deleted.append(key)
    monkeypatch.setattr(brawl_service, "get_cache", fake_get_cache)
    monkeypatch.setattr(brawl_service, "set_cache_keep_ttl", fake_set_cache_keep_ttl)
    monkeypatch.setattr(brawl_service, "delete_cache", fake_delete_cache)

    player = brawl_service.Player(TAG, _ExecuteDB())
    await player.activate_automatic_acquisition(activate_time=datetime.timedelta(hours=24), keep_cache=True)
    assert deleted == [] and kept == ["player:" + TAG]
    cached = cache["player:" + TAG]
    assert cached["level"] == 30 and cached["name"] == "a"
    assert cached["auto_track_expiration"] == brawl_service.format_utc_datetime(player.auto_track_expiration)
    assert 23 * 3600 < cached["remaining_time"] <= 24 * 3600

    # 延長などほかの経路は、これまでどおりキャッシュを消す
    await player.activate_automatic_acquisition(activate_time=datetime.timedelta(hours=1))
    assert deleted == ["player:" + TAG, "player_fresh:" + TAG]


def test_member_from_official_api_json():
    member = brawl_service.Member({"tag": "#ABC", "name": "n", "nameColor": "0xff", "role": "senior", "trophies": 12345, "icon": {"id": 28000001}})
    assert (member.tag, member.name, member.role, member.trophies, member.icon_id) == ("#ABC", "n", "senior", 12345, 28000001)
