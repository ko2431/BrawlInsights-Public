"""マルチプレイ（トロフィー帯）統計の集計と読み出し。"""
from __future__ import annotations

import asyncio
import datetime
import hashlib
import itertools
import secrets
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import asyncpg

from app.core.cache import get_cache, get_redis, set_cache, delete_cache
from app.core.logger import logger
from app.exceptions.custom_exceptions import DataBaseError
from app.services.brawl_service import get_brawler, _get_date_weight, _logistic_scale, _get_rank_grade
from app.services.map_mode_catalog import (
    ModeInfo,
    ensure_catalog,
    get_map_by_id,
    get_mode_by_id,
    get_mode_by_slug,
    iter_maps,
    iter_modes,
    mode_sort_key,
    sync_maps_and_modes_from_bsinfo,
)

TROPHY_BAND_REGULAR_MAX = 12
TROPHY_BAND_TOURNAMENT = 100
TROPHY_BAND_NO_TROPHY = 101
TROPHY_STATS_RETENTION_DAYS = 30
EXCLUDED_BATTLE_TYPES = frozenset({"soloRanked", "friendly"})
KNOWN_INCLUDED_BATTLE_TYPES = frozenset({
    "ranked",
    "tournament",
    "challenge",
    "championshipChallenge",
})
SHOWDOWN_SLUGS = frozenset({"soloShowdown", "duoShowdown", "trioShowdown"})
DUELS_SLUG = "duels"
MAP_ALL_SENTINEL_PREFIX = "m"
PG_INT4_MIN = -2_147_483_648
PG_INT4_MAX = 2_147_483_647

# [この部分は公開用リポジトリでは非公開にされています]


