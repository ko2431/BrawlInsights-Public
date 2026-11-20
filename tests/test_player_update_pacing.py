"""プレイヤーアップデート（ワーカーの裏の更新）の、次の確認の時刻の決め方・3段の確認・回の進め方を、DBを使わずに検証する。"""

import asyncio
import datetime
import time
from types import SimpleNamespace

import pytest

from app.background_tasks import player_update
from app.services import brawl_service, official_api
from app.services import player_update_schedule as schedule

NOW = datetime.datetime(2026, 11, 17, 12, 0, tzinfo=datetime.timezone.utc)
MIN = datetime.timedelta(minutes=1)
DAY = datetime.timedelta(days=1)


def _times(*minutes_ago: float) -> list[datetime.datetime]:
    return [NOW - datetime.timedelta(minutes=m) for m in minutes_ago]


def _api_time(t: datetime.datetime) -> str:
    return t.strftime("%Y%m%dT%H%M%S.000Z")


# --- 次の確認の時刻 ---

def test_group_for_level():
    assert schedule.group_for_level(30) == "auto"
    assert schedule.group_for_level(20) == "active"
    assert schedule.group_for_level(10) is None and schedule.group_for_level(None) is None


def test_auto_waits_an_hour_when_not_playing():
    delay = schedule.next_check_delay("auto", now=NOW, times=_times(90, 100), fetched_count=2, new_count=1, last_checked_at=NOW - 60 * MIN)
    assert delay == schedule.AUTO_IDLE_INTERVAL
    assert schedule.next_check_delay("auto", now=NOW, times=[], fetched_count=0, new_count=0, last_checked_at=None) == schedule.AUTO_IDLE_INTERVAL


def test_auto_checks_by_pace_while_playing():
    # 直近38分に20件（2分に1件）→ 20件ぶんは38分
    times = _times(*[2 * i for i in range(20)]) + _times(*[200 + i for i in range(5)])
    delay = schedule.next_check_delay("auto", now=NOW, times=times, fetched_count=25, new_count=20, last_checked_at=NOW - 60 * MIN)
    assert delay == 38 * MIN
    # 始めたばかりで3件だけ → 上限の1時間
    delay = schedule.next_check_delay("auto", now=NOW, times=_times(1, 4, 7), fetched_count=3, new_count=3, last_checked_at=NOW - 60 * MIN)
    assert delay == schedule.AUTO_IDLE_INTERVAL
    # とても速いペースでも20分より短くしない
    delay = schedule.next_check_delay("auto", now=NOW, times=_times(*[0.5 * i for i in range(24)]) + _times(300), fetched_count=25, new_count=24, last_checked_at=NOW - 20 * MIN)
    assert delay == schedule.AUTO_MIN_INTERVAL


def test_auto_checks_soon_when_all_battles_were_new():
    delay = schedule.next_check_delay("auto", now=NOW, times=_times(*[30 + 3 * i for i in range(25)]), fetched_count=25, new_count=25, last_checked_at=NOW - 3 * 60 * MIN)
    assert delay == schedule.AUTO_MIN_INTERVAL


def test_active_interval_follows_how_much_they_play():
    # 新しいバトルなし → 7日
    assert schedule.next_check_delay("active", now=NOW, times=_times(9000), fetched_count=25, new_count=0, last_checked_at=NOW - 7 * DAY) == schedule.ACTIVE_MAX_INTERVAL
    # 7日で14件（1日2件）→ 10日ぶん → 上限の7日
    assert schedule.next_check_delay("active", now=NOW, times=_times(*range(14)), fetched_count=25, new_count=14, last_checked_at=NOW - 7 * DAY) == schedule.ACTIVE_MAX_INTERVAL
    # 7日で21件（1日3件）→ 20件ぶんは6.7日
    delay = schedule.next_check_delay("active", now=NOW, times=_times(*range(21)), fetched_count=25, new_count=21, last_checked_at=NOW - 7 * DAY)
    assert 6.6 * DAY < delay < 6.7 * DAY
    # すべて新しく、25件が半日に収まる → 下限の3日
    times = _times(*[20 * i for i in range(25)])
    assert schedule.next_check_delay("active", now=NOW, times=times, fetched_count=25, new_count=25, last_checked_at=NOW - 7 * DAY) == schedule.ACTIVE_MIN_INTERVAL


