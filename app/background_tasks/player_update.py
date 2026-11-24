"""プレイヤーアップデート（ワーカーの裏の更新）のループ。

5分ごとの回で、次の確認の時刻（player_update_schedule）が来た自動追跡・アクティブの人と、非アクティブの人を選び、
回の中に均等に散らして、決まった数ずつ同時に更新する。自動追跡・アクティブの人は3段に分けて、要るものだけ呼ぶ
（notes_private/player_update_plan_2026-11-16.md 3.2）。

- 段1: 公式APIのバトル履歴だけ。新しいバトルがなく、MeowAPI・BSInfo を取る時期でもなければ、ここで終わる。
- 段2: 新しいバトルがあれば、公式APIのプロフィールも取って保存する（ガチバトルのポイントはプロフィールにしかない）。
- 段3: MeowAPI・BSInfo を取る時期なら、それらも取る（閲覧と同じフルの取得）。

非アクティブの人は、これまでどおり最後の更新が古い順に、フルの取得をする。
"""
import asyncio
import datetime
import math
import time
from dataclasses import dataclass
from typing import Any

import asyncpg

from app.core.config import settings
from app.core.logger import logger
from app.db.db import get_db_connection_for_bg_task, is_transient_pg_error, log_transient_pg_warning
from app.exceptions.custom_exceptions import BrawlStarsAPIError, DataBaseError
from app.services import official_api, player_update_schedule as schedule
from app.services.brawl_service import get_player, refresh_player_in_background
from app.utils.utils import is_expired

# [この部分は公開用リポジトリでは非公開にされています]

GROUP_LABELS = {"auto": "自動追跡", "active": "アクティブ", "inactive": "非アクティブ"}


@dataclass
class CheckResult:
    """1人ぶんの結果。stage は 1〜3（非アクティブのフルの取得は3）、取得しなかった（キャッシュ）ときは None。"""
    stage: int | None
    fetched: int | None = None
    new: int | None = None


class UpdateStats:
    """一定時間ぶんの集計（種類ごと）。取得の選び方の見直しの材料としてログに出す。"""

    def __init__(self) -> None:
        self.groups: dict[str, dict[str, float]] = {}

    def _group(self, group: str) -> dict[str, float]:
        return self.groups.setdefault(group, {
            "n": 0, "seconds": 0.0, "max_seconds": 0.0, "errors": 0, "not_fetched": 0,
            "stage1": 0, "stage2": 0, "stage3": 0,
            "new_none": 0, "new_some": 0, "new_all": 0, "new_battles": 0,
        })

    def add(self, group: str, seconds: float, result: CheckResult | None = None, *, error: bool = False) -> None:
        g = self._group(group)
        g["n"] += 1
        g["seconds"] += seconds
        g["max_seconds"] = max(g["max_seconds"], seconds)
        if error or result is None:
            g["errors"] += 1
            return
        if result.stage is None or result.fetched is None or result.new is None:
            g["not_fetched"] += 1  # 閲覧の直後でキャッシュを使った（非アクティブ）
            return
        g[f"stage{result.stage}"] += 1
        g["new_battles"] += result.new
        if result.new == 0:
            g["new_none"] += 1
        elif result.fetched >= schedule.BATTLELOG_PAGE_SIZE and result.new >= result.fetched:
            g["new_all"] += 1
        else:
            g["new_some"] += 1

    def lines(self) -> list[str]:
        out = []
        for key in ("auto", "active", "inactive"):
            g = self.groups.get(key)
            if not g or not g["n"]:
                continue
            out.append(
                f"{GROUP_LABELS[key]} {g['n']:.0f}人: "
                f"段1(バトル履歴だけ) {g['stage1']:.0f}人・段2(＋公式のプロフィール) {g['stage2']:.0f}人・段3(＋MeowAPI・BSInfo) {g['stage3']:.0f}人 / "
                f"処理 平均{g['seconds'] / g['n']:.2f}秒・最大{g['max_seconds']:.1f}秒 / "
                f"新しいバトル 0件 {g['new_none']:.0f}人・1件以上 {g['new_some']:.0f}人・"
                f"{schedule.BATTLELOG_PAGE_SIZE}件すべて新しい(取りこぼしの可能性) {g['new_all']:.0f}人（計{g['new_battles']:.0f}件） / "
                f"取得なし {g['not_fetched']:.0f}人・エラー {g['errors']:.0f}人"
            )
        return out


