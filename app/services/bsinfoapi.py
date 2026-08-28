import asyncio
import httpx
from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg

from app.core.cache import get_cache, set_cache
from app.core.logger import logger


BSINFO_BASE_URL = "https:// [この部分は公開用リポジトリでは非公開にされています]


async def _get_accessory_all_levels_bundled(
    *,
    kind: str,
    endpoint: str,
    list_key: str,
    brawler_id: int,
    include_cooldown: bool,
    include_use_rate: bool,
) -> dict[str, dict[str, dict[str, Any]]]:
    brawler_id = _to_int(brawler_id)
    if brawler_id is None or brawler_id <= 0:
        return {str(level): {} for level in BSINFO_POWER_LEVELS}

    cache_key_update_lock = f"bsinfo_update_lock:{kind}_all:{brawler_id}"
    cached_data_update_lock = await get_cache(cache_key_update_lock)
    if not cached_data_update_lock:
        await set_cache(key=cache_key_update_lock, value=True, ttl=BSINFO_ACCESSORY_UPDATE_LOCK_TTL)

    cache_key = f"bsinfo:{kind}_all:{brawler_id}"
    cached_data = await get_cache(cache_key)
    if cached_data_update_lock and isinstance(cached_data, dict) and cached_data:
        return cached_data

    semaphore = asyncio.Semaphore(6)

    async def _fetch_level(level: int) -> tuple[str, dict[str, dict[str, Any]]]:
        async with semaphore:
            level_data = await _fetch_brawler_accessory_map_uncached(
                endpoint=endpoint,
                list_key=list_key,
                brawler_id=brawler_id,
                level=level,
                include_cooldown=include_cooldown,
                include_use_rate=include_use_rate,
            )
            return str(level), level_data

    pairs = await asyncio.gather(*[_fetch_level(level) for level in BSINFO_POWER_LEVELS])
    bundled = {level_key: level_data for level_key, level_data in pairs}

    if any(bundled.values()):
        if include_use_rate and isinstance(cached_data, dict) and cached_data:
            bundled = _preserve_cached_use_rates_bundled(bundled, cached_data)
        await set_cache(key=cache_key, value=bundled, ttl=None)
        await set_cache(key=cache_key_update_lock, value=True, ttl=BSINFO_ACCESSORY_UPDATE_LOCK_TTL)
        return bundled

    if isinstance(cached_data, dict) and cached_data:
        return cached_data
    return bundled


async def _get_brawler_accessory_map(
    *,
    kind: str,
    endpoint: str,
    list_key: str,
    brawler_id: int,
    level: int,
    include_cooldown: bool,
    include_use_rate: bool,
) -> dict[str, dict[str, Any]]:
    brawler_id = _to_int(brawler_id)
    if brawler_id is None or brawler_id <= 0:
        return {}

    level = _normalize_power_level(level)

    cache_key_update_lock = f"bsinfo_update_lock:{kind}:{brawler_id}:level:{level}"
    cached_data_update_lock = await get_cache(cache_key_update_lock)
    if not cached_data_update_lock:
        await set_cache(key=cache_key_update_lock, value=True, ttl=BSINFO_ACCESSORY_UPDATE_LOCK_TTL)

    cache_key = f"bsinfo:{kind}:{brawler_id}:level:{level}"
    cached_data = await get_cache(cache_key)
    if cached_data_update_lock and isinstance(cached_data, dict):
        return cached_data

    merged = await _fetch_brawler_accessory_map_uncached(
        endpoint=endpoint,
        list_key=list_key,
        brawler_id=brawler_id,
        level=level,
        include_cooldown=include_cooldown,
        include_use_rate=include_use_rate,
    )

    if merged:
        if include_use_rate and isinstance(cached_data, dict) and cached_data:
            merged = _preserve_cached_use_rates(merged, cached_data)
        await set_cache(key=cache_key, value=merged, ttl=None)
        await set_cache(key=cache_key_update_lock, value=True, ttl=BSINFO_ACCESSORY_UPDATE_LOCK_TTL)
        return merged

    if isinstance(cached_data, dict):
        return cached_data
    return {}


async def _fetch_brawler_accessory_map_uncached(
    *,
    endpoint: str,
    list_key: str,
    brawler_id: int,
    level: int,
    include_cooldown: bool,
    include_use_rate: bool,
) -> dict[str, dict[str, Any]]:
    response_en, response_ja = await asyncio.gather(
        _api_client.get(f"{endpoint}/{brawler_id}", params={"lang": "en", "level": level}),
        _api_client.get(f"{endpoint}/{brawler_id}", params={"lang": "ja", "level": level}),
    )
    return _merge_brawler_accessory_lists(
        response_en=response_en if isinstance(response_en, dict) else None,
        response_ja=response_ja if isinstance(response_ja, dict) else None,
        list_key=list_key,
        include_cooldown=include_cooldown,
        include_use_rate=include_use_rate,
    )

