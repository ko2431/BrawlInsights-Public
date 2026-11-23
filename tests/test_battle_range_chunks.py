"""battles を日時の範囲で読む集計を1時間ずつに分けたこと、失敗の通知の本文が空にならないことのテスト。"""
import datetime as dt
import logging
import sys

import pytest

from app.core.admin_notification_log_handler import _record_to_payload
from app.core.logger import describe_exception
from app.exceptions.custom_exceptions import DataBaseError
from app.services import ranked_map_pool_service as ranked
from app.services import trophy_stats_service as trophy

UTC = dt.timezone.utc


def _raise_wrapped_timeout():
    try:
        raise TimeoutError()
    except TimeoutError as e:
        raise DataBaseError(e) from e


#* /---*---*---*---*---*---*---*---*/
#* 例外の説明・通知の本文
#* /---*---*---*---*---*---*---*---*/
def test_describe_exception_uses_message_when_present():
    assert describe_exception(ValueError("boom")) == "boom"


def test_describe_exception_falls_back_to_type_name():
    assert describe_exception(TimeoutError()) == "TimeoutError"


def test_describe_exception_follows_cause_of_empty_wrapper():
    with pytest.raises(DataBaseError) as info:
        _raise_wrapped_timeout()
    assert str(info.value) == ""
    assert describe_exception(info.value) == "TimeoutError"


def test_describe_exception_shows_cause_message_with_its_type():
    try:
        try:
            raise KeyError("result")
        except KeyError as e:
            raise RuntimeError("") from e
    except RuntimeError as wrapper:
        assert describe_exception(wrapper) == "KeyError: 'result'"


def _record_with_exception(message: str, exc_factory) -> logging.LogRecord:
    try:
        exc_factory()
    except Exception:
        exc_info = sys.exc_info()
    return logging.LogRecord("brawl_insights", logging.ERROR, "/x/tasks.py", 308, message, None, exc_info)


def test_log_notification_summary_names_empty_exception():
    record = _record_with_exception("マルチプレイ統計のアップデートタスクでエラーが発生しました: ", _raise_wrapped_timeout)
    payload = _record_to_payload(record)
    assert payload["event_key"] == "log_error"
    assert payload["summary"] == "[tasks.py:308] マルチプレイ統計のアップデートタスクでエラーが発生しました: TimeoutError"


def test_log_notification_summary_does_not_repeat_description():
    record = _record_with_exception("タスク 'trophy_stats' が失敗しました (run_id=1): TimeoutError", _raise_wrapped_timeout)
    assert _record_to_payload(record)["summary"].endswith("(run_id=1): TimeoutError")


def test_log_notification_summary_keeps_message_with_normal_exception():
    def fail():
        raise ValueError("boom")

    record = _record_with_exception("失敗しました: boom", fail)
    assert _record_to_payload(record)["summary"] == "[tasks.py:308] 失敗しました: boom"


#* /---*---*---*---*---*---*---*---*/
#* ガチバトルマップ: 1時間ずつ読む
#* /---*---*---*---*---*---*---*---*/
class _RankedDB:
    def __init__(self, last_synced=None):
        self.ranges: list[tuple[dt.datetime, dt.datetime]] = []
        self.written: list[tuple] = []
        self.last_synced = last_synced

    async def fetch(self, query, start, end):
        self.ranges.append((start, end))
        hour = start.replace(minute=0, second=0, microsecond=0)
        return [{"hour": hour, "map_id": 15000001, "mode_id": 2, "map_en": "Hard Rock Mine", "battle_count": 3}]

    async def executemany(self, query, args):
        self.written.extend(args)

    async def fetchval(self, query, *args):
        return self.last_synced


@pytest.fixture
def ranked_catalog(monkeypatch):
    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(ranked, "ensure_catalog", noop)
    monkeypatch.setattr(ranked, "ensure_map_stub", noop)
    monkeypatch.setattr(ranked, "get_map_by_id", lambda map_id: {"id": map_id})


async def test_aggregate_observations_reads_one_hour_at_a_time(ranked_catalog):
    db = _RankedDB()
    start = dt.datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
    end = dt.datetime(2026, 11, 22, 12, 30, tzinfo=UTC)

    stored = await ranked.aggregate_observations(db, start, end)

    assert db.ranges == [
        (start, dt.datetime(2026, 11, 22, 11, 0, tzinfo=UTC)),
        (dt.datetime(2026, 11, 22, 11, 0, tzinfo=UTC), dt.datetime(2026, 11, 22, 12, 0, tzinfo=UTC)),
        (dt.datetime(2026, 11, 22, 12, 0, tzinfo=UTC), end),
    ]
    assert stored == 3
    assert [row[0].hour for row in db.written] == [10, 11, 12]


