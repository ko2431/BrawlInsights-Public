import httpx
from typing import Any

from app.core.cache import get_cache, set_cache
from app.core.logger import logger


BSINFO_BASE_URL = "https://api.bsinfox.com/"
BSINFO_BRAWLER_UPDATE_LOCK_TTL = 6 * 60 * 60
BSINFO_SKINS_UPDATE_LOCK_TTL = 60 * 60


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
