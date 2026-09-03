import asyncpg
import datetime
from typing import Any

from app.core.cache import delete_cache, get_cache, set_cache
from app.core.logger import logger
from app.core.templating import censor_filter
from app.exceptions.custom_exceptions import DataBaseError
from app.services.board_service import EMOJIS, get_message, get_post, get_player_icon_from_db
from app.services.user_service import get_blocked_ids, get_user
from app.utils.utils import get_icon_path

NOTIFICATION_LIST_LIMIT = 1000
NOTIFICATION_PAGE_SIZE = 100
BRAWLER_GUIDE_PARTICIPATED_THREAD_NOTIFICATION_LIMIT = 3

NOTIFICATION_TYPE_POST_LIKE = "post_like"
NOTIFICATION_TYPE_OWN_POST_MESSAGE = "own_post_message"
NOTIFICATION_TYPE_PARTICIPATED_THREAD_MESSAGE = "participated_thread_message"
NOTIFICATION_TYPE_MESSAGE_REPLY = "message_reply"
NOTIFICATION_TYPE_MESSAGE_REACTION = "message_reaction"
NOTIFICATION_TYPE_TOKEN_GIFT = "token_gift"

AGGREGATED_NOTIFICATION_TYPES = {
    NOTIFICATION_TYPE_POST_LIKE,
    NOTIFICATION_TYPE_MESSAGE_REACTION,
}

NOTIFICATION_GROUP_ID_SQL = f"""
    CASE
        WHEN notification_type = '{NOTIFICATION_TYPE_POST_LIKE}' THEN post_id
        WHEN notification_type = '{NOTIFICATION_TYPE_MESSAGE_REACTION}' THEN message_id
        ELSE id
    END
"""

NOTIFICATION_TYPE_SETTING_KEYS = {
    NOTIFICATION_TYPE_POST_LIKE: "notification_post_like_enabled",
    NOTIFICATION_TYPE_OWN_POST_MESSAGE: "notification_own_post_message_enabled",
    NOTIFICATION_TYPE_PARTICIPATED_THREAD_MESSAGE: "notification_participated_thread_message_enabled",
    NOTIFICATION_TYPE_MESSAGE_REPLY: "notification_message_reply_enabled",
    NOTIFICATION_TYPE_MESSAGE_REACTION: "notification_message_reaction_enabled",
    NOTIFICATION_TYPE_TOKEN_GIFT: "notification_token_gift_enabled",
}

VALID_NOTIFICATION_FILTERS = frozenset({"all", *NOTIFICATION_TYPE_SETTING_KEYS})

MESSAGE_NOTIFICATION_TYPE_PRIORITY = (
    NOTIFICATION_TYPE_MESSAGE_REPLY,
    NOTIFICATION_TYPE_OWN_POST_MESSAGE,
    NOTIFICATION_TYPE_PARTICIPATED_THREAD_MESSAGE,
)

POST_TYPE_LABELS_JA = {
    "team": "チーム募集",
    "friend": "フレンド募集",
    "club": "クラブ募集",
    "general": "なんでも掲示板",
}

POST_TYPE_LABELS_EN = {
    "team": "team recruitment",
    "friend": "friend recruitment",
    "club": "club recruitment",
    "general": "general board",
}


def _badge_cache_key(user_id: int) -> str:
    return f"notification_badge_count:{user_id}"


def _settings_cache_key(user_id: int) -> str:
    return f"notification_settings:{user_id}"


async def invalidate_notification_cache(user_id: int) -> None:
    await delete_cache(_badge_cache_key(user_id))
    await delete_cache(_settings_cache_key(user_id))


