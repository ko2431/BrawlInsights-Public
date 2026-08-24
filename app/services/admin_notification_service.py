"""管理者向け通知センターの発行・既読・一覧。掲示板通知とは別系統。"""
from __future__ import annotations

import datetime
import re
from typing import Any, NamedTuple

import asyncpg

from app.core.cache import delete_cache, get_cache, get_redis, set_cache
from app.core.logger import logger
from app.db.db import get_db_connection_for_bg_task

ADMIN_NOTIFICATION_PAGE_SIZE = 20
ADMIN_NOTIFICATION_BADGE_TTL = 60
ADMIN_NOTIFICATION_LEVELS_TTL = 60
ADMIN_NOTIFICATION_LEVELS = frozenset({0, 10, 20, 30})
BADGE_CACHE_PREFIX = "admin_notification_badge_count:"
EVENT_LEVELS_CACHE_KEY = "admin_notification_event_levels"
SCHEDULE_GRACE = datetime.timedelta(hours=6)

POST_TYPE_LABELS_JA = {
    "team": "チーム募集",
    "friend": "フレンド募集",
    "club": "クラブ募集",
    "general": "なんでも掲示板",
}

ADMIN_PATH_SLUG_TO_CATEGORY = {
    "logs": "logs",
    "announcements": "announcements",
    "faqs": "faqs",
    "giftcodes": "giftcodes",
    "feedbacks": "feedbacks",
    "reports": "reports",
    "users": "users",
    "posts": "posts",
    "messages": "messages",
    "reactions": "reactions",
    "blocks": "blocks",
    "purchases": "purchases",
    "image-generation-jobs": "image_generation_jobs",
    "minigame-campaigns": "minigame_campaigns",
    "minigame-plays": "minigame_plays",
    "brawl_videos": "brawl_videos",
    "brawlers": "brawlers",
    "skins": "skins",
    "modes": "modes",
    "maps": "maps",
    "titles": "titles",
    "frames": "frames",
    "accessories": "accessories",
    "pins": "pins",
    "regions": "regions",
    "secretquestions": "secretquestions",
}


class AdminNotificationEvent(NamedTuple):
    key: str
    category: str
    label: str
    default_level: int
    target_path: str


