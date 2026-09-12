import datetime
from typing import Any

import asyncpg

from app.core.cache import delete_cache
from app.core.logger import logger
from app.exceptions.custom_exceptions import DataBaseError
from app.services.admin_notification_service import (
    clip_admin_notification_text,
    emit_admin_notification,
    format_admin_user_label,
)
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
                SELECT id, tokens, token_limit, main_account, is_prohibit_posting, is_invalid,
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
    comment_text = clip_admin_notification_text(normalized_comment, 80)
    summary = (
        f"スレッド {post.id} / {format_admin_user_label(giver.name, giver.id)}"
        f" → {format_admin_user_label(preview['recipient_name'], recipient_user_id)}"
        f" / {format_token_amount(amount)}トークン（手数料{format_token_amount(fee)}）"
        f"{'「' + comment_text + '」' if comment_text else ''}"
    )
    await emit_admin_notification(
        db,
        "token_gift_created",
        title="トークン進呈",
        summary=summary,
        actor_user_id=giver.id,
        payload={
            "gift_id": gift_id,
            "message_id": message_id,
            "thread_id": post.id,
            "giver_user_id": giver.id,
            "recipient_user_id": recipient_user_id,
            "amount": amount,
            "fee": fee,
        },
        target_path=f"/admin/token-gifts?gift_id={gift_id}",
    )
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
