"""フィードバック確認の詳細（個別返信・公開の回答・チェック）と、回答のお知らせの作成。

一覧は admin.py（/admin/feedbacks）。admin.py が大きいため分けている。管理者は日本人しかいないため日本語で表示する。
検討メモ notes_private/feedback_plan_2026-10-31.md の 6〜8章・10章。権限は admin_permissions の
resolve_admin_route_permission（詳細は閲覧、返信は個別返信、回答とお知らせの作成は公開の回答、チェックはチェック更新）。
"""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlencode

import asyncpg
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.admin_permissions import (
    PERM_ANNOUNCEMENTS_EDIT,
    PERM_FEEDBACKS_ANSWER,
    PERM_FEEDBACKS_EDIT,
    PERM_FEEDBACKS_REPLY,
)
from app.core.logger import logger
from app.core.templating import templates
from app.db.db import get_shared_db
from app.exceptions.custom_exceptions import DataBaseError
from app.routers.admin import (
    ADMIN_FEEDBACK_PLATFORMS,
    ADMIN_FEEDBACK_STATUS_FILTERS,
    ADMIN_FEEDBACK_TYPES,
    _build_admin_feedback_filter_query,
    _notify_admin_action,
    _parse_admin_datetime_filter,
    _parse_admin_numeric_filter,
    enforce_admin_route_permission,
    format_admin_jst_datetime,
    get_current_active_user,
)
from app.services import feedback_service
from app.services.announcement_html import html_to_text
from app.services.admin_notification_service import clip_admin_notification_text
from app.services.user_service import User

router = APIRouter(
    prefix="/{lang}/admin/feedbacks",
    tags=["Admin Feedbacks"],
    dependencies=[Depends(enforce_admin_route_permission)],
)


def _error(e: feedback_service.FeedbackError, status: int = 400) -> JSONResponse:
    return JSONResponse({"success": False, "message": e.message_ja}, status_code=status)


def _list_filters(query: str) -> tuple[str, list]:
    """一覧の絞り込み（URL の検索の部分）から WHERE 句を作る（前・次の移動を一覧と同じ並びにするため）。"""
    params = {key: values[-1] for key, values in parse_qs(query).items()}
    status_filter = params.get("status_filter", "unchecked")
    feedback_type = params.get("feedback_type", "all")
    platform_filter = params.get("platform_filter", "all")
    answer_filter = params.get("answer_filter", "all")
    return _build_admin_feedback_filter_query(
        _parse_admin_numeric_filter(params.get("feedback_id", "")),
        status_filter if status_filter in ADMIN_FEEDBACK_STATUS_FILTERS else "unchecked",
        params.get("user_name", "")[:100],
        _parse_admin_numeric_filter(params.get("user_id", "")),
        feedback_type if feedback_type in ADMIN_FEEDBACK_TYPES else "all",
        params.get("comment_text", "")[:200],
        params.get("comment_regex", "")[:200] if not params.get("comment_text") else "",
        _parse_admin_datetime_filter(params.get("created_after", "")),
        _parse_admin_datetime_filter(params.get("created_before", "")),
        platform_filter if platform_filter in ADMIN_FEEDBACK_PLATFORMS else "all",
        "with" if params.get("image_filter") == "with" else "all",
        params.get("reply_filter") if params.get("reply_filter") in ("with", "without") else "all",
        answer_filter if answer_filter in feedback_service.ANSWER_STATES else "all",
        _parse_admin_numeric_filter(params.get("announcement_id", "")),
        _parse_admin_numeric_filter(params.get("thread_of", "")),
    )


async def _neighbors(db: asyncpg.Connection, row: asyncpg.Record, query: str) -> dict[str, int | None]:
    """一覧（新しい順）で1つ上（新しい）・1つ下（古い）のもの。"""
    where_sql, args = _list_filters(query)
    base = where_sql.replace("WHERE ", "", 1) if where_sql else "TRUE"
    n = len(args)
    result: dict[str, int | None] = {}
    for key, op, order in (("newer", ">", "ASC"), ("older", "<", "DESC")):
        try:
            result[key] = await db.fetchval(
                f"""
                SELECT f.id FROM feedbacks f LEFT JOIN users u ON u.id = f.user_id
                WHERE ({base}) AND (f.datetime, f.id) {op} (${n + 1}, ${n + 2})
                ORDER BY f.datetime {order}, f.id {order}
                LIMIT 1
                """,
                *args, row["datetime"], row["id"],
            )
        except asyncpg.PostgresError as e:
            # 正規表現の誤りなど。前・次を出さないだけにする
            logger.info(f"フィードバックの前・次の取得に失敗しました: {e}")
            result[key] = None
    return result


