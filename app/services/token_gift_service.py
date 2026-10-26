import datetime
from typing import Any

import asyncpg

from app.core.cache import delete_cache
from app.core.logger import logger
from app.exceptions.custom_exceptions import DataBaseError
# [この部分は公開用リポジトリでは非公開にされています]
from app.services.brawl_service import get_player_name
from app.services.user_service import User, get_user
from app.utils.utils import get_normalized_ip


TOKEN_GIFT_MESSAGE_TYPE = "token_gift"
TOKEN_GIFT_COMMENT_MAX_LENGTH = 200
TOKEN_GIFT_COOLDOWN_SECONDS = 3
TOKEN_GIFT_OPTIONS: dict[int, int] = {
    5: 0,
    10: 0,
    20: 2,
    30: 3,
    50: 5,
    75: 7,
    100: 10,
}
TOKEN_GIFT_TIER_BY_AMOUNT: dict[int, str] = {
    20: "bronze",
    30: "silver",
    50: "gold",
    75: "platinum",
    100: "black",
}
# アカウント作成からの UTC 日付差がこの日数以下なら、PV ではなく日数で上限を決める。
# (max_age_days_inclusive, give_limit, receive_limit)
TOKEN_GIFT_NEW_ACCOUNT_LIMITS: list[tuple[int, int, int]] = [
    (0, 0, 0),
    (1, 1, 1),
    (3, 2, 2),
    (6, 3, 3),
]
# 8日目以降。最後にマッチした min_pv を採用する。
# (min_pv, give_limit, receive_limit)
TOKEN_GIFT_PV_LIMITS: list[tuple[int, int, int]] = [
    (0, 3, 3),
    (100, 4, 5),
    (500, 5, 7),
    (1000, 7, 10),
    (2000, 10, 15),
    (3000, 13, 20),
    (5000, 16, 25),
    (7000, 20, 30),
    (10000, 25, 35),
]


def _as_utc(dt: datetime.datetime) -> datetime.datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def _utc_day_start(now: datetime.datetime) -> datetime.datetime:
    now_utc = _as_utc(now)
    return datetime.datetime(now_utc.year, now_utc.month, now_utc.day, tzinfo=datetime.timezone.utc)


def get_token_gift_daily_limits(
    registration_datetime: datetime.datetime | None,
    pv_count: int | None,
    now: datetime.datetime | None = None,
) -> tuple[int, int]:
    """アカウント日数と累計PVから、その日の贈る/受け取る上限を返す。"""
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    today = _as_utc(now).date()
    if registration_datetime is None:
        age_days = 0
    else:
        age_days = max(0, (today - _as_utc(registration_datetime).date()).days)

    for max_age_days, give_limit, receive_limit in TOKEN_GIFT_NEW_ACCOUNT_LIMITS:
        if age_days <= max_age_days:
            return give_limit, receive_limit

    give_limit = 0
    receive_limit = 0
    pv = int(pv_count or 0)
    for min_pv, give, receive in TOKEN_GIFT_PV_LIMITS:
        if pv < min_pv:
            break
        give_limit = give
        receive_limit = receive
    return give_limit, receive_limit


class TokenGiftError(Exception):
    def __init__(self, code: str, message_ja: str, message_en: str) -> None:
        super().__init__(message_ja)
        self.code = code
        self.message_ja = message_ja
        self.message_en = message_en

    def client_message(self, lang: str) -> str:
        return self.message_ja if lang == "ja" else self.message_en


def gift_tier_class(amount: int | None) -> str:
    if not amount:
        return ""
    tier = TOKEN_GIFT_TIER_BY_AMOUNT.get(int(amount))
    return f"token-gift-event--{tier}" if tier else ""


def format_token_amount(amount: int) -> str:
    return f"{int(amount):,}"


def format_token_gift_preview_label(amount: int | None, lang: str) -> str:
    amount_text = format_token_amount(amount or 0)
    if lang == "ja":
        return f"{amount_text}トークンの進呈"
    return f"Gift of {amount_text} tokens"


def normalize_gift_comment(comment: str | None) -> str | None:
    if comment is None:
        return None
    text = str(comment).replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > TOKEN_GIFT_COMMENT_MAX_LENGTH:
        raise TokenGiftError(
            "invalid_comment",
            f"コメントは{TOKEN_GIFT_COMMENT_MAX_LENGTH}文字以内で入力してください。",
            f"Comments must be {TOKEN_GIFT_COMMENT_MAX_LENGTH} characters or fewer.",
        )
    stripped = text.strip()
    return stripped or None