async def get_notification_settings(db: asyncpg.Connection, user_id: int) -> dict[str, bool | datetime.datetime | None]:
    cache_key = _settings_cache_key(user_id)
    cached = await get_cache(cache_key)
    if cached:
        if cached.get("notifications_last_read_at"):
            cached["notifications_last_read_at"] = datetime.datetime.fromisoformat(cached["notifications_last_read_at"])
        return cached

    try:
        row = await db.fetchrow(
            """
            SELECT notification_badge_enabled,
                   notification_post_like_enabled,
                   notification_own_post_message_enabled,
                   notification_participated_thread_message_enabled,
                   notification_message_reply_enabled,
                   notification_message_reaction_enabled,
                   notification_token_gift_enabled,
                   notifications_last_read_at
            FROM users
            WHERE id = $1
            """,
            user_id,
        )
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e

    if not row:
        raise ValueError("User not found")

    settings = {
        "notification_badge_enabled": bool(row["notification_badge_enabled"]),
        "notification_post_like_enabled": bool(row["notification_post_like_enabled"]),
        "notification_own_post_message_enabled": bool(row["notification_own_post_message_enabled"]),
        "notification_participated_thread_message_enabled": bool(row["notification_participated_thread_message_enabled"]),
        "notification_message_reply_enabled": bool(row["notification_message_reply_enabled"]),
        "notification_message_reaction_enabled": bool(row["notification_message_reaction_enabled"]),
        "notification_token_gift_enabled": bool(row["notification_token_gift_enabled"]),
        "notifications_last_read_at": row["notifications_last_read_at"],
    }
    cache_value = settings.copy()
    if cache_value["notifications_last_read_at"]:
        cache_value["notifications_last_read_at"] = cache_value["notifications_last_read_at"].isoformat()
    await set_cache(cache_key, cache_value, ttl=600)
    return settings


async def update_notification_setting(
    db: asyncpg.Connection,
    user_id: int,
    setting_key: str,
    enabled: bool,
) -> None:
    allowed_keys = {
        "notification_badge_enabled",
        "notification_post_like_enabled",
        "notification_own_post_message_enabled",
        "notification_participated_thread_message_enabled",
        "notification_message_reply_enabled",
        "notification_message_reaction_enabled",
        "notification_token_gift_enabled",
    }
    if setting_key not in allowed_keys:
        raise ValueError("Invalid notification setting key")

    try:
        await db.execute(
            f"UPDATE users SET {setting_key} = $1 WHERE id = $2",
            enabled,
            user_id,
        )
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e

    await invalidate_notification_cache(user_id)


async def mark_all_notifications_as_read(db: asyncpg.Connection, user_id: int) -> None:
    try:
        await db.execute(
            "UPDATE users SET notifications_last_read_at = NOW() WHERE id = $1",
            user_id,
        )
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e
    await invalidate_notification_cache(user_id)


def _is_type_enabled(settings: dict[str, bool | datetime.datetime | None], notification_type: str) -> bool:
    setting_key = NOTIFICATION_TYPE_SETTING_KEYS.get(notification_type)
    if not setting_key:
        return True
    return bool(settings.get(setting_key, True))


async def _is_actor_blocked(
    db: asyncpg.Connection,
    recipient_user_id: int,
    actor_user_id: int,
) -> bool:
    blocked_ids = await get_blocked_ids(db, blocker_user_id=recipient_user_id, blocker_anonymous_id=None)
    return actor_user_id in blocked_ids["user_ids"]