async def test_aggregate_observations_aligns_chunks_to_the_hour(ranked_catalog):
    db = _RankedDB()
    start = dt.datetime(2026, 11, 22, 10, 15, tzinfo=UTC)
    end = dt.datetime(2026, 11, 22, 11, 45, tzinfo=UTC)

    await ranked.aggregate_observations(db, start, end)

    assert db.ranges == [
        (start, dt.datetime(2026, 11, 22, 11, 0, tzinfo=UTC)),
        (dt.datetime(2026, 11, 22, 11, 0, tzinfo=UTC), end),
    ]


async def test_aggregate_observations_backfill_day_is_24_queries(ranked_catalog):
    db = _RankedDB()
    start = dt.datetime(2026, 11, 22, tzinfo=UTC)

    await ranked.aggregate_observations(db, start, start + dt.timedelta(days=1), include_archive=True)

    assert len(db.ranges) == 24
    assert all(end - begin == dt.timedelta(hours=1) for begin, end in db.ranges)


#* /---*---*---*---*---*---*---*---*/
#* ガチバトルマップ: 途切れていた分をさかのぼる
#* /---*---*---*---*---*---*---*---*/
NOW = dt.datetime(2026, 11, 24, 10, 40, 12, tzinfo=UTC)


class _FakeRedis:
    def __init__(self, value: str | None = None):
        self.store: dict[str, bytes] = {}
        if value is not None:
            self.store[ranked.SYNC_LAST_AT_KEY] = value.encode()

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value):
        self.store[key] = value.encode() if isinstance(value, str) else value


@pytest.mark.parametrize(
    ("last_synced", "expected"),
    [
        (NOW - dt.timedelta(hours=6), dt.datetime(2026, 11, 22, 22, 0, tzinfo=UTC)),  # ふだん: 36時間
        (NOW - dt.timedelta(hours=8), dt.datetime(2026, 11, 22, 22, 0, tzinfo=UTC)),  # 少し遅れただけ: 36時間
        (dt.datetime(2026, 11, 22, 6, 40, 30, tzinfo=UTC), dt.datetime(2026, 11, 20, 18, 0, tzinfo=UTC)),  # 失敗が続いた: 前回の36時間前から
        (NOW - dt.timedelta(days=30), dt.datetime(2026, 11, 17, 10, 0, tzinfo=UTC)),  # 長く止まっていた: 7日まで
    ],
)
async def test_sync_start_at_catches_up_after_failed_syncs(monkeypatch, last_synced, expected):
    monkeypatch.setattr(ranked, "get_redis", lambda: _FakeRedis(last_synced.isoformat()))
    # 観測の最終更新は古くても、Redis の同期時刻を優先する(ガチバトルが無い間に毎回7日分読み直さない)
    db = _RankedDB(last_synced=NOW - dt.timedelta(days=10))
    assert await ranked._sync_start_at(db, NOW) == expected


async def test_sync_start_at_falls_back_to_observations_without_redis_key(monkeypatch):
    monkeypatch.setattr(ranked, "get_redis", lambda: _FakeRedis())
    db = _RankedDB(last_synced=dt.datetime(2026, 11, 22, 6, 40, 30, tzinfo=UTC))
    assert await ranked._sync_start_at(db, NOW) == dt.datetime(2026, 11, 20, 18, 0, tzinfo=UTC)


async def test_sync_start_at_without_redis_or_observations(monkeypatch):
    monkeypatch.setattr(ranked, "get_redis", lambda: None)
    assert await ranked._sync_start_at(_RankedDB(), NOW) == dt.datetime(2026, 11, 22, 22, 0, tzinfo=UTC)


async def test_remembered_sync_time_round_trips(monkeypatch):
    redis = _FakeRedis()
    monkeypatch.setattr(ranked, "get_redis", lambda: redis)
    await ranked._remember_synced_at(NOW)
    assert await ranked._last_synced_at(_RankedDB()) == NOW


#* /---*---*---*---*---*---*---*---*/
#* マルチプレイ統計: 1時間ずつ読む
#* /---*---*---*---*---*---*---*---*/
class _TrophyDB:
    def __init__(self):
        self.ranges: list[tuple[dt.datetime, dt.datetime]] = []
        self.executed: list[str] = []

    async def fetch(self, query, start, end):
        self.ranges.append((start, end))
        return []

    async def execute(self, query, *args):
        self.executed.append(query)


async def test_update_trophy_stats_reads_the_day_one_hour_at_a_time(monkeypatch):
    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(trophy, "ensure_catalog", noop)
    db = _TrophyDB()

    await trophy.update_trophy_stats(db, dt.date(2026, 11, 22))

    day_start = dt.datetime(2026, 11, 22, tzinfo=UTC)
    assert db.ranges == [
        (day_start + dt.timedelta(hours=h), day_start + dt.timedelta(hours=h + 1)) for h in range(24)
    ]
    assert any("DELETE FROM trophy_stats_brawler" in query for query in db.executed)
