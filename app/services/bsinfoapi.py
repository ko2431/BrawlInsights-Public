import asyncio
import httpx
from typing import Any

from app.core.cache import get_cache, set_cache
from app.core.logger import logger


BSINFO_BASE_URL = "https://api.bsinfox.com/"
BSINFO_BRAWLER_UPDATE_LOCK_TTL = 6 * 60 * 60
BSINFO_ACCESSORY_UPDATE_LOCK_TTL = 6 * 60 * 60
BSINFO_SKINS_UPDATE_LOCK_TTL = 60 * 60
BSINFO_POWER_LEVELS = tuple(range(1, 12))

# BSInfo API側の不正確な説明文を補正する
_GEAR_DESCRIPTION_OVERRIDES: dict[int, dict[str, str]] = {
    62000017: {
        "description_ja": "ガジェットのクールダウンが15%短縮される。",
        "description_en": "Reduces Gadget cooldown by 15%",
    },
}


class ApiClient:
    """Shared async client for BSInfo API."""

    def __init__(self, base_url: str):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)
        logger.info(f"BSInfo ApiClient initialized for {base_url}")

    async def get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict | list | None:
        """Send GET request and return JSON response."""
        try:
            response = await self._client.get(endpoint, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            error_text = e.response.text.replace("\n", " ").replace("\r", "")
            summary = (error_text[:200] + "...") if len(error_text) > 200 else error_text
            logger.warning(
                f"BSInfo HTTP error: {e.response.status_code} - {e.request.url} response summary: {summary}"
            )
            return None
        except httpx.RequestError as e:
            logger.warning(f"BSInfo request error: {e.request.url} - {e}")
            return None

    async def aclose(self):
        """Close internal httpx client."""
        await self._client.aclose()
        logger.info("BSInfo ApiClient closed")


_api_client = ApiClient(base_url=BSINFO_BASE_URL)


class BSInfoBrawler:
    def __init__(self, brawler_id: int | None = None):
        self.id: int | None = brawler_id
        self.class_ja: str | None = None
        self.class_en: str | None = None
        self.class_id: int | None = None
        self.title_mastery_ja: str | None = None
        self.title_mastery_en: str | None = None
        self.title_prestige_ja: str | None = None
        self.title_prestige_en: str | None = None
        self.hp: int | None = None
        self.speed: int | None = None
        self.damage: int | None = None
        self.range: float | None = None
        self.reload_speed: float | None = None
        self.max_ammo: int | None = None
        self.spread: int | float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "class_ja": self.class_ja,
            "class_en": self.class_en,
            "class_id": self.class_id,
            "title_mastery_ja": self.title_mastery_ja,
            "title_mastery_en": self.title_mastery_en,
            "title_prestige_ja": self.title_prestige_ja,
            "title_prestige_en": self.title_prestige_en,
            "hp": self.hp,
            "speed": self.speed,
            "damage": self.damage,
            "range": self.range,
            "reload_speed": self.reload_speed,
            "max_ammo": self.max_ammo,
            "spread": self.spread,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]):
        instance = cls(brawler_id=_to_int(data.get("id")))
        instance.class_ja = _as_optional_str(data.get("class_ja"))
        instance.class_en = _as_optional_str(data.get("class_en"))
        instance.class_id = _to_int(data.get("class_id"))
        instance.title_mastery_ja = _as_optional_str(data.get("title_mastery_ja"))
        instance.title_mastery_en = _as_optional_str(data.get("title_mastery_en"))
        instance.title_prestige_ja = _as_optional_str(data.get("title_prestige_ja"))
        instance.title_prestige_en = _as_optional_str(data.get("title_prestige_en"))
        instance.hp = _to_int(data.get("hp"))
        instance.speed = _to_int(data.get("speed"))
        instance.damage = _to_int(data.get("damage"))
        instance.range = _to_float(data.get("range"))
        instance.reload_speed = _to_float(data.get("reload_speed"))
        instance.max_ammo = _to_int(data.get("max_ammo"))
        instance.spread = _to_number(data.get("spread"))
        return instance

    @classmethod
    def from_responses(
        cls,
        brawler_id: int,
        response_en: dict[str, Any] | None,
        response_ja: dict[str, Any] | None,
    ):
        instance = cls(brawler_id=brawler_id)

        brawler_en = _extract_brawler_payload(response_en)
        brawler_ja = _extract_brawler_payload(response_ja)
        status_en = _extract_status_payload(brawler_en)
        status_ja = _extract_status_payload(brawler_ja)

        instance.class_ja = _as_optional_str(brawler_ja.get("class"))
        instance.class_en = _as_optional_str(brawler_en.get("class"))
        instance.class_id = _coalesce(_to_int(brawler_ja.get("classId")), _to_int(brawler_en.get("classId")))

        instance.title_mastery_ja = _as_optional_str(brawler_ja.get("titleMastery"))
        instance.title_mastery_en = _as_optional_str(brawler_en.get("titleMastery"))
        instance.title_prestige_ja = _as_optional_str(brawler_ja.get("titlePrestige"))
        instance.title_prestige_en = _as_optional_str(brawler_en.get("titlePrestige"))

        instance.hp = _coalesce(_to_int(status_ja.get("HP")), _to_int(status_en.get("HP")))
        instance.speed = _coalesce(_to_int(status_ja.get("SPEED")), _to_int(status_en.get("SPEED")))
        instance.damage = _coalesce(_to_int(status_ja.get("DAMAGE")), _to_int(status_en.get("DAMAGE")))
        instance.range = _coalesce(_to_float(status_ja.get("RANGE")), _to_float(status_en.get("RANGE")))
        instance.reload_speed = _coalesce(
            _to_float(status_ja.get("RELOADSPEED")),
            _to_float(status_en.get("RELOADSPEED")),
        )
        instance.max_ammo = _coalesce(_to_int(status_ja.get("MAXAMMO")), _to_int(status_en.get("MAXAMMO")))
        instance.spread = _coalesce(_to_number(status_ja.get("SPREAD")), _to_number(status_en.get("SPREAD")))

        return instance

    def has_any_data(self) -> bool:
        return any(
            value is not None
            for value in (
                self.class_ja,
                self.class_en,
                self.class_id,
                self.title_mastery_ja,
                self.title_mastery_en,
                self.title_prestige_ja,
                self.title_prestige_en,
                self.hp,
                self.speed,
                self.damage,
                self.range,
                self.reload_speed,
                self.max_ammo,
                self.spread,
            )
        )