class ExtrasPause:
    """MeowAPI の障害中に、裏の更新で MeowAPI・BSInfo（段3）を取りに行かないようにする（回をまたいで続く）。

    失敗が EXTRAS_FAILURES_BEFORE_PAUSE 回続いたら EXTRAS_PAUSE_SECONDS の間は取りに行かない（新しいバトルがあれば段2）。
    止めた後は、取れるまで1回の失敗でまた止める（障害が続いている間は、30分に数回の様子見だけになる）。
    """

    def __init__(self) -> None:
        self.failures = 0
        self.paused_until = 0.0
        self.tripped = False  # 止めてから、まだ一度も取れていない

    def is_paused(self) -> bool:
        return time.monotonic() < self.paused_until

    def record(self, success: bool) -> None:
        if success:
            if self.tripped:
                logger.info("プレイヤーアップデート: MeowAPI から取れるようになったので、MeowAPI・BSInfo の取得を戻します。")
            self.failures = 0
            self.tripped = False
            return
        self.failures += 1
        if self.is_paused():
            return
        if self.tripped or self.failures >= EXTRAS_FAILURES_BEFORE_PAUSE:
            logger.warning(
                f"プレイヤーアップデート: MeowAPI の取得の失敗が{self.failures}回続いたため、"
                f"{EXTRAS_PAUSE_SECONDS // 60}分間は MeowAPI・BSInfo を取りません（新しいバトルがある人は公式APIだけで更新します）。"
            )
            self.paused_until = time.monotonic() + EXTRAS_PAUSE_SECONDS
            self.tripped = True


_extras_pause = ExtrasPause()


def per_round(per_hour: int) -> int:
    """1時間あたりの上限を、1回あたりの人数にする（端数は切り上げ）。"""
    if per_hour <= 0:
        return 0
    return math.ceil(per_hour * ROUND_SECONDS / 3600)


async def select_round(db: asyncpg.Connection, now: datetime.datetime) -> list[tuple[str, asyncpg.Record]]:
    """今回の回で更新する人を選ぶ（自動追跡 → アクティブ → 非アクティブの順）。"""
    items: list[tuple[str, asyncpg.Record]] = []
    # [この部分は公開用リポジトリでは非公開にされています]
    return items


async def _apply_level_changes_without_fetch(db: asyncpg.Connection, row: Any, times: list[datetime.datetime], now: datetime.datetime) -> int:
    """段1で終わる人の level の付け替え（自動追跡の期限切れ・5日より前のバトルで非アクティブ）。付け替え後の level を返す。"""
    level = row["level"]
    if level >= 30 and row["auto_track_expiration"] and is_expired(row["auto_track_expiration"]):
        await db.execute(
            "UPDATE players SET level = LEAST(level, 20), auto_track_expiration = NULL WHERE tag = $1 AND level >= 30",
            row["tag"],
        )
        level = 20
    if level == 20 and times and now - times[0] > INACTIVE_AFTER:
        await db.execute("UPDATE players SET level = 10 WHERE tag = $1 AND level = 20", row["tag"])
        level = 10
    return level


async def check_player(db: asyncpg.Connection, group: str, row: Any) -> CheckResult:
    """自動追跡・アクティブの人を1人確認する（段1〜3）。"""
    # [この部分は公開用リポジトリでは非公開にされています]


async def _update_inactive(db: asyncpg.Connection, tag: str) -> CheckResult:
    """非アクティブの人は、これまでどおりフルの取得をする（閲覧の直後ならキャッシュを使う）。"""
    player = await get_player(tag, db, is_bg_task=True)
    if player.fetched_battle_count is None or player.new_battle_count is None:
        return CheckResult(stage=None)
    return CheckResult(stage=3, fetched=player.fetched_battle_count, new=player.new_battle_count)


class _RoundState:
    def __init__(self) -> None:
        self.api_errors = 0  # 続いている公式APIのエラーの数（並列なのでおおよそ）


async def _postpone(tag: str) -> None:
    try:
        async with get_db_connection_for_bg_task() as db:
            await schedule.postpone_after_error(db, tag)
    except Exception as e:
        logger.debug(f"プレイヤー {tag} の次の確認を後ろにずらせませんでした: {e}")


async def _process(group: str, row: Any, state: _RoundState, stats: UpdateStats) -> None:
    tag = row["tag"]
    started = time.monotonic()
    try:
        async with get_db_connection_for_bg_task() as db:
            if group == "inactive":
                result = await _update_inactive(db, tag)
            else:
                result = await check_player(db, group, row)
    except asyncio.CancelledError:
        raise
    except BrawlStarsAPIError as e:
        stats.add(group, time.monotonic() - started, error=True)
        state.api_errors += 1
        logger.warning(f"プレイヤー {tag}のアップデート処理中にAPIエラーが発生しました: {e} {state.api_errors}連続のAPIエラーです。")
        if group != "inactive":
            await _postpone(tag)
    except DataBaseError as e:
        stats.add(group, time.monotonic() - started, error=True)
        logger.error(f"プレイヤー {tag}のアップデート処理中にデータベースエラーが発生しました: {e}", exc_info=True)
        await asyncio.sleep(60)
    except Exception as e:
        stats.add(group, time.monotonic() - started, error=True)
        if is_transient_pg_error(e):
            log_transient_pg_warning(f"プレイヤー {tag}のアップデート処理中に接続エラー: {e}")
        else:
            logger.error(f"プレイヤー {tag}のアップデート処理中に予期せぬエラーが発生しました: {e}", exc_info=True)
        if group != "inactive":
            await _postpone(tag)
        await asyncio.sleep(60)
    else:
        state.api_errors = 0
        stats.add(group, time.monotonic() - started, result)


