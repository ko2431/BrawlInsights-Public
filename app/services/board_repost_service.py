import datetime
from typing import Any

import asyncpg

from app.core.cache import delete_cache, set_cache
from app.core.logger import logger
from app.exceptions.custom_exceptions import DataBaseError
from app.services.board_service import GENERAL_POSTS_CACHE_VERSION_KEY, GENERAL_POSTS_CACHE_VERSION_TTL_SECONDS
from app.services.user_service import User, try_claim_tutorial_mission
from app.utils.utils import format_utc_datetime


#* /---*---*---*---*---*---*---*---*/
#* なんでも掲示板の投稿のリポスト
#* /---*---*---*---*---*---*---*---*/
# リポストは新規投稿とはみなさない（投稿ミッション・投稿のクールダウンには関わらない）。
# 投稿者本人がトークンを使って「最新」のタイムラインの上に戻し、「話題」の経過時間もリポストした日時から数え直す。
GENERAL_POST_REPOST_COST = 10
# 同じユーザーのリポストは、どの投稿かに関わらずこの間隔を空ける
GENERAL_POST_REPOST_USER_INTERVAL_SECONDS = 30 * 60
# 投稿してからの経過時間ごとの、前回の掲載（投稿・リポスト）から次にリポストできるまでの間隔。
# (経過時間の上限[時間], 間隔[分])。経過時間が上限以内ならその段。どの段にも入らなければ最後の間隔
GENERAL_POST_REPOST_COOLDOWN_TIERS: tuple[tuple[int, int], ...] = (
    (6, 30),
    (12, 60),
    (24, 2 * 60),
    (2 * 24, 3 * 60),
    (3 * 24, 6 * 60),
    (7 * 24, 12 * 60),
    (14 * 24, 24 * 60),
)
GENERAL_POST_REPOST_COOLDOWN_MAX_MINUTES = 48 * 60


def _as_utc(dt: datetime.datetime) -> datetime.datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def next_post_repost_at(created_at: datetime.datetime, reposted_at: datetime.datetime | None) -> datetime.datetime:
    """この投稿を次にリポストできる日時（投稿単位の間隔だけで判定）。

    間隔の段は、リポストしようとした時点での投稿からの経過時間で決まる。段が上がるほど間隔は長くなるので、
    「前回の掲載から経過した時間 >= その時点の段の間隔」を満たす最も早い時刻を、段の順に探せば一意に決まる。
    """
    created = _as_utc(created_at)
    last = _as_utc(reposted_at) if reposted_at else created
    lower = datetime.timedelta(0)
    for upper_hours, interval_minutes in GENERAL_POST_REPOST_COOLDOWN_TIERS:
        upper = datetime.timedelta(hours=upper_hours)
        candidate = max(last + datetime.timedelta(minutes=interval_minutes), created + lower)
        if candidate - created <= upper:
            return candidate
        lower = upper
    return max(
        last + datetime.timedelta(minutes=GENERAL_POST_REPOST_COOLDOWN_MAX_MINUTES),
        created + lower,
    )


def _next_user_repost_at(last_user_repost_at: datetime.datetime | None) -> datetime.datetime | None:
    if not last_user_repost_at:
        return None
    return _as_utc(last_user_repost_at) + datetime.timedelta(seconds=GENERAL_POST_REPOST_USER_INTERVAL_SECONDS)


