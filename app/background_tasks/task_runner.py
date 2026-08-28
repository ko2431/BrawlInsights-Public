from __future__ import annotations

import asyncio
import os
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from inspect import signature
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg

from app.background_tasks.task_registry import (
    HEAVY_TASK_KEYS,
    TASKS,
    TaskDef,
    TaskKind,
    get_task,
    iter_tasks,
)
from app.core.cache import get_redis
from app.core.config import settings
from app.core.logger import logger
from app.db.db import get_db_connection_for_bg_task

PLAYER_UPDATE_START_DELAY_SEC = 30
SHUTDOWN_TIMEOUT_SEC = 45.0
HEARTBEAT_INTERVAL_SEC = 30
STALE_HEARTBEAT_SEC = 120
DISPATCH_POLL_SEC = 1.0
MAX_LIGHT_INFLIGHT = 4
HEAVY_LOCK_KEY = "lock:worker_task:heavy"
HEAVY_LOCK_TTL_SEC = 90
TASK_SUCCESS_ALIASES: dict[str, tuple[str, ...]] = {}
LEGACY_TASK_NAME_JA: dict[str, str] = {}
# [この部分は公開用リポジトリでは非公開にされています]
ERROR_MESSAGE_MAX_LEN = 2000
_JST = ZoneInfo("Asia/Tokyo")
QUIET_HOURS_END_HOUR_JST = 8
BACKUP_HOUR_JST = 3
PRESTIGE_CATCHUP_WINDOW_SEC = 15 * 60

_owned_run_ids: set[int] = set()
_inflight_tasks: dict[int, asyncio.Task[Any]] = {}
_heavy_run_id: int | None = None


def current_worker_id() -> str:
    return f"{settings.SERVER_NAME}:{os.getpid()}"


def utc_day_bounds(day: date | None = None) -> tuple[datetime, datetime]:
    if day is None:
        day = datetime.now(timezone.utc).date()
    start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def _truncate_error(message: str | None) -> str | None:
    if not message:
        return None
    if len(message) <= ERROR_MESSAGE_MAX_LEN:
        return message
    return message[: ERROR_MESSAGE_MAX_LEN - 1] + "…"


def format_duration_seconds(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "-"
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}時間{minutes}分"
    if minutes:
        return f"{minutes}分{secs}秒"
    return f"{secs}秒"


def next_scheduled_at(
    task: TaskDef,
    now: datetime | None = None,
    *,
    last_finished: datetime | None = None,
) -> datetime | None:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if task.kind == TaskKind.ALWAYS_ON:
        return None
    if task.interval_minutes:
        if last_finished is not None:
            base = last_finished
            if base.tzinfo is None:
                base = base.replace(tzinfo=timezone.utc)
            candidate = base + timedelta(minutes=task.interval_minutes)
            return candidate if candidate > now else now
        return now + timedelta(minutes=task.interval_minutes)
    if task.cron_hour_utc is None:
        return None
    candidate = now.replace(
        hour=task.cron_hour_utc,
        minute=task.cron_minute_utc,
        second=0,
        microsecond=0,
    )
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    ok: bool
    run_id: int | None
    reason: str
    already_succeeded: bool = False