def _gift_error_messages(lang: str) -> dict[str, str]:
    if lang == "ja":
        return {
            "recipient_at_limit": "相手のトークンが所持上限に達しているため、トークンを贈ることができません。",
            "multi_account": "複数アカウントを作成してトークンを贈り合う自演行為は固く禁止されています。不正行為が発覚した場合、利用制限の対象となることがあります。",
            "insufficient_tokens": "トークンが足りません。",
            "permission": "このチャットに参加する権限がありません。",
            "prohibit_posting": "現在投稿機能が制限されています。一時的なシステムの障害時、または利用規約に違反する投稿が確認された場合、投稿機能が制限されることがあります。",
            "blocked": "このユーザーにはトークンを贈ることができません。",
            "cooldown": "連続した進呈はできません。少し時間をおいてから再度お試しください。",
            "daily_limit": "1日に贈れる回数の上限に達しています。",
            "giver_too_new": "アカウントを作成したばかりのため、安全上トークンを贈る機能はまだ利用できません。通常1日以内に利用できるようになります。",
            "recipient_too_new": "相手がアカウントを作成したばかりのため、まだこのユーザーにトークンを贈ることはできません。",
            "recipient_daily_limit": "相手が1日にトークンを受け取れる回数の上限に達しています。今日はもうこのユーザーにトークンを贈ることができません。",
            "recipient_negative_tokens": "相手のトークン所持数がマイナスのため、このユーザーにトークンを贈ることができません。",
            "invalid_amount": "無効な数量です。",
            "invalid_recipient": "このユーザーにはトークンを贈ることができません。",
            "not_found": "対象の進呈が見つかりません。",
            "forbidden": "この操作を行う権限がありません。",
        }
    return {
        "recipient_at_limit": "You cannot gift tokens because the recipient has reached the holding limit.",
        "multi_account": "Creating multiple accounts to gift tokens to each other is strictly prohibited. Confirmed abuse may result in usage restrictions.",
        "insufficient_tokens": "You do not have enough tokens.",
        "permission": "You do not have permission to join this chat.",
        "prohibit_posting": "Posting is currently restricted. Posting may be restricted during temporary system issues, or when posts that violate the Terms of Service are found.",
        "blocked": "You cannot gift tokens to this user.",
        "cooldown": "Please wait a moment before gifting again.",
        "daily_limit": "You have reached the daily gift limit.",
        "giver_too_new": "Because this account was just created, gifting tokens is not yet available for safety reasons. It usually becomes available within a day.",
        "recipient_too_new": "Because this user just created their account, you cannot gift tokens to them yet.",
        "recipient_daily_limit": "This user has reached the daily limit for receiving tokens. You cannot gift tokens to this user today.",
        "recipient_negative_tokens": "You cannot gift tokens to this user because their token balance is negative.",
        "invalid_amount": "That gift amount is invalid.",
        "invalid_recipient": "You cannot gift tokens to this user.",
        "not_found": "The gift was not found.",
        "forbidden": "You do not have permission to perform this action.",
    }


def token_gift_error(code: str, lang: str) -> TokenGiftError:
    ja_messages = _gift_error_messages("ja")
    en_messages = _gift_error_messages("en")
    ja_text = ja_messages.get(code, ja_messages["invalid_recipient"])
    en_text = en_messages.get(code, en_messages["invalid_recipient"])
    return TokenGiftError(code, ja_text, en_text)


async def _clear_user_token_caches(user_id: int) -> None:
    await delete_cache(f"user:{user_id}")
    await delete_cache(f"user_include_invalid:{user_id}")