@dataclass
class TrophyBrawlerStat:
    brawler_id: int = 0
    name_ja: str = ""
    names_ja: list[str] = field(default_factory=list)
    name_en: str = ""
    rarity: int | None = 0
    games_played: float = 0.0
    wins: float = 0.0
    draws: float = 0.0
    star_player_count: float = 0.0
    star_player_opportunities: float = 0.0
    use_rate: float = 0.0
    strength_score: float = 0.0
    profile_name: str = "default"
    used_decisive_win_rate: bool = False

    @property
    def losses(self) -> float:
        return max(self.games_played - self.wins - self.draws, 0.0)

    @property
    def win_rate_inclusive(self) -> float:
        if self.games_played <= 0:
            return 0.0
        return (self.wins / self.games_played) * 100

    @property
    def win_rate_decisive(self) -> float:
        denom = self.wins + self.losses
        if denom <= 0:
            return 0.0
        return (self.wins / denom) * 100

    @property
    def win_rate(self) -> float:
        if self.used_decisive_win_rate:
            return self.win_rate_decisive
        return self.win_rate_inclusive

    @property
    def star_player_rate(self) -> float:
        if self.star_player_opportunities <= 0:
            return 0.0
        return (self.star_player_count / self.star_player_opportunities) * 100

    @property
    def rank_grade(self) -> str:
        return _get_rank_grade(self.strength_score)

    def to_dict(self) -> dict[str, Any]:
        return {
            "brawler_id": self.brawler_id,
            "name_ja": self.name_ja,
            "names_ja": self.names_ja,
            "name_en": self.name_en,
            "rarity": self.rarity,
            "games_played": self.games_played,
            "wins": self.wins,
            "draws": self.draws,
            "star_player_count": self.star_player_count,
            "star_player_opportunities": self.star_player_opportunities,
            "use_rate": self.use_rate,
            "strength_score": self.strength_score,
            "profile_name": self.profile_name,
            "used_decisive_win_rate": self.used_decisive_win_rate,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrophyBrawlerStat:
        instance = cls()
        instance.brawler_id = data.get("brawler_id", 0)
        instance.name_ja = data.get("name_ja", "")
        instance.names_ja = data.get("names_ja") or []
        instance.name_en = data.get("name_en", "")
        instance.rarity = data.get("rarity", 0)
        instance.games_played = data.get("games_played", 0.0)
        instance.wins = data.get("wins", 0.0)
        instance.draws = data.get("draws", 0.0)
        instance.star_player_count = data.get("star_player_count", 0.0)
        instance.star_player_opportunities = data.get("star_player_opportunities", 0.0)
        instance.use_rate = data.get("use_rate", 0.0)
        instance.strength_score = data.get("strength_score", 0.0)
        instance.profile_name = data.get("profile_name", "default")
        instance.used_decisive_win_rate = bool(data.get("used_decisive_win_rate", False))
        return instance


def _parse_positive_int4(value: str | int | None) -> int | None:
    """クエリ値を PostgreSQL INTEGER に収まる正の整数へ正規化する。"""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = value if isinstance(value, int) else int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if parsed <= 0 or parsed < PG_INT4_MIN or parsed > PG_INT4_MAX:
        return None
    return parsed


def parse_mode_id_param(value: str | int | None) -> int | None:
    return _parse_positive_int4(value)


def parse_map_id_param(value: str | int | None) -> tuple[int | None, int | None]:
    """マップクエリを (mode_id_from_sentinel, map_id) に分解する。"""
    if value is None or value == "":
        return None, None
    if isinstance(value, int):
        return None, _parse_positive_int4(value)
    raw = str(value).strip()
    if not raw:
        return None, None
    if raw.startswith(MAP_ALL_SENTINEL_PREFIX):
        return _parse_positive_int4(raw[1:]), None
    return None, _parse_positive_int4(raw)


def resolve_trophy_filter_ids(
    mode_id: int | None,
    map_id: int | None,
    pool: list[dict[str, Any]],
) -> tuple[int | None, int | None]:
    """直打ちされたモード/マップを、フィルター候補にある値だけに落とす。"""
    mode_id = _parse_positive_int4(mode_id)
    map_id = _parse_positive_int4(map_id)
    modes = {item["mode_id"] for item in pool}
    map_to_mode: dict[int, int] = {}
    for item in pool:
        owner_mode_id = item["mode_id"]
        for map_data in item.get("maps") or []:
            map_to_mode[map_data["map_id"]] = owner_mode_id

    if map_id is not None:
        owner_mode_id = map_to_mode.get(map_id)
        if owner_mode_id is None:
            map_id = None
        else:
            mode_id = owner_mode_id

    if mode_id is not None and mode_id not in modes:
        mode_id = None
        map_id = None

    return mode_id, map_id


# [この部分は公開用リポジトリでは非公開にされています]


async def update_trophy_stats(db: asyncpg.Connection, target_date: datetime.date) -> None:
    """指定UTC日のマルチプレイ統計を集計して更新する。"""
    # [この部分は公開用リポジトリでは非公開にされています]


async def get_trophy_stats(
    db: asyncpg.Connection,
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    mode_id: int | None = None,
    map_id: int | None = None,
    use_cache: bool = True,
) -> list[TrophyBrawlerStat]:
    """マルチプレイ統計を読み出し、モード別プロファイルでスコア化する。"""
    # [この部分は公開用リポジトリでは非公開にされています]


async def get_trophy_filter_pool(
    db: asyncpg.Connection,
    use_cache: bool = True,
    lookback_days: int = 7,
) -> list[dict[str, Any]]:
    """直近の集計に出たモード/マップを、フィルター用に返す。"""
    await ensure_catalog(db)
    end_date = await _get_latest_trophy_stats_date(db)
    start_date = end_date - datetime.timedelta(days=lookback_days - 1)
    cache_key = f"trophy_stats_pool:{start_date}_{end_date}"
    if use_cache:
        cached = await get_cache(cache_key)
        if cached:
            return cached

    try:
        rows = await db.fetch(
            """
            SELECT DISTINCT mode_id, map_id
            FROM trophy_stats_brawler
            WHERE date BETWEEN $1 AND $2
            """,
            start_date,
            end_date,
        )
    except asyncpg.UndefinedTableError:
        logger.warning("trophy_stats_brawler テーブルが未作成のため、空のフィルター候補を返します。")
        return []
    maps_by_mode: dict[int, set[int]] = defaultdict(set)
    for row in rows:
        maps_by_mode[row["mode_id"]].add(row["map_id"])

    catalog_maps_by_mode: dict[int, list] = defaultdict(list)
    for map_info in iter_maps():
        if map_info.mode_id:
            catalog_maps_by_mode[map_info.mode_id].append(map_info)

    pool: list[dict[str, Any]] = []
    for mode_id in sorted(maps_by_mode.keys(), key=lambda item: mode_sort_key(item)):
        mode = get_mode_by_id(mode_id)
        maps: list[dict[str, Any]] = []
        seen: set[int] = set()
        for map_info in catalog_maps_by_mode.get(mode_id, []):
            if map_info.id in maps_by_mode[mode_id]:
                maps.append({
                    "map_id": map_info.id,
                    "map_ja": map_info.ja,
                    "map_en": map_info.en,
                })
                seen.add(map_info.id)
        for map_id in sorted(maps_by_mode[mode_id] - seen):
            map_info = get_map_by_id(map_id)
            maps.append({
                "map_id": map_id,
                "map_ja": map_info.ja if map_info else None,
                "map_en": map_info.en if map_info else None,
            })
        maps.sort(key=lambda item: ((item["map_ja"] or item["map_en"] or "").lower(), item["map_id"]))
        pool.append({
            "mode_id": mode_id,
            "mode_ja": mode.ja if mode else None,
            "mode_en": mode.en if mode else None,
            "mode_slug": mode.slug if mode else None,
            "maps": maps,
        })

    await set_cache(cache_key, pool, ttl=TROPHY_POOL_CACHE_TTL)
    return pool


async def warmup_trophy_stats_caches(
    db: asyncpg.Connection,
    target_date: datetime.date,
    ctx: Any = None,
) -> None:
    """総合・直近モード・直近マップのキャッシュをウォームアップする。"""
    # [この部分は公開用リポジトリでは非公開にされています]


async def prepare_trophy_stats_catalog(db: asyncpg.Connection) -> None:
    """集計前にマップ/モードカタログを軽く同期する。"""
    try:
        sync_result = await sync_maps_and_modes_from_bsinfo(db)
        logger.info(f"マルチプレイ統計用のマップ/モード同期が完了しました: {sync_result}")
    except Exception as e:
        logger.warning(f"マルチプレイ統計用のマップ/モード同期に失敗しました: {e}", exc_info=True)
    await ensure_catalog(db)