EVENT_CATALOG: tuple[AdminNotificationEvent, ...] = (
    AdminNotificationEvent("log_warning", "logs", "WARNINGログ", 20, "/admin/logs"),
    AdminNotificationEvent("log_error", "logs", "ERRORログ", 30, "/admin/logs"),
    AdminNotificationEvent("log_critical", "logs", "CRITICALログ", 30, "/admin/logs"),
    AdminNotificationEvent("announcement_created", "announcements", "アナウンス追加", 10, "/admin/announcements"),
    AdminNotificationEvent("announcement_updated", "announcements", "アナウンス変更", 10, "/admin/announcements"),
    AdminNotificationEvent("announcement_deleted", "announcements", "アナウンス削除", 10, "/admin/announcements"),
    AdminNotificationEvent("announcement_published", "announcements", "アナウンス配信時刻", 20, "/admin/announcements"),
    AdminNotificationEvent("faq_created", "faqs", "FAQ追加", 10, "/admin/faqs"),
    AdminNotificationEvent("faq_updated", "faqs", "FAQ変更", 10, "/admin/faqs"),
    AdminNotificationEvent("giftcode_used", "giftcodes", "ギフトコード使用", 10, "/admin/giftcodes?filter=valid"),
    AdminNotificationEvent("giftcode_giveaway_used", "giftcodes", "プレゼント企画コード使用", 0, "/admin/giftcodes?filter=valid"),
    AdminNotificationEvent("giftcode_created", "giftcodes", "ギフトコード追加", 10, "/admin/giftcodes?filter=valid"),
    AdminNotificationEvent("giftcode_updated", "giftcodes", "ギフトコード変更", 10, "/admin/giftcodes"),
    AdminNotificationEvent("giftcode_limit_reached", "giftcodes", "ギフトコード総利用上限", 20, "/admin/giftcodes?filter=valid"),
    AdminNotificationEvent("feedback_created", "feedbacks", "新しいフィードバック", 30, "/admin/feedbacks"),
    AdminNotificationEvent("feedback_checked", "feedbacks", "フィードバックのチェック更新", 10, "/admin/feedbacks"),
    AdminNotificationEvent("report_created", "reports", "新しい通報", 30, "/admin/reports"),
    AdminNotificationEvent("report_checked", "reports", "通報のチェック更新", 10, "/admin/reports"),
    AdminNotificationEvent("user_created", "users", "新しいユーザー登録", 10, "/admin/users"),
    AdminNotificationEvent("user_invalidated", "users", "アカウント無効化", 20, "/admin/users"),
    AdminNotificationEvent("user_updated_by_admin", "users", "ユーザー情報の強制編集", 10, "/admin/users"),
    AdminNotificationEvent("post_created", "posts", "新しい投稿", 10, "/admin/posts"),
    AdminNotificationEvent("post_closed_by_admin", "posts", "投稿をクローズ", 10, "/admin/posts"),
    AdminNotificationEvent("post_deleted_by_admin", "posts", "投稿を削除", 10, "/admin/posts"),
    AdminNotificationEvent("message_created", "messages", "新しいメッセージ", 0, "/admin/messages"),
    AdminNotificationEvent("message_deleted_by_admin", "messages", "メッセージ削除", 10, "/admin/messages"),
    AdminNotificationEvent("reaction_created", "reactions", "新しいリアクション", 0, "/admin/reactions"),
    AdminNotificationEvent("reaction_deleted", "reactions", "リアクション削除", 0, "/admin/reactions"),
    AdminNotificationEvent("block_created", "blocks", "新しいブロック", 10, "/admin/blocks"),
    AdminNotificationEvent("block_deleted", "blocks", "ブロック解除", 10, "/admin/blocks"),
    AdminNotificationEvent("purchase_new", "purchases", "新しい購入", 30, "/admin/purchases"),
    AdminNotificationEvent("purchase_refund", "purchases", "購入のキャンセル／返金", 30, "/admin/purchases"),
    AdminNotificationEvent("purchase_other", "purchases", "その他の購入イベント", 10, "/admin/purchases"),
    AdminNotificationEvent("image_job_completed", "image_generation_jobs", "画像生成成功", 0, "/admin/image-generation-jobs"),
    AdminNotificationEvent("image_job_failed", "image_generation_jobs", "画像生成失敗", 30, "/admin/image-generation-jobs"),
    AdminNotificationEvent("minigame_campaign_created", "minigame_campaigns", "ミニゲーム企画追加", 10, "/admin/minigame-campaigns"),
    AdminNotificationEvent("minigame_campaign_updated", "minigame_campaigns", "ミニゲーム企画変更", 10, "/admin/minigame-campaigns"),
    AdminNotificationEvent("minigame_started", "minigame_campaigns", "ミニゲーム開始", 20, "/admin/minigame-campaigns"),
    AdminNotificationEvent("minigame_ended", "minigame_campaigns", "ミニゲーム終了", 20, "/admin/minigame-campaigns"),
    AdminNotificationEvent("minigame_stock_empty", "minigame_plays", "ギフト在庫切れ", 20, "/admin/minigame-plays"),
    AdminNotificationEvent("minigame_play", "minigame_plays", "ミニゲーム参加", 0, "/admin/minigame-plays"),
    AdminNotificationEvent("minigame_gift_won", "minigame_plays", "ギフト景品当選", 30, "/admin/minigame-plays"),
    AdminNotificationEvent("minigame_gift_status", "minigame_plays", "ギフト発送状態の変更", 10, "/admin/minigame-plays"),
    AdminNotificationEvent("brawl_video_created", "brawl_videos", "ブロスタ動画追加", 10, "/admin/brawl_videos"),
    AdminNotificationEvent("brawl_video_updated", "brawl_videos", "ブロスタ動画変更", 10, "/admin/brawl_videos"),
    AdminNotificationEvent("brawler_created", "brawlers", "新しいキャラクター追加", 20, "/admin/brawlers"),
    AdminNotificationEvent("brawler_updated", "brawlers", "キャラクター情報変更", 10, "/admin/brawlers"),
    AdminNotificationEvent("skin_created", "skins", "新しいスキン追加", 20, "/admin/skins"),
    AdminNotificationEvent("skin_updated", "skins", "スキン情報変更", 10, "/admin/skins"),
    AdminNotificationEvent("mode_created", "modes", "新しいゲームモード追加", 20, "/admin/modes"),
    AdminNotificationEvent("mode_updated", "modes", "ゲームモード情報変更", 10, "/admin/modes"),
    AdminNotificationEvent("map_created", "maps", "新しいマップ追加", 20, "/admin/maps"),
    AdminNotificationEvent("map_updated", "maps", "マップ情報変更", 10, "/admin/maps"),
    AdminNotificationEvent("title_created", "titles", "新しいキャッチフレーズ追加", 20, "/admin/titles"),
    AdminNotificationEvent("title_updated", "titles", "キャッチフレーズ情報変更", 10, "/admin/titles"),
    AdminNotificationEvent("frame_created", "frames", "新しいバトルカード背景追加", 20, "/admin/frames"),
    AdminNotificationEvent("frame_updated", "frames", "バトルカード背景情報変更", 10, "/admin/frames"),
    AdminNotificationEvent("accessory_created", "accessories", "新しいアクセサリ追加", 0, "/admin/accessories"),
    AdminNotificationEvent("accessory_updated", "accessories", "アクセサリ情報変更", 10, "/admin/accessories"),
    AdminNotificationEvent("pin_created", "pins", "新しいピンズ追加", 0, "/admin/pins"),
    AdminNotificationEvent("pin_updated", "pins", "ピンズ情報変更", 10, "/admin/pins"),
    AdminNotificationEvent("region_created", "regions", "地域情報追加", 10, "/admin/regions"),
    AdminNotificationEvent("region_updated", "regions", "地域情報変更", 10, "/admin/regions"),
    AdminNotificationEvent("secretquestion_created", "secretquestions", "秘密の質問追加", 10, "/admin/secretquestions"),
    AdminNotificationEvent("secretquestion_updated", "secretquestions", "秘密の質問変更", 10, "/admin/secretquestions"),
)