async def attach_token_gift_payloads(db: asyncpg.Connection, messages: list[Any]) -> None:
    """token_gift メッセージに進呈メタデータを付与する。"""
    gift_messages = [m for m in messages if getattr(m, "message_type", None) == TOKEN_GIFT_MESSAGE_TYPE]
    if not gift_messages:
        return

    message_ids = [m.id for m in gift_messages]
    try:
        rows = await db.fetch(
            """
            SELECT message_id, amount, fee, recipient_user_id, is_comment_deleted, comment
            FROM token_gifts
            WHERE message_id = ANY($1::int[])
            """,
            message_ids,
        )
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e

    gifts_by_message = {row["message_id"]: row for row in rows}
    recipient_ids = {row["recipient_user_id"] for row in rows if row["recipient_user_id"]}
    recipient_profiles: dict[int, dict[str, Any]] = {}
    for recipient_id in recipient_ids:
        user = await get_user(db, recipient_id)
        if not user:
            continue
        main_account_name = None
        if user.main_account:
            try:
                main_account_name = await get_player_name(user.main_account, db)
            except Exception as e:
                logger.debug(f"進呈先メインアカウント名の取得に失敗: user_id={recipient_id}, error={e}")
        recipient_profiles[recipient_id] = {
            "user_name": user.name,
            "main_account_tag": user.main_account,
            "main_account_name": main_account_name,
        }

    for message in gift_messages:
        row = gifts_by_message.get(message.id)
        if not row:
            continue
        recipient_id = row["recipient_user_id"]
        profile = recipient_profiles.get(recipient_id) if recipient_id else None
        message.gift_amount = row["amount"]
        message.gift_fee = row["fee"]
        message.gift_recipient_user_id = recipient_id
        message.gift_recipient_user_name = profile["user_name"] if profile else None
        message.gift_recipient_main_account_tag = profile["main_account_tag"] if profile else None
        message.gift_recipient_main_account_name = profile["main_account_name"] if profile else None
        message.is_comment_deleted = bool(row["is_comment_deleted"])
        if message.is_comment_deleted:
            message.message = ""


async def _are_users_blocked(db: asyncpg.Connection, user_id_a: int, user_id_b: int) -> bool:
    try:
        row = await db.fetchrow(
            """
            SELECT 1
            FROM user_blocks
            WHERE (blocker_user_id = $1 AND blocked_user_id = $2)
               OR (blocker_user_id = $2 AND blocked_user_id = $1)
            LIMIT 1
            """,
            user_id_a,
            user_id_b,
        )
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e
    return row is not None