async def get_brawler_status(brawler_id: int, level: int = 1) -> BSInfoBrawler:
    """Fetch brawler status from BSInfo API using en/ja and return merged object.

    Cache policy:
    - data cache: persistent (ttl=None)
    - update lock: 6 hours
    """
    brawler_id = _to_int(brawler_id)
    if brawler_id is None or brawler_id <= 0:
        return BSInfoBrawler()

    level = _to_int(level)
    if level is None or level <= 0:
        level = 1

    cache_key_update_lock = f"bsinfo_update_lock:brawler:{brawler_id}:level:{level}"
    cached_data_update_lock = await get_cache(cache_key_update_lock)
    if not cached_data_update_lock:
        await set_cache(key=cache_key_update_lock, value=True, ttl=BSINFO_BRAWLER_UPDATE_LOCK_TTL)

    cache_key = f"bsinfo:brawler:{brawler_id}:level:{level}"
    cached_data = await get_cache(cache_key)
    if cached_data_update_lock and cached_data:
        return BSInfoBrawler.from_dict(cached_data)

    response_en = await _api_client.get(
        f"brawlers/{brawler_id}",
        params={"lang": "en", "level": level},
    )
    response_ja = await _api_client.get(
        f"brawlers/{brawler_id}",
        params={"lang": "ja", "level": level},
    )

    merged = BSInfoBrawler.from_responses(
        brawler_id=brawler_id,
        response_en=response_en if isinstance(response_en, dict) else None,
        response_ja=response_ja if isinstance(response_ja, dict) else None,
    )

    if merged.has_any_data():
        await set_cache(key=cache_key, value=merged.to_dict(), ttl=None)
        await set_cache(key=cache_key_update_lock, value=True, ttl=BSINFO_BRAWLER_UPDATE_LOCK_TTL)
        return merged

    if cached_data:
        return BSInfoBrawler.from_dict(cached_data)

    return merged


