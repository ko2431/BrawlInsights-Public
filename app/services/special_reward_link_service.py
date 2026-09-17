"""特別報酬リンクの検証・在庫・配布。"""
from __future__ import annotations

import datetime
import ipaddress
import re
from typing import Any
from urllib.parse import urlparse, urlunparse

import asyncpg

from app.core.cache import delete_cache, get_cache, get_redis, set_cache
from app.core.logger import logger
from app.services.admin_notification_service import (
    clip_admin_notification_text,
    emit_admin_notification,
    format_admin_user_label,
)
from app.services.user_service import User

SPECIAL_REWARD_COMPENSATION_TOKENS = 200
CLAIM_WINDOW = datetime.timedelta(hours=72)
DEFAULT_ICON_PATH = "mode_icons/presentPlunder.png"
VOUCHER_HOST = "link.brawlstars.com"
VOUCHER_PATH_PREFIX = "/voucher/"
HOME_BANNERS_CACHE_KEY = "special_reward:home_banners"
HOME_BANNERS_CACHE_TTL = 30
LINK_TYPES = frozenset({"single_unlimited", "single_limited", "link_set"})
FINITE_TYPES = frozenset({"single_limited", "link_set"})
BANNER_COLORS = frozenset({"red", "orange", "yellow", "green", "blue", "purple"})
DEFAULT_BANNER_COLOR = "blue"
SOURCE_HOME = "home_banner"
SOURCE_MINIGAME = "minigame"
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


def extract_special_reward_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        items = payload.get("items")
    else:
        items = payload
    if not isinstance(items, list):
        return []
    found: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict) and item.get("type") == "special_reward_link":
            found.append(item)
    return found


def special_reward_link_ids_from_prizes(prizes: Any) -> dict[int, dict[str, Any]]:
    """企画 prizes から link_id -> {allocation, quantity, names} を集計する。"""
    result: dict[int, dict[str, Any]] = {}
    tiers = prizes.get("tiers") if isinstance(prizes, dict) else None
    if not isinstance(tiers, list):
        return result
    for tier in tiers:
        if not isinstance(tier, dict):
            continue
        allocation = tier.get("allocation")
        quantity = tier.get("quantity")
        for item in extract_special_reward_items(tier):
            try:
                link_id = int(item.get("link_id"))
            except (TypeError, ValueError):
                continue
            entry = result.setdefault(link_id, {
                "allocation": allocation,
                "stock_quantity": 0,
                "used_as_weight": False,
                "name_ja": item.get("name_ja") or "",
                "name_en": item.get("name_en") or "",
            })
            if allocation == "stock" and isinstance(quantity, int):
                entry["stock_quantity"] += quantity
            if allocation == "weight":
                entry["used_as_weight"] = True
    return result


def sanitize_special_reward_prize_items(prizes: Any) -> None:
    """景品JSONから特別報酬リンクのURLなど余分な項目を落とす。"""
    tiers = prizes.get("tiers") if isinstance(prizes, dict) else None
    if not isinstance(tiers, list):
        return
    for tier in tiers:
        items = tier.get("items") if isinstance(tier, dict) else None
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            if not isinstance(item, dict) or item.get("type") != "special_reward_link":
                continue
            cleaned: dict[str, Any] = {"type": "special_reward_link"}
            try:
                cleaned["link_id"] = int(item.get("link_id"))
            except (TypeError, ValueError):
                cleaned["link_id"] = item.get("link_id")
            cleaned["name_ja"] = str(item.get("name_ja") or "").strip()
            cleaned["name_en"] = str(item.get("name_en") or "").strip()
            items[index] = cleaned


