"""マップ周期の取得、推論の適用、表示ペイロード。"""
from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
import httpx

from app.core.cache import get_cache, get_redis, set_cache
from app.core.config import settings
from app.core.logger import logger
from app.exceptions.custom_exceptions import BrawlStarsAPIError, DataBaseError
from app.services.map_mode_catalog import (
    ensure_catalog,
    ensure_map_stub,
    ensure_mode_stub,
    format_mode_slug_to_display,
    get_mode_by_slug,
    normalize_official_mode_id,
)
from app.services.brawl_service import is_brawlstats_api_unavailable, mark_brawlstats_api_unavailable
from app.utils.utils import parse_api_utc_datetime

ROTATION_URL = "https:// [この部分は公開用リポジトリでは非公開にされています]


def _latest_index(cycle: asyncpg.Record, maps: list[dict[str, Any]], latest: asyncpg.Record | None) -> int:
    """現在枠が周期のどの位置か。基準時刻から求め、食い違う場合(スライド直後など)はマップIDで探す。"""
    if not maps:
        return 0
    if latest is None:
        return 0
    duration = int(cycle["duration_minutes"] or 1440)
    elapsed = (latest["start_time"] - cycle["anchor_start"]).total_seconds() / (duration * 60)
    index = math.ceil(elapsed - 0.5) % len(maps)
    if maps[index]["map_id"] == latest["map_id"]:
        return index
    for candidate, item in enumerate(maps):
        if item["map_id"] == latest["map_id"]:
            return candidate
    return index


async def build_map_rotation_payload(db: asyncpg.Connection) -> dict[str, Any]:
    version = await _cache_version()
    cache_key = f"map_rotation:payload:{version}"
    cached = await get_cache(cache_key)
    if isinstance(cached, dict) and cached.get("slots") is not None:
        return cached

    rows = await db.fetch(
        """
        SELECT s.id, s.name_ja, s.name_en, s.icons, s.icon_path, s.duration_minutes AS slot_duration,
               c.status, c.cycle_length, c.duration_minutes, c.anchor_start, c.maps,
               o.start_time, o.end_time, o.map_id
        FROM map_rotation_slots s
        JOIN LATERAL (
            SELECT status, cycle_length, duration_minutes, anchor_start, maps
            FROM map_rotation_cycles
            WHERE slot_id = s.id AND status IN ('working', 'confirmed')
            ORDER BY CASE status WHEN 'working' THEN 0 ELSE 1 END
            LIMIT 1
        ) c ON TRUE
        LEFT JOIN LATERAL (
            SELECT start_time, end_time, map_id
            FROM event_rotation_observations
            WHERE api_slot_id = s.primary_api_slot_id
            ORDER BY start_time DESC
            LIMIT 1
        ) o ON TRUE
        WHERE s.is_visible
        ORDER BY s.display_order, s.id
        """
    )
    # [この部分は公開用リポジトリでは非公開にされています]
            message = "調整中にしました。"
        elif command == "confirm":
            episode = await _active_episode(db, payload.get("episode_id"))
            await _promote_working_cycles(db, episode, now, "manual")
            message = "現在の作業周期を確定しました。"
        elif command == "cancel":
            episode = await _active_episode(db, payload.get("episode_id"))
            async with db.transaction():
                await db.execute("DELETE FROM map_rotation_cycles WHERE status = 'working'")
                await db.execute(
                    "UPDATE map_rotation_episodes SET status = 'cancelled', completed_at = $2 WHERE id = $1",
                    episode["id"],
                    now,
                )
                # [この部分は公開用リポジトリでは非公開にされています]
        await bump_rotation_cache()
        return "観測から周期を再推論しました。"
    raise ValueError("不明な操作です。")
