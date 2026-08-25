"""マップ/モードのIDカタログ、BSInfo同期、名前解決。"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

import asyncpg

from app.core.cache import get_redis
from app.core.logger import logger
from app.exceptions.custom_exceptions import DataBaseError
from app.services.admin_notification_service import emit_admin_notification
from app.services import bsinfoapi

MODE_ID_OFFSET = 48_000_000
CATALOG_TTL_SECONDS = 10 * 60
CATALOG_VERSION_KEY = b"maps_modes:catalog_version"

# gemGrab, brawlBall, heist, bounty, hotZone, knockout, soloShowdown, duoShowdown, trioShowdown, duels, airHockey, wipeout, basketBrawl, brawlArena, ...
MODE_ORDER_PRIORITY_IDS: tuple[int, ...] = (
    48000000,
    48000005,
    48000002,
    48000003,
    48000017,
    48000020,
    48000006,
    48000009,
    48000038,
    48000024,
    48000045,
    48000025,
    48000022,
    48000048,
    48000033,
    48000032,
    48000035,
    48000031,
)
MODE_ORDER_PRIORITY_INDEX: dict[int, int] = {
    mode_id: index for index, mode_id in enumerate(MODE_ORDER_PRIORITY_IDS)
}

MODE_ICON_STATIC_PREFIX = "/images/mode_icons/"
MODE_ICON_MYSTERY_PATH = "/images/ui/mystery.png"

# カタログ未ロード時の slug→ID。公式 modeId + 48000000。
WELL_KNOWN_MODE_IDS_BY_SLUG: dict[str, int] = {
    "gemGrab": 48_000_000,
    "heist": 48_000_002,
    "bounty": 48_000_003,
    "brawlBall": 48_000_005,
    "soloShowdown": 48_000_006,
    "duoShowdown": 48_000_009,
    "hotZone": 48_000_017,
    "knockout": 48_000_020,
    "basketBrawl": 48_000_022,
    "duels": 48_000_024,
    "wipeout": 48_000_025,
    "trioShowdown": 48_000_038,
    "airHockey": 48_000_045,
    "brawlHockey": 48_000_045,
    "brawlArena": 48_000_048,
}

_CAMEL_SPLIT_RE = re.compile(r"(.)([A-Z][a-z]+)")
_CAMEL_SPLIT_RE2 = re.compile(r"([a-z0-9])([A-Z])")


@dataclass(slots=True)
class ModeInfo:
    id: int
    slug: str | None = None
    en: str | None = None
    ja: str | None = None
    en_is_manual: bool = False
    ja_is_manual: bool = False
    desc_ja: str | None = None
    desc_en: str | None = None
    desc2_ja: str | None = None
    desc2_en: str | None = None
    overtime: bool | None = None
    overtime_text_ja: str | None = None
    overtime_text_en: str | None = None
    format_ja: str | None = None
    format_en: str | None = None
    color: str | None = None
    bg_color: str | None = None
    battle_time: int | None = None
    respawn_time: int | None = None
    disabled: bool = True
    is_boss_fight: bool = False
    is_special_event: bool = False
    is_not_rewarding_trophies: bool = False
    is_trophy_mode: bool = False
    rounds: int | None = None
    team_size: int | None = None
    team_count: int | None = None

    def display_name(self, lang: str) -> str | None:
        if lang == "ja" and self.ja:
            return self.ja
        if self.en:
            return self.en
        if self.slug:
            return format_mode_slug_to_display(self.slug)
        return self.ja


@dataclass(slots=True)
class MapInfo:
    id: int
    en: str | None = None
    ja: str | None = None
    en_is_manual: bool = False
    ja_is_manual: bool = False
    codename: str | None = None
    theme: int | None = None
    mode_id: int | None = None
    disabled: bool = False

    def display_name(self, lang: str) -> str | None:
        if lang == "ja" and self.ja:
            return self.ja
        return self.en or self.ja


_modes_by_id: dict[int, ModeInfo] = {}
_modes_by_slug: dict[str, ModeInfo] = {}
_maps_by_id: dict[int, MapInfo] = {}
_maps_by_en_lower: dict[str, MapInfo] = {}
_local_version: int | None = None
_local_loaded_at: float = 0.0


def format_mode_slug_to_display(slug: str | None) -> str:
    """'gemGrab' → 'Gem Grab'。"""
    if not slug:
        return ""
    spaced = _CAMEL_SPLIT_RE.sub(r"\1 \2", slug)
    spaced = _CAMEL_SPLIT_RE2.sub(r"\1 \2", spaced)
    return spaced.replace("_", " ").title()


def bsinfo_name_to_display(name: str | None) -> str | None:
    """'GEM GRAB' → 'Gem Grab'。空なら None。"""
    if not name or not str(name).strip():
        return None
    return str(name).strip().title()


def slug_from_bsinfo_name(name: str | None) -> str | None:
    """'GEM GRAB' → 'gemGrab'。公式slugと一致しない場合があるので初期値専用。"""
    if not name or not str(name).strip():
        return None
    words = [part for part in str(name).replace("_", " ").split() if part]
    if not words:
        return None
    first = words[0].lower()
    rest = "".join(word[:1].upper() + word[1:].lower() for word in words[1:])
    return first + rest


def _normalize_mode_icon_slug(slug: str | None) -> str | None:
    if not slug or not str(slug).strip():
        return None
    cleaned = str(slug).strip()
    if cleaned.lower().endswith(".png"):
        cleaned = cleaned[:-4]
    return cleaned or None


def resolve_mode_id_for_icon(
    mode_id: int | None = None,
    slug: str | None = None,
) -> tuple[int | None, str | None]:
    """アイコン用に mode_id と slug を解決する。カタログがあれば優先し、なければ既知slugを使う。"""
    resolved_id = mode_id
    resolved_slug = _normalize_mode_icon_slug(slug)
    if resolved_id is None and resolved_slug:
        info = get_mode_by_slug(resolved_slug)
        if info:
            resolved_id = info.id
            resolved_slug = info.slug or resolved_slug
        else:
            resolved_id = WELL_KNOWN_MODE_IDS_BY_SLUG.get(resolved_slug)
    return resolved_id, resolved_slug


def mode_icon_candidates(mode_id: int | None = None, slug: str | None = None) -> list[str]:
    """static からの相対パス。IDファイル → slugファイル → mystery.png。"""
    resolved_id, resolved_slug = resolve_mode_id_for_icon(mode_id, slug)
    paths: list[str] = []
    if resolved_id:
        paths.append(f"{MODE_ICON_STATIC_PREFIX}{resolved_id}.png")
    if resolved_slug:
        slug_path = f"{MODE_ICON_STATIC_PREFIX}{resolved_slug}.png"
        if slug_path not in paths:
            paths.append(slug_path)
    if MODE_ICON_MYSTERY_PATH not in paths:
        paths.append(MODE_ICON_MYSTERY_PATH)
    return paths


def mode_icon_asset_relpaths(
    mode_id: int | None = None,
    slug: str | None = None,
) -> tuple[str, str]:
    """画像レンダラ用。IMAGES_DIR からの相対パス (primary, fallback)。"""
    candidates = mode_icon_candidates(mode_id, slug)
    primary = candidates[0].removeprefix("/images/")
    fallback = candidates[1].removeprefix("/images/") if len(candidates) > 1 else "ui/mystery.png"
    return primary, fallback


def get_mode_slug_to_id() -> dict[str, int]:
    """フロントの slug→ID 解決用。カタログがあれば上書きする。"""
    mapping = dict(WELL_KNOWN_MODE_IDS_BY_SLUG)
    mapping.update({slug: info.id for slug, info in _modes_by_slug.items() if slug})
    return mapping


def normalize_official_mode_id(raw_mode_id: int | None) -> int | None:
    if raw_mode_id is None:
        return None
    if raw_mode_id < MODE_ID_OFFSET:
        return MODE_ID_OFFSET + raw_mode_id
    return raw_mode_id


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return bool(value)


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _mode_from_row(row: asyncpg.Record) -> ModeInfo:
    return ModeInfo(
        id=row["id"],
        slug=row["slug"],
        en=row["en"],
        ja=row["ja"],
        en_is_manual=_bool(row["en_is_manual"]),
        ja_is_manual=_bool(row["ja_is_manual"]),
        desc_ja=row["desc_ja"],
        desc_en=row["desc_en"],
        desc2_ja=row["desc2_ja"],
        desc2_en=row["desc2_en"],
        overtime=_optional_bool(row["overtime"]),
        overtime_text_ja=row["overtime_text_ja"],
        overtime_text_en=row["overtime_text_en"],
        format_ja=row["format_ja"],
        format_en=row["format_en"],
        color=row["color"],
        bg_color=row["bg_color"],
        battle_time=row["battle_time"],
        respawn_time=row["respawn_time"],
        disabled=_bool(row["disabled"], default=True),
        is_boss_fight=_bool(row["is_boss_fight"]),
        is_special_event=_bool(row["is_special_event"]),
        is_not_rewarding_trophies=_bool(row["is_not_rewarding_trophies"]),
        is_trophy_mode=_bool(row["is_trophy_mode"]),
        rounds=row["rounds"],
        team_size=row["team_size"],
        team_count=row["team_count"],
    )


def _map_from_row(row: asyncpg.Record) -> MapInfo:
    return MapInfo(
        id=row["id"],
        en=row["en"],
        ja=row["ja"],
        en_is_manual=_bool(row["en_is_manual"]),
        ja_is_manual=_bool(row["ja_is_manual"]),
        codename=row["codename"],
        theme=row["theme"],
        mode_id=row["mode_id"],
        disabled=_bool(row["disabled"]),
    )


async def _read_catalog_version() -> int:
    redis_client = get_redis()
    if not redis_client:
        return 0
    try:
        raw = await redis_client.get(CATALOG_VERSION_KEY)
        if raw is None:
            return 0
        return int(raw)
    except Exception:
        return 0


async def bump_catalog_version() -> None:
    global _local_version
    _local_version = None
    redis_client = get_redis()
    if not redis_client:
        return
    try:
        await redis_client.incr(CATALOG_VERSION_KEY)
    except Exception as exc:
        logger.warning(f"マップ/モードカタログ世代の更新に失敗しました: {exc}")


def get_mode_by_id(mode_id: int | None) -> ModeInfo | None:
    if not mode_id:
        return None
    return _modes_by_id.get(mode_id)


def get_mode_by_slug(slug: str | None) -> ModeInfo | None:
    if not slug:
        return None
    return _modes_by_slug.get(slug)


def get_map_by_id(map_id: int | None) -> MapInfo | None:
    if not map_id:
        return None
    return _maps_by_id.get(map_id)


def get_map_by_en(en: str | None) -> MapInfo | None:
    if not en:
        return None
    return _maps_by_en_lower.get(en.lower())


def resolve_mode_filter_label(mode_id: int, lang: str) -> str:
    mode = get_mode_by_id(mode_id)
    if mode:
        return mode.display_name(lang) or str(mode_id)
    return str(mode_id)


def resolve_map_filter_label(map_id: int, lang: str) -> str:
    map_info = get_map_by_id(map_id)
    if map_info:
        return map_info.display_name(lang) or str(map_id)
    return str(map_id)


def iter_modes() -> list[ModeInfo]:
    return list(_modes_by_id.values())


def iter_maps() -> list[MapInfo]:
    return list(_maps_by_id.values())


def mode_sort_key(mode_id: int | None = None, slug: str | None = None) -> tuple[int, int, str]:
    resolved_id = mode_id
    if resolved_id is None and slug:
        mode = get_mode_by_slug(slug)
        if mode:
            resolved_id = mode.id
    remainder_id = resolved_id if resolved_id is not None else 10**9
    slug_key = slug or ""
    if resolved_id in MODE_ORDER_PRIORITY_INDEX:
        return (MODE_ORDER_PRIORITY_INDEX[resolved_id], remainder_id, slug_key)
    return (len(MODE_ORDER_PRIORITY_IDS), remainder_id, slug_key)


async def ensure_catalog(db: asyncpg.Connection) -> None:
    global _local_version, _local_loaded_at, _modes_by_id, _modes_by_slug, _maps_by_id, _maps_by_en_lower
    now = time.monotonic()
    remote_version = await _read_catalog_version()
    if (
        _modes_by_id
        and _local_version == remote_version
        and (now - _local_loaded_at) < CATALOG_TTL_SECONDS
    ):
        return

    try:
        mode_rows = await db.fetch("SELECT * FROM modes")
        map_rows = await db.fetch("SELECT * FROM maps")
    except asyncpg.PostgresError as exc:
        logger.error(f"マップ/モードカタログの読み込みに失敗しました: {exc}")
        raise DataBaseError(str(exc)) from exc

    modes_by_id: dict[int, ModeInfo] = {}
    modes_by_slug: dict[str, ModeInfo] = {}
    for row in mode_rows:
        info = _mode_from_row(row)
        modes_by_id[info.id] = info
        if info.slug:
            modes_by_slug[info.slug] = info

    maps_by_id: dict[int, MapInfo] = {}
    maps_by_en_lower: dict[str, MapInfo] = {}
    for row in map_rows:
        info = _map_from_row(row)
        maps_by_id[info.id] = info
        if info.en:
            maps_by_en_lower.setdefault(info.en.lower(), info)

    _modes_by_id = modes_by_id
    _modes_by_slug = modes_by_slug
    _maps_by_id = maps_by_id
    _maps_by_en_lower = maps_by_en_lower
    _local_version = remote_version
    _local_loaded_at = now
    logger.debug(f"マップ/モードカタログを読み込みました: modes={len(modes_by_id)} maps={len(maps_by_id)}")


def _row_to_jsonable(row: asyncpg.Record) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in dict(row).items():
        if hasattr(value, "isoformat"):
            result[key] = value.isoformat()
        else:
            result[key] = value
    return result


async def get_all_maps(db: asyncpg.Connection) -> list[dict[str, Any]]:
    try:
        rows = await db.fetch(
            """
            SELECT maps.*, modes.slug AS mode_slug, modes.en AS mode_en, modes.ja AS mode_ja
            FROM maps
            LEFT JOIN modes ON modes.id = maps.mode_id
            ORDER BY maps.ja ASC NULLS LAST, maps.en ASC NULLS LAST, maps.id ASC
            """
        )
    except asyncpg.PostgresError as exc:
        raise DataBaseError(str(exc)) from exc
    return [_row_to_jsonable(row) for row in rows]


async def get_all_modes(db: asyncpg.Connection) -> list[dict[str, Any]]:
    try:
        rows = await db.fetch(
            """
            SELECT *
            FROM modes
            ORDER BY ja ASC NULLS LAST, en ASC NULLS LAST, id ASC
            """
        )
    except asyncpg.PostgresError as exc:
        raise DataBaseError(str(exc)) from exc
    return [_row_to_jsonable(row) for row in rows]


async def get_japanese_map_name(en: str, db: asyncpg.Connection) -> str | None:
    if not en:
        return None
    await ensure_catalog(db)
    info = get_map_by_en(en)
    return info.ja if info else None


async def get_english_map_name(ja: str, db: asyncpg.Connection) -> str | None:
    if not ja:
        return None
    await ensure_catalog(db)
    for info in _maps_by_id.values():
        if info.ja == ja:
            return info.en
    return None


async def get_japanese_mode_name(en_or_slug: str, db: asyncpg.Connection) -> str | None:
    if not en_or_slug:
        return None
    await ensure_catalog(db)
    info = get_mode_by_slug(en_or_slug)
    if info:
        return info.ja
    lowered = en_or_slug.lower()
    for mode in _modes_by_id.values():
        if (mode.en and mode.en.lower() == lowered) or (mode.ja and mode.ja == en_or_slug):
            return mode.ja
    return None


async def get_english_mode_name(ja: str, db: asyncpg.Connection) -> str | None:
    if not ja:
        return None
    await ensure_catalog(db)
    for mode in _modes_by_id.values():
        if mode.ja == ja:
            return mode.en or (format_mode_slug_to_display(mode.slug) if mode.slug else None)
    return None


async def ensure_mode_stub(
    db: asyncpg.Connection,
    mode_id: int,
    slug: str | None = None,
) -> None:
    existing = get_mode_by_id(mode_id)
    if existing:
        if slug and not existing.slug:
            await apply_official_mode_slug(db, mode_id, slug)
        return
    try:
        result = await db.execute(
            """
            INSERT INTO modes (id, slug, disabled, updated_at)
            VALUES ($1, NULL, FALSE, NOW())
            ON CONFLICT (id) DO NOTHING
            """,
            mode_id,
        )
    except asyncpg.PostgresError as exc:
        logger.error(f"モードstubの保存に失敗しました (id={mode_id}): {exc}")
        raise DataBaseError(str(exc)) from exc
    if result == "INSERT 0 1":
        await bump_catalog_version()
        await ensure_catalog(db)
    if slug:
        await apply_official_mode_slug(db, mode_id, slug)


async def ensure_map_stub(
    db: asyncpg.Connection,
    map_id: int,
    en: str | None = None,
    mode_id: int | None = None,
) -> None:
    if map_id <= 0:
        return
    existing = get_map_by_id(map_id)
    if existing:
        needs_en = bool(en) and not existing.en
        needs_mode = bool(mode_id) and not existing.mode_id
        if not needs_en and not needs_mode:
            return
        try:
            await db.execute(
                """
                UPDATE maps SET
                    en = COALESCE(en, $2),
                    mode_id = COALESCE(mode_id, $3),
                    updated_at = NOW()
                WHERE id = $1
                  AND ((en IS NULL AND $2 IS NOT NULL) OR (mode_id IS NULL AND $3 IS NOT NULL))
                """,
                map_id,
                en,
                mode_id,
            )
        except asyncpg.PostgresError as exc:
            logger.error(f"マップstubの更新に失敗しました (id={map_id}): {exc}")
            raise DataBaseError(str(exc)) from exc
        await bump_catalog_version()
        await ensure_catalog(db)
        return
    try:
        result = await db.execute(
            """
            INSERT INTO maps (id, en, mode_id, disabled, updated_at)
            VALUES ($1, $2, $3, FALSE, NOW())
            ON CONFLICT (id) DO NOTHING
            """,
            map_id,
            en,
            mode_id,
        )
    except asyncpg.PostgresError as exc:
        logger.error(f"マップstubの保存に失敗しました (id={map_id}): {exc}")
        raise DataBaseError(str(exc)) from exc
    if result == "INSERT 0 1":
        await bump_catalog_version()
        await ensure_catalog(db)


async def apply_official_mode_slug(
    db: asyncpg.Connection,
    mode_id: int,
    slug: str | None,
) -> None:
    if not slug:
        return
    try:
        result = await db.execute(
            """
            UPDATE modes
            SET slug = $2, updated_at = NOW()
            WHERE id = $1 AND slug IS NULL
              AND NOT EXISTS (SELECT 1 FROM modes m2 WHERE m2.slug = $2)
            """,
            mode_id,
            slug,
        )
    except asyncpg.PostgresError as exc:
        logger.error(f"モードslugの補完に失敗しました (id={mode_id}): {exc}")
        raise DataBaseError(str(exc)) from exc
    if result == "UPDATE 1":
        await bump_catalog_version()


async def resolve_mode_id_for_battle(
    *,
    mode_id: int | None,
    event_id: int | None,
    event_mode: str | None,
    battle_mode: str | None,
) -> int | None:
    if mode_id:
        return mode_id
    if event_id:
        map_info = get_map_by_id(event_id)
        if map_info and map_info.mode_id:
            return map_info.mode_id
    for slug in (event_mode, battle_mode):
        mode = get_mode_by_slug(slug)
        if mode:
            return mode.id
    return None


async def prepare_battle_mode_id(
    db: asyncpg.Connection,
    *,
    mode_id: int | None,
    event_id: int | None,
    event_map: str | None,
    event_mode: str | None,
    battle_mode: str | None,
) -> int | None:
    await ensure_catalog(db)
    resolved = await resolve_mode_id_for_battle(
        mode_id=mode_id,
        event_id=event_id,
        event_mode=event_mode,
        battle_mode=battle_mode,
    )
    preferred_slug = event_mode or battle_mode
    if resolved:
        await ensure_mode_stub(db, resolved, preferred_slug)
        await apply_official_mode_slug(db, resolved, event_mode)
    elif preferred_slug:
        mode = get_mode_by_slug(preferred_slug)
        if mode:
            resolved = mode.id
    if event_id and event_id > 0:
        await ensure_map_stub(db, event_id, event_map, resolved)
    await ensure_catalog(db)
    return resolved


async def sync_maps_and_modes_from_bsinfo(
    db: asyncpg.Connection,
    *,
    force: bool = False,
) -> dict[str, int]:
    """BSInfoの一覧で maps/modes を同期する。名前は手動フラグが無い限り上書きする。"""
    modes_payload = await bsinfoapi.get_gamemode_catalog(force=force)
    maps_payload = await bsinfoapi.get_maps_catalog(force=force)
    if modes_payload is None or maps_payload is None:
        logger.warning("BSInfoのマップ/モード同期をスキップしました（取得失敗）")
        return {"modes_inserted": 0, "modes_updated": 0, "maps_inserted": 0, "maps_updated": 0}

    existing_mode_ids = {row["id"] for row in await db.fetch("SELECT id FROM modes")}
    existing_map_ids = {row["id"] for row in await db.fetch("SELECT id FROM maps")}

    mode_records = []
    for item in modes_payload:
        mode_id = item["id"]
        raw_en = item.get("name_en")
        mode_records.append((
            mode_id,
            None,
            bsinfo_name_to_display(raw_en),
            item.get("name_ja"),
            item.get("desc_ja"),
            item.get("desc_en"),
            item.get("desc2_ja"),
            item.get("desc2_en"),
            item.get("overtime"),
            item.get("overtime_text_ja"),
            item.get("overtime_text_en"),
            item.get("format_ja"),
            item.get("format_en"),
            item.get("color"),
            item.get("bg_color"),
            item.get("battle_time"),
            item.get("respawn_time"),
            bool(item.get("disabled", True)),
            bool(item.get("is_boss_fight", False)),
            bool(item.get("is_special_event", False)),
            bool(item.get("is_not_rewarding_trophies", False)),
            bool(item.get("is_trophy_mode", False)),
            item.get("rounds"),
            item.get("team_size"),
            item.get("team_count"),
        ))

    try:
        await db.executemany(
            """
            INSERT INTO modes (
                id, slug, en, ja,
                desc_ja, desc_en, desc2_ja, desc2_en,
                overtime, overtime_text_ja, overtime_text_en, format_ja, format_en,
                color, bg_color, battle_time, respawn_time,
                disabled, is_boss_fight, is_special_event, is_not_rewarding_trophies, is_trophy_mode,
                rounds, team_size, team_count, updated_at
            ) VALUES (
                $1, $2, $3, $4,
                $5, $6, $7, $8,
                $9, $10, $11, $12, $13,
                $14, $15, $16, $17,
                $18, $19, $20, $21, $22,
                $23, $24, $25, NOW()
            )
            ON CONFLICT (id) DO UPDATE SET
                slug = COALESCE(modes.slug, EXCLUDED.slug),
                en = CASE WHEN modes.en_is_manual THEN modes.en ELSE EXCLUDED.en END,
                ja = CASE WHEN modes.ja_is_manual THEN modes.ja ELSE EXCLUDED.ja END,
                desc_ja = EXCLUDED.desc_ja,
                desc_en = EXCLUDED.desc_en,
                desc2_ja = EXCLUDED.desc2_ja,
                desc2_en = EXCLUDED.desc2_en,
                overtime = EXCLUDED.overtime,
                overtime_text_ja = EXCLUDED.overtime_text_ja,
                overtime_text_en = EXCLUDED.overtime_text_en,
                format_ja = EXCLUDED.format_ja,
                format_en = EXCLUDED.format_en,
                color = EXCLUDED.color,
                bg_color = EXCLUDED.bg_color,
                battle_time = EXCLUDED.battle_time,
                respawn_time = EXCLUDED.respawn_time,
                disabled = EXCLUDED.disabled,
                is_boss_fight = EXCLUDED.is_boss_fight,
                is_special_event = EXCLUDED.is_special_event,
                is_not_rewarding_trophies = EXCLUDED.is_not_rewarding_trophies,
                is_trophy_mode = EXCLUDED.is_trophy_mode,
                rounds = EXCLUDED.rounds,
                team_size = EXCLUDED.team_size,
                team_count = EXCLUDED.team_count,
                updated_at = NOW()
            """,
            mode_records,
        )
    except asyncpg.PostgresError as exc:
        logger.error(f"モード同期中にエラー: {exc}", exc_info=True)
        raise DataBaseError(str(exc)) from exc

    map_records = []
    for item in maps_payload:
        map_records.append((
            item["id"],
            item.get("name_en"),
            item.get("name_ja"),
            item.get("codename"),
            item.get("theme"),
            item.get("mode_id"),
            bool(item.get("disabled", False)),
        ))

    try:
        await db.executemany(
            """
            INSERT INTO maps (
                id, en, ja, codename, theme, mode_id, disabled, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, NOW()
            )
            ON CONFLICT (id) DO UPDATE SET
                en = CASE WHEN maps.en_is_manual THEN maps.en ELSE EXCLUDED.en END,
                ja = CASE WHEN maps.ja_is_manual THEN maps.ja ELSE EXCLUDED.ja END,
                codename = EXCLUDED.codename,
                theme = EXCLUDED.theme,
                mode_id = COALESCE(EXCLUDED.mode_id, maps.mode_id),
                disabled = EXCLUDED.disabled,
                updated_at = NOW()
            """,
            map_records,
        )
    except asyncpg.PostgresError as exc:
        logger.error(f"マップ同期中にエラー: {exc}", exc_info=True)
        raise DataBaseError(str(exc)) from exc

    modes_inserted = 0
    for item in modes_payload:
        mode_id = item["id"]
        if mode_id in existing_mode_ids:
            continue
        modes_inserted += 1
        label = item.get("name_ja") or bsinfo_name_to_display(item.get("name_en")) or str(mode_id)
        await emit_admin_notification(
            db,
            "mode_created",
            title="新しいゲームモードが追加されました",
            summary=f"ID: {mode_id} / {label}",
            payload={"id": mode_id, "en": item.get("name_en"), "ja": item.get("name_ja")},
        )

    maps_inserted = 0
    for item in maps_payload:
        map_id = item["id"]
        if map_id in existing_map_ids:
            continue
        maps_inserted += 1
        label = item.get("name_ja") or item.get("name_en") or str(map_id)
        await emit_admin_notification(
            db,
            "map_created",
            title="新しいマップが追加されました",
            summary=f"ID: {map_id} / {label}",
            payload={"id": map_id, "en": item.get("name_en"), "ja": item.get("name_ja")},
        )

    await bump_catalog_version()
    await ensure_catalog(db)

    result = {
        "modes_inserted": modes_inserted,
        "modes_updated": max(0, len(modes_payload) - modes_inserted),
        "maps_inserted": maps_inserted,
        "maps_updated": max(0, len(maps_payload) - maps_inserted),
    }
    logger.info(
        "BSInfoマップ/モード同期が完了しました: "
        f"modes +{result['modes_inserted']}/upd {result['modes_updated']}, "
        f"maps +{result['maps_inserted']}/upd {result['maps_updated']}"
    )
    return result


async def fill_slugs_from_legacy_modes(db: asyncpg.Connection) -> int:
    """modes_legacy.en（旧slug）を突合して空の slug を埋める。ja は転写しない。"""
    exists = await db.fetchval("SELECT to_regclass('public.modes_legacy')")
    if not exists:
        return 0
    legacy_rows = await db.fetch("SELECT DISTINCT en FROM modes_legacy WHERE en IS NOT NULL")
    updated = 0
    for row in legacy_rows:
        slug = row["en"]
        slug_taken = await db.fetchval("SELECT 1 FROM modes WHERE slug = $1", slug)
        if slug_taken:
            continue
        display_upper = format_mode_slug_to_display(slug).upper()
        candidates = await db.fetch(
            """
            SELECT id FROM modes
            WHERE slug IS NULL AND en IS NOT NULL AND upper(en) = $1
            ORDER BY id
            """,
            display_upper,
        )
        if len(candidates) != 1:
            continue
        result = await db.execute(
            """
            UPDATE modes
            SET slug = $2, updated_at = NOW()
            WHERE id = $1 AND slug IS NULL
            """,
            candidates[0]["id"],
            slug,
        )
        if result == "UPDATE 1":
            updated += 1
    if updated:
        await bump_catalog_version()
        await ensure_catalog(db)
    logger.info(f"legacy slug を {updated} 件補完しました")
    return updated


async def update_map_from_admin(db: asyncpg.Connection, payload: dict[str, Any]) -> None:
    map_id = int(payload["id"])
    en = _empty_to_none(payload.get("en"))
    ja = _empty_to_none(payload.get("ja"))
    current = await db.fetchrow("SELECT en, ja FROM maps WHERE id = $1", map_id)
    if current is None:
        raise DataBaseError(f"map {map_id} not found")
    en_is_manual = current["en"] != en
    ja_is_manual = current["ja"] != ja
    try:
        result = await db.execute(
            """
            UPDATE maps SET
                en = $2,
                ja = $3,
                en_is_manual = CASE WHEN $4 THEN TRUE ELSE en_is_manual END,
                ja_is_manual = CASE WHEN $5 THEN TRUE ELSE ja_is_manual END,
                codename = $6,
                theme = $7,
                mode_id = $8,
                disabled = $9,
                updated_at = NOW()
            WHERE id = $1
            """,
            map_id,
            en,
            ja,
            en_is_manual,
            ja_is_manual,
            _empty_to_none(payload.get("codename")),
            _optional_int(payload.get("theme")),
            _optional_int(payload.get("mode_id")),
            bool(payload.get("disabled", False)),
        )
    except asyncpg.PostgresError as exc:
        raise DataBaseError(str(exc)) from exc
    if result != "UPDATE 1":
        raise DataBaseError(f"map {map_id} not updated")
    await bump_catalog_version()


async def update_mode_from_admin(db: asyncpg.Connection, payload: dict[str, Any]) -> None:
    mode_id = int(payload["id"])
    en = _empty_to_none(payload.get("en"))
    ja = _empty_to_none(payload.get("ja"))
    current = await db.fetchrow("SELECT en, ja FROM modes WHERE id = $1", mode_id)
    if current is None:
        raise DataBaseError(f"mode {mode_id} not found")
    en_is_manual = current["en"] != en
    ja_is_manual = current["ja"] != ja
    try:
        result = await db.execute(
            """
            UPDATE modes SET
                slug = $2,
                en = $3,
                ja = $4,
                en_is_manual = CASE WHEN $5 THEN TRUE ELSE en_is_manual END,
                ja_is_manual = CASE WHEN $6 THEN TRUE ELSE ja_is_manual END,
                desc_ja = $7,
                desc_en = $8,
                desc2_ja = $9,
                desc2_en = $10,
                overtime = $11,
                overtime_text_ja = $12,
                overtime_text_en = $13,
                format_ja = $14,
                format_en = $15,
                color = $16,
                bg_color = $17,
                battle_time = $18,
                respawn_time = $19,
                disabled = $20,
                is_boss_fight = $21,
                is_special_event = $22,
                is_not_rewarding_trophies = $23,
                is_trophy_mode = $24,
                rounds = $25,
                team_size = $26,
                team_count = $27,
                updated_at = NOW()
            WHERE id = $1
            """,
            mode_id,
            _empty_to_none(payload.get("slug")),
            en,
            ja,
            en_is_manual,
            ja_is_manual,
            _empty_to_none(payload.get("desc_ja")),
            _empty_to_none(payload.get("desc_en")),
            _empty_to_none(payload.get("desc2_ja")),
            _empty_to_none(payload.get("desc2_en")),
            _optional_bool_or_none(payload.get("overtime")),
            _empty_to_none(payload.get("overtime_text_ja")),
            _empty_to_none(payload.get("overtime_text_en")),
            _empty_to_none(payload.get("format_ja")),
            _empty_to_none(payload.get("format_en")),
            _empty_to_none(payload.get("color")),
            _empty_to_none(payload.get("bg_color")),
            _optional_int(payload.get("battle_time")),
            _optional_int(payload.get("respawn_time")),
            bool(payload.get("disabled", False)),
            bool(payload.get("is_boss_fight", False)),
            bool(payload.get("is_special_event", False)),
            bool(payload.get("is_not_rewarding_trophies", False)),
            bool(payload.get("is_trophy_mode", False)),
            _optional_int(payload.get("rounds")),
            _optional_int(payload.get("team_size")),
            _optional_int(payload.get("team_count")),
        )
    except asyncpg.PostgresError as exc:
        raise DataBaseError(str(exc)) from exc
    if result != "UPDATE 1":
        raise DataBaseError(f"mode {mode_id} not updated")
    await bump_catalog_version()


def _empty_to_none(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


def _optional_int(value: Any) -> int | None:
    value = _empty_to_none(value)
    if value is None:
        return None
    return int(value)


def _optional_bool_or_none(value: Any) -> bool | None:
    value = _empty_to_none(value)
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return bool(value)


async def collect_unresolved_report(
    db: asyncpg.Connection,
    *,
    include_battle_scans: bool = False,
) -> dict[str, Any]:
    """カタログの欠けを返す。battles 全表スキャンはスクリプト用に opt-in。"""
    modes_without_slug = await db.fetch(
        "SELECT id, en, ja FROM modes WHERE slug IS NULL ORDER BY id"
    )
    nameless_modes = await db.fetch(
        "SELECT id FROM modes WHERE en IS NULL AND ja IS NULL ORDER BY id"
    )
    maps_without_name = await db.fetch(
        "SELECT id FROM maps WHERE en IS NULL AND ja IS NULL ORDER BY id"
    )

    legacy_maps_unmatched: list[str] = []
    legacy_modes_unmatched: list[str] = []
    if await db.fetchval("SELECT to_regclass('public.maps_legacy')"):
        legacy_maps_unmatched = [
            row["en"]
            for row in await db.fetch(
                """
                SELECT en FROM maps_legacy
                WHERE en IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM maps WHERE maps.en IS NOT NULL AND lower(maps.en) = lower(maps_legacy.en)
                  )
                ORDER BY en
                """
            )
        ]
    if await db.fetchval("SELECT to_regclass('public.modes_legacy')"):
        legacy_modes_unmatched = [
            row["en"]
            for row in await db.fetch(
                """
                SELECT en FROM modes_legacy
                WHERE en IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM modes WHERE modes.slug = modes_legacy.en
                  )
                ORDER BY en
                """
            )
        ]

    result: dict[str, Any] = {
        "modes_without_slug": [dict(row) for row in modes_without_slug],
        "nameless_mode_ids": [row["id"] for row in nameless_modes],
        "maps_without_name_ids": [row["id"] for row in maps_without_name],
        "legacy_maps_unmatched": legacy_maps_unmatched,
        "legacy_modes_unmatched": legacy_modes_unmatched,
        "missing_map_ids": None,
        "null_mode_id_battles": None,
        "null_mode_id_archived": None,
    }
    if not include_battle_scans:
        return result

    missing_maps = await db.fetchval(
        """
        SELECT COUNT(*) FROM (
            SELECT DISTINCT event_id
            FROM battles
            WHERE event_id IS NOT NULL AND event_id > 0
              AND NOT EXISTS (SELECT 1 FROM maps WHERE maps.id = battles.event_id)
        ) t
        """
    )
    result["missing_map_ids"] = int(missing_maps or 0)
    result["null_mode_id_battles"] = int(
        await db.fetchval("SELECT COUNT(*) FROM battles WHERE mode_id IS NULL") or 0
    )
    result["null_mode_id_archived"] = int(
        await db.fetchval("SELECT COUNT(*) FROM archived_battles WHERE mode_id IS NULL") or 0
    )
    return result


async def get_map_names_by_id(db: asyncpg.Connection) -> dict[str, dict[str, Any]]:
    await ensure_catalog(db)
    result: dict[str, dict[str, Any]] = {}
    for info in _maps_by_id.values():
        mode = get_mode_by_id(info.mode_id) if info.mode_id else None
        result[str(info.id)] = {
            "ja": info.ja,
            "en": info.en,
            "mode_id": info.mode_id,
            "mode_slug": mode.slug if mode else None,
        }
    return result
