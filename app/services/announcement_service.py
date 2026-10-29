"""お知らせ（アナウンス）の読み書き。

検討メモ notes_private/announcement_plan_2026-10-29.md の 3章（データ）・6章（管理画面）・7章（表示とキャッシュ）。
本文の HTML の決まりは announcement_html.py。

- ユーザー向け（ホーム・一覧・個別ページ）は Redis のキャッシュから返し、キャッシュ切れのときだけ
  プロセスごとに1件ずつ DB に問い合わせる。キャッシュのキーには「お知らせの版」を入れ、保存のたびに版を上げて古いものを使わなくする。
- 管理画面の関数は呼び出し元の接続を使う。保存・削除・復元はトランザクションの中で変更の履歴を1件足す。
"""
from __future__ import annotations

import asyncio
import datetime
import re
from typing import Any

import asyncpg

from app.core.cache import get_cache, get_redis, set_cache
from app.core.logger import logger
from app.db.db import get_db_connection_for_bg_task, load_serialized_within_request_timeout
from app.exceptions.custom_exceptions import DataBaseError
from app.services.announcement_html import (
    first_image_src,
    html_to_text,
    is_blank_body,
    render_html,
    sanitize_body,
    sanitize_title,
)

CATEGORIES: dict[int, tuple[str, str]] = {
    1: ("緊急のお知らせ", "Urgent Notice"),
    2: ("お知らせ", "Notice"),
    3: ("アップデート", "Update"),
    4: ("プレゼント企画", "Giveaway"),
    5: ("当選発表", "Giveaway Results"),
}
PLATFORM_COLUMNS = {"web": "show_web", "ios": "show_ios", "android": "show_android"}
LANGS = ("ja", "en")

HOME_LIMIT = 3
LIST_PAGE_SIZE = 20
LIST_MAX_ITEMS = 200  # 一覧を最初から出す件数の上限（「もっと見る」の後の戻る用。7.2）
LIST_MAX_OFFSET = 5000
CACHE_TTL = 60
LEGACY_CACHE_TTL = 24 * 60 * 60
CACHE_VERSION_KEY = "announcements:ver"
CACHE_PREFIX = "announcements:v2:"
ADMIN_PER_PAGE = 50
TITLE_MAX_TEXT = 400
BODY_MAX_HTML = 200_000
JST = datetime.timezone(datetime.timedelta(hours=9))

EDITABLE_FIELDS = (
    "category", "status", "published_at", "show_web", "show_ios", "show_android", "show_ja", "show_en",
    "on_home", "listed", "title_ja", "title_en", "body_ja", "body_en", "source_type", "source_id", "source_role",
)
TEMPLATE_FIELDS = ("kind", "name", "category", "title_ja", "title_en", "body_ja", "body_en", "settings", "sort_order")

_locks: dict[str, asyncio.Lock] = {}


class AnnouncementError(Exception):
    """管理画面に出す入力の誤り・状態の不一致（同時編集など）。"""

    def __init__(self, message: str, *, code: str = "invalid") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


# --- 共通 -----------------------------------------------------------------------------
def normalize_platform(platform: str | None) -> str:
    """request.state.platform を、掲載の判定に使う値（web・ios・android・app）にする。"""
    return platform if platform in ("web", "ios", "android", "app") else "web"


def _platform_sql(platform: str) -> str:
    if platform == "app":
        return "(show_ios OR show_android)"
    return PLATFORM_COLUMNS.get(platform, "show_web")


def _lang_sql(lang: str) -> str:
    return "show_en" if lang == "en" else "show_ja"


def _public_where(platform: str, lang: str) -> str:
    return (
        f"status = 'published' AND deleted_at IS NULL AND published_at <= now() "
        f"AND {_platform_sql(platform)} AND {_lang_sql(lang)}"
    )


def format_display_date(dt: datetime.datetime | None, lang: str, *, with_time: bool = True) -> str:
    """日本語は日本時間、英語は UTC（今の表示と同じ）。"""
    if dt is None:
        return ""
    local = dt.astimezone(JST if lang == "ja" else datetime.timezone.utc)
    return local.strftime("%Y-%m-%d %H:%M" if with_time else "%Y-%m-%d")


