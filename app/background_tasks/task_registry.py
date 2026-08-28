from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TaskKind(StrEnum):
    ALWAYS_ON = "always_on"
    INTERVAL_LIGHT = "interval_light"
    DAILY_LIGHT = "daily_light"
    DAILY_HEAVY = "daily_heavy"


@dataclass(frozen=True, slots=True)
class TaskDef:
    key: str
    name_ja: str
    kind: TaskKind
    once_per_day: bool
    allow_manual: bool
    needs_db: bool
    schedule_ja: str
    description_ja: str
    display_order: int
    cron_hour_utc: int | None = None
    cron_minute_utc: int = 0
    interval_minutes: int | None = None


def _build_tasks() -> dict[str, TaskDef]:
    items: list[TaskDef] = [
        TaskDef(
            key="player_update",
            name_ja="プレイヤーアップデート",
            kind=TaskKind.ALWAYS_ON,
            once_per_day=False,
            allow_manual=False,
            needs_db=False,
            schedule_ja="常時稼働（最大3,000人/ループ）",
            description_ja="閲覧済みプレイヤーを last_updated_at 順に更新します。再起動後も未更新プレイヤーから継続します。",
            display_order=1,
        ),
        TaskDef(
            key="cleanup_expired_profile_images",
            name_ja="古い画像のクリーンアップ",
            kind=TaskKind.INTERVAL_LIGHT,
            once_per_day=False,
            allow_manual=True,
            needs_db=True,
            schedule_ja="15分ごと",
            description_ja="期限切れのプロフィール画像ファイルとDBパスを削除します。",
            display_order=14,
            interval_minutes=15,
        ),
    ]
    # [この部分は公開用リポジトリでは非公開にされています]
    return {item.key: item for item in items}


TASKS: dict[str, TaskDef] = _build_tasks()

HEAVY_TASK_KEYS: tuple[str, ...] = tuple(
    task.key for task in TASKS.values() if task.kind == TaskKind.DAILY_HEAVY
)


def get_task(task_key: str) -> TaskDef | None:
    return TASKS.get(task_key)


def iter_tasks() -> list[TaskDef]:
    return sorted(TASKS.values(), key=lambda task: (task.display_order, task.key))
