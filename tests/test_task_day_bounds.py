"""1日1回のタスクの「本日成功済み」の区切りのテスト。

UTC の日付で区切ると、前の回が UTC 0時(JST 9時)をまたいで終わったとき、その日の予定時刻の実行が飛ばされていた。
"""
import datetime as dt

import pytest

from app.background_tasks import task_runner
from app.background_tasks.task_registry import get_task

UTC = dt.timezone.utc


def _utc(month, day, hour, minute=0, second=0):
    return dt.datetime(2026, month, day, hour, minute, second, tzinfo=UTC)


@pytest.mark.parametrize(
    ("now", "expected_start"),
    [
        # 予定時刻(UTC 20:05)の直後は、その日の予定時刻から
        (_utc(11, 23, 20, 5), _utc(11, 23, 20, 5)),
        # 予定時刻より前(JST 9時過ぎなど)は、前日の予定時刻から
        (_utc(11, 23, 0, 15), _utc(11, 22, 20, 5)),
        (_utc(11, 23, 20, 4, 59), _utc(11, 22, 20, 5)),
    ],
)
def test_task_day_starts_at_scheduled_time(now, expected_start):
    start, end = task_runner.task_day_bounds(get_task("accessory_stats"), now)
    assert (start, end) == (expected_start, expected_start + dt.timedelta(days=1))


def test_previous_run_crossing_utc_midnight_does_not_count_for_next_day():
    """11-22 JST 5:05 に載り 9:15 に終わった回が、11-23 JST 5:05 の回を止めないこと。"""
    task = get_task("accessory_stats")
    created = _utc(11, 21, 20, 5)
    finished = _utc(11, 22, 0, 15)
    # 終わった直後(JST 9:15)は、その日の分として成功済み
    assert task_runner._success_in_task_day(task, created_at=created, finished_at=finished, now=_utc(11, 22, 0, 20))
    # 翌日の予定時刻には成功済みではない
    assert not task_runner._success_in_task_day(task, created_at=created, finished_at=finished, now=_utc(11, 22, 20, 5))


def test_prestige_day_starts_before_its_catchup_window():
    """prestige_borders は予定時刻(UTC 7:59)の15分前から追い実行で動くので、区切りもそこに寄せる。"""
    task = get_task("prestige_borders")
    start, _ = task_runner.task_day_bounds(task, _utc(11, 24, 7, 44, 4))
    assert start == _utc(11, 24, 7, 44)
    catchup_created = _utc(11, 24, 7, 44, 4)
    # 7:44 に追い実行で成功していれば、7:59 の予定時刻の回は成功済みとして止まる
    assert task_runner._success_in_task_day(
        task, created_at=catchup_created, finished_at=catchup_created, now=_utc(11, 24, 7, 59)
    )
    # 前日の追い実行は数えない
    assert not task_runner._success_in_task_day(
        task, created_at=catchup_created - dt.timedelta(days=1), finished_at=catchup_created, now=_utc(11, 24, 7, 44, 4)
    )


def test_task_without_schedule_keeps_utc_day_by_finish_time():
    now = _utc(11, 24, 12)
    assert task_runner.task_day_bounds(None, now) == (_utc(11, 24, 0), _utc(11, 25, 0))
    assert task_runner._success_in_task_day(None, created_at=_utc(11, 23, 23), finished_at=_utc(11, 24, 0, 30), now=now)


class _LedgerDB:
    def __init__(self):
        self.query = ""
        self.args: tuple = ()

    async def fetchval(self, query, *args):
        self.query = query
        self.args = args
        return None


async def test_has_success_today_filters_scheduled_task_by_queue_time():
    db = _LedgerDB()
    await task_runner._has_success_today(db, "accessory_stats", now=_utc(11, 23, 20, 5))
    assert "created_at >= $2" in db.query
    assert db.args[1:] == (_utc(11, 23, 20, 5), _utc(11, 24, 20, 5))