def _announcement_state(post: asyncpg.Record | None) -> str:
    if post is None:
        return ""
    if post["deleted_at"] is not None:
        return "削除済み"
    if post["status"] == "draft":
        return "下書き"
    return "予約" if post["is_future"] else "公開中"


@router.get("/{feedback_id:int}", name="admin_feedback_detail")
async def admin_feedback_detail(
    request: Request,
    lang: str,
    feedback_id: int,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    row = await db.fetchrow(
        """
        SELECT f.*, u.name AS user_name, cu.name AS checked_by_name, au.name AS answer_updated_by_name
        FROM feedbacks f
        LEFT JOIN users u ON u.id = f.user_id
        LEFT JOIN users cu ON cu.id = f.checked_by
        LEFT JOIN users au ON au.id = f.answer_updated_by
        WHERE f.id = $1
        """,
        feedback_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="フィードバックが見つかりません")
    list_query = request.query_params.get("q", "")
    neighbors = await _neighbors(db, row, list_query)
    replies = await db.fetch(
        """
        SELECT r.*, cu.name AS created_by_name, wu.name AS withdrawn_by_name
        FROM feedback_replies r
        LEFT JOIN users cu ON cu.id = r.created_by
        LEFT JOIN users wu ON wu.id = r.withdrawn_by
        WHERE r.feedback_id = $1 ORDER BY r.id
        """,
        feedback_id,
    )
    images = (await feedback_service.images_for(db, [feedback_id], lang=lang)).get(feedback_id, [])
    related = await db.fetch(
        """
        SELECT id, datetime, comment FROM feedbacks
        WHERE id = $1 OR reply_to_id = $2 ORDER BY id
        """,
        row["reply_to_id"], feedback_id,
    )
    user_total = await db.fetchval("SELECT count(*) FROM feedbacks WHERE user_id = $1", row["user_id"])
    post = None
    if row["announcement_id"]:
        post = await db.fetchrow(
            "SELECT id, status, title_ja, deleted_at, published_at > now() AS is_future FROM announcement_posts WHERE id = $1",
            row["announcement_id"],
        )
    env = feedback_service.row_env(row)
    detail_query = f"?{urlencode({'q': list_query})}" if list_query else ""
    list_url = str(request.url_for("admin_feedbacks", lang=lang)) + (f"?{list_query}" if list_query else "")
    feedback = {
        "id": row["id"],
        "user_id": row["user_id"],
        "user_name": row["user_name"] or "不明なユーザー",
        "user_total": user_total,
        "datetime_text": format_admin_jst_datetime(row["datetime"]),
        "feedback_type": row["feedback_type"],
        "type_label": feedback_service.type_label(row["feedback_type"], "ja"),
        "comment": row["comment"],
        "is_checked": row["is_checked"],
        "checked_text": (
            f"{format_admin_jst_datetime(row['checked_at'])}・{row['checked_by_name'] or '-'}" if row["checked_at"] else ""
        ),
        "env_parts": feedback_service.env_parts(env, "ja", with_site=True),
        "ua": (env or {}).get("ua", ""),
        "images": images,
        "reply_to": next(({"id": r["id"], "comment": clip_admin_notification_text(r["comment"], 60)}
                          for r in related if r["id"] == row["reply_to_id"]), None),
        "followups": [{"id": r["id"], "datetime_text": format_admin_jst_datetime(r["datetime"]),
                       "comment": clip_admin_notification_text(r["comment"], 60)}
                      for r in related if r["id"] != row["reply_to_id"]],
    }
    answer = {
        "state": row["answer_state"],
        "state_label": feedback_service.ANSWER_STATE_LABELS.get(row["answer_state"], ""),
        # 掲載用の本文は、まだ書いていなければ原文から始める（要約・個人の情報を伏せるのは管理者）
        "public_text_ja": row["public_text_ja"] or (row["comment"] if row["answer_state"] == "none" else ""),
        "public_text_en": row["public_text_en"],
        "answer_ja": row["answer_ja"],
        "answer_en": row["answer_en"],
        "updated_text": (
            f"{format_admin_jst_datetime(row['answer_updated_at'])}・{row['answer_updated_by_name'] or '-'}"
            if row["answer_updated_at"] else ""
        ),
        "announcement": {
            "id": post["id"],
            "title": html_to_text(post["title_ja"], limit=80),
            "state": _announcement_state(post),
            "url": str(request.url_for("admin_announcement_edit", lang=lang, post_id=post["id"])),
        } if post else None,
    }
    context = {
        "request": request,
        "lang": lang,
        "current_page": "account",
        "feedback": feedback,
        "answer": answer,
        "replies": [{
            "id": r["id"],
            "body": r["body"],
            "html": feedback_service.linkify(r["body"]),
            "created_text": format_admin_jst_datetime(r["created_at"]),
            "created_by_name": r["created_by_name"] or "-",
            "read_text": format_admin_jst_datetime(r["read_at"]) if r["read_at"] else "",
            "withdrawn_text": (
                f"{format_admin_jst_datetime(r['withdrawn_at'])}・{r['withdrawn_by_name'] or '-'}" if r["withdrawn_at"] else ""
            ),
        } for r in replies],
        "list_url": list_url,
        "newer_url": (str(request.url_for("admin_feedback_detail", lang=lang, feedback_id=neighbors["newer"])) + detail_query)
        if neighbors.get("newer") else "",
        "older_url": (str(request.url_for("admin_feedback_detail", lang=lang, feedback_id=neighbors["older"])) + detail_query)
        if neighbors.get("older") else "",
        "detail_query": detail_query,
        "can_check": current_user.has_perm(PERM_FEEDBACKS_EDIT),
        "can_reply": current_user.has_perm(PERM_FEEDBACKS_REPLY),
        "can_answer": current_user.has_perm(PERM_FEEDBACKS_ANSWER),
        "limits": {
            "reply": feedback_service.REPLY_MAX_CHARS,
            "public_text": feedback_service.PUBLIC_TEXT_MAX_CHARS,
            "answer": feedback_service.ANSWER_MAX_CHARS,
        },
    }
    return templates.TemplateResponse("admin/feedback_detail.html", context)


@router.post("/{feedback_id:int}/check", name="admin_feedback_check")
async def admin_feedback_check(
    request: Request,
    lang: str,
    feedback_id: int,
    payload: dict = Body(...),
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    checked = bool(payload.get("checked"))
    changed = await feedback_service.set_checked(db, feedback_id, checked, by_user_id=current_user.id)
    if changed:
        await _notify_admin_action(
            db, current_user, "feedback_checked",
            "フィードバックのチェック状態を更新しました",
            f"管理者操作 / フィードバック (ID: {feedback_id}) を{'チェック済み' if checked else '未チェック'}に変更",
            {"feedback_id": feedback_id, "is_checked": checked},
            request=request, target_type="feedback", target_id=feedback_id,
        )
    return {"success": True, "checked": checked, "message": "チェック済みにしました。" if checked else "チェックを外しました。"}


@router.post("/{feedback_id:int}/reply", name="admin_feedback_reply")
async def admin_feedback_reply(
    request: Request,
    lang: str,
    feedback_id: int,
    payload: dict = Body(...),
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        result = await feedback_service.add_reply(db, feedback_id, payload.get("body") or "", admin_user_id=current_user.id)
    except feedback_service.FeedbackError as e:
        return _error(e)
    except (asyncpg.PostgresError, DataBaseError) as e:
        logger.error(f"フィードバック (ID: {feedback_id}) の個別返信の送信中にデータベースエラー: {e}", exc_info=True)
        return JSONResponse({"success": False, "message": "データベースエラーのため、送信できませんでした。"}, status_code=500)
    reply = result["reply"]
    await _notify_admin_action(
        db, current_user, "feedback_replied",
        "フィードバックに個別返信を送りました",
        f"管理者操作 / フィードバック (ID: {feedback_id}) に返信「{clip_admin_notification_text(reply['body'], 60)}」",
        {"feedback_id": feedback_id, "reply_id": reply["id"]},
        request=request, target_type="feedback", target_id=feedback_id,
    )
    return {"success": True, "message": "返信が送信されました。", "checked": True}


@router.post("/{feedback_id:int}/replies/{reply_id:int}/withdraw", name="admin_feedback_reply_withdraw")
async def admin_feedback_reply_withdraw(
    request: Request,
    lang: str,
    feedback_id: int,
    reply_id: int,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    if not await feedback_service.withdraw_reply(db, feedback_id, reply_id, admin_user_id=current_user.id):
        return JSONResponse({"success": False, "message": "返信が見つからないか、すでに取り消されています。"}, status_code=404)
    await _notify_admin_action(
        db, current_user, "feedback_reply_withdrawn",
        "フィードバックの個別返信を取り消しました",
        f"管理者操作 / フィードバック (ID: {feedback_id}) の返信 (ID: {reply_id}) を取り消し",
        {"feedback_id": feedback_id, "reply_id": reply_id},
        request=request, target_type="feedback", target_id=feedback_id,
    )
    return {"success": True, "message": "取り消しました。本人からは見えなくなりました。"}


@router.post("/{feedback_id:int}/answer", name="admin_feedback_answer")
async def admin_feedback_answer(
    request: Request,
    lang: str,
    feedback_id: int,
    payload: dict = Body(...),
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        row = await feedback_service.save_answer(db, feedback_id, payload, admin_user_id=current_user.id)
    except feedback_service.FeedbackError as e:
        return _error(e)
    await _notify_admin_action(
        db, current_user, "feedback_answer_saved",
        "フィードバックの公開の回答を保存しました",
        f"管理者操作 / フィードバック (ID: {feedback_id}) の公開の回答（{feedback_service.ANSWER_STATE_LABELS[row['answer_state']]}）"
        f"「{clip_admin_notification_text(row['answer_ja'], 60)}」",
        {"feedback_id": feedback_id, "answer_state": row["answer_state"]},
        request=request, target_type="feedback", target_id=feedback_id,
    )
    return {
        "success": True,
        "message": "保存しました。" + ("掲載待ちになりました。" if row["answer_state"] == "ready" else ""),
        "state": row["answer_state"],
        "state_label": feedback_service.ANSWER_STATE_LABELS[row["answer_state"]],
    }


@router.post("/announcement", name="admin_feedback_make_announcement")
async def admin_feedback_make_announcement(
    request: Request,
    lang: str,
    payload: dict = Body(...),
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    if not current_user.has_perm(PERM_ANNOUNCEMENTS_EDIT):
        return JSONResponse({"success": False, "message": "お知らせを作るには、アナウンス管理の編集の権限が要ります。"}, status_code=403)
    ids = payload.get("ids")
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        return JSONResponse({"success": False, "message": "選んだフィードバックの指定が正しくありません。"}, status_code=400)
    try:
        result = await feedback_service.create_announcement(db, ids, admin_user_id=current_user.id)
    except feedback_service.FeedbackError as e:
        return _error(e)
    except Exception as e:  # お知らせの検証（AnnouncementError）・データベースエラー
        logger.error(f"フィードバックの回答のお知らせの作成中にエラー: {e}", exc_info=True)
        return JSONResponse({"success": False, "message": f"お知らせを作れませんでした（{e}）。"}, status_code=500)
    await _notify_admin_action(
        db, current_user, "feedback_announcement_created",
        "フィードバックの回答のお知らせを作りました",
        f"管理者操作 / フィードバック {len(ids)} 件から回答のお知らせ #{result['post_id']} の下書きを作成",
        {"feedback_ids": ids, "announcement_id": result["post_id"]},
        request=request, target_type="announcement", target_id=result["post_id"],
    )
    message = f"お知らせ #{result['post_id']} の下書きができました。"
    if not result["count_en"]:
        message += "英語の回答がないため、英語には掲載しない設定です。"
    return {
        "success": True,
        "message": message,
        "post_id": result["post_id"],
        "edit_url": str(request.url_for("admin_announcement_edit", lang=lang, post_id=result["post_id"])),
    }