class TaskContext:
    def __init__(self, run_id: int, shutdown_event: asyncio.Event | None = None) -> None:
        self.run_id = run_id
        self.shutdown_event = shutdown_event
        self.heavy_lock_token: str | None = None
        self._progress: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    @property
    def should_stop(self) -> bool:
        return bool(self.shutdown_event and self.shutdown_event.is_set())

    def get_checkpoint(self) -> dict[str, Any]:
        checkpoint = self._progress.get("checkpoint")
        return dict(checkpoint) if isinstance(checkpoint, dict) else {}

    async def save_checkpoint(self, **kwargs: Any) -> None:
        await self.set_progress(checkpoint={**self.get_checkpoint(), **kwargs})

    async def set_progress(self, **kwargs: Any) -> None:
        async with self._lock:
            self._progress.update(kwargs)
            progress = dict(self._progress)
        try:
            async with get_db_connection_for_bg_task() as db:
                await db.execute(
                    """
                    UPDATE worker_task_runs
                    SET progress = $2, heartbeat_at = NOW()
                    WHERE id = $1 AND status = 'running'
                    """,
                    self.run_id,
                    progress,
                )
        except Exception as e:
            logger.warning(f"タスク進捗の更新に失敗しました (run_id={self.run_id}): {e}")

    async def heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)
                try:
                    async with get_db_connection_for_bg_task() as db:
                        await db.execute(
                            """
                            UPDATE worker_task_runs
                            SET heartbeat_at = NOW()
                            WHERE id = $1 AND status = 'running'
                            """,
                            self.run_id,
                        )
                    if self.heavy_lock_token:
                        await _refresh_heavy_lock(self.heavy_lock_token)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(f"タスクハートビートの更新に失敗しました (run_id={self.run_id}): {e}")
        except asyncio.CancelledError:
            return

    async def mark_success(self, result: dict[str, Any] | None = None) -> None:
        await _finish_run(self.run_id, "success", result=result)

    async def mark_failed(self, error_message: str) -> None:
        await _finish_run(self.run_id, "failed", error_message=error_message)

    async def mark_interrupted(self, error_message: str) -> None:
        await _finish_run(self.run_id, "interrupted", error_message=error_message)