EVENT_BY_KEY: dict[str, AdminNotificationEvent] = {event.key: event for event in EVENT_CATALOG}

CATEGORY_LABELS: dict[str, str] = {
    "logs": "ログ確認",
    "announcements": "アナウンス管理",
    "faqs": "FAQ管理",
    "giftcodes": "ギフトコード管理",
    "feedbacks": "フィードバック確認",
    "reports": "通報確認",
    "users": "ユーザー管理",
    "posts": "投稿確認",
    "messages": "メッセージ確認",
    "reactions": "リアクション確認",
    "blocks": "ブロック確認",
    "purchases": "購入情報確認",
    "image_generation_jobs": "画像生成履歴確認",
    "minigame_campaigns": "ミニゲーム企画管理",
    "minigame_plays": "ミニゲーム参加履歴",
    "brawl_videos": "ブロスタ動画管理",
    "brawlers": "キャラクター基本情報管理",
    "skins": "スキン情報管理",
    "modes": "ゲームモード情報管理",
    "maps": "マップ情報管理",
    "titles": "キャッチフレーズ情報管理",
    "frames": "バトルカード背景情報管理",
    "accessories": "アクセサリ情報管理",
    "pins": "ピンズ情報管理",
    "regions": "地域情報管理",
    "secretquestions": "秘密の質問管理",
}


def empty_admin_notification_badge_context() -> dict[str, Any]:
    return {
        "show_admin_notification_badge": False,
        "admin_notification_badge_text": "",
    }


def format_admin_user_label(name: str | None, user_id: int | None) -> str:
    if name and user_id is not None:
        return f"{name} (ID: {user_id})"
    if user_id is not None:
        return f"ID: {user_id}"
    if name:
        return name
    return "ゲスト"


def clip_admin_notification_text(value: str | None, max_len: int) -> str:
    text_value = re.sub(r"\s+", " ", (value or "").strip())
    if len(text_value) <= max_len:
        return text_value
    return text_value[: max(0, max_len - 1)] + "…"


def format_admin_notification_datetime(dt: datetime.datetime | None) -> str:
    if dt is None:
        return "-"
    jst = datetime.timezone(datetime.timedelta(hours=9))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    jst_dt = dt.astimezone(jst)
    now_jst = datetime.datetime.now(jst)
    time_str = jst_dt.strftime("%H:%M:%S")
    if jst_dt.date() == now_jst.date():
        return f"今日 {time_str}"
    if jst_dt.date() == (now_jst.date() - datetime.timedelta(days=1)):
        return f"昨日 {time_str}"
    if jst_dt.year == now_jst.year:
        return jst_dt.strftime("%m-%d %H:%M:%S")
    return jst_dt.strftime("%Y-%m-%d %H:%M:%S")