def test_active_without_previous_check_uses_battle_span():
    # 前回の確認が分からないときは、届いた分の幅（4日で10件）から見積もる → 8日ぶん → 7日
    times = [NOW - datetime.timedelta(hours=10 * i) for i in range(10)]
    assert schedule.next_check_delay("active", now=NOW, times=times, fetched_count=10, new_count=10, last_checked_at=None) == schedule.ACTIVE_MAX_INTERVAL


def test_extras_due():
    assert schedule.extras_due("auto", None, NOW)
    assert not schedule.extras_due("auto", NOW - 23 * 60 * MIN, NOW)
    assert schedule.extras_due("auto", NOW - 24 * 60 * MIN, NOW)
    assert not schedule.extras_due("active", NOW - 6 * DAY, NOW)
    assert schedule.extras_due("active", NOW - 7 * DAY, NOW)


def test_battle_times_and_new_count():
    items = [{"battleTime": _api_time(NOW - 5 * MIN)}, {"battleTime": "broken"}, {"battleTime": _api_time(NOW - 1 * MIN)}, {}]
    times = schedule.battle_times(items)
    assert times == [NOW - 1 * MIN, NOW - 5 * MIN]
    assert schedule.count_new_battles(times, None) == 2
    assert schedule.count_new_battles(times, NOW - 3 * MIN) == 1
    assert schedule.count_new_battles(times, NOW - 1 * MIN) == 0


# --- 予定の表への書き込み ---

class _FakeDB:
    def __init__(self, *, latest_saved=None, last_checked=None):
        self.latest_saved = latest_saved
        self.last_checked = last_checked
        self.executed: list[tuple[str, tuple]] = []

    async def execute(self, query, *args):
        self.executed.append((" ".join(query.split()), args))

    async def fetchval(self, query, *args):
        if "max(datetime)" in query:
            return self.latest_saved
        if "last_checked_at" in query:
            return self.last_checked
        raise AssertionError(query)

    def queries(self, word):
        return [(q, a) for q, a in self.executed if word in q]


@pytest.mark.asyncio
async def test_record_check_upserts_next_time_and_removes_lower_levels():
    db = _FakeDB(last_checked=NOW - 7 * DAY)
    await schedule.record_check(db, "#A", level=20, times=_times(*range(21)), fetched_count=25, new_count=21, extras_fetched=True, now=NOW)
    (query, args), = db.queries("INSERT INTO player_update_schedule")
    assert args[0] == "#A" and args[2] == NOW and args[3] == NOW
    assert 6.6 * DAY < args[1] - NOW < 6.7 * DAY
    # MeowAPI・BSInfo を取っていなければ、その時刻は前の値のまま（NULL を渡して COALESCE）
    db = _FakeDB()
    await schedule.record_check(db, "#A", level=30, times=[], fetched_count=0, new_count=0, extras_fetched=False, now=NOW, last_checked_at=None)
    (query, args), = db.queries("INSERT INTO player_update_schedule")
    assert args[1] == NOW + schedule.AUTO_IDLE_INTERVAL and args[3] is None and "COALESCE" in query
    # level 20未満は行を消す
    db = _FakeDB()
    await schedule.record_check(db, "#A", level=10, times=[], fetched_count=0, new_count=0, extras_fetched=True, now=NOW)
    assert [q for q, _ in db.executed] == ["DELETE FROM player_update_schedule WHERE tag = $1"]


# --- 3段の確認 ---