def is_visible_for(row: asyncpg.Record | dict, platform: str, lang: str) -> bool:
    if lang == "en":
        if not row["show_en"]:
            return False
    elif not row["show_ja"]:
        return False
    if platform == "app":
        return bool(row["show_ios"] or row["show_android"])
    return bool(row[PLATFORM_COLUMNS.get(platform, "show_web")])


def _display_item(row: asyncpg.Record | dict, platform: str, lang: str, *, with_body: bool = True) -> dict[str, Any]:
    title = row["title_en"] if lang == "en" else row["title_ja"]
    item: dict[str, Any] = {
        "id": row["id"],
        "legacy_key": row["legacy_key"],
        "category": row["category"],
        "date": format_display_date(row["published_at"], lang),
        "date_short": format_display_date(row["published_at"], lang, with_time=False),
        "title_html": render_html(title, platform=platform, lang=lang),
    }
    if with_body:
        body = row["body_en"] if lang == "en" else row["body_ja"]
        item["body_html"] = render_html(body, platform=platform, lang=lang)
    return item


async def _cache_version() -> int:
    r = get_redis()
    if not r:
        return 0
    try:
        value = await r.get(CACHE_VERSION_KEY)
        return int(value) if value else 0
    except Exception as e:  # Redis が落ちていても表示は続ける（DB から読む）
        logger.warning(f"お知らせのキャッシュの版の取得に失敗しました: {e}")
        return 0


async def bump_cache_version() -> None:
    """保存・削除・復元のあとに呼ぶ。ユーザー向けのキャッシュをすべて古いものにする。"""
    r = get_redis()
    if not r:
        return
    try:
        await r.incr(CACHE_VERSION_KEY)
    except Exception as e:
        logger.warning(f"お知らせのキャッシュの版の更新に失敗しました: {e}")


async def _next_scheduled_ttl(db: asyncpg.Connection) -> int:
    """次の予約の公開までが CACHE_TTL より短ければ、そこまでにする（予約が遅れて出ないように）。"""
    next_at = await db.fetchval(
        "SELECT min(published_at) FROM announcement_posts "
        "WHERE status = 'published' AND deleted_at IS NULL AND published_at > now()"
    )
    if next_at is None:
        return CACHE_TTL
    seconds = int((next_at - datetime.datetime.now(datetime.timezone.utc)).total_seconds()) + 1
    return max(1, min(CACHE_TTL, seconds))


async def _cached(key_suffix: str, load) -> Any:
    """キャッシュから返す。なければプロセスごとに1件だけ DB から作る（同じキーの同時アクセスは待たせる）。"""
    version = await _cache_version()
    key = f"{CACHE_PREFIX}{version}:{key_suffix}"
    cached = await get_cache(key)
    if cached is not None:
        return cached["v"]

    async def fill() -> Any:
        again = await get_cache(key)
        if again is not None:
            return again["v"]
        try:
            async with get_db_connection_for_bg_task() as db:
                value = await load(db)
                ttl = await _next_scheduled_ttl(db)
        except asyncpg.PostgresError as e:
            raise DataBaseError(e) from e
        await set_cache(key, {"v": value}, ttl=ttl)
        return value

    lock = _locks.setdefault(key_suffix, asyncio.Lock())
    return await load_serialized_within_request_timeout(lock, fill)


# --- ユーザー向け -------------------------------------------------------------------------
async def get_home_items(platform: str, lang: str) -> list[dict[str, Any]]:
    """ホームの最新3件（タイトルだけ）。"""
    platform = normalize_platform(platform)

    async def load(db: asyncpg.Connection) -> list[dict[str, Any]]:
        rows = await db.fetch(
            f"SELECT * FROM announcement_posts WHERE {_public_where(platform, lang)} AND on_home AND listed "
            f"ORDER BY published_at DESC, id DESC LIMIT {HOME_LIMIT}"
        )
        return [_display_item(row, platform, lang, with_body=False) for row in rows]

    return await _cached(f"home:{platform}:{lang}", load)


