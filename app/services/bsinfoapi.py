import asyncio
import re
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
    return normalized if normalized.startswith("# [この部分は公開用リポジトリでは非公開にされています]