async def _finish_run(
    run_id: int,
    status: str,
    *,
    result: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> None:
    try:
        async with get_db_connection_for_bg_task() as db:
            await db.execute(
                """
                UPDATE worker_task_runs
                SET status = $2,
                    finished_at = NOW(),
                    heartbeat_at = NOW(),
                    result = COALESCE($3, result),
                    error_message = $4,
                    progress = CASE
                        WHEN $2 = 'success' THEN (COALESCE(progress, '{}'::jsonb) - 'message')
                        ELSE progress
                    END
                WHERE id = $1 AND status IN ('queued', 'running')
                """,
                run_id,
                status,
                result,
                _truncate_error(error_message),
            )
    except asyncpg.InterfaceError as e:
        logger.warning(f"タスク実行結果の保存をスキップしました (run_id={run_id}, status={status}): {e}")
    except Exception as e:
        logger.error(f"タスク実行結果の保存に失敗しました (run_id={run_id}, status={status}): {e}", exc_info=True)
    finally:
        _owned_run_ids.discard(run_id)


async def interrupt_all_running_for_startup() -> int:
    """唯一のワーカー起動時に、前プロセスの running をすべて中断する。"""
    try:
        redis_client = get_redis()
        if redis_client:
            await redis_client.delete(
                HEAVY_LOCK_KEY,
                "lock:archive_expired_battles",
                "lock:purge_old_archived_battles",
            )
    except Exception as e:
        logger.warning(f"重いタスクの Redis ロック削除に失敗しました: {e}")

    try:
        async with get_db_connection_for_bg_task() as db:
            result = await db.execute(
                """
                UPDATE worker_task_runs
                SET status = 'interrupted',
                    finished_at = NOW(),
                    error_message = COALESCE(
                        error_message,
                        'ワーカー再起動により中断されました'
                    )
                WHERE status = 'running'
                """
            )
        count = int(result.split()[-1]) if result else 0
        if count:
            logger.warning(f"起動時に実行中タスクを {count} 件中断しました。")
        return count
    except asyncpg.UndefinedTableError:
        logger.error("worker_task_runs テーブルがありません。マイグレーションを適用してください。")
        return 0
    except Exception as e:
        logger.error(f"起動時の実行中タスク回収に失敗しました: {e}", exc_info=True)
        return 0


async def recover_stale_runs() -> int:
    stale_before = datetime.now(timezone.utc) - timedelta(seconds=STALE_HEARTBEAT_SEC)
    owned = list(_owned_run_ids)
    try:
        async with get_db_connection_for_bg_task() as db:
            result = await db.execute(
                """
                UPDATE worker_task_runs
                SET status = 'interrupted',
                    finished_at = NOW(),
                    error_message = COALESCE(
                        error_message,
                        'ハートビート途絶またはワーカー再起動により中断されました'
                    )
                WHERE status = 'running'
                  AND (heartbeat_at IS NULL OR heartbeat_at < $1)
                  AND NOT (id = ANY($2::int[]))
                """,
                stale_before,
                owned,
            )
        count = int(result.split()[-1]) if result else 0
        if count:
            logger.warning(f"中断されたワーカータスクを {count} 件回収しました。")
        return count
    except asyncpg.UndefinedTableError:
        logger.error("worker_task_runs テーブルがありません。マイグレーションを適用してください。")
        return 0
    except Exception as e:
        logger.error(f"停滞中タスクの回収に失敗しました: {e}", exc_info=True)
        return 0


async def _has_success_today(db: asyncpg.Connection, task_key: str) -> bool:
    start, end = utc_day_bounds()
    keys = [task_key, *TASK_SUCCESS_ALIASES.get(task_key, ())]
    found = await db.fetchval(
        """
        SELECT 1
        FROM worker_task_runs
        WHERE task_key = ANY($1::text[])
          AND status = 'success'
          AND finished_at >= $2
          AND finished_at < $3
        LIMIT 1
        """,
        keys,
        start,
        end,
    )
    return found is not None


async def enqueue_task(
    task_key: str,
    *,
    trigger: str,
    force: bool = False,
    created_by_user_id: int | None = None,
    scheduled_for: datetime | None = None,
) -> EnqueueResult:
    task = get_task(task_key)
    if task is None:
        return EnqueueResult(False, None, "unknown_task")
    if trigger == "manual" and not task.allow_manual:
        return EnqueueResult(False, None, "not_allowed")

    try:
        async with get_db_connection_for_bg_task() as db:
            inflight = await db.fetchval(
                """
                SELECT id
                FROM worker_task_runs
                WHERE task_key = $1 AND status IN ('queued', 'running')
                LIMIT 1
                """,
                task_key,
            )
            if inflight is not None:
                return EnqueueResult(False, int(inflight), "already_inflight")

            if task.once_per_day and not force:
                if await _has_success_today(db, task_key):
                    return EnqueueResult(False, None, "already_succeeded_today", already_succeeded=True)

            run_id = await db.fetchval(
                """
                INSERT INTO worker_task_runs (
                    task_key, status, trigger, scheduled_for, created_by_user_id, worker_id, progress
                )
                VALUES ($1, 'queued', $2, $3, $4, $5, $6)
                RETURNING id
                """,
                task_key,
                trigger,
                scheduled_for,
                created_by_user_id,
                current_worker_id(),
                {},
            )
            logger.info(
                f"タスク '{task_key}' をキューに追加しました (run_id={run_id}, trigger={trigger})。"
            )
            return EnqueueResult(True, int(run_id), "queued")
    except asyncpg.UniqueViolationError:
        return EnqueueResult(False, None, "already_inflight")
    except asyncpg.UndefinedTableError:
        logger.error("worker_task_runs テーブルがありません。マイグレーションを適用してください。")
        return EnqueueResult(False, None, "missing_table")
    except Exception as e:
        logger.error(f"タスク '{task_key}' のキュー追加に失敗しました: {e}", exc_info=True)
        return EnqueueResult(False, None, "error")


def _as_jst(now: datetime) -> datetime:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(_JST)


def in_quiet_hours(now: datetime | None = None) -> bool:
    return _as_jst(now or datetime.now(timezone.utc)).hour < QUIET_HOURS_END_HOUR_JST


def in_backup_window(now: datetime | None = None) -> bool:
    return _as_jst(now or datetime.now(timezone.utc)).hour == BACKUP_HOUR_JST


def in_prestige_catchup_window(now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    scheduled = now.replace(hour=7, minute=59, second=0, microsecond=0)
    return abs((now - scheduled).total_seconds()) <= PRESTIGE_CATCHUP_WINDOW_SEC


def is_cron_due_today(task: TaskDef, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if task.cron_hour_utc is None:
        return False
    scheduled = now.replace(
        hour=task.cron_hour_utc,
        minute=task.cron_minute_utc,
        second=0,
        microsecond=0,
    )
    return now >= scheduled


# [この部分は公開用リポジトリでは非公開にされています]


async def fetch_resumable_checkpoint(task_key: str, current_run_id: int) -> dict[str, Any]:
    try:
        async with get_db_connection_for_bg_task() as db:
            row = await db.fetchrow(
                """
                SELECT status, progress, started_at
                FROM worker_task_runs
                WHERE task_key = $1 AND id < $2
                ORDER BY id DESC
                LIMIT 1
                """,
                task_key,
                current_run_id,
            )
    except Exception:
        return {}
    if not row or row["status"] not in ("interrupted", "failed"):
        return {}
    started_at = row["started_at"]
    if started_at is None:
        return {}
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    start, end = utc_day_bounds()
    if not (start <= started_at < end):
        return {}
    progress = row["progress"] or {}
    checkpoint = progress.get("checkpoint") if isinstance(progress, dict) else None
    return dict(checkpoint) if isinstance(checkpoint, dict) else {}


async def start_player_update_run(shutdown_event: asyncio.Event) -> TaskContext | None:
    await recover_stale_runs()
    try:
        async with get_db_connection_for_bg_task() as db:
            run_id = await db.fetchval(
                """
                INSERT INTO worker_task_runs (
                    task_key, status, trigger, started_at, heartbeat_at, worker_id, progress
                )
                VALUES ('player_update', 'running', 'startup', NOW(), NOW(), $1, $2)
                RETURNING id
                """,
                current_worker_id(),
                {"step": "starting", "message": "起動待ち完了。ループを開始します。"},
            )
    except asyncpg.UniqueViolationError:
        logger.warning("プレイヤーアップデートは既に実行中のため、新しい実行行は作りません。")
        return None
    except Exception as e:
        logger.error(f"プレイヤーアップデート実行行の作成に失敗しました: {e}", exc_info=True)
        return None

    run_id = int(run_id)
    _owned_run_ids.add(run_id)
    return TaskContext(run_id, shutdown_event)


def _get_handler(task_key: str):
    from app.background_tasks.tasks import cleanup_expired_profile_images_task

    handlers = {
        "cleanup_expired_profile_images": cleanup_expired_profile_images_task,
    }
    # [この部分は公開用リポジトリでは非公開にされています]
    return handlers.get(task_key)


async def _invoke_handler(handler, *, db=None, ctx: TaskContext | None = None) -> None:
    kwargs: dict[str, Any] = {}
    try:
        if "ctx" in signature(handler).parameters:
            kwargs["ctx"] = ctx
    except (TypeError, ValueError):
        pass
    if db is None:
        await handler(**kwargs)
    else:
        await handler(db, **kwargs)


async def _run_interval_handler(task: TaskDef, handler) -> None:
    if task.needs_db:
        async with get_db_connection_for_bg_task() as db:
            await handler(db)
    else:
        await handler()


async def _start_interval_run(task_key: str) -> int | None:
    async with get_db_connection_for_bg_task() as db:
        async with db.transaction():
            inflight = await db.fetchval(
                """
                SELECT id
                FROM worker_task_runs
                WHERE task_key = $1 AND status IN ('queued', 'running')
                LIMIT 1
                """,
                task_key,
            )
            if inflight is not None:
                logger.debug(f"タスク '{task_key}' は実行中または待機中のため定期実行をスキップします。")
                return None

            latest_id = await db.fetchval(
                """
                SELECT id
                FROM worker_task_runs
                WHERE task_key = $1
                ORDER BY id DESC
                LIMIT 1
                """,
                task_key,
            )
            worker_id = current_worker_id()
            progress = {}
            if latest_id is None:
                run_id = await db.fetchval(
                    """
                    INSERT INTO worker_task_runs (
                        task_key, status, trigger, started_at, heartbeat_at, worker_id, progress
                    )
                    VALUES ($1, 'running', 'cron', NOW(), NOW(), $2, $3)
                    RETURNING id
                    """,
                    task_key,
                    worker_id,
                    progress,
                )
                return int(run_id)

            run_id = await db.fetchval(
                """
                UPDATE worker_task_runs
                SET status = 'running',
                    trigger = 'cron',
                    scheduled_for = NULL,
                    started_at = NOW(),
                    finished_at = NULL,
                    heartbeat_at = NOW(),
                    worker_id = $2,
                    progress = $3,
                    result = NULL,
                    error_message = NULL,
                    created_by_user_id = NULL
                WHERE id = $1 AND status NOT IN ('queued', 'running')
                RETURNING id
                """,
                latest_id,
                worker_id,
                progress,
            )
            return int(run_id) if run_id is not None else None


async def run_recorded_interval_task(task_key: str) -> None:
    """短周期タスクを実行し、最新行を台帳に上書きする。手動キューがある場合はスキップする。"""
    task = get_task(task_key)
    handler = _get_handler(task_key)
    if task is None or handler is None:
        logger.error(f"短周期タスク '{task_key}' のハンドラが見つかりません。")
        return

    try:
        run_id = await _start_interval_run(task_key)
    except asyncpg.UndefinedTableError:
        logger.error("worker_task_runs テーブルがありません。台帳なしで短周期タスクを実行します。")
        try:
            await _run_interval_handler(task, handler)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"短周期タスク '{task_key}' の実行に失敗しました: {e}", exc_info=True)
        return
    except asyncpg.UniqueViolationError:
        logger.debug(f"タスク '{task_key}' は他で実行中のため定期実行をスキップします。")
        return
    except Exception as e:
        logger.error(f"短周期タスク '{task_key}' の台帳更新に失敗しました: {e}", exc_info=True)
        return

    if run_id is None:
        return

    ctx = TaskContext(run_id)
    _owned_run_ids.add(run_id)
    try:
        await _run_interval_handler(task, handler)
        await ctx.mark_success()
    except asyncio.CancelledError:
        await ctx.mark_interrupted("ワーカー停止により中断されました")
        raise
    except Exception as e:
        await ctx.mark_failed(str(e))
        logger.error(f"短周期タスク '{task_key}' が失敗しました (run_id={run_id}): {e}", exc_info=True)
    finally:
        _owned_run_ids.discard(run_id)


async def _acquire_heavy_lock() -> str | None:
    redis_client = get_redis()
    if not redis_client:
        return "db-only"
    token = secrets.token_hex(16)
    acquired = await redis_client.set(HEAVY_LOCK_KEY, token, nx=True, ex=HEAVY_LOCK_TTL_SEC)
    return token if acquired else None


async def _refresh_heavy_lock(token: str) -> None:
    if token == "db-only":
        return
    redis_client = get_redis()
    if not redis_client:
        return
    await redis_client.eval(
        "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end",
        1,
        HEAVY_LOCK_KEY,
        token,
        str(HEAVY_LOCK_TTL_SEC),
    )


async def _release_heavy_lock(token: str | None) -> None:
    if not token or token == "db-only":
        return
    redis_client = get_redis()
    if not redis_client:
        return
    try:
        await redis_client.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
            1,
            HEAVY_LOCK_KEY,
            token,
        )
    except Exception:
        pass


async def _claim_next_run(*, allow_heavy: bool, heavy_only: bool = False) -> dict[str, Any] | None:
    heavy_keys = list(HEAVY_TASK_KEYS)
    if heavy_only and (not allow_heavy or not heavy_keys):
        return None
    try:
        async with get_db_connection_for_bg_task() as db:
            async with db.transaction():
                heavy_running = False
                if heavy_keys:
                    heavy_running = await db.fetchval(
                        """
                        SELECT 1
                        FROM worker_task_runs
                        WHERE status = 'running' AND task_key = ANY($1::text[])
                        LIMIT 1
                        """,
                        heavy_keys,
                    ) is not None
                worker_id = current_worker_id()
                # PostgreSQL では LIMIT のあとに FOR UPDATE を置く。逆だと syntax error になり、
                # queued のまま永遠に消化されない。
                if heavy_only:
                    claimed = await db.fetchrow(
                        """
                        WITH picked AS (
                            SELECT id
                            FROM worker_task_runs
                            WHERE status = 'queued' AND task_key = ANY($1::text[])
                            ORDER BY id
                            LIMIT 1
                            FOR UPDATE SKIP LOCKED
                        )
                        UPDATE worker_task_runs AS target
                        SET status = 'running',
                            started_at = NOW(),
                            heartbeat_at = NOW(),
                            worker_id = $2
                        FROM picked
                        WHERE target.id = picked.id
                        RETURNING target.*
                        """,
                        heavy_keys,
                        worker_id,
                    )
                elif (not allow_heavy or heavy_running) and heavy_keys:
                    claimed = await db.fetchrow(
                        """
                        WITH picked AS (
                            SELECT id
                            FROM worker_task_runs
                            WHERE status = 'queued' AND NOT (task_key = ANY($1::text[]))
                            ORDER BY id
                            LIMIT 1
                            FOR UPDATE SKIP LOCKED
                        )
                        UPDATE worker_task_runs AS target
                        SET status = 'running',
                            started_at = NOW(),
                            heartbeat_at = NOW(),
                            worker_id = $2
                        FROM picked
                        WHERE target.id = picked.id
                        RETURNING target.*
                        """,
                        heavy_keys,
                        worker_id,
                    )
                else:
                    claimed = await db.fetchrow(
                        """
                        WITH picked AS (
                            SELECT id
                            FROM worker_task_runs
                            WHERE status = 'queued'
                            ORDER BY id
                            LIMIT 1
                            FOR UPDATE SKIP LOCKED
                        )
                        UPDATE worker_task_runs AS target
                        SET status = 'running',
                            started_at = NOW(),
                            heartbeat_at = NOW(),
                            worker_id = $1
                        FROM picked
                        WHERE target.id = picked.id
                        RETURNING target.*
                        """,
                        worker_id,
                    )
                return dict(claimed) if claimed is not None else None
    except asyncpg.UndefinedTableError:
        logger.error("worker_task_runs テーブルがありません。マイグレーションを適用してください。")
        return None
    except Exception as e:
        logger.error(f"キュー済みタスクの取得に失敗しました: {e}", exc_info=True)
        return None


async def _execute_run(run: dict[str, Any], shutdown_event: asyncio.Event) -> None:
    global _heavy_run_id
    task_key = run["task_key"]
    run_id = int(run["id"])
    task = get_task(task_key)
    handler = _get_handler(task_key)
    ctx = TaskContext(run_id, shutdown_event)
    _owned_run_ids.add(run_id)
    heavy_token: str | None = None
    heartbeat_task = asyncio.create_task(ctx.heartbeat_loop(), name=f"heartbeat-{run_id}")

    try:
        if handler is None:
            raise RuntimeError(f"未登録のタスクです: {task_key}")

        is_heavy = bool(task and task.kind == TaskKind.DAILY_HEAVY)
        if is_heavy:
            heavy_token = await _acquire_heavy_lock()
            if not heavy_token:
                logger.info(f"重いタスク '{task_key}' は排他ロックを取れなかったため待機に戻します (run_id={run_id})。")
                async with get_db_connection_for_bg_task() as db:
                    await db.execute(
                        """
                        UPDATE worker_task_runs
                        SET status = 'queued', started_at = NULL, heartbeat_at = NULL, worker_id = NULL
                        WHERE id = $1 AND status = 'running'
                        """,
                        run_id,
                    )
                _owned_run_ids.discard(run_id)
                await asyncio.sleep(2)
                return
            ctx.heavy_lock_token = heavy_token
            _heavy_run_id = run_id

        checkpoint = await fetch_resumable_checkpoint(task_key, run_id)
        if checkpoint:
            await ctx.set_progress(
                checkpoint=checkpoint,
                message="前回の checkpoint から再開します",
            )
        elif task is not None:
            await ctx.set_progress(step="running", message=f"{task.name_ja} を実行中です")
        logger.info(f"タスク '{task_key}' の実行を開始します (run_id={run_id})。")

        if task is None or task.needs_db:
            async with get_db_connection_for_bg_task() as db:
                await _invoke_handler(handler, db=db, ctx=ctx)
        else:
            await _invoke_handler(handler, ctx=ctx)

        await ctx.mark_success()
        logger.info(f"タスク '{task_key}' が完了しました (run_id={run_id})。")
    except asyncio.CancelledError:
        await ctx.mark_interrupted("ワーカー停止により中断されました")
        logger.warning(f"タスク '{task_key}' がキャンセルされました (run_id={run_id})。")
    except Exception as e:
        await ctx.mark_failed(str(e))
        logger.error(f"タスク '{task_key}' が失敗しました (run_id={run_id}): {e}", exc_info=True)
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        if heavy_token:
            await _release_heavy_lock(heavy_token)
        if _heavy_run_id == run_id:
            _heavy_run_id = None
        _inflight_tasks.pop(run_id, None)
        _owned_run_ids.discard(run_id)


def _cleanup_inflight() -> None:
    global _heavy_run_id
    done_ids = [run_id for run_id, task in _inflight_tasks.items() if task.done()]
    for run_id in done_ids:
        _inflight_tasks.pop(run_id, None)
        if _heavy_run_id == run_id:
            _heavy_run_id = None


def _light_inflight_count() -> int:
    return sum(1 for run_id in _inflight_tasks if run_id != _heavy_run_id)


async def task_dispatcher_loop(shutdown_event: asyncio.Event) -> None:
    global _heavy_run_id
    logger.info("タスクディスパッチャを開始します。")
    await recover_stale_runs()
    try:
        while not shutdown_event.is_set():
            try:
                await recover_stale_runs()
                _cleanup_inflight()

                can_start_light = _light_inflight_count() < MAX_LIGHT_INFLIGHT
                can_start_heavy = _heavy_run_id is None
                if can_start_light or can_start_heavy:
                    claimed = await _claim_next_run(
                        allow_heavy=can_start_heavy,
                        heavy_only=not can_start_light,
                    )
                    if claimed is not None:
                        run_id = int(claimed["id"])
                        task_def = get_task(claimed["task_key"])
                        is_heavy = bool(task_def and task_def.kind == TaskKind.DAILY_HEAVY)
                        _owned_run_ids.add(run_id)
                        exec_task = asyncio.create_task(
                            _execute_run(claimed, shutdown_event),
                            name=f"worker-task-{claimed['task_key']}-{claimed['id']}",
                        )
                        _inflight_tasks[run_id] = exec_task
                        if is_heavy:
                            _heavy_run_id = run_id
                        continue
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"タスクディスパッチャで予期せぬエラーが発生しました: {e}", exc_info=True)

            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=DISPATCH_POLL_SEC)
            except asyncio.TimeoutError:
                pass
    except asyncio.CancelledError:
        logger.info("タスクディスパッチャがキャンセルされました。")
        raise
    finally:
        pending = [task for task in _inflight_tasks.values() if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        logger.info("タスクディスパッチャを終了します。")


def _row_to_public_dict(row: asyncpg.Record | None) -> dict[str, Any] | None:
    if row is None:
        return None
    started_at = row["started_at"]
    finished_at = row["finished_at"]
    duration_sec = None
    if started_at is not None:
        end = finished_at or datetime.now(timezone.utc)
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        duration_sec = max(0.0, (end - started_at).total_seconds())
    progress = row["progress"] or {}
    progress_text = progress.get("message") if isinstance(progress, dict) else None
    if row["status"] == "success":
        progress_text = None
    elif (
        row["status"] != "running"
        and isinstance(progress_text, str)
        and progress_text.endswith("を実行中です")
    ):
        progress_text = None
    return {
        "id": row["id"],
        "task_key": row["task_key"],
        "status": row["status"],
        "trigger": row["trigger"],
        "started_at": started_at,
        "finished_at": finished_at,
        "heartbeat_at": row["heartbeat_at"],
        "created_at": row["created_at"],
        "progress": progress,
        "result": row["result"],
        "error_message": row["error_message"],
        "worker_id": row["worker_id"],
        "created_by_user_id": row["created_by_user_id"],
        "duration_sec": duration_sec,
        "duration_text": format_duration_seconds(duration_sec),
        "progress_text": progress_text,
        "progress_current": progress.get("current") if isinstance(progress, dict) else None,
        "progress_total": progress.get("total") if isinstance(progress, dict) else None,
    }


async def fetch_task_summaries(db: asyncpg.Connection) -> list[dict[str, Any]]:
    latest_rows = await db.fetch(
        """
        SELECT DISTINCT ON (task_key)
            id, task_key, status, trigger, started_at, finished_at, heartbeat_at,
            created_at, progress, result, error_message, worker_id, created_by_user_id
        FROM worker_task_runs
        ORDER BY task_key, id DESC
        """
    )
    latest_by_key = {row["task_key"]: row for row in latest_rows}

    start, end = utc_day_bounds()
    today_success_rows = await db.fetch(
        """
        SELECT DISTINCT ON (task_key) task_key, finished_at
        FROM worker_task_runs
        WHERE status = 'success' AND finished_at >= $1 AND finished_at < $2
        ORDER BY task_key, finished_at DESC
        """,
        start,
        end,
    )
    success_today = {row["task_key"] for row in today_success_rows}

    now = datetime.now(timezone.utc)
    summaries = []
    for task in iter_tasks():
        latest = _row_to_public_dict(latest_by_key.get(task.key))
        last_finished = None
        if latest:
            last_finished = latest.get("finished_at") or latest.get("started_at") or latest.get("created_at")
        next_at = next_scheduled_at(task, now, last_finished=last_finished)
        summaries.append(
            {
                "key": task.key,
                "name_ja": task.name_ja,
                "kind": task.kind.value,
                "once_per_day": task.once_per_day,
                "allow_manual": task.allow_manual,
                "schedule_ja": task.schedule_ja,
                "description_ja": task.description_ja,
                "is_heavy": task.kind == TaskKind.DAILY_HEAVY,
                "success_today": task.key in success_today or any(
                    alias in success_today for alias in TASK_SUCCESS_ALIASES.get(task.key, ())
                ),
                "next_scheduled_at": next_at,
                "latest": latest,
            }
        )
    return summaries


async def fetch_task_run_history(
    db: asyncpg.Connection,
    *,
    task_key: str | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    where = []
    args: list[Any] = []
    if task_key:
        args.append(task_key)
        where.append(f"task_key = ${len(args)}")
    if status:
        args.append(status)
        where.append(f"status = ${len(args)}")
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    total = await db.fetchval(f"SELECT COUNT(*) FROM worker_task_runs {where_sql}", *args) or 0
    args.extend([limit, offset])
    rows = await db.fetch(
        f"""
        SELECT id, task_key, status, trigger, started_at, finished_at, heartbeat_at,
               created_at, progress, result, error_message, worker_id, created_by_user_id
        FROM worker_task_runs
        {where_sql}
        ORDER BY id DESC
        LIMIT ${len(args) - 1} OFFSET ${len(args)}
        """,
        *args,
    )
    return [_row_to_public_dict(row) for row in rows], int(total)
