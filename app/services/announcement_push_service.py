"""お知らせのプッシュ通知（検討メモ notes_private/announcement_plan_2026-10-29.md の 8章・AD25〜AD33）。

- お知らせ1件に1回だけ（announcement_pushes）。送るのは公開と同時（予約の公開ならその時刻）。
  送る時刻はお知らせの published_at に付いていくので、ここでは持たない。下書きに戻す・削除すると送らない。
- 届くのは、プッシュ通知の設定で「お知らせ」をオンにした人の端末のうち、お知らせの端末・言語に合うもの。
- 文面が空なら、送るときにお知らせのタイトル・本文の先頭から作る（端末ごとの文言・iOS の置き換えも通す）。
- ここは編集画面の側（設定の読み書き・届く端末の目安・文面の組み立て）。送信は worker の push_sender.announcement_loop。
"""
from __future__ import annotations

import datetime
from typing import Any, Mapping

import asyncpg

from app.services import media_storage
from app.services import push_rules as rules
from app.services.announcement_html import first_image_src, html_to_text

PLATFORMS = ("web", "ios", "android")
LANGS = ("ja", "en")
EDITABLE_STATUSES = ("pending", "canceled")  # 送る前（送った後・送信中は変えない）
TITLE_MAX_CHARS = 100
BODY_MAX_CHARS = 300
JST = datetime.timezone(datetime.timedelta(hours=9))

# 送る相手の端末（worker が push_devices.id の順に少しずつ引く。届く端末の目安も同じ条件）
RECIPIENT_WHERE = """
    u.push_announcement_enabled AND NOT u.is_invalid
    AND d.platform = ANY($1::text[]) AND d.lang = ANY($2::text[])
"""