async def get_player_owned_skins(tag: str) -> dict[int, list[int]]:
    """Fetch owned skin IDs grouped by brawler ID from BSInfo API.

    Cache policy:
    - data cache: persistent (ttl=None)
    - update lock: 1 hour
    """
    normalized_tag = _normalize_tag(tag)
    if not normalized_tag:
        return {}

    cache_key_update_lock = f"bsinfo_update_lock:skins:{normalized_tag}"
    cached_data_update_lock = await get_cache(cache_key_update_lock)
    if not cached_data_update_lock:
        await set_cache(key=cache_key_update_lock, value=True, ttl=BSINFO_SKINS_UPDATE_LOCK_TTL)

    cache_key = f"bsinfo:skins:{normalized_tag}"
    cached_data = await get_cache(cache_key)
    if cached_data_update_lock and cached_data:
        return _normalize_owned_skins_dict(cached_data)

    response = await _api_client.get(f"skins/{normalized_tag[1:]}")
    parsed, is_valid_response = _parse_owned_skins_response(response)

    if is_valid_response:
        await set_cache(key=cache_key, value=parsed, ttl=None)
        await set_cache(key=cache_key_update_lock, value=True, ttl=BSINFO_SKINS_UPDATE_LOCK_TTL)
        return parsed

    if cached_data:
        return _normalize_owned_skins_dict(cached_data)

    return {}


async def get_gadgets_for_level(brawler_id: int, level: int = 11) -> dict[str, dict[str, Any]]:
    """Fetch gadgets for a brawler/level. Keys are accessory ID strings."""
    return await _get_brawler_accessory_map(
        kind="gadgets",
        endpoint="gadgets",
        list_key="gadgets",
        brawler_id=brawler_id,
        level=level,
        include_cooldown=True,
        include_use_rate=True,
    )


async def get_starpowers_for_level(brawler_id: int, level: int = 11) -> dict[str, dict[str, Any]]:
    """Fetch star powers for a brawler/level. Keys are accessory ID strings."""
    return await _get_brawler_accessory_map(
        kind="starpower",
        endpoint="starpower",
        list_key="starPowers",
        brawler_id=brawler_id,
        level=level,
        include_cooldown=False,
        include_use_rate=True,
    )


async def get_hypercharges_for_level(brawler_id: int, level: int = 11) -> dict[str, dict[str, Any]]:
    """Fetch hypercharges for a brawler/level. Keys are accessory ID strings."""
    return await _get_brawler_accessory_map(
        kind="hypercharge",
        endpoint="hypercharge",
        list_key="hyperCharges",
        brawler_id=brawler_id,
        level=level,
        include_cooldown=False,
        include_use_rate=False,
    )