def _row(level=30, *, extras_updated_at=None, last_checked_at=None, auto_track_expiration=None):
    return {"tag": "#A", "level": level, "auto_track_expiration": auto_track_expiration,
            "last_checked_at": last_checked_at, "extras_updated_at": extras_updated_at}


@pytest.fixture
def probe(monkeypatch):
    """公式APIのバトル履歴と、段2・段3の取得を差し替える。"""
    state = {"battlelog": {"items": []}, "refreshed": []}

    async def get_battlelog(tag):
        value = state["battlelog"]
        if isinstance(value, Exception):
            raise value
        return value

    async def refresh(tag, db, *, extras, battlelog):
        state["refreshed"].append(extras)
        return SimpleNamespace(fetched_battle_count=len(battlelog["items"]), new_battle_count=1)

    monkeypatch.setattr(official_api, "get_battlelog", get_battlelog)
    monkeypatch.setattr(player_update, "refresh_player_in_background", refresh)
    monkeypatch.setattr(schedule, "utcnow", lambda: NOW)
    return state


@pytest.mark.asyncio
async def test_check_ends_at_stage1_without_new_battles(probe):
    probe["battlelog"] = {"items": [{"battleTime": _api_time(NOW - 30 * MIN)}]}
    db = _FakeDB(latest_saved=NOW - 30 * MIN)
    result = await player_update.check_player(db, "auto", _row(extras_updated_at=NOW - 60 * MIN))
    assert (result.stage, result.fetched, result.new) == (1, 1, 0)
    assert probe["refreshed"] == []
    (query, args), = db.queries("INSERT INTO player_update_schedule")
    assert args[1] == NOW + schedule.AUTO_IDLE_INTERVAL and args[3] is None


@pytest.mark.asyncio
async def test_check_goes_to_stage2_with_new_battles_and_stage3_when_extras_are_due(probe):
    probe["battlelog"] = {"items": [{"battleTime": _api_time(NOW - 2 * MIN)}, {"battleTime": _api_time(NOW - 30 * MIN)}]}
    db = _FakeDB(latest_saved=NOW - 30 * MIN)
    result = await player_update.check_player(db, "auto", _row(extras_updated_at=NOW - 60 * MIN))
    assert result.stage == 2 and probe["refreshed"] == [False]
    # 新しいバトルがなくても、MeowAPI・BSInfo を取る時期なら段3
    db = _FakeDB(latest_saved=NOW - 2 * MIN)
    result = await player_update.check_player(db, "auto", _row(extras_updated_at=NOW - 25 * 60 * MIN))
    assert result.stage == 3 and probe["refreshed"] == [False, True]


@pytest.mark.asyncio
async def test_check_treats_404_as_no_battles_and_raises_other_errors(probe):
    probe["battlelog"] = official_api.OfficialAPIError(404, "Not Found")
    db = _FakeDB(latest_saved=NOW - DAY)
    result = await player_update.check_player(db, "auto", _row(extras_updated_at=NOW - 60 * MIN))
    assert (result.stage, result.fetched) == (1, 0)
    probe["battlelog"] = official_api.OfficialAPIError(503, "Service Unavailable")
    with pytest.raises(brawl_service.BrawlStarsAPIError):
        await player_update.check_player(db, "auto", _row(extras_updated_at=NOW - 60 * MIN))


@pytest.mark.asyncio
async def test_stage1_applies_level_changes(probe):
    # 自動追跡の期限切れ → level 20（アクティブの間隔で予定する）
    probe["battlelog"] = {"items": [{"battleTime": _api_time(NOW - DAY)}]}
    db = _FakeDB(latest_saved=NOW - DAY)
    row = _row(30, extras_updated_at=NOW - 60 * MIN, auto_track_expiration=NOW - 60 * MIN)
    await player_update.check_player(db, "auto", row)
    assert db.queries("auto_track_expiration = NULL")
    (query, args), = db.queries("INSERT INTO player_update_schedule")
    assert args[1] == NOW + schedule.ACTIVE_MAX_INTERVAL
    # アクティブで最新のバトルが5日より前 → level 10（予定の行を消す）
    probe["battlelog"] = {"items": [{"battleTime": _api_time(NOW - 6 * DAY)}]}
    db = _FakeDB(latest_saved=NOW - 6 * DAY)
    await player_update.check_player(db, "active", _row(20, extras_updated_at=NOW - DAY, last_checked_at=NOW - 7 * DAY))
    assert db.queries("SET level = 10") and db.queries("DELETE FROM player_update_schedule")


