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

# 旧ページで使っていた手作りのマップ一覧画像。データが無いシーズンの代わりに表示する。
LEGACY_IMAGE_DIR = Path(__file__).resolve().parent.parent / "static" / "images" / "ranked_maps"
LEGACY_IMAGE_RE = re.compile(r"^ranked_maps_(\d{4})(\d{2})_(ja|en)\.webp$")
# 公式のシーズン切替が名目時刻(第3木曜 08:00 UTC)より早まることもあるので、窓を少し前に広げる
SEASON_EDGE_MARGIN = timedelta(hours=2)
FIRST_LEGACY_SEASON = 34

# 定番の6モード。この順に表示し、スロット名には略称を使う。
STANDARD_MODES: tuple[tuple[str, str, str], ...] = (
    ("gemGrab", "エメハン", "Gem Grab"),
    ("brawlBall", "サッカー", "Brawl Ball"),
    ("heist", "強奪", "Heist"),
    ("bounty", "賞金稼ぎ", "Bounty"),
    ("hotZone", "ホッゾ", "Hot Zone"),
    ("knockout", "ノック", "Knockout"),
)
_STANDARD_INDEX = {slug: index for index, (slug, _, _) in enumerate(STANDARD_MODES)}
_STANDARD_SHORT = {slug: (ja, en) for slug, ja, en in STANDARD_MODES}

# [この部分は公開用リポジトリでは非公開にされています]
# 終了したシーズンは、窓の中でこの割合以上の時間に観測が無いと自動では公開しない
MIN_PUBLIC_COVERAGE = 0.5


#* /---*---*---*---*---*---*---*---*/
#* シーズン・キャッシュ
#* /---*---*---*---*---*---*---*---*/
def season_start_at(season: int) -> datetime:
    """シーズンの名目上の開始日時(UTC)。"""
    start_date, _ = get_ranked_season_period(season)
    return datetime.combine(start_date, time(8, 0), tzinfo=UTC)


def season_dates(season: int) -> tuple[date, date]:
    return get_ranked_season_period(season)


def _floor_hour(value: datetime) -> datetime:
    return value.replace(minute=0, second=0, microsecond=0)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


async def bump_ranked_maps_cache() -> None:
    redis_client = get_redis()
    if not redis_client:
        return
    try:
        await redis_client.incr(CACHE_VERSION_KEY)
    except Exception as exc:
        logger.warning(f"ガチバトルマップ一覧キャッシュ世代の更新に失敗しました: {exc}")


async def ranked_maps_cache_version() -> int:
    redis_client = get_redis()
    if not redis_client:
        return 0
    try:
        raw = await redis_client.get(CACHE_VERSION_KEY)
        return int(raw) if raw is not None else 0
    except Exception:
        return 0


def legacy_images() -> dict[int, dict[str, str]]:
    """シーズン番号 → {lang: static相対パス}。"""
    result: dict[int, dict[str, str]] = {}
    if not LEGACY_IMAGE_DIR.is_dir():
        return result
    for path in LEGACY_IMAGE_DIR.iterdir():
        matched = LEGACY_IMAGE_RE.match(path.name)
        if not matched:
            continue
        year, month, lang = int(matched.group(1)), int(matched.group(2)), matched.group(3)
        season = FIRST_LEGACY_SEASON + (year - 2025) * 12 + (month - 6)
        result.setdefault(season, {})[lang] = f"/images/ranked_maps/{path.name}"
    return result


async def _ensure_season_row(db: asyncpg.Connection, season: int) -> asyncpg.Record:
    await db.execute("INSERT INTO ranked_seasons (season) VALUES ($1) ON CONFLICT (season) DO NOTHING", season)
    return await db.fetchrow("SELECT * FROM ranked_seasons WHERE season = $1", season)


# [この部分は公開用リポジトリでは非公開にされています]


#* /---*---*---*---*---*---*---*---*/
#* 表示用
#* /---*---*---*---*---*---*---*---*/
async def get_current_max_power_brawler_ids(db: asyncpg.Connection) -> list[int]:
    """今シーズンの最大レベルキャラのIDを返す。未設定なら空。キャッシュ世代ごとに10分キャッシュする。"""
    version = await ranked_maps_cache_version()
    current = calc_ranked_season()
    cache_key = f"ranked_maps:max_power:{version}:{current}"
    cached = await get_cache(cache_key)
    if isinstance(cached, dict) and isinstance(cached.get("ids"), list):
        return cached["ids"]

    row = await db.fetchrow("SELECT max_power_brawler_ids FROM ranked_seasons WHERE season = $1", current)
    raw_ids = row["max_power_brawler_ids"] if row else None
    ids = [int(item) for item in raw_ids] if raw_ids else []
    await set_cache(cache_key, {"ids": ids}, ttl=PAYLOAD_TTL_SECONDS)
    return ids


