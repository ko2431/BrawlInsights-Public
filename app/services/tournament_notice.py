"""大会のお知らせ（大会ページの「お知らせ」タブ。検討メモ 9.3・D47）。

器は大会のチャットと同じく掲示板の posts + messages（posts.type = tournament_notice。1件のお知らせ＝1件の message）。
- 書けるのは主催者と管理者。公開後から終了・中止の後まで書ける（下書き・削除済みは不可）。返信と編集はない。
  リアクション・通報はチャットのAPI（tournament_chat）をそのまま使う。
- 器は最初の投稿のときに作る（tournaments.notice_post_id）。お知らせのない大会では、大会ページの描画でDBを引かない。
- 開いている大会ページには、大会ロビーのチャットの配信（tn_notice / tn_notice_delete）で知らせ、読み込み直さずに足す・消す。
  ロビーのシステムメッセージにはしない（見ている人全員のページが読み込み直されるため）。
- 「参加者に通知する」がオンなら、参加者に大会の通知（notice_posted）を送る。お知らせを消したら、その通知も消す。
"""
from __future__ import annotations

import asyncpg

from app.core import cache as cache_module
from app.core.logger import logger
from app.services import tournament_chat as chat
from app.services.tournament_access import is_posting_prohibited
from app.services.tournament_service import TournamentError, audit

NOTICE_MAX_LENGTH = 1000
LIST_LIMIT = 50
POST_INTERVAL_SECONDS = 30  # 同じ大会への連続投稿（二重投稿）を防ぐ。消したときは解除して、すぐ書き直せるようにする
EXCERPT_LENGTH = 40  # 通知・操作の記録に入れる本文の長さ
RATE_KEY = "tournament:notice_rate:{}"


def can_write(user, t: dict) -> bool:
    """お知らせを書けるか（主催者・管理者。利用制限中は不可）。"""
    return bool(user and chat.is_staff(user, t) and not is_posting_prohibited(user)
                and t["status"] != "draft" and not t.get("is_deleted"))


def _excerpt(text: str) -> str:
    """通知・操作の記録に入れる本文の先頭（1行で見せるので、改行は空白にする）。"""
    line = " ".join(text.split())
    return line if len(line) <= EXCERPT_LENGTH else line[:EXCERPT_LENGTH] + "…"


async def _ensure_post(db: asyncpg.Connection, t: dict) -> int:
    """お知らせの器を用意する（最初の投稿のとき。呼び出し側で大会の行をロックしている）。"""
    current = await db.fetchval("SELECT notice_post_id FROM tournaments WHERE id = $1", t["id"])
    if current:
        return current
    post_id = await chat._insert_post(db, chat.NOTICE_TYPE, {"tournament_id": t["id"], "public_id": t["public_id"]})
    await db.execute("UPDATE tournaments SET notice_post_id = $2 WHERE id = $1", t["id"], post_id)
    return post_id


async def list_notices(db: asyncpg.Connection, t: dict, lang: str) -> list[dict]:
    """お知らせ（新しい順）。表示データはチャットのメッセージと同じ形。"""
    post_id = t.get("notice_post_id")
    if not post_id:
        return []
    rows = await db.fetch(
        chat._MESSAGE_SQL + " WHERE m.thread_id = $1 AND NOT m.is_deleted ORDER BY m.id DESC LIMIT $2",
        post_id, LIST_LIMIT,
    )
    reactions = await chat._reactions(db, [r["id"] for r in rows])
    return [chat._view(dict(r), lang, t, {}, reactions.get(r["id"])) for r in rows]