async def get_list_page(platform: str, lang: str, category: int | None, offset: int, limit: int) -> dict[str, Any]:
    """一覧の1ページ分。{"items": [...], "has_more": bool}。"""
    platform = normalize_platform(platform)
    offset = max(0, min(offset, LIST_MAX_OFFSET))
    limit = max(1, min(limit, LIST_MAX_ITEMS))
    category = category if category in CATEGORIES else None

    async def load(db: asyncpg.Connection) -> dict[str, Any]:
        where = f"{_public_where(platform, lang)} AND listed"
        args: list[Any] = []
        if category is not None:
            where += " AND category = $1"
            args.append(category)
        rows = await db.fetch(
            f"SELECT * FROM announcement_posts WHERE {where} "
            f"ORDER BY published_at DESC, id DESC OFFSET {offset} LIMIT {limit + 1}",
            *args,
        )
        return {
            "items": [_display_item(row, platform, lang) for row in rows[:limit]],
            "has_more": len(rows) > limit,
        }

    return await _cached(f"list:{platform}:{lang}:{category or 0}:{offset}:{limit}", load)


async def get_public_post(post_id: int, platform: str, lang: str) -> dict[str, Any]:
    """個別ページ。{"state": "ok" | "hidden" | "not_found", "item": ..., "prev": ..., "next": ...}。

    hidden はこの端末・言語には出していないもの（共有されたリンクで来た人に、一覧へ案内する）。
    """
    platform = normalize_platform(platform)

    async def load(db: asyncpg.Connection) -> dict[str, Any]:
        row = await db.fetchrow(
            "SELECT * FROM announcement_posts WHERE id = $1 AND status = 'published' "
            "AND deleted_at IS NULL AND published_at <= now()",
            post_id,
        )
        if row is None:
            return {"state": "not_found"}
        if not is_visible_for(row, platform, lang):
            return {"state": "hidden"}
        item = _display_item(row, platform, lang)
        body = row["body_en"] if lang == "en" else row["body_ja"]
        item["description"] = html_to_text(body, platform=platform, lang=lang, limit=120)
        item["title_text"] = html_to_text(row["title_en"] if lang == "en" else row["title_ja"], platform=platform, lang=lang)
        item["cover_url"] = row["cover_url"]
        where = f"{_public_where(platform, lang)} AND listed"
        newer = await db.fetchrow(
            f"SELECT id, title_ja, title_en FROM announcement_posts WHERE {where} "
            f"AND (published_at, id) > ($1, $2) ORDER BY published_at, id LIMIT 1",
            row["published_at"], row["id"],
        )
        older = await db.fetchrow(
            f"SELECT id, title_ja, title_en FROM announcement_posts WHERE {where} "
            f"AND (published_at, id) < ($1, $2) ORDER BY published_at DESC, id DESC LIMIT 1",
            row["published_at"], row["id"],
        )

        def nav(r: asyncpg.Record | None) -> dict[str, Any] | None:
            if r is None:
                return None
            title = r["title_en"] if lang == "en" else r["title_ja"]
            return {"id": r["id"], "title_html": render_html(title, platform=platform, lang=lang)}

        return {"state": "ok", "item": item, "newer": nav(newer), "older": nav(older)}

    return await _cached(f"post:{post_id}:{platform}:{lang}", load)


async def resolve_legacy_key(key: str) -> int | None:
    """旧 ID（web1・G91 など）から、新しい番号を引く。"""
    key = (key or "").strip()[:64]
    if not key:
        return None
    cache_key = f"{CACHE_PREFIX}legacy:{key}"
    cached = await get_cache(cache_key)
    if cached is not None:
        return cached or None
    try:
        async with get_db_connection_for_bg_task() as db:
            post_id = await db.fetchval("SELECT id FROM announcement_posts WHERE legacy_key = $1", key)
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e
    await set_cache(cache_key, post_id or 0, ttl=LEGACY_CACHE_TTL)
    return post_id


async def get_admin_preview_post(db: asyncpg.Connection, post_id: int, platform: str, lang: str) -> dict[str, Any] | None:
    """管理者が下書き・予約・削除済みを個別ページの形で見るとき（キャッシュしない）。"""
    row = await db.fetchrow("SELECT * FROM announcement_posts WHERE id = $1", post_id)
    if row is None:
        return None
    platform = normalize_platform(platform)
    item = _display_item(row, platform, lang)
    item["description"] = html_to_text(row["body_en"] if lang == "en" else row["body_ja"], limit=120)
    item["title_text"] = html_to_text(row["title_en"] if lang == "en" else row["title_ja"])
    item["cover_url"] = row["cover_url"]
    return {"state": "ok", "item": item, "newer": None, "older": None, "row": dict(row)}