def _merge_brawler_accessory_lists(
    *,
    response_en: dict[str, Any] | None,
    response_ja: dict[str, Any] | None,
    list_key: str,
    include_cooldown: bool,
    include_use_rate: bool,
) -> dict[str, dict[str, Any]]:
    items_en = _extract_accessory_list(response_en, list_key)
    items_ja = _extract_accessory_list(response_ja, list_key)
    if not items_en and not items_ja:
        return {}

    by_id_en = {_to_int(item.get("id")): item for item in items_en if _to_int(item.get("id")) is not None}
    by_id_ja = {_to_int(item.get("id")): item for item in items_ja if _to_int(item.get("id")) is not None}
    all_ids = [accessory_id for accessory_id in by_id_en.keys() | by_id_ja.keys() if accessory_id is not None]

    merged: dict[str, dict[str, Any]] = {}
    for accessory_id in all_ids:
        item_en = by_id_en.get(accessory_id, {})
        item_ja = by_id_ja.get(accessory_id, {})
        entry: dict[str, Any] = {
            "id": accessory_id,
            "name_ja": _as_optional_str(item_ja.get("name")),
            "name_en": _as_optional_str(item_en.get("name")),
            "description_ja": _as_optional_str(item_ja.get("desc")),
            "description_en": _as_optional_str(item_en.get("desc")),
            "buddy_description_ja": _as_optional_str(item_ja.get("buddy_desc")),
            "buddy_description_en": _as_optional_str(item_en.get("buddy_desc")),
        }
        if include_cooldown:
            entry["cooldown"] = _coalesce(_to_float(item_ja.get("cooldown")), _to_float(item_en.get("cooldown")))
        if include_use_rate:
            entry["use_rate"] = _coalesce(_to_float(item_ja.get("useRate")), _to_float(item_en.get("useRate")))
        merged[str(accessory_id)] = entry
    return merged


def _merge_gear_responses(
    *,
    response_en: dict[str, Any] | None,
    response_ja: dict[str, Any] | None,
) -> dict[str, Any] | None:
    item_en = _extract_gear_item(response_en)
    item_ja = _extract_gear_item(response_ja)
    if not item_en and not item_ja:
        return None

    gear_id = _coalesce(_to_int(item_ja.get("id")), _to_int(item_en.get("id")))
    if gear_id is None:
        return None

    return {
        "id": gear_id,
        "name_ja": _as_optional_str(item_ja.get("name")),
        "name_en": _as_optional_str(item_en.get("name")),
        "description_ja": _as_optional_str(item_ja.get("desc")),
        "description_en": _as_optional_str(item_en.get("desc")),
    }


def _extract_accessory_list(response: dict[str, Any] | None, list_key: str) -> list[dict[str, Any]]:
    brawler_payload = _extract_brawler_payload(response)
    raw_list = brawler_payload.get(list_key)
    if not isinstance(raw_list, list):
        return []
    return [item for item in raw_list if isinstance(item, dict)]