async def post_notice(db: asyncpg.Connection, t: dict, user, text: str, *, notify: bool, ip: str, lang: str) -> dict:
    """お知らせを書く（書けるかは呼び出し側で確かめる）。配信と同じ形の表示データを返す。"""
    from app.services import tournament_notify  # 循環 import を避ける
    from app.services.board_service import create_message
    body = (text or "").strip()
    if not body:
        raise TournamentError("empty", "お知らせを入力してください。", "Please enter an announcement.")
    if len(body) > NOTICE_MAX_LENGTH:
        raise TournamentError("too_long", f"お知らせは{NOTICE_MAX_LENGTH}文字以内にしてください。",
                              f"Announcements can be up to {NOTICE_MAX_LENGTH} characters.")
    redis = cache_module.redis_pool
    if redis is not None:
        try:
            if not await redis.set(RATE_KEY.format(t["id"]), 1, ex=POST_INTERVAL_SECONDS, nx=True):
                raise TournamentError("too_fast", "続けて投稿するときは、少し時間をおいてください。",
                                      "Please wait a moment before posting another announcement.")
        except TournamentError:
            raise
        except Exception as e:
            logger.debug(f"大会のお知らせの投稿間隔の確認に失敗しました: {e}")
    outbox = chat.Outbox()
    async with db.transaction():
        await db.execute("SELECT 1 FROM tournaments WHERE id = $1 FOR UPDATE", t["id"])  # 器を二重に作らない
        post_id = await _ensure_post(db, t)
        message_id = await create_message(db, thread_id=post_id, ip=ip, message=body, user_id=user.id)
        await audit(db, t["id"], user.id, "notice_posted", {"message_id": message_id, "text": _excerpt(body)})
        if notify:
            # 参加者（承認待ち・キャンセル待ちを含む）と、管理者が書いたときは主催者にも。書いた本人には送らない
            users = await tournament_notify.entry_user_ids(db, await tournament_notify.participant_entry_ids(db, t["id"]))
            recipients = [u for u in users + [t["host_user_id"]] if u and u != user.id]
            await tournament_notify.notify(db, outbox, t, recipients, "notice_posted",
                                           text=_excerpt(body), msg=message_id, tab="notice")
        row = await db.fetchrow(chat._MESSAGE_SQL + " WHERE m.id = $1", message_id)
    t["notice_post_id"] = post_id
    await outbox.flush()
    # 器の ID も渡す（最初のお知らせのときは、開いている画面がまだ知らない。リアクション・通報に使う）
    view = chat._view(dict(row), lang, t, {}) | {"post_id": post_id}
    logger.info(f"大会のお知らせを投稿しました (tournament_id={t['id']}, message_id={message_id}, by={user.id}, notify={notify})")
    # 開いている大会ページへ（大会ロビーのチャットの配信に乗せる。届いた画面はカードを足すだけで、読み込み直さない）
    if t.get("lobby_post_id"):
        await chat.publish(t["lobby_post_id"], {"type": "tn_notice", "data": view})
    return view


async def delete_notice(db: asyncpg.Connection, t: dict, user, message_id: int) -> None:
    """お知らせを消す（主催者・管理者。管理者の警告は、削除の権限を持つ人だけ。チャットと同じ）。"""
    from app.services import tournament_notify  # 循環 import を避ける
    post_id = t.get("notice_post_id")
    row = await db.fetchrow(
        "SELECT id, user_id, message_type, message FROM messages WHERE id = $1 AND thread_id = $2 AND NOT is_deleted",
        message_id, post_id,
    ) if post_id else None
    if row is None or row["message_type"] == chat.SYSTEM_TYPE:
        raise TournamentError("not_found", "お知らせが見つかりません。", "Announcement not found.")
    allowed = user.has_perm("chat.delete_messages") or (chat.is_staff(user, t) and row["message_type"] != chat.WARNING_TYPE)
    if not allowed:
        raise TournamentError("forbidden", "このお知らせは削除できません。", "You can't delete this announcement.")
    async with db.transaction():
        await db.execute("UPDATE messages SET is_deleted = TRUE WHERE id = $1", message_id)
        await tournament_notify.remove_for_message(db, t["id"], "notice_posted", message_id)
        if row["message_type"] != chat.WARNING_TYPE:
            await audit(db, t["id"], user.id, "notice_deleted", {"message_id": message_id, "text": _excerpt(row["message"] or "")})
    await cache_module.delete_cache(f"message:{message_id}")  # 掲示板側の get_message のキャッシュ
    redis = cache_module.redis_pool
    if redis is not None:
        try:
            await redis.delete(RATE_KEY.format(t["id"]))  # 書き間違いを消して、すぐ書き直せるように
        except Exception as e:
            logger.debug(f"大会のお知らせの投稿間隔を解除できませんでした: {e}")
    logger.info(f"大会のお知らせを削除しました (tournament_id={t['id']}, message_id={message_id}, by={user.id})")
    if t.get("lobby_post_id"):
        await chat.publish(t["lobby_post_id"], {"type": "tn_notice_delete", "data": {"id": message_id}})
