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

ROTATION_URL = "https://api.brawlstars.com/v1/events/rotation"
CACHE_VERSION_KEY = "map_rotation:version"
PAYLOAD_TTL_SECONDS = 10 * 60
FETCH_ATTEMPTS = 3


def _maps_payload(raw_maps: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_maps, list):
        return []
    result = []
    for item in raw_maps:
        if not isinstance(item, dict):
            continue
        map_id = item.get("map_id")
        result.append({
            "map_id": int(map_id) if map_id is not None else None,
            "state": item.get("state") or "predicted",
        })
    return result


async def bump_rotation_cache() -> None:
    redis_client = get_redis()
    if not redis_client:
        return
    try:
        await redis_client.incr(CACHE_VERSION_KEY)
    except Exception as exc:
        logger.warning(f"マップ周期キャッシュ世代の更新に失敗しました: {exc}")


async def _cache_version() -> int:
    redis_client = get_redis()
    if not redis_client:
        return 0
    try:
        raw = await redis_client.get(CACHE_VERSION_KEY)
        return int(raw) if raw is not None else 0
    except Exception:
        return 0


async def _set_replay_after(db: asyncpg.Connection, moment: datetime) -> None:
    """これより前の観測を推論に使わないようにする。周期の確定・取り消し後に、古い周期の観測で再びエピソードが立つのを防ぐ。

    暫定ローテを切り捨てて周期を決めたスロットは、新周期の開始(settled_from)の方が後ならそちらを使う。
    """
    await db.execute(
        """
        UPDATE map_rotation_slots
        SET inference_state = jsonb_set(
            COALESCE(inference_state, '{}'::jsonb) - 'settled_from',
            '{replay_after}',
            CASE
                WHEN (inference_state->>'settled_from')::timestamptz > $2::timestamptz
                THEN inference_state->'settled_from'
                ELSE to_jsonb($1::text)
            END,
            true
        )
        """,
        moment.isoformat(),
        moment,
    )


async def _save_settled_from(db: asyncpg.Connection, slot_id: int, moment: datetime | None) -> None:
    if moment is None:
        await db.execute(
            """
            UPDATE map_rotation_slots
            SET inference_state = inference_state - 'settled_from'
            WHERE id = $1 AND inference_state ? 'settled_from'
            """,
            slot_id,
        )
        return
    await db.execute(
        """
        UPDATE map_rotation_slots
        SET inference_state = jsonb_set(
            COALESCE(inference_state, '{}'::jsonb), '{settled_from}', to_jsonb($2::text), true
        )
        WHERE id = $1
        """,
        slot_id,
        moment.isoformat(),
    )


def _episode_boundary(episode: asyncpg.Record) -> datetime:
    started = episode["started_at"]
    override = episode["started_at_override"]
    # 両方より後の観測だけを残せば、新しい周期と必ず整合する。
    return max(started, override) if override else started


async def _promote_working_cycles(db: asyncpg.Connection, episode: asyncpg.Record, now: datetime, source: str) -> None:
    async with db.transaction():
        await db.execute(
            """
            UPDATE map_rotation_cycles
            SET status = 'archived'
            WHERE status = 'confirmed'
              AND slot_id IN (SELECT slot_id FROM map_rotation_cycles WHERE status = 'working')
            """
        )
        await db.execute(
            """
            UPDATE map_rotation_cycles
            SET status = 'confirmed', source = $2, confirmed_at = $1
            WHERE status = 'working'
            """,
            now,
            source,
        )
        await db.execute(
            """
            UPDATE map_rotation_episodes
            SET status = 'completed', completed_at = $2
            WHERE id = $1 AND status = 'active'
            """,
            episode["id"],
            now,
        )
        await _set_replay_after(db, _episode_boundary(episode))


# [この部分は公開用リポジトリでは非公開にされています]


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
               c.status, c.source, c.confirmed_at, c.cycle_length, c.duration_minutes, c.anchor_start, c.maps,
               o.start_time, o.end_time, o.map_id
        FROM map_rotation_slots s
        JOIN LATERAL (
            SELECT status, source, confirmed_at, cycle_length, duration_minutes, anchor_start, maps
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
    # 説明文の日付は、進行中または直近に完了した周期変更の開始日。
    episode = await db.fetchrow(
        """
        SELECT status, started_at, started_at_override, expected_complete_at, expected_complete_override
        FROM map_rotation_episodes
        WHERE status IN ('active', 'completed')
        ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, id DESC
        LIMIT 1
        """
    )
    payload_slots = []
    any_working = False
    for row in rows:
        maps = _maps_payload(row["maps"])
        latest = row if row["start_time"] is not None else None
        working = row["status"] == "working"
        any_working = any_working or working
        # スロット単位の調整中ラベルは、そのスロットの周期が確定し次第外す。
        # 確定周期への昇格は全スロットがそろってからなので、昇格待ちの間もここで判定する。
        slot_settled = (
            row["cycle_length"] is not None
            and bool(maps)
            and all(item["state"] == "verified" for item in maps)
            and (row["confirmed_at"] is not None or row["source"] == "manual")
        )
        payload_slots.append({
            "id": row["id"],
            "name_ja": row["name_ja"],
            "name_en": row["name_en"],
            "icons": list(row["icons"] or []),
            "iconPath": row["icon_path"],
            "durationMinutes": row["duration_minutes"] or row["slot_duration"],
            "cycleLength": row["cycle_length"],
            "lengthUncertain": row["cycle_length"] is None,
            "adjusting": working and not slot_settled,
            "anchorStart": row["anchor_start"].isoformat(),
            "latestStart": latest["start_time"].isoformat() if latest else None,
            "latestEnd": latest["end_time"].isoformat() if latest else None,
            "latestIndex": _latest_index(row, maps, latest),
            "currentMapId": latest["map_id"] if latest else None,
            "maps": maps,
        })

    # ページ上部の説明文は、全スロットの周期が確定周期に昇格するまで表示し続ける。
    adjusting = bool(episode and episode["status"] == "active") and any_working
    updated_at = None
    expected_complete_at = None
    if episode:
        started = episode["started_at_override"] or episode["started_at"]
        updated_at = started.isoformat() if started else None
        if adjusting:
            expected = episode["expected_complete_override"] or episode["expected_complete_at"]
            expected_complete_at = expected.isoformat() if expected else None

    payload = {
        "slots": payload_slots,
        "visibleCount": len(payload_slots),
        "adjusting": adjusting,
        "startedAt": updated_at,
        "expectedCompleteAt": expected_complete_at,
    }
    await set_cache(cache_key, payload, ttl=PAYLOAD_TTL_SECONDS)
    return payload


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


# [この部分は公開用リポジトリでは非公開にされています]
