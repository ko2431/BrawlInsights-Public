"""特別報酬リンクの検証・在庫・配布。"""
from __future__ import annotations

import asyncio
import datetime
import ipaddress
import re
from typing import Any
from urllib.parse import urlparse, urlunparse

import asyncpg

from app.core.cache import delete_cache, get_cache, get_redis, set_cache
from app.core.logger import logger
from app.db.db import get_db_connection_for_bg_task, load_serialized_within_request_timeout
# [この部分は公開用リポジトリでは非公開にされています]
from app.services.user_service import User

SPECIAL_REWARD_COMPENSATION_TOKENS = 200
CLAIM_WINDOW = datetime.timedelta(hours=72)
DEFAULT_ICON_PATH = "mode_icons/presentPlunder.png"
VOUCHER_HOST = "link.brawlstars.com"
VOUCHER_PATH_PREFIX = "/voucher/"
HOME_BANNERS_CACHE_KEY = "special_reward:home_banners"
HOME_BANNERS_CACHE_TTL = 30
# キャッシュ切れの瞬間にアクセスが集中しても、DBへ問い合わせるのはプロセスごとに1件だけにする
_home_banners_lock = asyncio.Lock()
LINK_TYPES = frozenset({"single_unlimited", "single_limited", "link_set"})
FINITE_TYPES = frozenset({"single_limited", "link_set"})
BANNER_COLORS = frozenset({"red", "orange", "yellow", "green", "blue", "purple"})
DEFAULT_BANNER_COLOR = "blue"
SOURCE_HOME = "home_banner"
# [この部分は公開用リポジトリでは非公開にされています]
# [この部分は公開用リポジトリでは非公開にされています]

_JST = datetime.timezone(datetime.timedelta(hours=9))
_ICON_PATH_RE = re.compile(r"^[\w./-]+$")

# [この部分は公開用リポジトリでは非公開にされています]


class SpecialRewardLinkError(ValueError):
    """ユーザー向けに返せる特別報酬リンクのエラー。"""

    def __init__(self, message_ja: str, message_en: str, *, code: str = "error"):
        super().__init__(message_ja)
        self.message_ja = message_ja
        self.message_en = message_en
        self.code = code
        self.hide_banner = code in {"ended", "stock_empty"}

    def message(self, lang: str) -> str:
        return self.message_ja if lang == "ja" else self.message_en