def normalize_voucher_url(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise SpecialRewardLinkError("リンクを入力してください。", "Please enter a link.")
    if ":// [この部分は公開用リポジトリでは非公開にされています]


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
    minigame_claim_count = int(await db.fetchval(
        "SELECT COUNT(*) FROM special_reward_link_claims WHERE link_id = $1 AND source = $2",
        link_id, SOURCE_MINIGAME,
    ) or 0)
    if is_unlimited(link_type):
        return {
            "unlimited": True,
            "physical_remaining": None,
            "locked": 0,
            "available": None,
            "claim_count": claim_count,
            "home_claim_count": home_claim_count,
            "minigame_claim_count": minigame_claim_count,
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
        "minigame_claim_count": minigame_claim_count,
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


async def create_link(db: asyncpg.Connection, user: User, payload: dict[str, Any]) -> dict[str, Any]:
    link_type = str(payload.get("link_type") or "").strip()
    if link_type not in LINK_TYPES:
        raise SpecialRewardLinkError("リンクタイプが不正です。", "The link type is invalid.")
    name_ja = str(payload.get("name_ja") or "").strip()
    name_en = str(payload.get("name_en") or "").strip()
    if not name_ja or not name_en:
        raise SpecialRewardLinkError(
            "報酬名（日本語・英語）は必須です。",
            "Reward names (Japanese and English) are required.",
        )
    starts_at = parse_jst_datetime(payload.get("starts_at"))
    expires_at = parse_jst_datetime(payload.get("expires_at"))
    _validate_period(starts_at, expires_at)

    url = None
    max_uses = None
    item_urls: list[str] = []
    if link_type == "link_set":
        item_urls = _parse_urls_text(str(payload.get("urls_text") or ""))
        max_uses = len(item_urls)
    else:
        url = normalize_voucher_url(str(payload.get("url") or ""))
        await _assert_url_available(db, url)
        if link_type == "single_limited":
            try:
                max_uses = int(payload.get("max_uses"))
            except (TypeError, ValueError) as e:
                raise SpecialRewardLinkError(
                    "使用可能回数は1以上の整数で入力してください。",
                    "Usable count must be an integer of 1 or more.",
                ) from e
            if max_uses < 1:
                raise SpecialRewardLinkError(
                    "使用可能回数は1以上の整数で入力してください。",
                    "Usable count must be an integer of 1 or more.",
                )

    async with db.transaction():
        try:
            row = await db.fetchrow(
                """INSERT INTO special_reward_links (
                       link_type, name_ja, name_en, url, max_uses, starts_at, expires_at, created_by_user_id
                   ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                   RETURNING *""",
                link_type, name_ja, name_en, url, max_uses, starts_at, expires_at, user.id,
            )
        except asyncpg.UniqueViolationError as e:
            raise SpecialRewardLinkError(
                "このリンクはすでに登録されています。",
                "This link is already registered.",
            ) from e
        if item_urls:
            await _insert_items(db, row["id"], item_urls)
    return dict(row)


async def update_link(db: asyncpg.Connection, link_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    async with db.transaction():
        row = await db.fetchrow(
            "SELECT * FROM special_reward_links WHERE id = $1 FOR UPDATE", link_id
        )
        if not row:
            raise SpecialRewardLinkError("リンクが見つかりません。", "The link was not found.")
        if row["is_invalid"]:
            raise SpecialRewardLinkError("無効なリンクは編集できません。", "An invalid link cannot be edited.")
        name_ja = str(payload.get("name_ja") or "").strip()
        name_en = str(payload.get("name_en") or "").strip()
        if not name_ja or not name_en:
            raise SpecialRewardLinkError(
                "報酬名（日本語・英語）は必須です。",
                "Reward names (Japanese and English) are required.",
            )
        starts_at = parse_jst_datetime(payload.get("starts_at"))
        expires_at = parse_jst_datetime(payload.get("expires_at"))
        _validate_period(starts_at, expires_at)
        max_uses = row["max_uses"]
        if row["link_type"] == "single_limited":
            try:
                max_uses = int(payload.get("max_uses"))
            except (TypeError, ValueError) as e:
                raise SpecialRewardLinkError(
                    "使用可能回数は1以上の整数で入力してください。",
                    "Usable count must be an integer of 1 or more.",
                ) from e
            inventory = await get_inventory(db, row)
            used_and_locked = (inventory["claim_count"] or 0) + (inventory["locked"] or 0)
            if max_uses < used_and_locked:
                raise SpecialRewardLinkError(
                    f"使用可能回数は消費済みとロック中の合計（{used_and_locked}）未満にはできません。",
                    f"Usable count cannot be lower than used + locked ({used_and_locked}).",
                )
        extra_urls = _parse_urls_text(str(payload.get("add_urls_text") or "")) if (
            row["link_type"] == "link_set" and str(payload.get("add_urls_text") or "").strip()
        ) else []
        if extra_urls:
            await _insert_items(db, link_id, extra_urls)
            max_uses = int(await db.fetchval(
                "SELECT COUNT(*) FROM special_reward_link_items WHERE link_id = $1", link_id
            ) or 0)
        updated = await db.fetchrow(
            """UPDATE special_reward_links
               SET name_ja = $2, name_en = $3, starts_at = $4, expires_at = $5,
                   max_uses = $6, updated_at = now()
             WHERE id = $1 RETURNING *""",
            link_id, name_ja, name_en, starts_at, expires_at, max_uses,
        )
    await invalidate_home_banner_cache()
    return dict(updated)


async def invalidate_link(db: asyncpg.Connection, link_id: int) -> dict[str, Any]:
    async with db.transaction():
        row = await db.fetchrow(
            "SELECT * FROM special_reward_links WHERE id = $1 FOR UPDATE", link_id
        )
        if not row:
            raise SpecialRewardLinkError("リンクが見つかりません。", "The link was not found.")
        if row["is_invalid"]:
            return dict(row)
        active_banner = await db.fetchval(
            """SELECT id FROM special_reward_home_banners
               WHERE link_id = $1 AND ended_reason IS NULL AND ends_at > now()""",
            link_id,
        )
        if active_banner:
            raise SpecialRewardLinkError(
                "掲載中または掲載予定のバナーがあるため無効化できません。先に掲載を終了してください。",
                "This link has an active or scheduled banner. End the listing first.",
            )
        locked = await _locked_count(db, link_id)
        if locked > 0:
            raise SpecialRewardLinkError(
                "ミニゲーム景品としてロック中のため無効化できません。",
                "This link is locked as a minigame prize and cannot be invalidated.",
            )
        updated = await db.fetchrow(
            """UPDATE special_reward_links
               SET is_invalid = TRUE, updated_at = now()
             WHERE id = $1 RETURNING *""",
            link_id,
        )
    await invalidate_home_banner_cache()
    return dict(updated)


def _banner_fits_link(
    link: asyncpg.Record | dict[str, Any],
    starts_at: datetime.datetime,
    ends_at: datetime.datetime,
) -> None:
    if ends_at <= starts_at:
        raise SpecialRewardLinkError(
            "掲載終了日時は掲載開始日時より後にしてください。",
            "The listing end time must be after the start time.",
        )
    link_start = _aware(link.get("starts_at") if isinstance(link, dict) else link["starts_at"])
    link_exp = _aware(link.get("expires_at") if isinstance(link, dict) else link["expires_at"])
    if link_start and starts_at < link_start:
        raise SpecialRewardLinkError(
            "掲載開始日時はリンクの利用開始日時より前にできません。",
            "The listing cannot start before the link becomes usable.",
        )
    if link_exp and ends_at > link_exp:
        raise SpecialRewardLinkError(
            "掲載終了日時はリンクの有効期限を超えられません。",
            "The listing cannot end after the link expires.",
        )


async def save_banner(
    db: asyncpg.Connection, user: User, link_id: int, payload: dict[str, Any]
) -> dict[str, Any] | None:
    enabled = bool(payload.get("enabled"))
    async with db.transaction():
        link = await db.fetchrow(
            "SELECT * FROM special_reward_links WHERE id = $1 FOR UPDATE", link_id
        )
        if not link or link["is_invalid"]:
            raise SpecialRewardLinkError("リンクが見つかりません。", "The link was not found.")
        existing = await db.fetchrow(
            """SELECT * FROM special_reward_home_banners
               WHERE link_id = $1 AND ended_reason IS NULL
               ORDER BY starts_at DESC, id DESC
               LIMIT 1
               FOR UPDATE""",
            link_id,
        )
        if not enabled:
            if existing:
                updated = await db.fetchrow(
                    """UPDATE special_reward_home_banners
                       SET ended_reason = 'manual', updated_at = now()
                     WHERE id = $1 RETURNING *""",
                    existing["id"],
                )
                await invalidate_home_banner_cache()
                return dict(updated)
            return None
        starts_at = parse_jst_datetime(payload.get("starts_at"))
        ends_at = parse_jst_datetime(payload.get("ends_at"))
        if not starts_at or not ends_at:
            raise SpecialRewardLinkError(
                "掲載開始日時と掲載終了日時は必須です。",
                "Listing start and end times are required.",
            )
        _banner_fits_link(link, starts_at, ends_at)
        title_ja = str(payload.get("title_ja") or "").strip()
        title_en = str(payload.get("title_en") or "").strip()
        if not title_ja or not title_en:
            raise SpecialRewardLinkError(
                "バナー文言（日本語・英語）は必須です。",
                "Banner titles (Japanese and English) are required.",
            )
        icon_path = normalize_icon_path(payload.get("icon_path"))
        color = normalize_banner_color(payload.get("color"))
        overlap = await db.fetchval(
            """SELECT id FROM special_reward_home_banners
               WHERE link_id = $1 AND ended_reason IS NULL
                 AND starts_at < $3 AND ends_at > $2
                 AND ($4::int IS NULL OR id <> $4)""",
            link_id, starts_at, ends_at, existing["id"] if existing else None,
        )
        if overlap:
            raise SpecialRewardLinkError(
                "同じリンクの掲載期間が重なっています。",
                "This link already has an overlapping listing period.",
            )
        if not is_unlimited(link["link_type"]):
            inventory = await get_inventory(db, link)
            if (inventory["available"] or 0) <= 0:
                raise SpecialRewardLinkError(
                    "在庫に空きがないため掲載できません。",
                    "There is no remaining stock to list this reward.",
                )
        if existing:
            row = await db.fetchrow(
                """UPDATE special_reward_home_banners
                   SET starts_at = $2, ends_at = $3, icon_path = $4, color = $5, title_ja = $6, title_en = $7,
                       ended_reason = NULL, updated_at = now()
                 WHERE id = $1 RETURNING *""",
                existing["id"], starts_at, ends_at, icon_path, color, title_ja, title_en,
            )
        else:
            row = await db.fetchrow(
                """INSERT INTO special_reward_home_banners (
                       link_id, starts_at, ends_at, icon_path, color, title_ja, title_en, created_by_user_id
                   ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                   RETURNING *""",
                link_id, starts_at, ends_at, icon_path, color, title_ja, title_en, user.id,
            )
    await invalidate_home_banner_cache()
    return dict(row)


async def _mark_banners_stock_empty(db: asyncpg.Connection, link_id: int) -> None:
    rows = await db.fetch(
        """UPDATE special_reward_home_banners
           SET ended_reason = 'stock_empty', updated_at = now()
         WHERE link_id = $1 AND ended_reason IS NULL AND ends_at > now()
         RETURNING id""",
        link_id,
    )
    if rows:
        await invalidate_home_banner_cache()
        link = await db.fetchrow("SELECT name_ja, name_en FROM special_reward_links WHERE id = $1", link_id)
        name = (link["name_ja"] if link else "") or (link["name_en"] if link else "") or str(link_id)
        await emit_admin_notification(
            db,
            "special_reward_stock_empty",
            title="特別報酬リンクの在庫がなくなりました",
            summary=f"「{clip_admin_notification_text(name, 80)}」の在庫切れによりホーム掲載を終了しました。",
            payload={"link_id": link_id},
            dedupe_key=f"special_reward_stock_empty:{link_id}:{rows[0]['id']}",
        )


async def decorate_link(
    db: asyncpg.Connection, row: asyncpg.Record | dict[str, Any]
) -> dict[str, Any]:
    link = dict(row)
    inventory = await get_inventory(db, link)
    creator = None
    if link.get("created_by_user_id"):
        creator = await db.fetchval("SELECT name FROM users WHERE id = $1", link["created_by_user_id"])
    items = []
    if link["link_type"] == "link_set":
        item_rows = await db.fetch(
            """SELECT id, url, claimed_at, claimed_by_user_id, source
               FROM special_reward_link_items WHERE link_id = $1 ORDER BY id""",
            link["id"],
        )
        items = [dict(item) for item in item_rows]
        for item in items:
            if item.get("claimed_at"):
                item["claimed_at"] = _aware(item["claimed_at"]).isoformat()
    banner = await db.fetchrow(
        """SELECT * FROM special_reward_home_banners
           WHERE link_id = $1
           ORDER BY CASE WHEN ended_reason IS NULL THEN 0 ELSE 1 END, starts_at DESC, id DESC
           LIMIT 1""",
        link["id"],
    )
    banner_data = None
    if banner:
        banner_data = dict(banner)
        banner_data["starts_at_input"] = to_jst_input(banner["starts_at"])
        banner_data["ends_at_input"] = to_jst_input(banner["ends_at"])
        banner_data.pop("starts_at", None)
        banner_data.pop("ends_at", None)
        banner_data.pop("created_at", None)
        banner_data.pop("updated_at", None)
    click_count = int(await db.fetchval(
        "SELECT COALESCE(SUM(click_count), 0) FROM special_reward_home_banners WHERE link_id = $1",
        link["id"],
    ) or 0)
    link.update(inventory)
    link["creator_name"] = creator
    link["items"] = items
    link["banner"] = banner_data
    link["home_click_count"] = click_count
    link["starts_at_input"] = to_jst_input(link.get("starts_at"))
    link["expires_at_input"] = to_jst_input(link.get("expires_at"))
    link["created_at_text"] = to_jst_input(link.get("created_at"))
    for key in ("starts_at", "expires_at", "created_at", "updated_at"):
        link.pop(key, None)
    return link


async def list_links_for_admin(db: asyncpg.Connection) -> list[dict[str, Any]]:
    rows = await db.fetch(
        """SELECT l.*, u.name AS creator_name
           FROM special_reward_links l
           LEFT JOIN users u ON u.id = l.created_by_user_id
           ORDER BY l.id DESC"""
    )
    result = []
    for row in rows:
        decorated = await decorate_link(db, row)
        decorated["creator_name"] = row["creator_name"]
        result.append(decorated)
    return result


async def list_links_for_minigame_select(db: asyncpg.Connection) -> list[dict[str, Any]]:
    rows = await db.fetch(
        """SELECT l.*, u.name AS creator_name
           FROM special_reward_links l
           LEFT JOIN users u ON u.id = l.created_by_user_id
           WHERE NOT l.is_invalid
           ORDER BY l.id DESC"""
    )
    result = []
    for row in rows:
        inventory = await get_inventory(db, row)
        result.append({
            "id": row["id"],
            "link_type": row["link_type"],
            "name_ja": row["name_ja"],
            "name_en": row["name_en"],
            "creator_name": row["creator_name"],
            "unlimited": inventory["unlimited"],
            "available": inventory["available"],
            "starts_at": _aware(row["starts_at"]).isoformat() if row["starts_at"] else None,
            "expires_at": _aware(row["expires_at"]).isoformat() if row["expires_at"] else None,
        })
    return result


async def validate_minigame_special_reward_prizes(
    db: asyncpg.Connection,
    prizes: dict[str, Any],
    *,
    starts_at: datetime.datetime,
    ends_at: datetime.datetime,
    exclude_campaign_id: int | None = None,
) -> list[str]:
    errors: list[str] = []
    sanitize_special_reward_prize_items(prizes)
    usage = special_reward_link_ids_from_prizes(prizes)
    if not usage:
        return errors
    starts_at = _aware(starts_at)
    ends_at = _aware(ends_at)
    required_expiry = ends_at + CLAIM_WINDOW if ends_at else None
    for link_id, info in usage.items():
        row = await db.fetchrow("SELECT * FROM special_reward_links WHERE id = $1", link_id)
        if not row or row["is_invalid"]:
            errors.append(f"特別報酬リンク # [この部分は公開用リポジトリでは非公開にされています]