def _extract_gear_item(response: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {}
    item = response.get("item")
    return item if isinstance(item, dict) else {}


def _normalize_power_level(level: int | None) -> int:
    parsed = _to_int(level)
    if parsed is None or parsed < 1:
        return 1
    if parsed > 11:
        return 11
    return parsed


def _extract_brawler_payload(response: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {}
    payload = response.get("brawler")
    return payload if isinstance(payload, dict) else {}


def _extract_status_payload(brawler_payload: dict[str, Any]) -> dict[str, Any]:
    status_payload = brawler_payload.get("status")
    return status_payload if isinstance(status_payload, dict) else {}


def _parse_owned_skins_response(response: Any) -> tuple[dict[int, list[int]], bool]:
    if not isinstance(response, dict):
        return {}, False

    brawlers = response.get("brawlers")
    if not isinstance(brawlers, list):
        return {}, False

    result: dict[int, list[int]] = {}

    for item in brawlers:
        if not isinstance(item, dict):
            continue

        brawler_id = _to_int(item.get("id"))
        if brawler_id is None:
            continue

        owned = item.get("owned")
        owned_ids: list[int] = []
        seen: set[int] = set()
        if isinstance(owned, list):
            for skin_data in owned:
                if not isinstance(skin_data, dict):
                    continue
                skin_id = _to_int(skin_data.get("id"))
                if skin_id is None or skin_id in seen:
                    continue
                seen.add(skin_id)
                owned_ids.append(skin_id)

        result[brawler_id] = owned_ids

    return result, True


def _normalize_owned_skins_dict(data: Any) -> dict[int, list[int]]:
    if not isinstance(data, dict):
        return {}

    normalized: dict[int, list[int]] = {}

    for key, value in data.items():
        brawler_id = _to_int(key)
        if brawler_id is None:
            continue

        skin_ids: list[int] = []
        seen: set[int] = set()
        if isinstance(value, list):
            for skin_id_raw in value:
                skin_id = _to_int(skin_id_raw)
                if skin_id is None or skin_id in seen:
                    continue
                seen.add(skin_id)
                skin_ids.append(skin_id)

        normalized[brawler_id] = skin_ids

    return normalized


def _normalize_tag(tag: str | None) -> str:
    if not isinstance(tag, str):
        return ""
    normalized = tag.strip().upper()
    if not normalized:
        return ""
    return normalized if normalized.startswith("#") else f"#{normalized}"


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _to_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_number(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        as_float = float(value)
        return int(as_float) if as_float.is_integer() else as_float

    try:
        as_float = float(str(value).strip())
    except (TypeError, ValueError):
        return None

    return int(as_float) if as_float.is_integer() else as_float


def _coalesce(*values):
    for value in values:
        if value is not None:
            return value
    return None


def _preserve_cached_use_rates(
    new_map: dict[str, dict[str, Any]],
    cached_map: dict[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    """Keep previously cached use_rate when a fresh API response omits or nulls it."""
    if not isinstance(cached_map, dict) or not cached_map:
        return new_map

    for accessory_id, entry in new_map.items():
        if not isinstance(entry, dict) or entry.get("use_rate") is not None:
            continue
        cached_entry = cached_map.get(accessory_id)
        if not isinstance(cached_entry, dict):
            continue
        cached_use_rate = cached_entry.get("use_rate")
        if cached_use_rate is not None:
            entry["use_rate"] = cached_use_rate
    return new_map


def _preserve_cached_use_rates_bundled(
    new_bundled: dict[str, dict[str, dict[str, Any]]],
    cached_bundled: dict[str, dict[str, dict[str, Any]]] | None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Preserve use_rate per level/accessory across bundled accessory caches."""
    if not isinstance(cached_bundled, dict) or not cached_bundled:
        return new_bundled

    for level_key, new_level_map in new_bundled.items():
        if not isinstance(new_level_map, dict):
            continue
        cached_level_map = cached_bundled.get(level_key)
        if isinstance(cached_level_map, dict):
            _preserve_cached_use_rates(new_level_map, cached_level_map)
    return new_bundled


def apply_bsinfo_overlay_to_accessory(
    accessory: dict[str, Any],
    overlay: dict[str, Any] | None,
    *,
    clear_missing_buffie: bool = True,
) -> dict[str, Any]:
    """Overwrite DB accessory display fields with BSInfo data when available."""
    if not isinstance(accessory, dict) or not isinstance(overlay, dict) or not overlay:
        return accessory

    name = accessory.get("name")
    if not isinstance(name, dict):
        name = {}
        accessory["name"] = name
    if overlay.get("name_ja"):
        name["ja"] = overlay["name_ja"]
    if overlay.get("name_en"):
        name["en"] = overlay["name_en"]

    description = accessory.get("description")
    if not isinstance(description, dict):
        description = {}
        accessory["description"] = description
    if overlay.get("description_ja"):
        description["ja"] = overlay["description_ja"]
    if overlay.get("description_en"):
        description["en"] = overlay["description_en"]

    buddy_ja = overlay.get("buddy_description_ja")
    buddy_en = overlay.get("buddy_description_en")
    if buddy_ja or buddy_en:
        accessory["description_with_buffie"] = {"ja": buddy_ja, "en": buddy_en}
    elif clear_missing_buffie:
        accessory["description_with_buffie"] = None

    if overlay.get("cooldown") is not None:
        accessory["cooldown"] = overlay["cooldown"]
        accessory["cooldown_with_buffie"] = overlay["cooldown"]

    if overlay.get("use_rate") is not None:
        accessory["use_rate"] = overlay["use_rate"]

    return accessory


def apply_bsinfo_overlays_for_level(
    accessories: list[dict[str, Any]],
    level_map: dict[str, dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Apply a single-level BSInfo map onto a list of DB accessories."""
    if not accessories:
        return accessories
    if not isinstance(level_map, dict) or not level_map:
        return accessories

    for accessory in accessories:
        accessory_id = accessory.get("id")
        if accessory_id is None:
            continue
        apply_bsinfo_overlay_to_accessory(accessory, level_map.get(str(accessory_id)))
    return accessories


def apply_bsinfo_gear_overlays(
    gears: list[dict[str, Any]],
    gear_map: dict[str, dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Apply BSInfo gear name/description overlays. Rarity stays from DB."""
    if not gears:
        return gears

    for gear in gears:
        gear_id = gear.get("id")
        if gear_id is None:
            continue
        if isinstance(gear_map, dict):
            apply_bsinfo_overlay_to_accessory(
                gear,
                gear_map.get(str(gear_id)),
                clear_missing_buffie=False,
            )
        _apply_known_gear_description_override_to_accessory(gear)
    return gears


BSINFO_MAPS_CACHE_KEY = "bsinfo:maps:catalog"
BSINFO_GAMEMODE_CACHE_KEY = "bsinfo:gamemodes:catalog"
BSINFO_MAPS_UPDATE_LOCK_KEY = "bsinfo:maps:update_lock"
BSINFO_GAMEMODE_UPDATE_LOCK_KEY = "bsinfo:gamemodes:update_lock"
BSINFO_MAPS_UPDATE_LOCK_TTL = 6 * 60 * 60
BSINFO_GAMEMODE_UPDATE_LOCK_TTL = 6 * 60 * 60


def _extract_named_list(payload: dict | list | None, key: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        items = payload.get(key)
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        single = payload.get(key.rstrip("s"))
        if isinstance(single, dict):
            return [single]
    return []


def _merge_lang_items(
    items_en: list[dict[str, Any]],
    items_ja: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    by_id_en = {_to_int(item.get("id")): item for item in items_en}
    by_id_ja = {_to_int(item.get("id")): item for item in items_ja}
    merged: dict[int, dict[str, Any]] = {}
    for item_id in [key for key in (by_id_en.keys() | by_id_ja.keys()) if key is not None]:
        merged[item_id] = {
            "en": by_id_en.get(item_id, {}),
            "ja": by_id_ja.get(item_id, {}),
        }
    return merged


async def _get_locked_catalog(
    *,
    cache_key: str,
    lock_key: str,
    lock_ttl: int,
    fetcher,
    force: bool,
) -> list[dict[str, Any]] | None:
    if not force:
        cached = await get_cache(cache_key)
        if isinstance(cached, list) and cached:
            lock = await get_cache(lock_key)
            if lock:
                return cached
    fetched = await fetcher()
    if fetched is not None:
        await set_cache(key=cache_key, value=fetched, ttl=None)
        await set_cache(key=lock_key, value=True, ttl=lock_ttl)
        return fetched
    cached = await get_cache(cache_key)
    if isinstance(cached, list):
        return cached
    return None


async def get_maps_catalog(*, force: bool = False) -> list[dict[str, Any]] | None:
    """Return merged map dicts keyed fields: id, name_en, name_ja, codename, theme, mode_id, disabled."""
    async def fetcher() -> list[dict[str, Any]] | None:
        response_en, response_ja = await asyncio.gather(
            _api_client.get("maps", params={"lang": "en"}),
            _api_client.get("maps", params={"lang": "ja"}),
        )
        items_en = _extract_named_list(response_en if isinstance(response_en, dict) else None, "maps")
        items_ja = _extract_named_list(response_ja if isinstance(response_ja, dict) else None, "maps")
        if not items_en and not items_ja:
            return None
        merged_items = _merge_lang_items(items_en, items_ja)
        result: list[dict[str, Any]] = []
        for map_id, langs in merged_items.items():
            item_en = langs["en"]
            item_ja = langs["ja"]
            result.append({
                "id": map_id,
                "name_en": _as_optional_str(item_en.get("name")),
                "name_ja": _as_optional_str(item_ja.get("name")),
                "codename": _as_optional_str(item_en.get("codename") or item_ja.get("codename")),
                "theme": _coalesce(_to_int(item_en.get("theme")), _to_int(item_ja.get("theme"))),
                "mode_id": _coalesce(_to_int(item_en.get("gameMode")), _to_int(item_ja.get("gameMode"))),
                "disabled": bool(_coalesce(item_en.get("disabled"), item_ja.get("disabled"), False)),
            })
        result.sort(key=lambda item: item["id"])
        return result

    return await _get_locked_catalog(
        cache_key=BSINFO_MAPS_CACHE_KEY,
        lock_key=BSINFO_MAPS_UPDATE_LOCK_KEY,
        lock_ttl=BSINFO_MAPS_UPDATE_LOCK_TTL,
        fetcher=fetcher,
        force=force,
    )


async def get_gamemode_catalog(*, force: bool = False) -> list[dict[str, Any]] | None:
    """Return merged gamemode dicts with ja/en text fields."""
    async def fetcher() -> list[dict[str, Any]] | None:
        response_en, response_ja = await asyncio.gather(
            _api_client.get("gamemode", params={"lang": "en"}),
            _api_client.get("gamemode", params={"lang": "ja"}),
        )
        items_en = _extract_named_list(response_en if isinstance(response_en, dict) else None, "gamemode")
        items_ja = _extract_named_list(response_ja if isinstance(response_ja, dict) else None, "gamemode")
        if not items_en and not items_ja:
            return None
        merged_items = _merge_lang_items(items_en, items_ja)
        result: list[dict[str, Any]] = []
        for mode_id, langs in merged_items.items():
            item_en = langs["en"]
            item_ja = langs["ja"]
            result.append({
                "id": mode_id,
                "name_en": _as_optional_str(item_en.get("name")),
                "name_ja": _as_optional_str(item_ja.get("name")),
                "desc_en": _as_optional_str(item_en.get("desc")),
                "desc_ja": _as_optional_str(item_ja.get("desc")),
                "desc2_en": _as_optional_str(item_en.get("desc2")),
                "desc2_ja": _as_optional_str(item_ja.get("desc2")),
                "overtime": _coalesce(item_en.get("overtime"), item_ja.get("overtime")),
                "overtime_text_en": _as_optional_str(item_en.get("overtimeText")),
                "overtime_text_ja": _as_optional_str(item_ja.get("overtimeText")),
                "format_en": _as_optional_str(item_en.get("format")),
                "format_ja": _as_optional_str(item_ja.get("format")),
                "color": _as_optional_str(item_en.get("Color") or item_ja.get("Color")),
                "bg_color": _as_optional_str(item_en.get("BgColor") or item_ja.get("BgColor")),
                "battle_time": _coalesce(_to_int(item_en.get("battleTime")), _to_int(item_ja.get("battleTime"))),
                "respawn_time": _coalesce(_to_int(item_en.get("respawnTime")), _to_int(item_ja.get("respawnTime"))),
                "disabled": bool(_coalesce(item_en.get("disabled"), item_ja.get("disabled"), True)),
                "is_boss_fight": bool(_coalesce(item_en.get("isBossFight"), item_ja.get("isBossFight"), False)),
                "is_special_event": bool(_coalesce(item_en.get("isSpecialEvent"), item_ja.get("isSpecialEvent"), False)),
                "is_not_rewarding_trophies": bool(_coalesce(item_en.get("isNotRewardingTrophies"), item_ja.get("isNotRewardingTrophies"), False)),
                "is_trophy_mode": bool(_coalesce(item_en.get("isTrophyMode"), item_ja.get("isTrophyMode"), False)),
                "rounds": _coalesce(_to_int(item_en.get("Rounds")), _to_int(item_ja.get("Rounds"))),
                "team_size": _coalesce(_to_int(item_en.get("TeamSize")), _to_int(item_ja.get("TeamSize"))),
                "team_count": _coalesce(_to_int(item_en.get("TeamCount")), _to_int(item_ja.get("TeamCount"))),
            })
        result.sort(key=lambda item: item["id"])
        return result

    return await _get_locked_catalog(
        cache_key=BSINFO_GAMEMODE_CACHE_KEY,
        lock_key=BSINFO_GAMEMODE_UPDATE_LOCK_KEY,
        lock_ttl=BSINFO_GAMEMODE_UPDATE_LOCK_TTL,
        fetcher=fetcher,
        force=force,
    )


def _apply_gear_description_overrides(gear: dict[str, Any]) -> dict[str, Any]:
    """Return a gear dict with known incorrect BSInfo descriptions corrected."""
    gear_id = _to_int(gear.get("id"))
    if gear_id is None:
        return gear
    override = _GEAR_DESCRIPTION_OVERRIDES.get(gear_id)
    if not override:
        return gear
    corrected = dict(gear)
    corrected.update(override)
    return corrected


def _apply_known_gear_description_override_to_accessory(gear: dict[str, Any]) -> None:
    """Overwrite accessory description fields for known incorrect gear texts."""
    gear_id = _to_int(gear.get("id"))
    if gear_id is None:
        return
    override = _GEAR_DESCRIPTION_OVERRIDES.get(gear_id)
    if not override:
        return
    description = gear.get("description")
    if not isinstance(description, dict):
        description = {}
        gear["description"] = description
    if override.get("description_ja"):
        description["ja"] = override["description_ja"]
    if override.get("description_en"):
        description["en"] = override["description_en"]