async def get_gear(gear_id: int) -> dict[str, Any] | None:
    """Fetch a single gear by gear ID (name/description only)."""
    gear_id = _to_int(gear_id)
    if gear_id is None or gear_id <= 0:
        return None

    cache_key_update_lock = f"bsinfo_update_lock:gear:{gear_id}"
    cached_data_update_lock = await get_cache(cache_key_update_lock)
    if not cached_data_update_lock:
        await set_cache(key=cache_key_update_lock, value=True, ttl=BSINFO_ACCESSORY_UPDATE_LOCK_TTL)

    cache_key = f"bsinfo:gear:{gear_id}"
    cached_data = await get_cache(cache_key)
    if cached_data_update_lock and isinstance(cached_data, dict) and cached_data:
        return _apply_gear_description_overrides(cached_data)

    response_en, response_ja = await asyncio.gather(
        _api_client.get(f"gears/{gear_id}", params={"lang": "en"}),
        _api_client.get(f"gears/{gear_id}", params={"lang": "ja"}),
    )

    merged = _merge_gear_responses(
        response_en=response_en if isinstance(response_en, dict) else None,
        response_ja=response_ja if isinstance(response_ja, dict) else None,
    )
    if merged:
        merged = _apply_gear_description_overrides(merged)
        await set_cache(key=cache_key, value=merged, ttl=None)
        await set_cache(key=cache_key_update_lock, value=True, ttl=BSINFO_ACCESSORY_UPDATE_LOCK_TTL)
        return merged

    if isinstance(cached_data, dict) and cached_data:
        return _apply_gear_description_overrides(cached_data)
    return None


async def get_gadgets_all_levels(brawler_id: int) -> dict[str, dict[str, dict[str, Any]]]:
    """Prefetch gadgets for power levels 1-11. Outer keys are level strings."""
    return await _get_accessory_all_levels_bundled(
        kind="gadgets",
        endpoint="gadgets",
        list_key="gadgets",
        brawler_id=brawler_id,
        include_cooldown=True,
        include_use_rate=True,
    )


async def get_starpowers_all_levels(brawler_id: int) -> dict[str, dict[str, dict[str, Any]]]:
    """Prefetch star powers for power levels 1-11. Outer keys are level strings."""
    return await _get_accessory_all_levels_bundled(
        kind="starpower",
        endpoint="starpower",
        list_key="starPowers",
        brawler_id=brawler_id,
        include_cooldown=False,
        include_use_rate=True,
    )


async def get_hypercharges_all_levels(brawler_id: int) -> dict[str, dict[str, dict[str, Any]]]:
    """Prefetch hypercharges for power levels 1-11. Outer keys are level strings."""
    return await _get_accessory_all_levels_bundled(
        kind="hypercharge",
        endpoint="hypercharge",
        list_key="hyperCharges",
        brawler_id=brawler_id,
        include_cooldown=False,
        include_use_rate=False,
    )


async def get_gears_by_ids(gear_ids: list[int]) -> dict[str, dict[str, Any]]:
    """Fetch multiple gears in parallel. Keys are gear ID strings."""
    unique_ids: list[int] = []
    seen: set[int] = set()
    for raw_id in gear_ids:
        gear_id = _to_int(raw_id)
        if gear_id is None or gear_id <= 0 or gear_id in seen:
            continue
        seen.add(gear_id)
        unique_ids.append(gear_id)

    if not unique_ids:
        return {}

    results = await asyncio.gather(*[get_gear(gear_id) for gear_id in unique_ids])
    merged: dict[str, dict[str, Any]] = {}
    for gear_id, item in zip(unique_ids, results):
        if isinstance(item, dict) and item:
            merged[str(gear_id)] = item
    return merged


async def get_brawler_guide_accessories(
    brawler_id: int,
    gear_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Prefetch all accessory overlays used by the brawler guide page."""
    gadgets_task = get_gadgets_all_levels(brawler_id)
    starpowers_task = get_starpowers_all_levels(brawler_id)
    hypercharges_task = get_hypercharges_all_levels(brawler_id)
    gears_task = get_gears_by_ids(gear_ids or [])

    gadgets, starpowers, hypercharges, gears = await asyncio.gather(
        gadgets_task,
        starpowers_task,
        hypercharges_task,
        gears_task,
    )
    return {
        "gadgets": gadgets,
        "star_powers": starpowers,
        "hypercharges": hypercharges,
        "gears": gears,
    }


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

    if "use_rate" in overlay:
        accessory["use_rate"] = overlay.get("use_rate")

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
