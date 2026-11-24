"""裏の更新（ワーカー）で、次にいつプレイヤーのバトル履歴を確認するかを決め、player_update_schedule に書く。

自動追跡（level 30以上）とアクティブ（level 20）の人だけが対象。閲覧で取得したときも、次の確認の時刻を決め直す
（閲覧の多い人はワーカーで確認しなくて済む）。決め方は docs/plans/player_update_plan_2026-11-16.md 3章。
"""
import datetime
from collections.abc import Sequence
from typing import Any

import asyncpg

from app.utils.utils import parse_api_utc_datetime

# [この部分は公開用リポジトリでは非公開にされています]


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def group_for_level(level: int | None) -> str | None:
    """level から裏の更新の種類を返す。自動追跡は "auto"、アクティブは "active"、それ以外は対象外（None）。"""
    if level is None:
        return None
    if level >= 30:
        return "auto"
    if level >= 20:
        return "active"
    return None


def battle_times(items: Sequence[dict[str, Any]]) -> list[datetime.datetime]:
    """公式APIのバトル履歴の items から、バトルの時刻（UTC）を新しい順に取り出す。読めない時刻は飛ばす。"""
    times = []
    for item in items:
        try:
            times.append(parse_api_utc_datetime(item["battleTime"]))
        except (KeyError, TypeError, ValueError):
            continue
    times.sort(reverse=True)
    return times


def count_new_battles(times: Sequence[datetime.datetime], latest_saved: datetime.datetime | None) -> int:
    """保存済みの最新のバトルより新しいバトルの数。"""
    if latest_saved is None:
        return len(times)
    return sum(1 for t in times if t > latest_saved)


def extras_due(group: str, extras_updated_at: datetime.datetime | None, now: datetime.datetime) -> bool:
    """MeowAPI・BSInfo（段3）を取る時期か。"""
    # [この部分は公開用リポジトリでは非公開にされています]


def next_check_delay(
    group: str,
    *,
    now: datetime.datetime,
    times: Sequence[datetime.datetime],
    fetched_count: int,
    new_count: int,
    last_checked_at: datetime.datetime | None,
) -> datetime.timedelta:
    """次の確認までの時間。times は今回届いたバトルの時刻（新しい順）。"""
    # [この部分は公開用リポジトリでは非公開にされています]


_MISSING: Any = object()


async def record_check(
    db: asyncpg.Connection,
    tag: str,
    *,
    level: int | None,
    times: Sequence[datetime.datetime],
    fetched_count: int,
    new_count: int,
    extras_fetched: bool,
    extras_attempted: bool = False,
    now: datetime.datetime | None = None,
    last_checked_at: datetime.datetime | None = _MISSING,
) -> None:
    """バトル履歴を確認した（閲覧・ワーカーのどちらでも）ことを記録し、次の確認の時刻を決める。

    level 20未満の人は対象外なので、行があれば消す。extras_fetched は MeowAPI・BSInfo も取れたか。
    extras_attempted は取りに行ったか（取れなかったときは EXTRAS_RETRY_AFTER 後に取り直す）。
    last_checked_at を省くと、前回の確認の時刻を表から読む。
    """
    group = group_for_level(level)
    if group is None:
        await db.execute("DELETE FROM player_update_schedule WHERE tag = $1", tag)
        return
    now = now or utcnow()
    if last_checked_at is _MISSING:
        last_checked_at = await db.fetchval("SELECT last_checked_at FROM player_update_schedule WHERE tag = $1", tag)
    delay = next_check_delay(
        group, now=now, times=times, fetched_count=fetched_count, new_count=new_count, last_checked_at=last_checked_at,
    )
    extras_updated_at = extras_time(group, now, fetched=extras_fetched, attempted=extras_attempted)
    # GREATEST は NULL を無視するので、取りに行かなかった（None）ときは今の値のまま、取り直しの時刻は今の値より新しいときだけ進める
    await db.execute(
        """
        INSERT INTO player_update_schedule AS s (tag, next_update_at, last_checked_at, extras_updated_at)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (tag) DO UPDATE SET
            next_update_at = EXCLUDED.next_update_at,
            last_checked_at = EXCLUDED.last_checked_at,
            extras_updated_at = GREATEST(s.extras_updated_at, EXCLUDED.extras_updated_at)
        """,
        tag, now + delay, now, extras_updated_at,
    )


def extras_time(group: str, now: datetime.datetime, *, fetched: bool, attempted: bool) -> datetime.datetime | None:
    """extras_updated_at に書く時刻。取れたら今。取りに行って失敗したら、EXTRAS_RETRY_AFTER 後に取る時期が来る時刻
    （今 − 間隔 ＋ EXTRAS_RETRY_AFTER）。取りに行かなかったら None（今の値のまま）。"""
    if fetched:
        return now
    # [この部分は公開用リポジトリでは非公開にされています]
    return None


async def postpone_after_error(db: asyncpg.Connection, tag: str, now: datetime.datetime | None = None) -> None:
    """確認に失敗した人を、少し後にもう一度試す（すぐに選び直さないため）。"""
    now = now or utcnow()
    await db.execute(
        "UPDATE player_update_schedule SET next_update_at = $2 WHERE tag = $1",
        tag, now + ERROR_RETRY_INTERVAL,
    )


async def schedule_soon(db: asyncpg.Connection, tag: str, within: datetime.timedelta = datetime.timedelta(minutes=30)) -> None:
    """自動追跡が有効になった人を、遅くとも within 後に確認する（アクティブの間隔のまま待たせないため）。"""
    await db.execute(
        """
        INSERT INTO player_update_schedule AS s (tag, next_update_at)
        SELECT $1, now() + $2::interval
        WHERE EXISTS (SELECT 1 FROM players WHERE tag = $1)
        ON CONFLICT (tag) DO UPDATE SET next_update_at = LEAST(s.next_update_at, EXCLUDED.next_update_at)
        """,
        tag, within,
    )


async def sync_with_levels(db: asyncpg.Connection) -> tuple[int, int]:
    """level 20未満・無効・消えた人の行を消し、行のない level 20以上の人を足す（毎日の降格処理の後に呼ぶ）。

    Returns:
        (消した数, 足した数)
    """
    deleted = await db.fetchval(
        """
        WITH removed AS (
            DELETE FROM player_update_schedule s
            WHERE NOT EXISTS (
                SELECT 1 FROM players p WHERE p.tag = s.tag AND p.level >= 20 AND p.is_invalid = FALSE
            )
            RETURNING 1
        )
        SELECT count(*) FROM removed
        """
    )
    added = await db.fetchval(
        """
        WITH added AS (
            INSERT INTO player_update_schedule (tag, next_update_at)
            SELECT p.tag, now()
            FROM players p
            WHERE p.level >= 20 AND p.is_invalid = FALSE
              AND NOT EXISTS (SELECT 1 FROM player_update_schedule s WHERE s.tag = p.tag)
            ON CONFLICT (tag) DO NOTHING
            RETURNING 1
        )
        SELECT count(*) FROM added
        """
    )
    return int(deleted or 0), int(added or 0)