# --- 段2: MeowAPI・BSInfo を呼ばず、段1のバトル履歴を使い回す ---

@pytest.mark.asyncio
async def test_requests_without_extras_skip_meow_bsinfo_and_battlelog(monkeypatch):
    called = []

    def record(name, value):
        async def call(*args, **kwargs):
            called.append(name)
            return value
        return call

    monkeypatch.setattr(brawl_service.official_api, "get_player", record("player", {}))
    monkeypatch.setattr(brawl_service.official_api, "get_battlelog", record("battlelog", {"items": []}))
    monkeypatch.setattr(brawl_service.meowapi, "get_player", record("meow", None))
    monkeypatch.setattr(brawl_service.bsinfoapi, "get_player_owned_skins", record("skins", None))
    prefetched = {"items": [{"battleTime": "x"}]}
    requests = brawl_service._PlayerAPIRequests("#A", extras=False, battlelog=prefetched)
    results = await asyncio.gather(requests.player, requests.battlelog, requests.meow, requests.owned_skins)
    await requests.discard()
    assert called == ["player"]
    assert results[1] is prefetched and results[2] is None and results[3] is None


# --- 集計と回の進め方 ---

@pytest.mark.asyncio
async def test_select_round_takes_overdue_active_players_first(monkeypatch):
    monkeypatch.setattr(player_update.settings, "PLAYER_UPDATE_AUTO_PER_HOUR", 120)  # 1回10人
    monkeypatch.setattr(player_update.settings, "PLAYER_UPDATE_ACTIVE_PER_HOUR", 36)  # 1回3人
    monkeypatch.setattr(player_update.settings, "PLAYER_UPDATE_INACTIVE_PER_HOUR", 12)  # 1回1人
    calls = []

    class SelectDB:
        async def fetch(self, query, *args):
            calls.append((" ".join(query.split()), args))
            if "WITH auto" in query:
                return [{"tag": "#AUTO"}]
            if "s.last_checked_at <= $1" in query:
                assert args[0] == NOW - schedule.ACTIVE_MAX_INTERVAL and args[2] == 3
                return [{"tag": "#OLD"}]
            if "<> ALL" in query:
                assert args[1] == ["#OLD"] and args[2] == 2  # 残りの枠
                return [{"tag": "#DUE1"}, {"tag": "#DUE2"}]
            if "level = 10" in query:
                return [{"tag": "#INACTIVE"}]
            raise AssertionError(query)

    items = await player_update.select_round(SelectDB(), NOW)
    assert [(g, r["tag"]) for g, r in items] == [
        ("auto", "#AUTO"), ("active", "#OLD"), ("active", "#DUE1"), ("active", "#DUE2"), ("inactive", "#INACTIVE"),
    ]
    assert calls[0][1][1] == 10
    # 自動追跡は、次の時刻が来た人に加えて、最後の確認から1時間を過ぎた人も選ぶ（6.5 の競合で長く待たされないため）
    auto_query, auto_args = calls[0]
    assert "s.next_update_at <= $1 OR s.last_checked_at <= $1 - $3::interval" in auto_query
    assert "ORDER BY LEAST(s.next_update_at, s.last_checked_at + $3::interval)" in auto_query
    assert auto_args[0] == NOW and auto_args[2] == schedule.AUTO_IDLE_INTERVAL
    # 上限を過ぎた人で枠が埋まれば、時刻の来た人は選ばない
    calls.clear()

    class FullDB(SelectDB):
        async def fetch(self, query, *args):
            if "s.last_checked_at <= $1" in query:
                return [{"tag": f"#OLD{i}"} for i in range(3)]
            return await super().fetch(query, *args)

    items = await player_update.select_round(FullDB(), NOW)
    assert [r["tag"] for g, r in items if g == "active"] == ["#OLD0", "#OLD1", "#OLD2"]
    assert not any("<> ALL" in q for q, _ in calls)