def format_repost_wait_text(seconds: float, lang: str) -> str:
    """「あと◯時間◯分」の部分。1分未満は1分に切り上げる。"""
    minutes = max(1, int((seconds + 59) // 60))
    hours, minutes = divmod(minutes, 60)
    if lang == "ja":
        if hours and minutes:
            return f"{hours}時間{minutes}分"
        return f"{hours}時間" if hours else f"{minutes}分"
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return " ".join(parts)


class PostRepostError(Exception):
    def __init__(self, code: str, message_ja: str, message_en: str) -> None:
        super().__init__(message_ja)
        self.code = code
        self.message_ja = message_ja
        self.message_en = message_en

    def client_message(self, lang: str) -> str:
        return self.message_ja if lang == "ja" else self.message_en


def _repost_error(code: str, wait_seconds: float = 0) -> PostRepostError:
    wait_ja = format_repost_wait_text(wait_seconds, "ja")
    wait_en = format_repost_wait_text(wait_seconds, "en")
    messages = {
        "not_found": ("投稿が見つかりませんでした。", "Post not found."),
        "forbidden": ("自分の投稿だけリポストできます。", "You can only repost your own posts."),
        "closed": ("このチャットは閉鎖されているため、リポストできません。", "This chat has been closed, so it can't be reposted."),
        "prohibit_posting": ("投稿が制限されているため、リポストできません。", "You can't repost because posting is restricted on your account."),
        "cooldown": (
            f"この投稿は、あと{wait_ja}でリポストできます。",
            f"You can repost this post in {wait_en}.",
        ),
        "user_interval": (
            f"リポストは30分に1回までです。あと{wait_ja}でリポストできます。",
            f"You can repost once every 30 minutes. You can repost again in {wait_en}.",
        ),
        "insufficient_tokens": (
            f"トークンが足りないため、リポストできません（{GENERAL_POST_REPOST_COST}トークン必要です）。",
            f"You don't have enough tokens to repost ({GENERAL_POST_REPOST_COST} tokens needed).",
        ),
    }
    message_ja, message_en = messages.get(code, messages["not_found"])
    return PostRepostError(code, message_ja, message_en)


async def _get_last_user_repost_at(db: asyncpg.Connection, user_id: int) -> datetime.datetime | None:
    try:
        return await db.fetchval(
            "SELECT created_at FROM post_reposts WHERE user_id = $1 ORDER BY created_at DESC LIMIT 1",
            user_id,
        )
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e


def _is_repostable_post(post: Any, user: User | None) -> bool:
    return bool(
        user
        and post.type == "general"
        and post.host_id == user.id
        and not post.is_deleted
        and post.chat_permission_level != 0
    )


def _build_repost_state(post: Any, user: User, next_user_at: datetime.datetime | None) -> dict[str, Any]:
    """カード・チャットのメニューに埋め込む状態。時刻で変わる判定はフロントでも行う。"""
    return {
        "cost": GENERAL_POST_REPOST_COST,
        "tokens": int(user.tokens or 0),
        "prohibited": bool(user.is_prohibit_posting),
        "post_available_at": format_utc_datetime(next_post_repost_at(post.created_at, post.reposted_at)),
        "user_available_at": format_utc_datetime(next_user_at) if next_user_at else None,
    }


async def get_general_post_repost_states(
    db: asyncpg.Connection,
    user: User | None,
    posts: list[Any],
) -> dict[int, dict[str, Any]]:
    """表示中の投稿のうち、リポストできる自分の投稿の状態をまとめて返す（自分の投稿がなければ問い合わせない）。"""
    own_posts = [post for post in posts if _is_repostable_post(post, user)]
    if not own_posts:
        return {}
    next_user_at = _next_user_repost_at(await _get_last_user_repost_at(db, user.id))
    return {post.id: _build_repost_state(post, user, next_user_at) for post in own_posts}


async def repost_general_post(
    db: asyncpg.Connection,
    *,
    post_id: int,
    user: User,
) -> dict[str, Any]:
    """なんでも掲示板の自分の投稿をリポストする。トークンを使い、reposted_at を今の日時にする。

    Raises:
        PostRepostError: リポストできない場合
        DataBaseError: データベースエラー
    """
    async with db.transaction():
        try:
            # ユーザーの行をロックして、同じユーザーのリポスト・トークン消費を直列にする
            user_row = await db.fetchrow(
                "SELECT id, tokens, is_prohibit_posting FROM users WHERE id = $1 FOR UPDATE",
                user.id,
            )
            post_row = await db.fetchrow(
                """
                SELECT id, type, host_id, is_deleted, chat_permission_level, created_at, reposted_at
                FROM posts
                WHERE id = $1
                FOR UPDATE
                """,
                post_id,
            )
            now = await db.fetchval("SELECT NOW()")
        except asyncpg.PostgresError as e:
            raise DataBaseError(e) from e

        if not user_row or not post_row or post_row["is_deleted"] or post_row["type"] != "general":
            raise _repost_error("not_found")
        if post_row["host_id"] != user.id:
            raise _repost_error("forbidden")
        if post_row["chat_permission_level"] == 0:
            raise _repost_error("closed")
        if user_row["is_prohibit_posting"]:
            raise _repost_error("prohibit_posting")

        next_post_at = next_post_repost_at(post_row["created_at"], post_row["reposted_at"])
        if now < next_post_at:
            raise _repost_error("cooldown", (next_post_at - now).total_seconds())
        next_user_at = _next_user_repost_at(await _get_last_user_repost_at(db, user.id))
        if next_user_at and now < next_user_at:
            raise _repost_error("user_interval", (next_user_at - now).total_seconds())

        tokens_before = int(user_row["tokens"])
        if tokens_before < GENERAL_POST_REPOST_COST:
            raise _repost_error("insufficient_tokens")

        try:
            tokens_after = await db.fetchval(
                "UPDATE users SET tokens = tokens - $1 WHERE id = $2 RETURNING tokens",
                GENERAL_POST_REPOST_COST,
                user.id,
            )
            await db.execute("UPDATE posts SET reposted_at = $1 WHERE id = $2", now, post_id)
            await db.execute(
                "INSERT INTO post_reposts (post_id, user_id, tokens_spent, created_at) VALUES ($1, $2, $3, $4)",
                post_id,
                user.id,
                GENERAL_POST_REPOST_COST,
                now,
            )
        except asyncpg.PostgresError as e:
            raise DataBaseError(e) from e

    user.tokens = tokens_after
    await delete_cache(f"user:{user.id}")
    await delete_cache(f"user_include_invalid:{user.id}")
    await delete_cache(f"post:{post_id}")
    await set_cache(
        GENERAL_POSTS_CACHE_VERSION_KEY,
        str(int(now.timestamp() * 1000)),
        ttl=GENERAL_POSTS_CACHE_VERSION_TTL_SECONDS,
    )
    logger.info(
        f"{user.name} (ID: {user.id}) が投稿(ID: {post_id})をリポストしました。"
        f"トークン: {tokens_before} → {tokens_after}"
    )
    await try_claim_tutorial_mission(user, db, "spend_tokens")

    next_user_at = _next_user_repost_at(now)
    return {
        "tokens_before": tokens_before,
        "tokens_after": int(tokens_after),
        "reposted_at": format_utc_datetime(now),
        "post_available_at": format_utc_datetime(next_post_repost_at(post_row["created_at"], now)),
        "user_available_at": format_utc_datetime(next_user_at),
    }