def _record(row: asyncpg.Record | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def _aware(dt: datetime.datetime | None) -> datetime.datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def to_jst_input(dt: datetime.datetime | None) -> str:
    aware = _aware(dt)
    if aware is None:
        return ""
    return aware.astimezone(_JST).strftime("%Y-%m-%dT%H:%M")


def parse_jst_datetime(value: str | None) -> datetime.datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    naive = datetime.datetime.fromisoformat(text)
    jst_dt = naive.replace(tzinfo=_JST) if naive.tzinfo is None else naive.astimezone(_JST)
    return jst_dt.astimezone(datetime.timezone.utc)


def is_unlimited(link_type: str) -> bool:
    return link_type == "single_unlimited"


VOUCHER_URL_VISIBLE_CHARS = 5


def mask_voucher_url(url: str | None, *, visible: int = VOUCHER_URL_VISIBLE_CHARS) -> str:
    """voucher/ 以降は先頭数文字だけ残して省略する。"""
    text = str(url or "").strip()
    if not text:
        return ""
    marker = "/voucher/"
    idx = text.lower().find(marker)
    if idx < 0:
        return text[:visible] + "..." if len(text) > visible else text
    keep = idx + len(marker) + visible
    if len(text) <= keep:
        return text
    return text[:keep] + "..."


def redact_link_urls(link: dict[str, Any]) -> None:
    """有限リンクの実URLを表示用に省略する（破壊的）。"""
    if link.get("url"):
        link["url"] = mask_voucher_url(link["url"])
    items = link.get("items")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("url"):
                item["url"] = mask_voucher_url(item["url"])
    link["url_redacted"] = True


def link_display_url(link: dict[str, Any]) -> str:
    if link.get("link_type") == "link_set":
        items = link.get("items") or []
        if not items:
            return ""
        first = str(items[0].get("url") or "")
        extra = len(items) - 1
        return f"{first} など{len(items)}件" if extra > 0 else first
    return str(link.get("url") or "")


# [この部分は公開用リポジトリでは非公開にされています]


def normalize_voucher_url(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise SpecialRewardLinkError("リンクを入力してください。", "Please enter a link.")
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host != VOUCHER_HOST:
        raise SpecialRewardLinkError(
            "link.brawlstars.com/voucher/ で始まるリンクのみ登録できます。",
            "Only link.brawlstars.com/voucher/ links can be registered.",
        )
    if parsed.username or parsed.password:
        raise SpecialRewardLinkError(
            "リンクの形式が不正です。",
            "The link format is invalid.",
        )
    path = parsed.path or ""
    if not path.startswith(VOUCHER_PATH_PREFIX) or len(path) <= len(VOUCHER_PATH_PREFIX):
        raise SpecialRewardLinkError(
            "link.brawlstars.com/voucher/ で始まるリンクのみ登録できます。",
            "Only link.brawlstars.com/voucher/ links can be registered.",
        )
    return urlunparse(("https", VOUCHER_HOST, path, "", parsed.query, ""))


def normalize_icon_path(raw: str | None) -> str | None:
    text = (raw or "").strip().lstrip("/")
    if not text:
        return None
    if ".." in text or text.startswith("/") or ":" in text or not _ICON_PATH_RE.fullmatch(text):
        raise SpecialRewardLinkError("アイコンパスが不正です。", "The icon path is invalid.")
    return text


def display_icon_path(raw: str | None) -> str:
    return raw or DEFAULT_ICON_PATH


def display_banner_color(raw: str | None) -> str:
    return raw if raw in BANNER_COLORS else DEFAULT_BANNER_COLOR


def normalize_banner_color(raw: Any) -> str:
    color = str(raw or "").strip()
    if color not in BANNER_COLORS:
        raise SpecialRewardLinkError(
            "バナーの色は必須です。",
            "Banner color is required.",
        )
    return color


def _parse_urls_text(raw: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for line in (raw or "").splitlines():
        text = line.strip()
        if not text:
            continue
        url = normalize_voucher_url(text)
        if url in seen:
            raise SpecialRewardLinkError(
                "同じリンクが重複しています。",
                "The same link is duplicated.",
            )
        seen.add(url)
        urls.append(url)
    if not urls:
        raise SpecialRewardLinkError("リンクを1件以上入力してください。", "Enter at least one link.")
    return urls


async def invalidate_home_banner_cache() -> None:
    await delete_cache(HOME_BANNERS_CACHE_KEY)


# [この部分は公開用リポジトリでは非公開にされています]


def _as_ip(value: str | None):
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value.split("%")[0]))
    except ValueError:
        return None


async def _locked_count(
    db: asyncpg.Connection, link_id: int, *, exclude_campaign_id: int | None = None
) -> int:
    # [この部分は公開用リポジトリでは非公開にされています]
    return 0


async def _claim_count(db: asyncpg.Connection, link_id: int) -> int:
    return int(await db.fetchval(
        "SELECT COUNT(*) FROM special_reward_link_claims WHERE link_id = $1", link_id
    ) or 0)


async def get_inventory(
    db: asyncpg.Connection,
    link: dict[str, Any] | asyncpg.Record,
    *,
    exclude_campaign_id: int | None = None,
) -> dict[str, Any]:
    link_id = int(link["id"])
    link_type = link["link_type"]
    claim_count = await _claim_count(db, link_id)
    home_claim_count = int(await db.fetchval(
        "SELECT COUNT(*) FROM special_reward_link_claims WHERE link_id = $1 AND source = $2",
        link_id, SOURCE_HOME,
    ) or 0)
    # [この部分は公開用リポジトリでは非公開にされています]
    if is_unlimited(link_type):
        return {
            "unlimited": True,
            "physical_remaining": None,
            "locked": 0,
            "available": None,
            "claim_count": claim_count,
            "home_claim_count": home_claim_count,
            # [この部分は公開用リポジトリでは非公開にされています]
            "item_total": None,
            "item_unused": None,
        }
    locked = await _locked_count(db, link_id, exclude_campaign_id=exclude_campaign_id)
    item_total = None
    item_unused = None
    if link_type == "link_set":
        item_total = int(await db.fetchval(
            "SELECT COUNT(*) FROM special_reward_link_items WHERE link_id = $1", link_id
        ) or 0)
        item_unused = int(await db.fetchval(
            "SELECT COUNT(*) FROM special_reward_link_items WHERE link_id = $1 AND claimed_at IS NULL",
            link_id,
        ) or 0)
        physical = item_unused
    else:
        physical = max(0, int(link["max_uses"] or 0) - claim_count)
    available = max(0, physical - locked)
    return {
        "unlimited": False,
        "physical_remaining": physical,
        "locked": locked,
        "available": available,
        "claim_count": claim_count,
        "home_claim_count": home_claim_count,
        # [この部分は公開用リポジトリでは非公開にされています]
        "item_total": item_total,
        "item_unused": item_unused,
    }


async def _assert_url_available(
    db: asyncpg.Connection, url: str, *, exclude_link_id: int | None = None
) -> None:
    existing_link = await db.fetchval(
        "SELECT id FROM special_reward_links WHERE url = $1 AND ($2::int IS NULL OR id <> $2)",
        url, exclude_link_id,
    )
    existing_item = await db.fetchval(
        """SELECT i.id FROM special_reward_link_items i
           WHERE i.url = $1 AND ($2::int IS NULL OR i.link_id <> $2)""",
        url, exclude_link_id,
    )
    if existing_link or existing_item:
        raise SpecialRewardLinkError(
            "このリンクはすでに登録されています。",
            "This link is already registered.",
        )


async def _insert_items(db: asyncpg.Connection, link_id: int, urls: list[str]) -> None:
    for url in urls:
        await _assert_url_available(db, url, exclude_link_id=link_id)
        try:
            await db.execute(
                "INSERT INTO special_reward_link_items (link_id, url) VALUES ($1, $2)",
                link_id, url,
            )
        except asyncpg.UniqueViolationError as e:
            raise SpecialRewardLinkError(
                "このリンクはすでに登録されています。",
                "This link is already registered.",
            ) from e


def _validate_period(
    starts_at: datetime.datetime | None, expires_at: datetime.datetime | None
) -> None:
    if starts_at and expires_at and expires_at <= starts_at:
        raise SpecialRewardLinkError(
            "有効期限は利用開始日時より後にしてください。",
            "The expiration must be after the start time.",
        )


# [この部分は公開用リポジトリでは非公開にされています]


async def claimed_home_link_ids(db: asyncpg.Connection, user_id: int) -> set[int]:
    rows = await db.fetch(
        """SELECT DISTINCT link_id FROM special_reward_link_claims
           WHERE user_id = $1 AND source = $2""",
        user_id, SOURCE_HOME,
    )
    return {int(row["link_id"]) for row in rows}


async def get_public_home_banners(
    db: asyncpg.Connection | None, *, user_id: int | None = None
) -> list[dict[str, Any]]:
    """ホームの特別報酬バナー。db が None なら、キャッシュ切れのときだけ接続を確保する。"""
    async def load() -> list[dict[str, Any]]:
        cached = await get_cache(HOME_BANNERS_CACHE_KEY)
        if cached is not None:
            return cached
        if db is not None:
            return await _load_public_home_banners(db)
        async with get_db_connection_for_bg_task() as conn:
            return await _load_public_home_banners(conn)

    cached = await get_cache(HOME_BANNERS_CACHE_KEY)
    if cached is None:
        cached = await load_serialized_within_request_timeout(_home_banners_lock, load)
    banners: list[dict[str, Any]] = list(cached)
    for banner in banners:
        banner["color"] = display_banner_color(banner.get("color"))
    if user_id:
        if db is None:
            raise ValueError("user_id を指定する場合は db が必要です")
        claimed = await claimed_home_link_ids(db, user_id)
        banners = [
            banner for banner in banners
            if not (banner.get("requires_login") and banner.get("link_id") in claimed)
        ]
    return banners


async def _load_public_home_banners(db: asyncpg.Connection) -> list[dict[str, Any]]:
    rows = await db.fetch(
        """SELECT b.id, b.link_id, b.title_ja, b.title_en, b.icon_path, b.color,
                  l.link_type, u.name AS organizer_name
           FROM special_reward_home_banners b
           JOIN special_reward_links l ON l.id = b.link_id
           LEFT JOIN users u ON u.id = l.created_by_user_id
           WHERE NOT l.is_invalid
             AND b.ended_reason IS NULL
             AND b.starts_at <= now()
             AND b.ends_at >= now()
           ORDER BY b.starts_at DESC, b.id DESC"""
    )
    banners = []
    for row in rows:
        if not is_unlimited(row["link_type"]):
            link = await db.fetchrow("SELECT * FROM special_reward_links WHERE id = $1", row["link_id"])
            inventory = await get_inventory(db, link) if link else None
            if not inventory or (inventory["available"] or 0) <= 0:
                await _mark_banners_stock_empty(db, row["link_id"])
                continue
        banners.append({
            "id": row["id"],
            "link_id": row["link_id"],
            "title_ja": row["title_ja"],
            "title_en": row["title_en"],
            "icon_path": display_icon_path(row["icon_path"]),
            "color": display_banner_color(row["color"]),
            "organizer_name": row["organizer_name"] or "",
            "requires_login": not is_unlimited(row["link_type"]),
        })
    await set_cache(HOME_BANNERS_CACHE_KEY, banners, ttl=HOME_BANNERS_CACHE_TTL)
    return banners


async def _existing_claim(
    db: asyncpg.Connection,
    *,
    source: str,
    link_id: int | None = None,
    user_id: int | None = None,
    play_id: int | None = None,
) -> dict[str, Any] | None:
    row = None
    # [この部分は公開用リポジトリでは非公開にされています]
    if row is None and source == SOURCE_HOME and user_id is not None and link_id is not None:
        row = await db.fetchrow(
            """SELECT c.*, i.url AS item_url, l.url AS link_url, l.link_type
               FROM special_reward_link_claims c
               JOIN special_reward_links l ON l.id = c.link_id
               LEFT JOIN special_reward_link_items i ON i.id = c.item_id
               WHERE c.link_id = $1 AND c.user_id = $2 AND c.source = $3""",
            link_id, user_id, SOURCE_HOME,
        )
    if not row:
        return None
    data = dict(row)
    data["url"] = data.get("item_url") or data.get("link_url")
    return data


# [この部分は公開用リポジトリでは非公開にされています]