class PushInputError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def targets(post: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """お知らせの端末・言語の設定から、送る端末の種類と言語。"""
    platforms = [p for p in PLATFORMS if post.get(f"show_{p}")]
    langs = [lang for lang in LANGS if post.get(f"show_{lang}")]
    return platforms, langs


def cover_image(post: Mapping[str, Any], lang: str) -> str:
    """通知に付ける画像（その言語の本文の最初の画像）。上げた画像は SNS と同じ JPEG のサイズ違い。"""
    src = first_image_src(post.get(f"body_{lang}") or "")
    if not src:
        return ""
    return media_storage.variant_url(src, "og", kind="announcement") or (src if src.startswith("/static/") else "")


def default_texts(post: Mapping[str, Any], platform: str, lang: str) -> tuple[str, str]:
    title = html_to_text(post.get(f"title_{lang}") or "", platform=platform, lang=lang)
    body = html_to_text(post.get(f"body_{lang}") or "", platform=platform, lang=lang, limit=rules.ANNOUNCEMENT_BODY_MAX)
    return title, body


def build_pushes(post: Mapping[str, Any], push: Mapping[str, Any], platforms, langs) -> dict[tuple[str, str], rules.Push]:
    """(端末の種類, 言語) ごとの通知。文面が空の言語は、お知らせのタイトル・本文から作る。"""
    out = {}
    for lang in langs:
        image = cover_image(post, lang) if push.get("with_image", True) else ""
        for platform in platforms:
            title, body = default_texts(post, platform, lang)
            out[(platform, lang)] = rules.announcement_push(
                lang, post_id=int(post["id"]),
                title=(push.get(f"title_{lang}") or "").strip() or title,
                body=(push.get(f"body_{lang}") or "").strip() or body,
                image=image,
            )
    return out


# --- 編集画面 -----------------------------------------------------------------------------------

def _clean_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def clean_push_input(data: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        raise PushInputError("プッシュ通知の設定が正しくありません。")
    return {
        "enabled": bool(data.get("enabled")),
        "title_ja": _clean_text(data.get("title_ja"), TITLE_MAX_CHARS),
        "title_en": _clean_text(data.get("title_en"), TITLE_MAX_CHARS),
        "body_ja": _clean_text(data.get("body_ja"), BODY_MAX_CHARS),
        "body_en": _clean_text(data.get("body_en"), BODY_MAX_CHARS),
        "with_image": bool(data.get("with_image", True)),
    }


def _jst_text(dt: datetime.datetime | None) -> str:
    return dt.astimezone(JST).strftime("%Y-%m-%d %H:%M") if dt else ""


def push_for_editor(row: Mapping[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {
            "status": "none", "enabled": False, "editable": True,
            "title_ja": "", "title_en": "", "body_ja": "", "body_en": "", "with_image": True,
            "target_count": 0, "sent_count": 0, "failed_count": 0, "gone_count": 0, "started_at": "", "finished_at": "",
        }
    return {
        "status": row["status"],
        "enabled": row["status"] != "canceled",
        "editable": row["status"] in EDITABLE_STATUSES,
        "title_ja": row["title_ja"], "title_en": row["title_en"],
        "body_ja": row["body_ja"], "body_en": row["body_en"],
        "with_image": row["with_image"],
        "target_count": row["target_count"], "sent_count": row["sent_count"],
        "failed_count": row["failed_count"], "gone_count": row["gone_count"],
        "started_at": _jst_text(row["started_at"]), "finished_at": _jst_text(row["finished_at"]),
    }


async def get_push(db: asyncpg.Connection, post_id: int) -> asyncpg.Record | None:
    return await db.fetchrow("SELECT * FROM announcement_pushes WHERE post_id = $1", post_id)


async def save_push(db: asyncpg.Connection, post_id: int, values: Mapping[str, Any], *, user_id: int) -> asyncpg.Record | None:
    """オン（送る前の状態）・オフにする。送った後・送信中のものは変えない（そのまま返す）。"""
    row = await db.fetchrow("SELECT * FROM announcement_pushes WHERE post_id = $1 FOR UPDATE", post_id)
    if row is not None and row["status"] not in EDITABLE_STATUSES:
        return row
    if row is None and not values["enabled"]:
        return None
    status = "pending" if values["enabled"] else "canceled"
    texts = [values["title_ja"], values["title_en"], values["body_ja"], values["body_en"], values["with_image"]]
    if row is None:
        return await db.fetchrow(
            """
            INSERT INTO announcement_pushes
                (post_id, status, title_ja, title_en, body_ja, body_en, with_image, armed_at, created_by, updated_by)
            VALUES ($1, $2, $3, $4, $5, $6, $7, now(), $8, $8)
            RETURNING *
            """,
            post_id, status, *texts, user_id,
        )
    return await db.fetchrow(
        """
        UPDATE announcement_pushes SET
            status = $2, title_ja = $3, title_en = $4, body_ja = $5, body_en = $6, with_image = $7,
            armed_at = CASE WHEN $2 = 'pending' AND status <> 'pending' THEN now() ELSE armed_at END,
            updated_by = $8, updated_at = now()
        WHERE id = $1
        RETURNING *
        """,
        row["id"], status, *texts, user_id,
    )


async def estimate(db: asyncpg.Connection, platforms: list[str], langs: list[str]) -> dict[str, Any]:
    """届く端末の目安（今の設定で数える。送るときには増減する）。"""
    by_platform = {p: 0 for p in platforms}
    if platforms and langs:
        rows = await db.fetch(
            f"""
            SELECT d.platform, COUNT(*) AS n
            FROM push_devices d JOIN users u ON u.id = d.user_id
            WHERE {RECIPIENT_WHERE}
            GROUP BY d.platform
            """,
            platforms, langs,
        )
        for r in rows:
            by_platform[r["platform"]] = int(r["n"])
    return {"devices": sum(by_platform.values()), "by_platform": by_platform}


async def count_same_day(db: asyncpg.Connection, post_id: int | None, at: datetime.datetime) -> int:
    """その日（日本時間）に、ほかのお知らせで送った・送る予定のプッシュ通知の数（送りすぎの念押し。AD32）。"""
    start = at.astimezone(JST).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + datetime.timedelta(days=1)
    return int(await db.fetchval(
        """
        SELECT COUNT(*) FROM announcement_pushes ap JOIN announcement_posts p ON p.id = ap.post_id
        WHERE ap.post_id IS DISTINCT FROM $1 AND (
            (ap.status IN ('sending', 'sent', 'interrupted') AND ap.started_at >= $2 AND ap.started_at < $3)
            OR (ap.status = 'pending' AND p.status = 'published' AND p.deleted_at IS NULL
                AND p.published_at >= $2 AND p.published_at < $3)
        )
        """,
        post_id, start, end,
    ))