async def _giver_recent_gift_row(db: asyncpg.Connection, giver_user_id: int) -> asyncpg.Record | None:
    try:
        return await db.fetchrow(
            """
            SELECT created_at
            FROM token_gifts
            WHERE giver_user_id = $1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            giver_user_id,
        )
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e


async def _giver_daily_gift_count(db: asyncpg.Connection, giver_user_id: int, now: datetime.datetime) -> int:
    day_start = _utc_day_start(now)
    try:
        count = await db.fetchval(
            """
            SELECT COUNT(*)::int
            FROM token_gifts
            WHERE giver_user_id = $1
              AND created_at >= $2
            """,
            giver_user_id,
            day_start,
        )
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e
    return int(count or 0)


async def _recipient_daily_gift_count(
    db: asyncpg.Connection, recipient_user_id: int, now: datetime.datetime
) -> int:
    day_start = _utc_day_start(now)
    try:
        count = await db.fetchval(
            """
            SELECT COUNT(*)::int
            FROM token_gifts
            WHERE recipient_user_id = $1
              AND created_at >= $2
            """,
            recipient_user_id,
            day_start,
        )
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e
    return int(count or 0)


async def get_remaining_give_count(
    db: asyncpg.Connection,
    user: User,
    now: datetime.datetime | None = None,
) -> int:
    """ログインユーザーが今日あと何回贈れるかを返す。"""
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    give_limit, _ = get_token_gift_daily_limits(user.registration_datetime, user.pv_count, now)
    given = await _giver_daily_gift_count(db, user.id, now)
    return max(0, give_limit - given)


def _raise_if_give_limit_reached(give_count: int, give_limit: int, lang: str) -> None:
    if give_count < give_limit:
        return
    if give_limit <= 0:
        raise token_gift_error("giver_too_new", lang)
    raise token_gift_error("daily_limit", lang)


def _raise_if_receive_limit_reached(receive_count: int, receive_limit: int, lang: str) -> None:
    if receive_count < receive_limit:
        return
    if receive_limit <= 0:
        raise token_gift_error("recipient_too_new", lang)
    raise token_gift_error("recipient_daily_limit", lang)


# [この部分は公開用リポジトリでは非公開にされています]


async def validate_token_gift(
    db: asyncpg.Connection,
    *,
    post,
    giver: User,
    recipient_user_id: int,
    amount: int,
    comment: str | None,
    giver_ip: str,
    lang: str,
) -> dict[str, Any]:
    """進呈可能か判定する。残高は動かさない。"""
    if giver.is_prohibit_posting:
        raise token_gift_error("prohibit_posting", lang)
    is_permitted = await post.is_permitted_to_chat(db, giver.id)
    if not is_permitted:
        raise token_gift_error("permission", lang)
    if amount not in TOKEN_GIFT_OPTIONS:
        raise token_gift_error("invalid_amount", lang)

    fee = TOKEN_GIFT_OPTIONS[amount]
    total_cost = amount + fee
    normalized_comment = normalize_gift_comment(comment)

    if recipient_user_id == giver.id:
        raise token_gift_error("multi_account", lang)

    recipient = await get_user(db, recipient_user_id)
    if not recipient or recipient.is_invalid:
        raise token_gift_error("invalid_recipient", lang)

    if await _are_users_blocked(db, giver.id, recipient.id):
        raise token_gift_error("blocked", lang)

    recipient_ip: str | None = None
    # [この部分は公開用リポジトリでは非公開にされています]

    if giver.tokens < total_cost:
        raise token_gift_error("insufficient_tokens", lang)

    recipient_limit = recipient.token_limit
    if recipient_limit is not None and recipient.tokens + amount > recipient_limit:
        raise token_gift_error("recipient_at_limit", lang)
    if recipient.tokens < 0:
        raise token_gift_error("recipient_negative_tokens", lang)

    now = datetime.datetime.now(datetime.timezone.utc)
    latest_gift = await _giver_recent_gift_row(db, giver.id)
    if latest_gift and latest_gift["created_at"]:
        created_at = latest_gift["created_at"]
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=datetime.timezone.utc)
        elapsed = (now - created_at).total_seconds()
        if elapsed < TOKEN_GIFT_COOLDOWN_SECONDS:
            raise token_gift_error("cooldown", lang)

    give_limit, _ = get_token_gift_daily_limits(giver.registration_datetime, giver.pv_count, now)
    daily_count = await _giver_daily_gift_count(db, giver.id, now)
    _raise_if_give_limit_reached(daily_count, give_limit, lang)

    _, receive_limit = get_token_gift_daily_limits(
        recipient.registration_datetime, recipient.pv_count, now
    )
    receive_count = await _recipient_daily_gift_count(db, recipient.id, now)
    _raise_if_receive_limit_reached(receive_count, receive_limit, lang)

    return {
        "amount": amount,
        "fee": fee,
        "total_cost": total_cost,
        "comment": normalized_comment,
        "recipient": recipient,
        "recipient_ip": recipient_ip,
        "giver_tokens_before": giver.tokens,
        "giver_tokens_after": giver.tokens - total_cost,
        "recipient_name": recipient.name,
    }


async def create_token_gift(
    db: asyncpg.Connection,
    *,
    post,
    giver: User,
    recipient_user_id: int,
    amount: int,
    comment: str | None,
    giver_ip: str,
    lang: str,
) -> dict[str, Any]:
    """トークン進呈を確定する。"""
    preview = await validate_token_gift(
        db,
        post=post,
        giver=giver,
        recipient_user_id=recipient_user_id,
        amount=amount,
        comment=comment,
        giver_ip=giver_ip,
        lang=lang,
    )
    fee = preview["fee"]
    total_cost = preview["total_cost"]
    normalized_comment = preview["comment"]
    recipient_ip = preview.get("recipient_ip")

    async with db.transaction():
        user_ids = sorted({giver.id, recipient_user_id})
        try:
            locked_rows = await db.fetch(
                """
                SELECT id, tokens, token_limit, main_account, sub_accounts, is_prohibit_posting, is_invalid,
                       registration_datetime, pv_count
                FROM users
                WHERE id = ANY($1::int[])
                FOR UPDATE
                """,
                user_ids,
            )
        except asyncpg.PostgresError as e:
            raise DataBaseError(e) from e

        locked = {row["id"]: row for row in locked_rows}
        giver_row = locked.get(giver.id)
        recipient_row = locked.get(recipient_user_id)
        if not giver_row or not recipient_row:
            raise token_gift_error("invalid_recipient", lang)
        if giver_row["is_prohibit_posting"]:
            raise token_gift_error("prohibit_posting", lang)
        if recipient_row["is_invalid"]:
            raise token_gift_error("invalid_recipient", lang)
        if giver_row["tokens"] < total_cost:
            raise token_gift_error("insufficient_tokens", lang)
        recipient_limit = recipient_row["token_limit"]
        if recipient_limit is not None and recipient_row["tokens"] + amount > recipient_limit:
            raise token_gift_error("recipient_at_limit", lang)
        if recipient_row["tokens"] < 0:
            raise token_gift_error("recipient_negative_tokens", lang)

        # [この部分は公開用リポジトリでは非公開にされています]

        now = datetime.datetime.now(datetime.timezone.utc)
        latest_gift = await _giver_recent_gift_row(db, giver.id)
        if latest_gift and latest_gift["created_at"]:
            created_at = latest_gift["created_at"]
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=datetime.timezone.utc)
            if (now - created_at).total_seconds() < TOKEN_GIFT_COOLDOWN_SECONDS:
                raise token_gift_error("cooldown", lang)
        give_limit, _ = get_token_gift_daily_limits(
            giver_row["registration_datetime"], giver_row["pv_count"], now
        )
        daily_count = await _giver_daily_gift_count(db, giver.id, now)
        _raise_if_give_limit_reached(daily_count, give_limit, lang)
        _, receive_limit = get_token_gift_daily_limits(
            recipient_row["registration_datetime"], recipient_row["pv_count"], now
        )
        receive_count = await _recipient_daily_gift_count(db, recipient_user_id, now)
        _raise_if_receive_limit_reached(receive_count, receive_limit, lang)

        giver_tokens_before = int(giver_row["tokens"])
        giver_tokens_after = giver_tokens_before - total_cost
        recipient_tokens_after = int(recipient_row["tokens"]) + amount

        try:
            await db.execute(
                "UPDATE users SET tokens = $1 WHERE id = $2",
                giver_tokens_after,
                giver.id,
            )
            await db.execute(
                "UPDATE users SET tokens = $1 WHERE id = $2",
                recipient_tokens_after,
                recipient_user_id,
            )
            message_id = await db.fetchval(
                """
                INSERT INTO messages (
                    thread_id, user_id, user_ip, message_type, message
                ) VALUES ($1, $2, $3, $4, $5)
                RETURNING id
                """,
                post.id,
                giver.id,
                giver_ip,
                TOKEN_GIFT_MESSAGE_TYPE,
                normalized_comment or "",
            )
            if message_id is None:
                raise DataBaseError("トークン進呈メッセージの作成に失敗しました。")
            gift_id = await db.fetchval(
                """
                INSERT INTO token_gifts (
                    thread_id, message_id, giver_user_id, recipient_user_id,
                    amount, fee, comment, giver_ip, recipient_ip
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING id
                """,
                post.id,
                message_id,
                giver.id,
                recipient_user_id,
                amount,
                fee,
                normalized_comment,
                giver_ip,
                recipient_ip,
            )
        except asyncpg.PostgresError as e:
            raise DataBaseError(e) from e

    giver.tokens = giver_tokens_after
    await _clear_user_token_caches(giver.id)
    await _clear_user_token_caches(recipient_user_id)
    logger.info(
        f"トークン進呈: gift_id={gift_id}, message_id={message_id}, thread={post.id}, "
        f"giver={giver.id} ({giver_tokens_before} -> {giver_tokens_after}), "
        f"recipient={recipient_user_id} (+{amount}), fee={fee}"
    )
    # [この部分は公開用リポジトリでは非公開にされています]
    return {
        "gift_id": gift_id,
        "message_id": message_id,
        "amount": amount,
        "fee": fee,
        "comment": normalized_comment,
        "recipient_user_id": recipient_user_id,
        "recipient_name": preview["recipient_name"],
        "giver_tokens_before": giver_tokens_before,
        "giver_tokens_after": giver_tokens_after,
    }


async def delete_token_gift_comment(
    db: asyncpg.Connection,
    *,
    message_id: int,
    actor: User,
    lang: str,
) -> int:
    """進呈コメントのみ削除する。トークン移動は取り消さない。"""
    try:
        row = await db.fetchrow(
            """
            SELECT g.id, g.thread_id, g.giver_user_id, g.is_comment_deleted, m.message_type
            FROM token_gifts g
            JOIN messages m ON m.id = g.message_id
            WHERE g.message_id = $1
            """,
            message_id,
        )
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e
    if not row:
        raise token_gift_error("not_found", lang)
    if row["is_comment_deleted"]:
        raise token_gift_error("not_found", lang)
    if not actor.has_perm("chat.delete_messages") and row["giver_user_id"] != actor.id:
        raise token_gift_error("forbidden", lang)

    try:
        await db.execute(
            "UPDATE token_gifts SET is_comment_deleted = TRUE WHERE id = $1",
            row["id"],
        )
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e

    await delete_cache(f"message:{message_id}")
    logger.info(
        f"トークン進呈コメントを削除: gift_id={row['id']}, message_id={message_id}, actor={actor.id}"
    )
    return row["thread_id"]