def test_per_round():
    assert player_update.per_round(0) == 0
    assert player_update.per_round(100) == 9  # 100 × 5分 / 60分 = 8.3 → 切り上げ
    assert player_update.per_round(2400) == 200


def test_update_stats_lines():
    stats = player_update.UpdateStats()
    stats.add("auto", 0.2, player_update.CheckResult(stage=1, fetched=25, new=0))
    stats.add("auto", 0.6, player_update.CheckResult(stage=2, fetched=25, new=12))
    stats.add("auto", 1.0, player_update.CheckResult(stage=3, fetched=25, new=25))
    stats.add("active", 2.0, error=True)
    stats.add("inactive", 0.1, player_update.CheckResult(stage=None))
    auto, active, inactive = stats.lines()
    assert auto.startswith("自動追跡 3人: 段1(バトル履歴だけ) 1人・段2(＋公式のプロフィール) 1人・段3(＋MeowAPI・BSInfo) 1人 / 処理 平均0.60秒・最大1.0秒")
    assert "0件 1人・1件以上 1人・25件すべて新しい(取りこぼしの可能性) 1人（計37件）" in auto
    assert "エラー 1人" in active and "取得なし 1人" in inactive


@pytest.mark.asyncio
async def test_run_round_limits_concurrency_and_spreads_starts(monkeypatch):
    monkeypatch.setattr(player_update, "ROUND_SECONDS", 0.5)
    monkeypatch.setattr(player_update.settings, "PLAYER_UPDATE_CONCURRENCY", 2)
    state = {"running": 0, "max": 0, "done": [], "starts": []}

    async def fake_process(group, row, round_state, stats):
        state["starts"].append(time.monotonic())
        state["running"] += 1
        state["max"] = max(state["max"], state["running"])
        await asyncio.sleep(0.15)
        state["running"] -= 1
        state["done"].append(row["tag"])

    monkeypatch.setattr(player_update, "_process", fake_process)
    items = [("auto", {"tag": f"#{i}"}) for i in range(6)]
    await player_update.run_round(items, player_update.UpdateStats())
    assert sorted(state["done"]) == sorted(row["tag"] for _, row in items)
    assert state["max"] <= 2
    # 回の9割（0.45秒）に散らす: 最後の開始は 0.45 × 5/6 秒ほど後
    assert state["starts"][-1] - state["starts"][0] >= 0.3


def test_save_battles_records_how_many_were_new(monkeypatch):
    saved = {"t3"}  # 新しい順に t1, t2, t3, t4。t3 から先は保存済み

    async def fake_insert(db, tag, battle):
        return battle.key in saved

    async def fake_fill(db, tag, battles):
        return None

    monkeypatch.setattr(brawl_service, "insert_api_battle", fake_insert)
    monkeypatch.setattr(brawl_service, "fill_missing_result_data", fake_fill)
    battles = [SimpleNamespace(key=f"t{i}", battle_type="ranked", datetime=NOW) for i in range(1, 5)]
    player = brawl_service.Player("#TEST", None)
    player.level = 30  # アクティブ判定の更新（DB への書き込み）を起こさない
    assert player.fetched_battle_count is None and player.new_battle_count is None
    asyncio.run(player._save_battles(battles))
    assert (player.fetched_battle_count, player.new_battle_count) == (4, 2)
    saved.clear()
    asyncio.run(player._save_battles(battles))
    assert (player.fetched_battle_count, player.new_battle_count) == (4, 4)