def _badge_cache_key(user_id: int) -> str:
    return f"{BADGE_CACHE_PREFIX}{user_id}"


def _log_internal(message: str, exc: BaseException | None = None) -> None:
    logger.warning(
        message,
        exc_info=exc is not None,
        extra={"skip_admin_notification": True},
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def events_grouped_for_settings() -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    by_category: dict[str, list[AdminNotificationEvent]] = {}
    for event in EVENT_CATALOG:
        by_category.setdefault(event.category, []).append(event)
    for category, events in by_category.items():
        groups.append({
            "category": category,
            "label": CATEGORY_LABELS.get(category, category),
            "events": [
                {
                    "key": event.key,
                    "label": event.label,
                    "default_level": event.default_level,
                }
                for event in events
            ],
        })
    return groups


def category_options() -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    seen: set[str] = set()
    for event in EVENT_CATALOG:
        if event.category in seen:
            continue
        seen.add(event.category)
        options.append({"key": event.category, "label": CATEGORY_LABELS.get(event.category, event.category)})
    return options


async def invalidate_admin_notification_badge_cache() -> None:
    r = get_redis()
    if not r:
        return
    try:
        async for key_bytes in r.scan_iter(match=f"{BADGE_CACHE_PREFIX}*"):
            await r.delete(key_bytes)
    except Exception as e:
        _log_internal(f"管理者通知バッジキャッシュの削除に失敗しました: {e}", e)


async def get_event_levels(db: asyncpg.Connection) -> dict[str, int]:
    cached = await get_cache(EVENT_LEVELS_CACHE_KEY)
    if isinstance(cached, dict):
        levels = {event.key: event.default_level for event in EVENT_CATALOG}
        for key, value in cached.items():
            try:
                level = int(value)
            except (TypeError, ValueError):
                continue
            if key in EVENT_BY_KEY and level in ADMIN_NOTIFICATION_LEVELS:
                levels[key] = level
        return levels

    levels = {event.key: event.default_level for event in EVENT_CATALOG}
    try:
        rows = await db.fetch("SELECT event_key, level FROM admin_notification_event_settings")
    except asyncpg.PostgresError as e:
        _log_internal(f"管理者通知レベルの取得に失敗しました: {e}", e)
        return levels

    for row in rows:
        key = row["event_key"]
        level = int(row["level"])
        if key in EVENT_BY_KEY and level in ADMIN_NOTIFICATION_LEVELS:
            levels[key] = level

    await set_cache(EVENT_LEVELS_CACHE_KEY, levels, ttl=ADMIN_NOTIFICATION_LEVELS_TTL)
    return levels


async def ensure_event_settings(db: asyncpg.Connection) -> dict[str, int]:
    levels = await get_event_levels(db)
    try:
        await db.executemany(
            """
            INSERT INTO admin_notification_event_settings (event_key, level)
            VALUES ($1, $2)
            ON CONFLICT (event_key) DO NOTHING
            """,
            [(event.key, event.default_level) for event in EVENT_CATALOG],
        )
    except asyncpg.PostgresError as e:
        _log_internal(f"管理者通知レベル設定の初期化に失敗しました: {e}", e)
        return levels
    await delete_cache(EVENT_LEVELS_CACHE_KEY)
    return await get_event_levels(db)


async def save_event_levels(db: asyncpg.Connection, updates: dict[str, int]) -> dict[str, int]:
    rows: list[tuple[str, int]] = []
    for key, raw_level in updates.items():
        if key not in EVENT_BY_KEY:
            continue
        try:
            level = int(raw_level)
        except (TypeError, ValueError):
            continue
        if level not in ADMIN_NOTIFICATION_LEVELS:
            continue
        rows.append((key, level))
    if rows:
        try:
            await db.executemany(
                """
                INSERT INTO admin_notification_event_settings (event_key, level, updated_at)
                VALUES ($1, $2, NOW())
                ON CONFLICT (event_key) DO UPDATE SET
                    level = EXCLUDED.level,
                    updated_at = NOW()
                """,
                rows,
            )
        except asyncpg.PostgresError as e:
            _log_internal(f"管理者通知レベル設定の保存に失敗しました: {e}", e)
            raise
        await delete_cache(EVENT_LEVELS_CACHE_KEY)
        await invalidate_admin_notification_badge_cache()
    return await get_event_levels(db)


async def emit_admin_notification(
    db: asyncpg.Connection | None,
    event_key: str,
    *,
    title: str,
    summary: str = "",
    actor_user_id: int | None = None,
    payload: dict[str, Any] | None = None,
    target_path: str | None = None,
    dedupe_key: str | None = None,
) -> int | None:
    """通知を1件INSERTする。失敗しても呼び出し元の処理は落とさない。"""
    event = EVENT_BY_KEY.get(event_key)
    if event is None:
        return None

    own_connection = db is None
    try:
        if own_connection:
            async with get_db_connection_for_bg_task() as conn:
                return await _insert_admin_notification(
                    conn,
                    event,
                    title=title,
                    summary=summary,
                    actor_user_id=actor_user_id,
                    payload=payload,
                    target_path=target_path,
                    dedupe_key=dedupe_key,
                )
        assert db is not None
        return await _insert_admin_notification(
            db,
            event,
            title=title,
            summary=summary,
            actor_user_id=actor_user_id,
            payload=payload,
            target_path=target_path,
            dedupe_key=dedupe_key,
        )
    except Exception as e:
        _log_internal(f"管理者通知の発行に失敗しました (event_key={event_key}): {e}", e)
        return None


async def _insert_admin_notification(
    db: asyncpg.Connection,
    event: AdminNotificationEvent,
    *,
    title: str,
    summary: str,
    actor_user_id: int | None,
    payload: dict[str, Any] | None,
    target_path: str | None,
    dedupe_key: str | None,
) -> int | None:
    levels = await get_event_levels(db)
    level = levels.get(event.key, event.default_level)
    if level <= 0:
        return None

    row = await db.fetchrow(
        """
        INSERT INTO admin_notifications (
            category, event_key, level, title, summary, payload, target_path, actor_user_id, dedupe_key
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (event_key, dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING
        RETURNING id
        """,
        event.category,
        event.key,
        level,
        clip_admin_notification_text(title, 200) or event.label,
        clip_admin_notification_text(summary, 500),
        payload or {},
        target_path or event.target_path,
        actor_user_id,
        dedupe_key,
    )
    if row is None:
        return None
    await invalidate_admin_notification_badge_cache()
    return int(row["id"])


async def mark_dashboard_read(db: asyncpg.Connection, admin_user_id: int) -> None:
    try:
        await db.execute(
            "UPDATE users SET admin_notifications_dashboard_read_at = NOW() WHERE id = $1",
            admin_user_id,
        )
        await delete_cache(_badge_cache_key(admin_user_id))
    except asyncpg.PostgresError as e:
        _log_internal(f"管理者通知の総合画面既読更新に失敗しました (user={admin_user_id}): {e}", e)


async def mark_category_visited(db: asyncpg.Connection, admin_user_id: int, category: str) -> None:
    if category not in CATEGORY_LABELS:
        return
    try:
        await db.execute(
            """
            INSERT INTO admin_notification_category_reads (admin_user_id, category, visited_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (admin_user_id, category) DO UPDATE SET visited_at = NOW()
            """,
            admin_user_id,
            category,
        )
        await delete_cache(_badge_cache_key(admin_user_id))
    except asyncpg.PostgresError as e:
        _log_internal(
            f"管理者通知のカテゴリ既読更新に失敗しました (user={admin_user_id}, category={category}): {e}",
            e,
        )


def resolve_admin_page_visit(path: str) -> str | None:
    """HTML管理画面の訪問種別。'dashboard' / カテゴリキー / 対象外は None。"""
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2 or parts[1] != "admin":
        return None
    if len(parts) == 2:
        return "dashboard"
    if len(parts) != 3:
        return None
    slug = parts[2]
    if slug in {"api", "stats", "notification-settings"}:
        return None
    return ADMIN_PATH_SLUG_TO_CATEGORY.get(slug)


async def apply_admin_page_visit(db: asyncpg.Connection, admin_user_id: int, path: str) -> None:
    visit = resolve_admin_page_visit(path)
    if visit == "dashboard":
        await mark_dashboard_read(db, admin_user_id)
    elif visit:
        await mark_category_visited(db, admin_user_id, visit)


async def count_unread_admin_notifications(db: asyncpg.Connection, admin_user_id: int) -> int:
    try:
        count = await db.fetchval(
            """
            SELECT COUNT(*)::int
            FROM admin_notifications n
            LEFT JOIN admin_notification_category_reads r
              ON r.admin_user_id = $1 AND r.category = n.category
            JOIN users u ON u.id = $1
            WHERE n.level >= 20
              AND (
                (n.level = 20 AND n.created_at > COALESCE(u.admin_notifications_dashboard_read_at, '-infinity'::timestamptz))
                OR (n.level >= 30 AND n.created_at > COALESCE(r.visited_at, '-infinity'::timestamptz))
              )
            """,
            admin_user_id,
        )
        return int(count or 0)
    except asyncpg.PostgresError as e:
        _log_internal(f"管理者通知の未読件数取得に失敗しました (user={admin_user_id}): {e}", e)
        return 0


async def get_admin_notification_badge_context(
    db: asyncpg.Connection,
    user_id: int | None,
) -> dict[str, Any]:
    if not user_id:
        return empty_admin_notification_badge_context()

    cache_key = _badge_cache_key(user_id)
    cached = await get_cache(cache_key)
    if isinstance(cached, int):
        count = cached
    else:
        count = await count_unread_admin_notifications(db, user_id)
        await set_cache(cache_key, count, ttl=ADMIN_NOTIFICATION_BADGE_TTL)

    show_badge = count > 0
    badge_text = f"{count}" if count < 100 else "99+"
    return {
        "show_admin_notification_badge": show_badge,
        "admin_notification_badge_text": badge_text if show_badge else "",
    }


def _parse_filter_datetime(value: str | None) -> datetime.datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        parsed = datetime.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        jst = datetime.timezone(datetime.timedelta(hours=9))
        parsed = parsed.replace(tzinfo=jst)
    return parsed.astimezone(datetime.timezone.utc)


async def list_admin_notifications(
    db: asyncpg.Connection,
    *,
    admin_user_id: int,
    lang: str = "ja",
    level: str | None = None,
    category: str | None = None,
    text: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    before_id: int | None = None,
    limit: int = ADMIN_NOTIFICATION_PAGE_SIZE,
) -> dict[str, Any]:
    page_size = max(1, min(int(limit or ADMIN_NOTIFICATION_PAGE_SIZE), 50))
    where = ["TRUE"]
    params: list[Any] = []

    if level and level != "all":
        try:
            level_int = int(level)
        except (TypeError, ValueError):
            level_int = None
        if level_int in {10, 20, 30}:
            params.append(level_int)
            where.append(f"n.level = ${len(params)}")

    if category and category != "all" and category in CATEGORY_LABELS:
        params.append(category)
        where.append(f"n.category = ${len(params)}")

    text_query = (text or "").strip()
    if text_query:
        params.append(f"%{_escape_like(text_query)}%")
        where.append(
            f"(n.title ILIKE ${len(params)} ESCAPE '\\' OR n.summary ILIKE ${len(params)} ESCAPE '\\')"
        )

    after_dt = _parse_filter_datetime(created_after)
    if after_dt is not None:
        params.append(after_dt)
        where.append(f"n.created_at >= ${len(params)}")

    before_dt = _parse_filter_datetime(created_before)
    if before_dt is not None:
        params.append(before_dt)
        where.append(f"n.created_at <= ${len(params)}")

    total_count: int | None = None
    if before_id is None:
        count_sql = f"SELECT COUNT(*)::int FROM admin_notifications n WHERE {' AND '.join(where)}"
        try:
            total_count = int(await db.fetchval(count_sql, *params) or 0)
        except asyncpg.PostgresError as e:
            _log_internal(f"管理者通知件数の取得に失敗しました: {e}", e)
            total_count = 0

    if before_id is not None:
        params.append(int(before_id))
        where.append(f"n.id < ${len(params)}")

    params.append(admin_user_id)
    admin_id_placeholder = f"${len(params)}"
    params.append(page_size + 1)
    limit_placeholder = f"${len(params)}"

    sql = f"""
        SELECT n.id, n.created_at, n.category, n.event_key, n.level, n.title, n.summary, n.target_path,
               CASE
                 WHEN n.level >= 30 THEN n.created_at > COALESCE(r.visited_at, '-infinity'::timestamptz)
                 WHEN n.level = 20 THEN n.created_at > COALESCE(u.admin_notifications_dashboard_read_at, '-infinity'::timestamptz)
                 ELSE FALSE
               END AS unread
        FROM admin_notifications n
        JOIN users u ON u.id = {admin_id_placeholder}
        LEFT JOIN admin_notification_category_reads r
          ON r.admin_user_id = {admin_id_placeholder} AND r.category = n.category
        WHERE {' AND '.join(where)}
        ORDER BY n.id DESC
        LIMIT {limit_placeholder}
    """
    try:
        rows = await db.fetch(sql, *params)
    except asyncpg.PostgresError as e:
        _log_internal(f"管理者通知一覧の取得に失敗しました: {e}", e)
        return {"items": [], "has_more": False, "total_count": total_count or 0}

    has_more = len(rows) > page_size
    rows = rows[:page_size]
    items = []
    for row in rows:
        target_path = row["target_path"] or "/"
        href = target_path if target_path.startswith("http") else f"/{lang}{target_path}"
        created_at = row["created_at"]
        items.append({
            "id": row["id"],
            "createdAt": created_at.isoformat() if created_at else None,
            "timeText": format_admin_notification_datetime(created_at),
            "level": int(row["level"]),
            "unread": bool(row["unread"]),
            "category": row["category"],
            "categoryLabel": CATEGORY_LABELS.get(row["category"], row["category"]),
            "title": row["title"],
            "summary": row["summary"] or "",
            "href": href,
        })
    return {
        "items": items,
        "has_more": has_more,
        "total_count": total_count,
    }


async def poll_admin_notification_schedule_events(db: asyncpg.Connection) -> None:
    """配信時刻・ミニゲーム開始/終了をポーリングして通知する。"""
    now = datetime.datetime.now(datetime.timezone.utc)
    since = now - SCHEDULE_GRACE

    try:
        announcements = await db.fetch(
            """
            SELECT id, ja_title, en_title, datetime
            FROM announcements
            WHERE datetime <= $1 AND datetime >= $2
            """,
            now,
            since,
        )
    except asyncpg.PostgresError as e:
        _log_internal(f"アナウンス配信時刻のポーリングに失敗しました: {e}", e)
        announcements = []

    for row in announcements:
        title_text = (row["ja_title"] or row["en_title"] or "").strip() or str(row["id"])
        await emit_admin_notification(
            db,
            "announcement_published",
            title="アナウンスの配信時刻になりました",
            summary=f"ID {row['id']}「{clip_admin_notification_text(title_text, 80)}」の表示を開始しました。",
            payload={"announcement_id": row["id"]},
            dedupe_key=f"announcement_published:{row['id']}",
        )

    try:
        campaigns = await db.fetch(
            """
            SELECT id, name_ja, name_en, starts_at, ends_at
            FROM minigame_campaigns
            WHERE (starts_at <= $1 AND starts_at >= $2)
               OR (ends_at <= $1 AND ends_at >= $2)
            """,
            now,
            since,
        )
    except asyncpg.PostgresError as e:
        _log_internal(f"ミニゲーム開始/終了のポーリングに失敗しました: {e}", e)
        campaigns = []

    for row in campaigns:
        name = (row["name_ja"] or row["name_en"] or f"ID {row['id']}").strip()
        if row["starts_at"] and since <= row["starts_at"] <= now:
            await emit_admin_notification(
                db,
                "minigame_started",
                title="ミニゲームが開始しました",
                summary=f"企画「{clip_admin_notification_text(name, 80)}」の開始時刻になりました。",
                payload={"campaign_id": row["id"]},
                dedupe_key=f"minigame_started:{row['id']}",
            )
        if row["ends_at"] and since <= row["ends_at"] <= now:
            await emit_admin_notification(
                db,
                "minigame_ended",
                title="ミニゲームが終了しました",
                summary=f"企画「{clip_admin_notification_text(name, 80)}」の終了時刻になりました。",
                payload={"campaign_id": row["id"]},
                dedupe_key=f"minigame_ended:{row['id']}",
            )
