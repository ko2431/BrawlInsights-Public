"""ガチバトルマップ一覧: バトル履歴からの出現集計、マッププールの推論・保存、表示用ペイロード、管理画面の操作。"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import asyncpg

from app.core.cache import get_cache, get_redis, set_cache
from app.core.logger import logger
from app.services.map_mode_catalog import (
    ensure_catalog,
    ensure_map_stub,
    get_map_by_id,
    get_mode_by_id,
    get_mode_theme,
    mode_icon_candidates,
    mode_sort_key,
)
from app.services.ranked_map_pool_inference import Observation, infer_ranked_pools
from app.utils.utils import calc_ranked_season, get_ranked_season_period

UTC = timezone.utc
JST = timezone(timedelta(hours=9))
CACHE_VERSION_KEY = "ranked_maps:version"
PAYLOAD_TTL_SECONDS = 10 * 60

# [この部分は公開用リポジトリでは非公開にされています]
# [この部分は公開用リポジトリでは非公開にされています]


# [この部分は公開用リポジトリでは非公開にされています]


def _is_public(settings_row: asyncpg.Record | None, periods: list[Any], season: int, current: int) -> bool:
    if not periods:
        return False
    visibility = settings_row["visibility"] if settings_row else "auto"
    if visibility == "hide":
        return False
    if visibility == "show" or season >= current:
        return True
    coverage = settings_row["coverage"] if settings_row else None
    return coverage is not None and coverage >= MIN_PUBLIC_COVERAGE


async def build_ranked_maps_payload(db: asyncpg.Connection) -> dict[str, Any]:
    """公開ページ用に、表示できる全シーズンの期間とマップをまとめる。キャッシュ世代ごとに10分キャッシュする。"""
    version = await ranked_maps_cache_version()
    current = calc_ranked_season()
    cache_key = f"ranked_maps:payload:{version}:{current}"
    cached = await get_cache(cache_key)
    if isinstance(cached, dict) and cached.get("seasons") is not None:
        return cached

    pool_rows = await db.fetch("SELECT season, seq, start_at, end_at, maps FROM ranked_map_pools ORDER BY season DESC, seq")
    settings_rows = {row["season"]: row for row in await db.fetch("SELECT * FROM ranked_seasons")}
    pools_by_season: dict[int, list[asyncpg.Record]] = {}
    for row in pool_rows:
        pools_by_season.setdefault(row["season"], []).append(row)
    images = legacy_images()

    seasons = []
    for season in sorted(set(pools_by_season) | set(images), reverse=True):
        if season > current:
            continue
        settings_row = settings_rows.get(season)
        periods = pools_by_season.get(season, [])
        public = _is_public(settings_row, periods, season, current)
        if not public and season not in images:
            continue
        start_date, end_date = season_dates(season)
        max_power = settings_row["max_power_brawler_ids"] if settings_row else None
        seasons.append({
            "season": season,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "source": "data" if public else "image",
            "max_power": [int(item) for item in max_power] if max_power is not None else None,
            "periods": [
                {
                    "seq": row["seq"],
                    "start_at": _iso(row["start_at"]),
                    "end_at": _iso(row["end_at"]),
                    "maps": [
                        {"map_id": item["map_id"], "mode_id": item.get("mode_id")}
                        for item in (row["maps"] or [])
                    ],
                }
                for row in periods
            ] if public else [],
            "image": images.get(season),
        })
    payload = {"current_season": current, "seasons": seasons}
    await set_cache(cache_key, payload, ttl=PAYLOAD_TTL_SECONDS)
    return payload


def _mode_group_key(slug: str | None, mode_id: int | None) -> tuple:
    if slug in _STANDARD_INDEX:
        return (0, _STANDARD_INDEX[slug], 0)
    return (1, *mode_sort_key(mode_id=mode_id, slug=slug)[:2])


def group_maps_by_mode(maps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """マップをモードごとにまとめ、定番6モード → その他の順に並べる。ensure_catalog 済みであること。"""
    groups: dict[int | str, dict[str, Any]] = {}
    for item in maps:
        map_id = int(item["map_id"])
        map_info = get_map_by_id(map_id)
        mode_id = item.get("mode_id") or (map_info.mode_id if map_info else None)
        mode_info = get_mode_by_id(mode_id)
        slug = mode_info.slug if mode_info else None
        key = mode_id or "unknown"
        if key not in groups:
            short_ja, short_en = _STANDARD_SHORT.get(slug or "", (None, None))
            name_ja = (mode_info.display_name("ja") if mode_info else None) or "その他"
            name_en = (mode_info.display_name("en") if mode_info else None) or "Other"
            groups[key] = {
                "mode_id": mode_id,
                "slug": slug,
                # [この部分は公開用リポジトリでは非公開にされています]
    else:
        raise ValueError("不明な操作です。")
    await bump_ranked_maps_cache()
    return message