async def get_current_ranked_pool_maps(db: asyncpg.Connection) -> list[dict[str, Any]] | None:
    """今シーズンの最新期間のマップを {map_id, mode_id, map_name} で返す。推論できていない・非表示なら None。

    map_name はバトル履歴上の名前で、ranked_stats_* の map 列と一致する。記録が無ければカタログの英語名で代用する。
    """
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
                # テーマ色が定義されたモードはその色でカードを塗る。それ以外は既定の色
                "theme": get_mode_theme(mode_id),
                "name_ja": name_ja,
                "name_en": name_en,
                "short_ja": short_ja or name_ja,
                "short_en": short_en or name_en,
                "icons": mode_icon_candidates(mode_id, slug),
                "maps": [],
            }
        groups[key]["maps"].append({
            "map_id": map_id,
            "name_ja": (map_info.display_name("ja") if map_info else None) or str(map_id),
            "name_en": (map_info.display_name("en") if map_info else None) or str(map_id),
        })
    ordered = sorted(groups.values(), key=lambda group: _mode_group_key(group["slug"], group["mode_id"]))
    for group in ordered:
        group["maps"].sort(key=lambda entry: entry["map_id"])
    return ordered


def _short_date(value: date, lang: str) -> str:
    return f"{value.month}/{value.day}" if lang == "ja" else value.strftime("%b %-d")


def _season_span(start: date, end: date, lang: str, this_year: int) -> str:
    """シーズン選択肢の期間。今年のシーズンは年を省略し、それ以外は年を付ける(年をまたぐ場合は終了側にも)。"""
    if start.year == this_year and end.year == this_year:
        if lang == "ja":
            return f"{start.month}/{start.day}〜{end.month}/{end.day}"
        return f"{start.strftime('%b %-d')} – {end.strftime('%b %-d')}"
    if lang == "ja":
        end_text = f"{end.month}/{end.day}" if end.year == start.year else f"{end.year}/{end.month}/{end.day}"
        return f"{start.year}/{start.month}/{start.day}〜{end_text}"
    if end.year == start.year:
        return f"{start.strftime('%b %-d')} – {end.strftime('%b %-d, %Y')}"
    return f"{start.strftime('%b %-d, %Y')} – {end.strftime('%b %-d, %Y')}"


def _local_date(value: str | None, lang: str) -> date | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed.astimezone(JST if lang == "ja" else UTC).date()


def build_ranked_maps_view(
    payload: dict[str, Any],
    *,
    lang: str,
    season: int | None,
    period: int | None,
    brawler_names: dict[int, dict[str, str]],
) -> dict[str, Any]:
    """公開ページのテンプレート用に、選択中のシーズン・期間のデータを整える。ensure_catalog 済みであること。"""
    seasons = payload.get("seasons") or []
    current = payload.get("current_season")
    view: dict[str, Any] = {"seasons": [], "selected": None, "current_pending": False}
    if not seasons:
        return view
    by_number = {item["season"]: item for item in seasons}
    selected_number = season if season in by_number else current if current in by_number else seasons[0]["season"]
    selected = by_number[selected_number]
    view["current_pending"] = current not in by_number and selected_number == seasons[0]["season"]

    this_year = datetime.now(JST if lang == "ja" else UTC).year
    for item in seasons:
        span = _season_span(date.fromisoformat(item["start_date"]), date.fromisoformat(item["end_date"]), lang, this_year)
        label = f"シーズン{item['season']}（{span}）" if lang == "ja" else f"Season {item['season']} ({span})"
        view["seasons"].append({"season": item["season"], "label": label, "selected": item["season"] == selected_number})

    start_date = date.fromisoformat(selected["start_date"])
    end_date = date.fromisoformat(selected["end_date"])
    info: dict[str, Any] = {
        "season": selected_number,
        "is_current": selected_number == current,
        "source": selected["source"],
        "image": None,
        "periods": [],
        "period_seq": None,
        "modes": [],
        "map_count": 0,
        "max_power": None,
    }
    if selected["source"] == "image":
        images = selected.get("image") or {}
        info["image"] = images.get(lang) or images.get("ja")
        view["selected"] = info
        return view

    periods = selected.get("periods") or []
    seqs = [item["seq"] for item in periods]
    selected_seq = period if period in seqs else seqs[-1]
    for index, item in enumerate(periods):
        start = start_date if index == 0 else _local_date(item["start_at"], lang)
        if item["end_at"] is None:
            end = None
        elif index == len(periods) - 1:
            end = end_date
        else:
            end = _local_date(item["end_at"], lang)
        start_text = _short_date(start, lang) if start else ""
        end_text = _short_date(end, lang) if end else ""
        separator = "〜" if lang == "ja" else " – "
        info["periods"].append({
            "seq": item["seq"],
            "label": f"{start_text}{separator}{end_text}",
            "selected": item["seq"] == selected_seq,
            "is_latest": index == len(periods) - 1,
        })
    active = next(item for item in periods if item["seq"] == selected_seq)
    info["period_seq"] = selected_seq
    info["modes"] = group_maps_by_mode(active["maps"])
    info["map_count"] = len(active["maps"])

    max_power = selected.get("max_power")
    if max_power is not None:
        info["max_power"] = [
            {
                "id": brawler_id,
                "name": (brawler_names.get(brawler_id) or {}).get(lang) or (brawler_names.get(brawler_id) or {}).get("en") or str(brawler_id),
            }
            for brawler_id in max_power
        ]
    view["selected"] = info
    return view


#* /---*---*---*---*---*---*---*---*/
#* 管理画面
#* /---*---*---*---*---*---*---*---*/
# [この部分は公開用リポジトリでは非公開にされています]