# --- 管理画面: 一覧 -----------------------------------------------------------------------
ADMIN_TABS = ("published", "scheduled", "draft", "deleted")
_TAB_WHERE = {
    "published": "deleted_at IS NULL AND status = 'published' AND published_at <= now()",
    "scheduled": "deleted_at IS NULL AND status = 'published' AND published_at > now()",
    "draft": "deleted_at IS NULL AND status = 'draft'",
    "deleted": "deleted_at IS NOT NULL",
}


async def count_admin_tabs(db: asyncpg.Connection) -> dict[str, int]:
    row = await db.fetchrow(
        "SELECT " + ", ".join(f"count(*) FILTER (WHERE {where}) AS {tab}" for tab, where in _TAB_WHERE.items())
        + " FROM announcement_posts"
    )
    return {tab: row[tab] for tab in ADMIN_TABS}


async def list_admin_posts(
    db: asyncpg.Connection,
    *,
    tab: str,
    q: str = "",
    category: str = "",
    platform: str = "",
    lang: str = "",
    source: str = "",
    page: int = 1,
) -> tuple[list[dict[str, Any]], int]:
    """(行, 全件数)。q は ID（#245・245）かタイトル・本文の部分一致。"""
    tab = tab if tab in ADMIN_TABS else "published"
    conditions = [_TAB_WHERE[tab]]
    args: list[Any] = []
    q = (q or "").strip()
    if q:
        id_match = re.fullmatch(r"#?(\d{1,9})", q)
        if id_match:
            args.append(int(id_match.group(1)))
            conditions.append(f"id = ${len(args)}")
        else:
            args.append("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
            n = len(args)
            conditions.append(
                f"(title_ja ILIKE ${n} OR title_en ILIKE ${n} OR body_ja ILIKE ${n} OR body_en ILIKE ${n} OR legacy_key ILIKE ${n})"
            )
    if category == "none":
        conditions.append("category IS NULL")
    elif category.isdigit() and int(category) in CATEGORIES:
        args.append(int(category))
        conditions.append(f"category = ${len(args)}")
    if platform in PLATFORM_COLUMNS:
        conditions.append(PLATFORM_COLUMNS[platform])
    if lang in LANGS:
        conditions.append(_lang_sql(lang))
    if source == "linked":
        conditions.append("source_type IS NOT NULL")
    elif source == "none":
        conditions.append("source_type IS NULL")
    where = " AND ".join(conditions)
    order = "deleted_at DESC, id DESC" if tab == "deleted" else (
        "updated_at DESC, id DESC" if tab == "draft" else
        "published_at ASC, id ASC" if tab == "scheduled" else "published_at DESC, id DESC"
    )
    page = max(1, page)
    total = await db.fetchval(f"SELECT count(*) FROM announcement_posts WHERE {where}", *args)
    rows = await db.fetch(
        f"""
        SELECT p.*, u.name AS updated_by_name
        FROM (
            SELECT id, legacy_key, category, status, published_at, show_web, show_ios, show_android,
                   show_ja, show_en, on_home, listed, title_ja, title_en, source_type, source_id,
                   source_role, updated_by, updated_at, deleted_at
            FROM announcement_posts WHERE {where}
            ORDER BY {order} OFFSET {(page - 1) * ADMIN_PER_PAGE} LIMIT {ADMIN_PER_PAGE}
        ) p LEFT JOIN users u ON u.id = p.updated_by
        ORDER BY {", ".join("p." + part.strip() for part in order.split(","))}
        """,
        *args,
    )
    items = []
    for row in rows:
        item = dict(row)
        item["title_text"] = html_to_text(row["title_ja"] or row["title_en"], limit=120)
        items.append(item)
    return items, total


# --- 管理画面: 1件 ------------------------------------------------------------------------
async def get_admin_post(db: asyncpg.Connection, post_id: int) -> dict[str, Any] | None:
    row = await db.fetchrow(
        """
        SELECT p.*, cu.name AS created_by_name, uu.name AS updated_by_name, du.name AS deleted_by_name
        FROM announcement_posts p
        LEFT JOIN users cu ON cu.id = p.created_by
        LEFT JOIN users uu ON uu.id = p.updated_by
        LEFT JOIN users du ON du.id = p.deleted_by
        WHERE p.id = $1
        """,
        post_id,
    )
    return dict(row) if row else None


def _snapshot(values: dict[str, Any]) -> dict[str, Any]:
    snap = {key: values.get(key) for key in EDITABLE_FIELDS}
    if isinstance(snap.get("published_at"), datetime.datetime):
        snap["published_at"] = snap["published_at"].isoformat()
    return snap


def clean_post_input(data: dict[str, Any]) -> dict[str, Any]:
    """管理画面から来た値を消毒・検証して、保存する形にする。誤りは AnnouncementError。"""
    status = data.get("status")
    if status not in ("draft", "published"):
        raise AnnouncementError("状態の指定が正しくありません。")
    category = data.get("category")
    if category in ("", None):
        category = None
    else:
        try:
            category = int(category)
        except (TypeError, ValueError):
            raise AnnouncementError("カテゴリーの指定が正しくありません。")
        if category not in CATEGORIES:
            raise AnnouncementError("カテゴリーの指定が正しくありません。")
    out: dict[str, Any] = {"status": status, "category": category}
    for key in ("show_web", "show_ios", "show_android", "show_ja", "show_en", "on_home", "listed"):
        out[key] = bool(data.get(key))
    if not (out["show_web"] or out["show_ios"] or out["show_android"]):
        raise AnnouncementError("掲載する端末を1つ以上選んでください。")
    if not (out["show_ja"] or out["show_en"]):
        raise AnnouncementError("掲載する言語を1つ以上選んでください。")
    for lang in LANGS:
        title = sanitize_title(data.get(f"title_{lang}") or "")
        body = sanitize_body(data.get(f"body_{lang}") or "")
        if len(html_to_text(title)) > TITLE_MAX_TEXT:
            raise AnnouncementError(f"{'日本語' if lang == 'ja' else '英語'}のタイトルが長すぎます（{TITLE_MAX_TEXT}字まで）。")
        if len(body) > BODY_MAX_HTML:
            raise AnnouncementError(f"{'日本語' if lang == 'ja' else '英語'}の本文が長すぎます。")
        out[f"title_{lang}"] = title
        out[f"body_{lang}"] = "" if is_blank_body(body) else body
    published_at = data.get("published_at")
    if published_at is not None and not isinstance(published_at, datetime.datetime):
        raise AnnouncementError("公開の日時が正しくありません。")
    if status == "published":
        if published_at is None:
            published_at = datetime.datetime.now(datetime.timezone.utc)
        for lang, label in (("ja", "日本語"), ("en", "英語")):
            if not out[f"show_{lang}"]:
                continue
            if not html_to_text(out[f"title_{lang}"]):
                raise AnnouncementError(f"{label}のタイトルを入力してください（{label}に掲載しないなら、言語のチェックを外してください）。")
            if not out[f"body_{lang}"]:
                raise AnnouncementError(f"{label}の本文を入力してください（{label}に掲載しないなら、言語のチェックを外してください）。")
    out["published_at"] = published_at
    for key in ("source_type", "source_id", "source_role"):
        value = data.get(key)
        out[key] = str(value)[:64] if value not in (None, "") else None
    return out


async def save_post(
    db: asyncpg.Connection,
    post_id: int | None,
    values: dict[str, Any],
    *,
    user_id: int,
    expected_version: int | None,
) -> dict[str, Any]:
    """作成（post_id が None）または更新する。values は clean_post_input の結果。

    更新のとき、expected_version が今の版と違えば保存しない（code = conflict）。
    戻り値は保存後の行（dict）と "changed"（更新で中身が変わったか）。
    """
    cover_url = first_image_src(values["body_ja"]) or first_image_src(values["body_en"])
    try:
        async with db.transaction():
            if post_id is None:
                row = await db.fetchrow(
                    f"""
                    INSERT INTO announcement_posts ({", ".join(EDITABLE_FIELDS)}, cover_url, created_by, updated_by)
                    VALUES ({", ".join(f"${i + 1}" for i in range(len(EDITABLE_FIELDS)))},
                            ${len(EDITABLE_FIELDS) + 1}, ${len(EDITABLE_FIELDS) + 2}, ${len(EDITABLE_FIELDS) + 2})
                    RETURNING *
                    """,
                    *[values[key] for key in EDITABLE_FIELDS], cover_url, user_id,
                )
                action = "create"
                changed = True
            else:
                current = await db.fetchrow("SELECT * FROM announcement_posts WHERE id = $1 FOR UPDATE", post_id)
                if current is None:
                    raise AnnouncementError("お知らせが見つかりません。", code="not_found")
                if current["deleted_at"] is not None:
                    raise AnnouncementError("削除済みのお知らせは編集できません。先に復元してください。", code="deleted")
                if expected_version is not None and current["version"] != expected_version:
                    raise AnnouncementError(
                        "ほかの管理者がこのお知らせを先に保存しました。最新の内容を読み込んでから保存してください。",
                        code="conflict",
                    )
                changed = any(current[key] != values[key] for key in EDITABLE_FIELDS)
                if not changed:
                    return {"row": dict(current), "changed": False}
                sets = ", ".join(f"{key} = ${i + 2}" for i, key in enumerate(EDITABLE_FIELDS))
                n = len(EDITABLE_FIELDS)
                row = await db.fetchrow(
                    f"""
                    UPDATE announcement_posts SET {sets}, cover_url = ${n + 2}, updated_by = ${n + 3},
                        updated_at = now(), version = version + 1
                    WHERE id = $1 RETURNING *
                    """,
                    post_id, *[values[key] for key in EDITABLE_FIELDS], cover_url, user_id,
                )
                action = "update"
            await db.execute(
                "INSERT INTO announcement_revisions (post_id, version, action, snapshot, edited_by) VALUES ($1, $2, $3, $4, $5)",
                row["id"], row["version"], action, _snapshot(dict(row)), user_id,
            )
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e
    await bump_cache_version()
    logger.info(f"お知らせ #{row['id']} を{'作成' if action == 'create' else '更新'}しました（版 {row['version']}）。")
    return {"row": dict(row), "changed": changed}


async def set_deleted(db: asyncpg.Connection, post_id: int, *, deleted: bool, user_id: int) -> dict[str, Any]:
    """論理削除・復元。復元すると元の状態（公開中・予約・下書き）に戻る。"""
    try:
        async with db.transaction():
            current = await db.fetchrow("SELECT * FROM announcement_posts WHERE id = $1 FOR UPDATE", post_id)
            if current is None:
                raise AnnouncementError("お知らせが見つかりません。", code="not_found")
            if deleted == (current["deleted_at"] is not None):
                raise AnnouncementError("すでに削除されています。" if deleted else "削除されていません。", code="state")
            if deleted:
                row = await db.fetchrow(
                    "UPDATE announcement_posts SET deleted_at = now(), deleted_by = $2, version = version + 1 "
                    "WHERE id = $1 RETURNING *",
                    post_id, user_id,
                )
            else:
                row = await db.fetchrow(
                    "UPDATE announcement_posts SET deleted_at = NULL, deleted_by = NULL, updated_by = $2, "
                    "updated_at = now(), version = version + 1 WHERE id = $1 RETURNING *",
                    post_id, user_id,
                )
            await db.execute(
                "INSERT INTO announcement_revisions (post_id, version, action, snapshot, edited_by) VALUES ($1, $2, $3, $4, $5)",
                post_id, row["version"], "delete" if deleted else "restore", _snapshot(dict(row)), user_id,
            )
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e
    await bump_cache_version()
    logger.info(f"お知らせ #{post_id} を{'削除' if deleted else '復元'}しました。")
    return dict(row)


async def list_revisions(db: asyncpg.Connection, post_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch(
        """
        SELECT r.id, r.version, r.action, r.edited_at, u.name AS edited_by_name
        FROM announcement_revisions r LEFT JOIN users u ON u.id = r.edited_by
        WHERE r.post_id = $1 ORDER BY r.id DESC LIMIT 200
        """,
        post_id,
    )
    return [dict(row) for row in rows]


async def get_revision(db: asyncpg.Connection, post_id: int, revision_id: int) -> dict[str, Any] | None:
    row = await db.fetchrow(
        "SELECT id, version, action, snapshot, edited_at FROM announcement_revisions WHERE post_id = $1 AND id = $2",
        post_id, revision_id,
    )
    return dict(row) if row else None


# --- 管理画面: テンプレート・部品 -----------------------------------------------------------
async def list_templates(db: asyncpg.Connection, kind: str | None = None) -> list[dict[str, Any]]:
    if kind:
        rows = await db.fetch(
            "SELECT * FROM announcement_templates WHERE kind = $1 ORDER BY sort_order, id", kind,
        )
    else:
        rows = await db.fetch("SELECT * FROM announcement_templates ORDER BY kind, sort_order, id")
    return [dict(row) for row in rows]


async def get_template(db: asyncpg.Connection, template_id: int) -> dict[str, Any] | None:
    row = await db.fetchrow("SELECT * FROM announcement_templates WHERE id = $1", template_id)
    return dict(row) if row else None


def clean_template_input(data: dict[str, Any]) -> dict[str, Any]:
    kind = data.get("kind")
    if kind not in ("template", "snippet"):
        raise AnnouncementError("種類の指定が正しくありません。")
    name = str(data.get("name") or "").strip()[:100]
    if not name:
        raise AnnouncementError("名前を入力してください。")
    category = data.get("category")
    if category in ("", None):
        category = None
    else:
        try:
            category = int(category)
        except (TypeError, ValueError):
            raise AnnouncementError("カテゴリーの指定が正しくありません。")
        if category not in CATEGORIES:
            raise AnnouncementError("カテゴリーの指定が正しくありません。")
    settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    settings = {key: bool(settings[key]) for key in (
        "show_web", "show_ios", "show_android", "show_ja", "show_en", "on_home", "listed",
    ) if key in settings}
    try:
        sort_order = int(data.get("sort_order") or 0)
    except (TypeError, ValueError):
        sort_order = 0
    out = {
        "kind": kind, "name": name, "category": category, "settings": settings, "sort_order": sort_order,
        "title_ja": sanitize_title(data.get("title_ja") or "") if kind == "template" else "",
        "title_en": sanitize_title(data.get("title_en") or "") if kind == "template" else "",
        "body_ja": sanitize_body(data.get("body_ja") or ""),
        "body_en": sanitize_body(data.get("body_en") or ""),
    }
    for lang in LANGS:
        if is_blank_body(out[f"body_{lang}"]):
            out[f"body_{lang}"] = ""
    if kind == "snippet" and not (out["body_ja"] or out["body_en"]):
        raise AnnouncementError("部品の中身が空です。")
    return out


async def save_template(db: asyncpg.Connection, template_id: int | None, values: dict[str, Any], *, user_id: int) -> dict[str, Any]:
    try:
        if template_id is None:
            row = await db.fetchrow(
                f"""
                INSERT INTO announcement_templates ({", ".join(TEMPLATE_FIELDS)}, updated_by)
                VALUES ({", ".join(f"${i + 1}" for i in range(len(TEMPLATE_FIELDS)))}, ${len(TEMPLATE_FIELDS) + 1})
                RETURNING *
                """,
                *[values[key] for key in TEMPLATE_FIELDS], user_id,
            )
        else:
            sets = ", ".join(f"{key} = ${i + 2}" for i, key in enumerate(TEMPLATE_FIELDS))
            row = await db.fetchrow(
                f"UPDATE announcement_templates SET {sets}, updated_by = ${len(TEMPLATE_FIELDS) + 2}, updated_at = now() "
                "WHERE id = $1 RETURNING *",
                template_id, *[values[key] for key in TEMPLATE_FIELDS], user_id,
            )
            if row is None:
                raise AnnouncementError("テンプレートが見つかりません。", code="not_found")
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e
    return dict(row)


async def delete_template(db: asyncpg.Connection, template_id: int) -> bool:
    try:
        result = await db.execute("DELETE FROM announcement_templates WHERE id = $1", template_id)
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e
    return result.endswith(" 1")