async def run_round(items: list[tuple[str, Any]], stats: UpdateStats, ctx=None) -> None:
    """選んだ人を、回の中に均等に散らして、決まった数ずつ同時に更新する。"""
    if not items:
        return
    started = time.monotonic()
    spacing = ROUND_SECONDS * SPREAD_RATIO / len(items)
    semaphore = asyncio.Semaphore(settings.PLAYER_UPDATE_CONCURRENCY)
    state = _RoundState()
    tasks: set[asyncio.Task] = set()

    async def worker(group: str, row: Any) -> None:
        try:
            await _process(group, row, state, stats)
        finally:
            semaphore.release()

    try:
        for index, (group, row) in enumerate(items, start=1):
            if ctx is not None and ctx.should_stop:
                raise asyncio.CancelledError
            wait = started + (index - 1) * spacing - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            if state.api_errors >= API_ERRORS_BEFORE_PAUSE:
                # 公式APIがメンテナンス中の可能性が高いので、1人ごとに待って試す
                await asyncio.sleep(API_ERROR_PAUSE_SECONDS)
            await semaphore.acquire()
            task = asyncio.create_task(worker(group, row))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
            if ctx is not None and (index % 25 == 0 or index == len(items)):
                await ctx.set_progress(
                    step="updating",
                    current=index,
                    total=len(items),
                    waiting_time_sec=round(spacing, 2),
                    message=f"{index}/{len(items)}人",
                )
        if tasks:
            await asyncio.gather(*tasks)
    finally:
        remaining = list(tasks)  # 止められたときだけ残っている
        for task in remaining:
            task.cancel()
        if remaining:
            await asyncio.gather(*remaining, return_exceptions=True)


async def _log_stats(stats: UpdateStats, seconds: float) -> None:
    for line in stats.lines():
        logger.info(f"プレイヤーアップデートの集計（{seconds / 60:.0f}分）: {line}")
    try:
        async with get_db_connection_for_bg_task() as db:
            due = await db.fetchval("SELECT count(*) FROM player_update_schedule WHERE next_update_at <= now()")
        logger.info(f"プレイヤーアップデート: 次の確認の時刻を過ぎている人は {due}人です（自動追跡・アクティブ。level の変わった人を含む）。")
    except Exception as e:
        logger.warning(f"プレイヤーアップデートの待ちの人数を数えられませんでした: {e}")


async def _sleep_until(deadline: float, ctx=None) -> None:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        if ctx is not None and ctx.should_stop:
            raise asyncio.CancelledError
        await asyncio.sleep(min(remaining, 5))


async def player_update_task_loop(ctx=None) -> None:
    """プレイヤーデータを定期的に更新するループタスク（内部で接続管理）"""
    logger.info("プレイヤーアップデートタスクループを開始します。")
    try:
        if not settings.PLAYER_UPDATE_ENABLED:
            logger.info("PLAYER_UPDATE_ENABLED が無効のため、プレイヤーアップデートは行いません。")
            if ctx is not None:
                await ctx.set_progress(step="disabled", message="停止中（PLAYER_UPDATE_ENABLED）")
            while True:
                await _sleep_until(time.monotonic() + 3600, ctx)

        logger.info(
            f"プレイヤーアップデート: 1時間の上限 自動追跡{settings.PLAYER_UPDATE_AUTO_PER_HOUR}人・"
            f"アクティブ{settings.PLAYER_UPDATE_ACTIVE_PER_HOUR}人・非アクティブ{settings.PLAYER_UPDATE_INACTIVE_PER_HOUR}人、"
            f"同時に{settings.PLAYER_UPDATE_CONCURRENCY}人、1回{ROUND_SECONDS // 60}分"
        )
        stats = UpdateStats()
        stats_started = time.monotonic()
        while True:
            round_started = time.monotonic()
            try:
                async with get_db_connection_for_bg_task() as db:
                    items = await select_round(db, schedule.utcnow())
                counts = {g: sum(1 for group, _ in items if group == g) for g in GROUP_LABELS}
                logger.debug(
                    f"プレイヤーアップデートの回: 自動追跡{counts['auto']}人・アクティブ{counts['active']}人・非アクティブ{counts['inactive']}人"
                )
                await run_round(items, stats, ctx)
            except asyncio.CancelledError:
                logger.info("プレイヤーアップデートタスクループがキャンセルされました。")
                raise
            except asyncpg.PostgresError as db_err:
                logger.error(f"プレイヤーアップデートタスクループ DBエラー: {db_err}", exc_info=True)
                await asyncio.sleep(60)
            except Exception as e:
                if is_transient_pg_error(e):
                    log_transient_pg_warning(f"プレイヤーアップデートタスクループで接続エラー: {e}")
                else:
                    logger.error(f"プレイヤーアップデートタスクループで予期せぬエラー: {e}", exc_info=True)
                await asyncio.sleep(60)

            elapsed = time.monotonic() - stats_started
            if elapsed >= STATS_INTERVAL_SECONDS:
                await _log_stats(stats, elapsed)
                stats = UpdateStats()
                stats_started = time.monotonic()
            await _sleep_until(round_started + ROUND_SECONDS, ctx)
    finally:
        logger.info("プレイヤーアップデートタスクループが終了しました。")