async def _create_notification(
    db: asyncpg.Connection,
    *,
    recipient_user_id: int,
    notification_type: str,
    actor_user_id: int,
    post_id: int | None = None,
    message_id: int | None = None,
    post_vote_id: int | None = None,
    reaction_id: int | None = None,
) -> None:
    if recipient_user_id == actor_user_id:
        return

    settings = await get_notification_settings(db, recipient_user_id)
    if not _is_type_enabled(settings, notification_type):
        return

    if await _is_actor_blocked(db, recipient_user_id, actor_user_id):
        return

    try:
        await db.execute(
            """
            INSERT INTO board_notifications (
                recipient_user_id, notification_type, actor_user_id,
                post_id, message_id, post_vote_id, reaction_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            recipient_user_id,
            notification_type,
            actor_user_id,
            post_id,
            message_id,
            post_vote_id,
            reaction_id,
        )
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e

    await invalidate_notification_cache(recipient_user_id)


async def handle_post_like_notification(
    db: asyncpg.Connection,
    *,
    post_id: int,
    actor_user_id: int,
    is_liked: bool,
    post_vote_id: int | None = None,
) -> None:
    if is_liked:
        try:
            post_row = await db.fetchrow(
                "SELECT host_id, type, is_deleted FROM posts WHERE id = $1",
                post_id,
            )
        except asyncpg.PostgresError as e:
            raise DataBaseError(e) from e

        if not post_row or post_row["is_deleted"] or post_row["type"] != "general":
            return

        host_id = post_row["host_id"]
        if not host_id:
            return

        if post_vote_id is None:
            vote_row = await db.fetchrow(
                "SELECT id FROM post_votes WHERE post_id = $1 AND user_id = $2 AND vote_type = 1",
                post_id,
                actor_user_id,
            )
            post_vote_id = vote_row["id"] if vote_row else None

        await _create_notification(
            db,
            recipient_user_id=host_id,
            notification_type=NOTIFICATION_TYPE_POST_LIKE,
            actor_user_id=actor_user_id,
            post_id=post_id,
            post_vote_id=post_vote_id,
        )
        return

    try:
        rows = await db.fetch(
            """
            DELETE FROM board_notifications
            WHERE notification_type = $1
              AND actor_user_id = $2
              AND post_id = $3
            RETURNING recipient_user_id
            """,
            NOTIFICATION_TYPE_POST_LIKE,
            actor_user_id,
            post_id,
        )
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e

    for row in rows:
        await invalidate_notification_cache(row["recipient_user_id"])


async def _create_brawler_guide_participated_recipient_ids(
    db: asyncpg.Connection,
    *,
    thread_id: int,
    message_id: int,
    sender_user_id: int,
) -> list[int]:
    """キャラクター図鑑スレッドで、参加通知対象のユーザーID一覧を返す。"""
    try:
        recipient_rows = await db.fetch(
            """
            WITH latest_user_messages AS (
                SELECT DISTINCT ON (user_id) user_id, id AS last_user_msg_id
                FROM messages
                WHERE thread_id = $1
                  AND user_id IS NOT NULL
                  AND is_deleted = FALSE
                  AND id < $2
                  AND COALESCE(message_type, 'message') = 'message'
                ORDER BY user_id, id DESC
            )
            SELECT lum.user_id
            FROM latest_user_messages lum
            WHERE lum.user_id != $3
              AND (
                  SELECT COUNT(*)
                  FROM messages m
                  WHERE m.thread_id = $1
                    AND m.is_deleted = FALSE
                    AND m.user_id IS NOT NULL
                    AND m.user_id != lum.user_id
                    AND m.id > lum.last_user_msg_id
                    AND m.id <= $2
                    AND COALESCE(m.message_type, 'message') = 'message'
              ) <= $4
            """,
            thread_id,
            message_id,
            sender_user_id,
            BRAWLER_GUIDE_PARTICIPATED_THREAD_NOTIFICATION_LIMIT,
        )
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e

    return [row["user_id"] for row in recipient_rows]


async def _create_notification_with_message_priority(
    db: asyncpg.Connection,
    *,
    recipient_user_id: int,
    candidate_types: set[str],
    actor_user_id: int,
    post_id: int,
    message_id: int,
) -> None:
    """候補種別のうち、優先度が高く設定がオンのもの1件だけ通知する。"""
    settings = await get_notification_settings(db, recipient_user_id)
    for notification_type in MESSAGE_NOTIFICATION_TYPE_PRIORITY:
        if notification_type not in candidate_types:
            continue
        if not _is_type_enabled(settings, notification_type):
            continue
        await _create_notification(
            db,
            recipient_user_id=recipient_user_id,
            notification_type=notification_type,
            actor_user_id=actor_user_id,
            post_id=post_id,
            message_id=message_id,
        )
        return


async def create_message_notifications(
    db: asyncpg.Connection,
    *,
    thread_id: int,
    message_id: int,
    sender_user_id: int,
    reply_to_message_id: int | None = None,
) -> None:
    if not sender_user_id:
        return

    source_message = await get_message(db, message_id, include_deleted_message=True)
    if not source_message or source_message.message_type != "message":
        return

    post = await get_post(db, thread_id)
    if not post or post.is_deleted:
        return

    candidates: dict[int, set[str]] = {}

    def add_candidate(user_id: int | None, notification_type: str) -> None:
        if not user_id or user_id == sender_user_id:
            return
        candidates.setdefault(user_id, set()).add(notification_type)

    if reply_to_message_id is not None:
        reply_target = await get_message(db, reply_to_message_id, include_deleted_message=True)
        if (
            reply_target
            and not reply_target.is_deleted
            and reply_target.thread_id == thread_id
            and reply_target.user_id
            and reply_target.user_id != sender_user_id
        ):
            add_candidate(reply_target.user_id, NOTIFICATION_TYPE_MESSAGE_REPLY)

    if post.type == "brawler_guide":
        for participant_id in await _create_brawler_guide_participated_recipient_ids(
            db,
            thread_id=thread_id,
            message_id=message_id,
            sender_user_id=sender_user_id,
        ):
            add_candidate(participant_id, NOTIFICATION_TYPE_PARTICIPATED_THREAD_MESSAGE)
    else:
        if post.host_id:
            add_candidate(post.host_id, NOTIFICATION_TYPE_OWN_POST_MESSAGE)

        try:
            participant_rows = await db.fetch(
                """
                SELECT DISTINCT user_id
                FROM messages
                WHERE thread_id = $1
                  AND user_id IS NOT NULL
                  AND user_id != $2
                  AND is_deleted = FALSE
                  AND id < $3
                  AND COALESCE(message_type, 'message') = 'message'
                """,
                thread_id,
                sender_user_id,
                message_id,
            )
        except asyncpg.PostgresError as e:
            raise DataBaseError(e) from e

        for row in participant_rows:
            add_candidate(row["user_id"], NOTIFICATION_TYPE_PARTICIPATED_THREAD_MESSAGE)

    for recipient_user_id, candidate_types in candidates.items():
        await _create_notification_with_message_priority(
            db,
            recipient_user_id=recipient_user_id,
            candidate_types=candidate_types,
            actor_user_id=sender_user_id,
            post_id=post.id,
            message_id=message_id,
        )


async def handle_message_reaction_notification(
    db: asyncpg.Connection,
    *,
    message_id: int,
    actor_user_id: int,
    reaction_id: int | None = None,
    is_added: bool = True,
) -> None:
    message = await get_message(db, message_id)
    if not message or message.is_deleted or not message.user_id:
        return
    if message.message_type != "message":
        return

    if is_added:
        await _create_notification(
            db,
            recipient_user_id=message.user_id,
            notification_type=NOTIFICATION_TYPE_MESSAGE_REACTION,
            actor_user_id=actor_user_id,
            post_id=message.thread_id,
            message_id=message_id,
            reaction_id=reaction_id,
        )
        return

    try:
        if reaction_id is not None:
            rows = await db.fetch(
                """
                DELETE FROM board_notifications
                WHERE notification_type = $1
                  AND reaction_id = $2
                RETURNING recipient_user_id
                """,
                NOTIFICATION_TYPE_MESSAGE_REACTION,
                reaction_id,
            )
        else:
            rows = await db.fetch(
                """
                DELETE FROM board_notifications
                WHERE notification_type = $1
                  AND actor_user_id = $2
                  AND message_id = $3
                RETURNING recipient_user_id
                """,
                NOTIFICATION_TYPE_MESSAGE_REACTION,
                actor_user_id,
                message_id,
            )
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e

    for row in rows:
        await invalidate_notification_cache(row["recipient_user_id"])


async def create_token_gift_notification(
    db: asyncpg.Connection,
    *,
    thread_id: int,
    message_id: int,
    giver_user_id: int,
    recipient_user_id: int,
) -> None:
    await _create_notification(
        db,
        recipient_user_id=recipient_user_id,
        notification_type=NOTIFICATION_TYPE_TOKEN_GIFT,
        actor_user_id=giver_user_id,
        post_id=thread_id,
        message_id=message_id,
    )


def format_notification_ago(created_at: datetime.datetime, lang: str) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=datetime.timezone.utc)
    delta = now - created_at
    total_seconds = int(delta.total_seconds())
    if total_seconds < 60:
        return "たった今" if lang == "ja" else "Just Now"
    minutes = total_seconds // 60
    if minutes < 60:
        return f"{minutes}分" if lang == "ja" else f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}時間" if lang == "ja" else f"{hours}h"
    days = hours // 24
    return f"{days}日" if lang == "ja" else f"{days}d"


async def _load_actor_profiles(
    db: asyncpg.Connection,
    actor_user_ids: list[int],
) -> dict[int, dict[str, Any]]:
    profiles: dict[int, dict[str, Any]] = {}
    for actor_id in actor_user_ids:
        user = await get_user(db, actor_id)
        if not user:
            continue
        icon_path = "/static/images/player_icon/0.png"
        main_account_tag = user.main_account
        main_account_name = None
        if main_account_tag:
            try:
                icon_id = await get_player_icon_from_db(main_account_tag, db)
                icon_path = "/" + get_icon_path(icon_id)
                from app.services.brawl_service import get_player_name
                main_account_name = await get_player_name(main_account_tag, db)
            except Exception as e:
                logger.debug(f"通知用アクター情報取得失敗 (user_id={actor_id}): {e}")
        profiles[actor_id] = {
            "user_id": actor_id,
            "user_name": user.name,
            "main_account_tag": main_account_tag,
            "main_account_name": main_account_name,
            "icon_path": icon_path,
        }
    return profiles


def _build_like_title(actors: list[dict[str, Any]], lang: str) -> str:
    names = [actor["user_name"] for actor in actors[:2]]
    if len(actors) == 1:
        if lang == "ja":
            return f"<b>{names[0]}</b>さんがあなたの投稿をいいねしました"
        return f"<b>{names[0]}</b> liked your post"
    if len(actors) == 2:
        if lang == "ja":
            return f"<b>{names[0]}</b>さんと<b>{names[1]}</b>さんがあなたの投稿をいいねしました"
        return f"<b>{names[0]}</b> and <b>{names[1]}</b> liked your post"
    others = len(actors) - 1
    if lang == "ja":
        return f"<b>{names[0]}</b>さんと他{others}人があなたの投稿をいいねしました"
    return f"<b>{names[0]}</b> and {others} others liked your post"


def _build_reaction_title(actors: list[dict[str, Any]], lang: str) -> str:
    names = [actor["user_name"] for actor in actors[:2]]
    if len(actors) == 1:
        if lang == "ja":
            return f"<b>{names[0]}</b>さんがあなたのメッセージにリアクションしました"
        return f"<b>{names[0]}</b> reacted to your message"
    if len(actors) == 2:
        if lang == "ja":
            return f"<b>{names[0]}</b>さんと<b>{names[1]}</b>さんがあなたのメッセージにリアクションしました"
        return f"<b>{names[0]}</b> and <b>{names[1]}</b> reacted to your message"
    others = len(actors) - 1
    if lang == "ja":
        return f"<b>{names[0]}</b>さんと他{others}人があなたのメッセージにリアクションしました"
    return f"<b>{names[0]}</b> and {others} others reacted to your message"


def _build_reply_title(actor_name: str, lang: str) -> str:
    if lang == "ja":
        return f"<b>{actor_name}</b>さんがあなたのメッセージにリプライしました"
    return f"<b>{actor_name}</b> replied to your message"


def _build_message_title(
    actor_name: str,
    post_type: str,
    notification_type: str,
    lang: str,
) -> str:
    post_label_ja = POST_TYPE_LABELS_JA.get(post_type, post_type)
    post_label_en = POST_TYPE_LABELS_EN.get(post_type, post_type)
    if notification_type == NOTIFICATION_TYPE_OWN_POST_MESSAGE:
        if lang == "ja":
            return f"<b>{actor_name}</b>さんがあなたの{post_label_ja}の投稿に返信しました"
        return f"<b>{actor_name}</b> replied to your {post_label_en} post"
    if post_type == "brawler_guide":
        if lang == "ja":
            return f"<b>{actor_name}</b>さんがあなたがメッセージを送ったキャラクター掲示板に返信しました"
        return f"<b>{actor_name}</b> replied to the brawler guide board you messaged in"
    if lang == "ja":
        return f"<b>{actor_name}</b>さんがあなたがメッセージを送った{post_label_ja}の投稿に返信しました"
    return f"<b>{actor_name}</b> replied to a {post_label_en} post you messaged in"


def empty_board_notification_context() -> dict[str, Any]:
    return {
        "unread_badge_count": 0,
        "show_notification_badge": False,
        "notification_badge_text": "",
    }


async def _build_notification_where(
    db: asyncpg.Connection,
    user_id: int,
    *,
    notification_filter: str,
    unread_only: bool,
    last_read_at: datetime.datetime | None,
    settings: dict[str, bool | datetime.datetime | None] | None = None,
) -> tuple[list[str], list[Any]] | None:
    """通知取得用の WHERE 句を組み立てる。有効な通知種別がなければ None。"""
    where_clauses = ["recipient_user_id = $1"]
    params: list[Any] = [user_id]

    if notification_filter != "all":
        params.append(notification_filter)
        where_clauses.append(f"notification_type = ${len(params)}")

    if unread_only and last_read_at is not None:
        params.append(last_read_at)
        where_clauses.append(f"created_at > ${len(params)}")

    if settings is None:
        settings = await get_notification_settings(db, user_id)
    enabled_types = [
        ntype for ntype in NOTIFICATION_TYPE_SETTING_KEYS
        if _is_type_enabled(settings, ntype)
    ]
    if not enabled_types:
        return None

    params.append(enabled_types)
    where_clauses.append(f"notification_type = ANY(${len(params)}::text[])")

    blocked_ids = await get_blocked_ids(db, blocker_user_id=user_id, blocker_anonymous_id=None)
    if blocked_ids["user_ids"]:
        params.append(blocked_ids["user_ids"])
        where_clauses.append(f"NOT (actor_user_id = ANY(${len(params)}::int[]))")

    return where_clauses, params


async def _fetch_notification_group_page(
    db: asyncpg.Connection,
    user_id: int,
    *,
    notification_filter: str,
    page: int,
    page_size: int,
) -> tuple[list[asyncpg.Record], bool]:
    """集約後の通知グループをページ単位で取得する。has_more も返す。"""
    if page < 1 or page_size < 1:
        return [], False

    offset = (page - 1) * page_size
    if offset >= NOTIFICATION_LIST_LIMIT:
        return [], False

    built = await _build_notification_where(
        db,
        user_id,
        notification_filter=notification_filter,
        unread_only=False,
        last_read_at=None,
    )
    if built is None:
        return [], False

    where_clauses, params = built
    fetch_limit = min(page_size, NOTIFICATION_LIST_LIMIT - offset)
    need_plus_one = offset + fetch_limit < NOTIFICATION_LIST_LIMIT
    params.append(fetch_limit + (1 if need_plus_one else 0))
    limit_placeholder = f"${len(params)}"
    params.append(offset)
    offset_placeholder = f"${len(params)}"

    query = f"""
        SELECT notification_type, group_id, MAX(created_at) AS latest_at
        FROM (
            SELECT
                notification_type,
                {NOTIFICATION_GROUP_ID_SQL} AS group_id,
                created_at
            FROM board_notifications
            WHERE {' AND '.join(where_clauses)}
        ) AS grouped_src
        GROUP BY notification_type, group_id
        ORDER BY MAX(created_at) DESC
        LIMIT {limit_placeholder} OFFSET {offset_placeholder}
    """
    try:
        group_rows = await db.fetch(query, *params)
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e

    has_more = need_plus_one and len(group_rows) > fetch_limit
    return list(group_rows[:fetch_limit]), has_more


async def _fetch_notification_rows_for_groups(
    db: asyncpg.Connection,
    user_id: int,
    group_rows: list[asyncpg.Record],
) -> list[asyncpg.Record]:
    if not group_rows:
        return []

    post_like_ids: list[int] = []
    reaction_ids: list[int] = []
    other_ids: list[int] = []
    for row in group_rows:
        group_id = row["group_id"]
        if group_id is None:
            continue
        ntype = row["notification_type"]
        if ntype == NOTIFICATION_TYPE_POST_LIKE:
            post_like_ids.append(group_id)
        elif ntype == NOTIFICATION_TYPE_MESSAGE_REACTION:
            reaction_ids.append(group_id)
        else:
            other_ids.append(group_id)

    query = """
        SELECT *
        FROM board_notifications
        WHERE recipient_user_id = $1
          AND (
            (notification_type = $2 AND post_id = ANY($3::int[]))
            OR (notification_type = $4 AND message_id = ANY($5::int[]))
            OR id = ANY($6::int[])
          )
        ORDER BY created_at DESC
    """
    try:
        return await db.fetch(
            query,
            user_id,
            NOTIFICATION_TYPE_POST_LIKE,
            post_like_ids,
            NOTIFICATION_TYPE_MESSAGE_REACTION,
            reaction_ids,
            other_ids,
        )
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e


async def _aggregate_notification_rows(
    db: asyncpg.Connection,
    rows: list[asyncpg.Record],
    lang: str,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int | None, int | None], list[asyncpg.Record]] = {}
    ordered_keys: list[tuple[str, int | None, int | None]] = []

    for row in rows:
        ntype = row["notification_type"]
        if ntype in AGGREGATED_NOTIFICATION_TYPES:
            group_key = (ntype, row["post_id"] if ntype == NOTIFICATION_TYPE_POST_LIKE else None, row["message_id"])
        else:
            group_key = (ntype, row["id"], None)

        if group_key not in grouped:
            grouped[group_key] = []
            ordered_keys.append(group_key)
        grouped[group_key].append(row)

    actor_ids: set[int] = set()
    post_ids: set[int] = set()
    message_ids: set[int] = set()
    for key in ordered_keys:
        for row in grouped[key]:
            actor_ids.add(row["actor_user_id"])
            if row["post_id"]:
                post_ids.add(row["post_id"])
            if row["message_id"]:
                message_ids.add(row["message_id"])

    actor_profiles = await _load_actor_profiles(db, list(actor_ids))

    post_comments: dict[int, str | None] = {}
    if post_ids:
        post_rows = await db.fetch(
            "SELECT id, comment, type, is_deleted FROM posts WHERE id = ANY($1::int[])",
            list(post_ids),
        )
        for post_row in post_rows:
            if not post_row["is_deleted"]:
                post_comments[post_row["id"]] = post_row["comment"]

    message_texts: dict[int, str | None] = {}
    post_types: dict[int, str] = {}
    if message_ids:
        message_rows = await db.fetch(
            """
            SELECT m.id, m.message, m.is_deleted, p.type AS post_type, p.is_deleted AS post_deleted
            FROM messages m
            JOIN posts p ON p.id = m.thread_id
            WHERE m.id = ANY($1::int[])
            """,
            list(message_ids),
        )
        for message_row in message_rows:
            if not message_row["is_deleted"] and not message_row["post_deleted"]:
                message_texts[message_row["id"]] = censor_filter(message_row["message"] or "")
                post_types[message_row["id"]] = message_row["post_type"]

    reaction_summaries: dict[int, str] = {}
    if message_ids:
        reaction_rows = await db.fetch(
            """
            SELECT message_id, emoji, COUNT(*)::int AS count
            FROM reactions
            WHERE message_id = ANY($1::int[])
            GROUP BY message_id, emoji
            """,
            list(message_ids),
        )
        reactions_by_message: dict[int, list[tuple[str, int]]] = {}
        for reaction_row in reaction_rows:
            reactions_by_message.setdefault(reaction_row["message_id"], []).append(
                (reaction_row["emoji"], reaction_row["count"])
            )
        for message_id, emoji_counts in reactions_by_message.items():
            emoji_counts.sort(key=lambda item: EMOJIS.index(item[0]) if item[0] in EMOJIS else len(EMOJIS))
            reaction_summaries[message_id] = "・".join(f"{emoji}{count}" for emoji, count in emoji_counts)

    gift_metas: dict[int, dict[str, Any]] = {}
    if message_ids:
        try:
            gift_rows = await db.fetch(
                """
                SELECT message_id, amount, comment, is_comment_deleted
                FROM token_gifts
                WHERE message_id = ANY($1::int[])
                """,
                list(message_ids),
            )
        except asyncpg.PostgresError as e:
            raise DataBaseError(e) from e
        from app.services.token_gift_service import TOKEN_GIFT_TIER_BY_AMOUNT
        for gift_row in gift_rows:
            gift_metas[gift_row["message_id"]] = {
                "amount": gift_row["amount"],
                "comment": gift_row["comment"],
                "is_comment_deleted": bool(gift_row["is_comment_deleted"]),
                "tier": TOKEN_GIFT_TIER_BY_AMOUNT.get(int(gift_row["amount"])),
            }

    notifications: list[dict[str, Any]] = []
    for key in ordered_keys:
        group_rows = sorted(grouped[key], key=lambda row: row["created_at"], reverse=True)
        latest_row = group_rows[0]
        ntype = latest_row["notification_type"]

        seen_actor_ids: list[int] = []
        for row in group_rows:
            if row["actor_user_id"] not in seen_actor_ids:
                seen_actor_ids.append(row["actor_user_id"])

        actors = [actor_profiles[actor_id] for actor_id in seen_actor_ids if actor_id in actor_profiles]
        if not actors:
            continue

        created_at = latest_row["created_at"]
        if isinstance(created_at, datetime.datetime) and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=datetime.timezone.utc)

        item: dict[str, Any] = {
            "notification_type": ntype,
            "created_at": created_at,
            "created_at_iso": created_at.isoformat() if isinstance(created_at, datetime.datetime) else str(created_at),
            "ago_text": format_notification_ago(created_at, lang),
            "actors": actors[:10],
            "title_html": "",
            "target_text": "",
            "target_kind": "",
            "thread_id": None,
            "message_id": None,
            "reaction_summary": "",
            "target_is_empty": False,
            "gift_tier": None,
        }

        if ntype == NOTIFICATION_TYPE_POST_LIKE:
            post_id = latest_row["post_id"]
            if not post_id or post_id not in post_comments:
                continue
            item["title_html"] = _build_like_title(actors, lang)
            item["target_text"] = post_comments[post_id] or ""
            item["target_kind"] = "general_own_posts"
        elif ntype in (
            NOTIFICATION_TYPE_OWN_POST_MESSAGE,
            NOTIFICATION_TYPE_PARTICIPATED_THREAD_MESSAGE,
            NOTIFICATION_TYPE_MESSAGE_REPLY,
        ):
            message_id = latest_row["message_id"]
            if not message_id or message_id not in message_texts:
                continue
            actor = actors[0]
            post_type = post_types.get(message_id, "team")
            if ntype == NOTIFICATION_TYPE_MESSAGE_REPLY:
                item["title_html"] = _build_reply_title(actor["user_name"], lang)
            else:
                item["title_html"] = _build_message_title(
                    actor["user_name"],
                    post_type,
                    ntype,
                    lang,
                )
            item["target_text"] = message_texts[message_id] or ""
            item["target_kind"] = "chat"
            item["thread_id"] = latest_row["post_id"]
            item["message_id"] = message_id
        elif ntype == NOTIFICATION_TYPE_MESSAGE_REACTION:
            message_id = latest_row["message_id"]
            if not message_id or message_id not in message_texts:
                continue
            item["title_html"] = _build_reaction_title(actors, lang)
            item["target_text"] = message_texts[message_id] or ""
            item["target_kind"] = "chat"
            item["thread_id"] = latest_row["post_id"]
            item["message_id"] = message_id
            item["reaction_summary"] = reaction_summaries.get(message_id, "")
        elif ntype == NOTIFICATION_TYPE_TOKEN_GIFT:
            message_id = latest_row["message_id"]
            gift_meta = gift_metas.get(message_id)
            if not message_id or not gift_meta:
                continue
            actor = actors[0]
            amount_text = f"{int(gift_meta['amount']):,}"
            if lang == "ja":
                item["title_html"] = (
                    f"<b>{actor['user_name']}</b>さんがあなたに<b>{amount_text}トークン</b>を贈りました"
                )
            else:
                item["title_html"] = (
                    f"<b>{actor['user_name']}</b> gifted you <b>{amount_text} tokens</b>"
                )
            if gift_meta["is_comment_deleted"]:
                item["target_text"] = ""
                item["target_is_empty"] = True
            elif gift_meta["comment"]:
                item["target_text"] = censor_filter(gift_meta["comment"])
                item["target_is_empty"] = False
            else:
                item["target_text"] = ""
                item["target_is_empty"] = True
            item["target_kind"] = "chat"
            item["thread_id"] = latest_row["post_id"]
            item["message_id"] = message_id
            item["gift_tier"] = gift_meta["tier"]
        else:
            continue

        notifications.append(item)

    notifications.sort(key=lambda item: item["created_at"], reverse=True)
    return notifications


async def get_notifications_for_display(
    db: asyncpg.Connection,
    user_id: int,
    *,
    notification_filter: str = "all",
    lang: str = "ja",
    page: int = 1,
    limit: int = NOTIFICATION_PAGE_SIZE,
) -> tuple[list[dict[str, Any]], bool]:
    group_rows, has_more = await _fetch_notification_group_page(
        db,
        user_id,
        notification_filter=notification_filter,
        page=page,
        page_size=limit,
    )
    rows = await _fetch_notification_rows_for_groups(db, user_id, group_rows)
    return await _aggregate_notification_rows(db, rows, lang), has_more


async def get_unread_badge_count(db: asyncpg.Connection, user_id: int) -> int:
    cache_key = _badge_cache_key(user_id)
    cached = await get_cache(cache_key)
    if cached is not None:
        return int(cached)

    settings = await get_notification_settings(db, user_id)
    built = await _build_notification_where(
        db,
        user_id,
        notification_filter="all",
        unread_only=True,
        last_read_at=settings.get("notifications_last_read_at"),
        settings=settings,
    )
    if built is None:
        await set_cache(cache_key, 0, ttl=60)
        return 0

    where_clauses, params = built
    # post_like / message_reaction は表示時と同じキーで集約して件数化（行を全件取得しない）
    query = f"""
        SELECT COUNT(*)::int AS count
        FROM (
            SELECT DISTINCT
                notification_type,
                {NOTIFICATION_GROUP_ID_SQL} AS group_id
            FROM board_notifications
            WHERE {' AND '.join(where_clauses)}
        ) AS grouped
    """
    try:
        count = int(await db.fetchval(query, *params) or 0)
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e

    await set_cache(cache_key, count, ttl=60)
    return count


async def get_board_notification_context(
    db: asyncpg.Connection,
    user_id: int | None,
) -> dict[str, Any]:
    if not user_id:
        return empty_board_notification_context()

    settings = await get_notification_settings(db, user_id)
    if not settings.get("notification_badge_enabled"):
        return empty_board_notification_context()

    unread_count = await get_unread_badge_count(db, user_id)
    show_badge = unread_count > 0
    badge_text = f"{unread_count}" if unread_count < 100 else "99+"
    return {
        "unread_badge_count": unread_count,
        "show_notification_badge": show_badge,
        "notification_badge_text": badge_text if show_badge else "",
    }
