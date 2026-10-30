"""アナウンス管理（お知らせの一覧・編集・テンプレート）。

admin.py が大きいため分けている。管理者は日本人しかいないため、言語設定にかかわらず日本語で表示する。
検討メモ notes_private/announcement_plan_2026-10-29.md の 6章。権限は admin_permissions の
resolve_admin_route_permission（GET は閲覧、POST は編集。プレビューだけは閲覧）。
"""
from __future__ import annotations

import datetime
import time
from pathlib import Path
from typing import Any

import asyncpg
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from PIL import Image

from app.core.admin_permissions import PERM_ANNOUNCEMENTS_EDIT
from app.core.logger import logger
from app.core.templating import templates
from app.db.db import get_shared_db
from app.exceptions.custom_exceptions import DataBaseError
from app.routers.admin import (
    _notify_admin_action,
    enforce_admin_route_permission,
    get_current_active_user,
    jst_local_str_to_utc_dt,
)
from app.services import announcement_service as svc
from app.services import media_service
from app.services.announcement_html import html_to_text, render_html, sanitize_body, sanitize_title
from app.services.user_service import User

router = APIRouter(
    prefix="/{lang}/admin/announcements",
    tags=["Admin Announcements"],
    dependencies=[Depends(enforce_admin_route_permission)],
)

JST = datetime.timezone(datetime.timedelta(hours=9))
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
STATIC_IMAGE_EXTS = {".png", ".webp", ".jpg", ".jpeg", ".gif", ".svg"}
STATIC_IMAGE_LIST_TTL = 600
_static_images: tuple[float, list[str]] = (0.0, [])


def _jst_input(dt: datetime.datetime | None) -> str:
    """datetime-local の入力欄の値（日本時間）。"""
    return dt.astimezone(JST).strftime("%Y-%m-%dT%H:%M") if dt else ""


def _jst_text(dt: datetime.datetime | None) -> str:
    return dt.astimezone(JST).strftime("%Y-%m-%d %H:%M") if dt else ""


def _post_state(row: dict[str, Any]) -> str:
    if row.get("deleted_at"):
        return "deleted"
    if row["status"] == "draft":
        return "draft"
    if row["published_at"] and row["published_at"] > datetime.datetime.now(datetime.timezone.utc):
        return "scheduled"
    return "published"


def _post_for_editor(row: dict[str, Any]) -> dict[str, Any]:
    """編集画面の JS に渡す形。"""
    data = {key: row.get(key) for key in svc.EDITABLE_FIELDS}
    data.update({
        "id": row.get("id"),
        "version": row.get("version"),
        "legacy_key": row.get("legacy_key"),
        "state": _post_state(row) if row.get("id") else "new",
        "published_at": _jst_input(row.get("published_at")),
        "publish_mode": "at" if row.get("published_at") else "now",
        "updated_at": _jst_text(row.get("updated_at")),
        "updated_by_name": row.get("updated_by_name"),
        "created_at": _jst_text(row.get("created_at")),
        "created_by_name": row.get("created_by_name"),
        "deleted_at": _jst_text(row.get("deleted_at")),
        "deleted_by_name": row.get("deleted_by_name"),
    })
    return data


def _empty_post() -> dict[str, Any]:
    return {
        "category": None, "status": "draft", "published_at": None,
        "show_web": True, "show_ios": True, "show_android": True, "show_ja": True, "show_en": True,
        "on_home": True, "listed": True,
        "title_ja": "", "title_en": "", "body_ja": "", "body_en": "",
        "source_type": None, "source_id": None, "source_role": None,
    }


def _template_for_editor(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"], "kind": row["kind"], "name": row["name"], "category": row["category"],
        "title_ja": row["title_ja"], "title_en": row["title_en"], "body_ja": row["body_ja"], "body_en": row["body_en"],
        "settings": row["settings"] or {}, "sort_order": row["sort_order"],
    }


def _can_edit(user: User) -> bool:
    return bool(user and user.has_perm(PERM_ANNOUNCEMENTS_EDIT))


def _parse_published_at(payload: dict[str, Any]) -> datetime.datetime | None:
    if payload.get("publish_mode") == "now":
        return None if payload.get("status") == "draft" else datetime.datetime.now(datetime.timezone.utc)
    text = (payload.get("published_at") or "").strip()
    if not text:
        if payload.get("status") == "draft":
            return None
        raise svc.AnnouncementError("公開の日時を入力してください。")
    parsed = jst_local_str_to_utc_dt(text)
    if parsed is None:
        raise svc.AnnouncementError("公開の日時が正しくありません。")
    return parsed


def _error(e: svc.AnnouncementError) -> JSONResponse:
    status = {"not_found": 404, "conflict": 409}.get(e.code, 400)
    return JSONResponse({"success": False, "message": e.message, "code": e.code}, status_code=status)


def _category_label(category: int | None) -> str:
    return svc.CATEGORIES[category][0] if category in svc.CATEGORIES else "なし"


# --- 一覧 ---------------------------------------------------------------------------------
@router.get("", name="admin_announcements")
async def admin_announcements(
    request: Request,
    lang: str,
    tab: str = Query("published"),
    q: str = Query("", max_length=200),
    category: str = Query("", max_length=8),
    platform: str = Query("", max_length=10),
    language: str = Query("", max_length=5),
    source: str = Query("", max_length=10),
    page: int = Query(1, ge=1, le=10000),
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    tab = tab if tab in svc.ADMIN_TABS else "published"
    try:
        counts = await svc.count_admin_tabs(db)
        items, total = await svc.list_admin_posts(
            db, tab=tab, q=q, category=category, platform=platform, lang=language, source=source, page=page,
        )
    except asyncpg.PostgresError as e:
        logger.error(f"アナウンス一覧の取得中にデータベースエラー: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="データベースエラー")
    for item in items:
        item["published_at_text"] = _jst_text(item["published_at"])
        item["deleted_at_text"] = _jst_text(item["deleted_at"])
        item["updated_at_text"] = _jst_text(item["updated_at"])
        item["category_label"] = _category_label(item["category"])
    total_pages = max(1, -(-total // svc.ADMIN_PER_PAGE))
    return templates.TemplateResponse("admin/announcements.html", {
        "request": request,
        "lang": lang,
        "current_page": "account",
        "items": items,
        "total": total,
        "counts": counts,
        "tab": tab,
        "filters": {"q": q, "category": category, "platform": platform, "language": language, "source": source},
        "page": min(page, total_pages),
        "total_pages": total_pages,
        "categories": svc.CATEGORIES,
        "can_edit": _can_edit(current_user),
    })


# --- 編集画面 -------------------------------------------------------------------------------
async def _editor_context(request: Request, lang: str, db: asyncpg.Connection, user: User, *, mode: str,
                          post: dict[str, Any] | None = None, template: dict[str, Any] | None = None) -> dict[str, Any]:
    snippets = await svc.list_templates(db, "snippet")
    context: dict[str, Any] = {
        "request": request,
        "lang": lang,
        "current_page": "account",
        "mode": mode,
        "categories": svc.CATEGORIES,
        "can_edit": _can_edit(user),
        "snippets": [_template_for_editor(s) for s in snippets],
        "editor_data": {"post": post, "template": template},
    }
    if mode == "post" and post and post.get("state") == "new":
        context["templates_list"] = [
            {"id": t["id"], "name": t["name"], "category": t["category"]}
            for t in await svc.list_templates(db, "template")
        ]
    return context


@router.get("/new", name="admin_announcement_new")
async def admin_announcement_new(
    request: Request,
    lang: str,
    template: int | None = Query(None, ge=1),
    duplicate: int | None = Query(None, ge=1),
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    post = _empty_post()
    if duplicate:
        source = await svc.get_admin_post(db, duplicate)
        if source is None:
            raise HTTPException(status_code=404, detail="複製元のお知らせが見つかりません")
        post.update({key: source[key] for key in svc.EDITABLE_FIELDS if key not in ("status", "published_at")})
        post["source_type"] = post["source_id"] = post["source_role"] = None
    elif template:
        tpl = await svc.get_template(db, template)
        if tpl is None or tpl["kind"] != "template":
            raise HTTPException(status_code=404, detail="テンプレートが見つかりません")
        post.update({key: tpl[key] for key in ("category", "title_ja", "title_en", "body_ja", "body_en")})
        post.update({key: value for key, value in (tpl["settings"] or {}).items() if key in post})
    data = _post_for_editor(post)
    data["duplicate_of"] = duplicate
    context = await _editor_context(request, lang, db, current_user, mode="post", post=data)
    return templates.TemplateResponse("admin/announcement_edit.html", context)


@router.get("/templates", name="admin_announcement_templates")
async def admin_announcement_templates(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    rows = await svc.list_templates(db)
    for row in rows:
        row["category_label"] = _category_label(row["category"])
        row["title_text"] = html_to_text(row["title_ja"] or row["title_en"], limit=80)
        row["body_text"] = html_to_text(row["body_ja"] or row["body_en"], limit=80)
        row["updated_at_text"] = _jst_text(row["updated_at"])
    return templates.TemplateResponse("admin/announcement_templates.html", {
        "request": request,
        "lang": lang,
        "current_page": "account",
        "templates_rows": [r for r in rows if r["kind"] == "template"],
        "snippet_rows": [r for r in rows if r["kind"] == "snippet"],
        "can_edit": _can_edit(current_user),
    })


@router.get("/templates/new", name="admin_announcement_template_new")
async def admin_announcement_template_new(
    request: Request,
    lang: str,
    kind: str = Query("template"),
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    kind = kind if kind in ("template", "snippet") else "template"
    template = {
        "id": None, "kind": kind, "name": "", "category": None, "title_ja": "", "title_en": "",
        "body_ja": "", "body_en": "", "settings": {}, "sort_order": 0,
    }
    context = await _editor_context(request, lang, db, current_user, mode="template", template=template)
    return templates.TemplateResponse("admin/announcement_edit.html", context)


@router.get("/templates/{template_id:int}", name="admin_announcement_template_edit")
async def admin_announcement_template_edit(
    request: Request,
    lang: str,
    template_id: int,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    row = await svc.get_template(db, template_id)
    if row is None:
        raise HTTPException(status_code=404, detail="テンプレートが見つかりません")
    context = await _editor_context(request, lang, db, current_user, mode="template", template=_template_for_editor(row))
    return templates.TemplateResponse("admin/announcement_edit.html", context)


@router.get("/static-images", name="admin_announcement_static_images")
async def admin_announcement_static_images(
    request: Request,
    lang: str,
    q: str = Query("", max_length=100),
):
    """「サイト内の画像」の検索。static/images の中をパスの部分一致で探す（大きさも返す）。"""
    global _static_images
    loaded_at, paths = _static_images
    if not paths or time.monotonic() - loaded_at > STATIC_IMAGE_LIST_TTL:
        images_dir = STATIC_DIR / "images"
        paths = sorted(
            "/static/" + p.relative_to(STATIC_DIR).as_posix()
            for p in images_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in STATIC_IMAGE_EXTS
        )
        _static_images = (time.monotonic(), paths)
    words = [w.lower() for w in q.split() if w]
    matched = [p for p in paths if all(w in p.lower() for w in words)] if words else paths
    items = []
    for path in matched[:60]:
        width = height = None
        if not path.endswith(".svg"):
            try:
                with Image.open(STATIC_DIR / path.removeprefix("/static/")) as im:
                    width, height = im.size
            except Exception:
                pass
        items.append({"src": path, "width": width, "height": height})
    return {"items": items, "total": len(matched)}


@router.get("/uploaded-images", name="admin_announcement_uploaded_images")
async def admin_announcement_uploaded_images(
    request: Request,
    lang: str,
    q: str = Query("", max_length=100),
    offset: int = Query(0, ge=0, le=100000),
    db: asyncpg.Connection = Depends(get_shared_db),
):
    """「これまでの画像」。お知らせに上げた画像を新しい順に（ファイル名で探せる）。"""
    return await media_service.list_media(db, "announcement", q=q, offset=offset)


@router.post("/uploaded-images/{media_id:int}/remove", name="admin_announcement_uploaded_image_remove")
async def admin_announcement_uploaded_image_remove(
    request: Request,
    lang: str,
    media_id: int,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    """「これまでの画像」から外す。お知らせの本文・履歴で使っているかもしれないので、ファイルは残す。"""
    if not await media_service.remove(db, "announcement", media_id, user_id=current_user.id):
        return JSONResponse({"success": False, "message": "画像が見つかりません。"}, status_code=404)
    logger.info(f"お知らせの画像 (ID: {media_id})を「これまでの画像」から外しました（ユーザー ID: {current_user.id}）")
    return {"success": True, "message": "一覧から外しました。"}


@router.get("/{post_id:int}", name="admin_announcement_edit")
async def admin_announcement_edit(
    request: Request,
    lang: str,
    post_id: int,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    row = await svc.get_admin_post(db, post_id)
    if row is None:
        raise HTTPException(status_code=404, detail="お知らせが見つかりません")
    context = await _editor_context(request, lang, db, current_user, mode="post", post=_post_for_editor(row))
    return templates.TemplateResponse("admin/announcement_edit.html", context)


@router.get("/{post_id:int}/data", name="admin_announcement_data")
async def admin_announcement_data(
    request: Request,
    lang: str,
    post_id: int,
    db: asyncpg.Connection = Depends(get_shared_db),
):
    """最新の内容（同時編集で先に保存されたときの読み込み直し用）。"""
    row = await svc.get_admin_post(db, post_id)
    if row is None:
        raise HTTPException(status_code=404, detail="お知らせが見つかりません")
    return {"post": _post_for_editor(row)}


@router.get("/{post_id:int}/revisions", name="admin_announcement_revisions")
async def admin_announcement_revisions(
    request: Request,
    lang: str,
    post_id: int,
    db: asyncpg.Connection = Depends(get_shared_db),
):
    rows = await svc.list_revisions(db, post_id)
    labels = {"create": "作成", "update": "保存", "delete": "削除", "restore": "復元", "migrate": "移行"}
    return {"items": [
        {"id": r["id"], "version": r["version"], "action": labels.get(r["action"], r["action"]),
         "edited_at": _jst_text(r["edited_at"]), "edited_by": r["edited_by_name"] or ("（移行）" if r["action"] == "migrate" else "")}
        for r in rows
    ]}


@router.get("/{post_id:int}/revisions/{revision_id:int}", name="admin_announcement_revision")
async def admin_announcement_revision(
    request: Request,
    lang: str,
    post_id: int,
    revision_id: int,
    db: asyncpg.Connection = Depends(get_shared_db),
):
    """その版の中身（編集画面に読み込み、保存すると「この版に戻す」になる）。"""
    row = await svc.get_revision(db, post_id, revision_id)
    if row is None:
        raise HTTPException(status_code=404, detail="変更の履歴が見つかりません")
    snap = dict(row["snapshot"])
    snap.pop("legacy", None)
    if snap.get("published_at"):
        snap["published_at"] = _jst_input(datetime.datetime.fromisoformat(snap["published_at"]))
    return {"snapshot": snap, "version": row["version"], "edited_at": _jst_text(row["edited_at"])}


# --- 保存・削除・復元 ------------------------------------------------------------------------
@router.post("/preview", name="admin_announcement_preview")
async def admin_announcement_preview(request: Request, lang: str, payload: dict = Body(...)):
    """プレビュー。保存と同じ消毒と、表示と同じ処理（端末・言語）を通す。"""
    platform = payload.get("platform") if payload.get("platform") in ("web", "ios", "android") else "web"
    view_lang = "en" if payload.get("lang") == "en" else "ja"
    title = sanitize_title(str(payload.get("title") or ""))
    body = sanitize_body(str(payload.get("body") or ""))
    return {
        "title_html": render_html(title, platform=platform, lang=view_lang),
        "body_html": render_html(body, platform=platform, lang=view_lang),
    }


@router.post("/save", name="admin_announcement_save")
async def admin_announcement_save(
    request: Request,
    lang: str,
    payload: dict = Body(...),
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    post_id = payload.get("id")
    if post_id is not None and not isinstance(post_id, int):
        return JSONResponse({"success": False, "message": "ID が正しくありません。"}, status_code=400)
    try:
        data = dict(payload)
        data["published_at"] = _parse_published_at(payload)
        values = svc.clean_post_input(data)
        expected = payload.get("version") if isinstance(payload.get("version"), int) else None
        result = await svc.save_post(db, post_id, values, user_id=current_user.id, expected_version=expected)
    except svc.AnnouncementError as e:
        return _error(e)
    except DataBaseError as e:
        logger.error(f"アナウンスの保存中にデータベースエラー: {e}", exc_info=True)
        return JSONResponse({"success": False, "message": "保存に失敗しました（データベースエラー）。"}, status_code=500)
    row = result["row"]
    title = html_to_text(row["title_ja"] or row["title_en"], limit=80) or "（タイトルなし）"
    if result["changed"]:
        created = post_id is None
        await _notify_admin_action(
            db, current_user,
            "announcement_created" if created else "announcement_updated",
            "アナウンスを追加しました" if created else "アナウンスを変更しました",
            f"管理者操作 / ID {row['id']}「{title}」" + ("" if created else " を更新"),
            {"announcement_id": row["id"]},
            request=request, target_type="announcement", target_id=row["id"],
        )
    state = _post_state(row)
    message = {
        "draft": "下書きを保存しました。",
        "scheduled": f"保存しました。{_jst_text(row['published_at'])} に公開されます。",
        "published": "保存しました。公開中です。",
    }[state] if result["changed"] else "変更はありません。"
    return {
        "success": True,
        "message": message,
        "id": row["id"],
        "version": row["version"],
        "state": state,
        "published_at": _jst_input(row["published_at"]),
        "updated_at": _jst_text(row["updated_at"]),
        "edit_url": str(request.url_for("admin_announcement_edit", lang=lang, post_id=row["id"])),
    }


async def _set_deleted(request: Request, lang: str, post_id: int, deleted: bool, db: asyncpg.Connection, user: User):
    try:
        row = await svc.set_deleted(db, post_id, deleted=deleted, user_id=user.id)
    except svc.AnnouncementError as e:
        return _error(e)
    except DataBaseError as e:
        logger.error(f"アナウンスの{'削除' if deleted else '復元'}中にデータベースエラー: {e}", exc_info=True)
        return JSONResponse({"success": False, "message": "データベースエラーが発生しました。"}, status_code=500)
    title = html_to_text(row["title_ja"] or row["title_en"], limit=80)
    await _notify_admin_action(
        db, user,
        "announcement_deleted" if deleted else "announcement_restored",
        "アナウンスを削除しました" if deleted else "アナウンスを復元しました",
        f"管理者操作 / ID {post_id}「{title}」を{'削除' if deleted else '復元'}",
        {"announcement_id": post_id},
        request=request, target_type="announcement", target_id=post_id,
    )
    return {"success": True, "message": "削除しました。「削除済み」から復元できます。" if deleted else "復元しました。",
            "version": row["version"], "state": _post_state(row)}


@router.post("/{post_id:int}/delete", name="admin_announcement_delete")
async def admin_announcement_delete(
    request: Request, lang: str, post_id: int,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    return await _set_deleted(request, lang, post_id, True, db, current_user)


@router.post("/{post_id:int}/restore", name="admin_announcement_restore")
async def admin_announcement_restore(
    request: Request, lang: str, post_id: int,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    return await _set_deleted(request, lang, post_id, False, db, current_user)


@router.post("/templates/save", name="admin_announcement_template_save")
async def admin_announcement_template_save(
    request: Request,
    lang: str,
    payload: dict = Body(...),
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    template_id = payload.get("id")
    if template_id is not None and not isinstance(template_id, int):
        return JSONResponse({"success": False, "message": "ID が正しくありません。"}, status_code=400)
    try:
        values = svc.clean_template_input(payload)
        row = await svc.save_template(db, template_id, values, user_id=current_user.id)
    except svc.AnnouncementError as e:
        return _error(e)
    except DataBaseError as e:
        logger.error(f"アナウンスのテンプレートの保存中にデータベースエラー: {e}", exc_info=True)
        return JSONResponse({"success": False, "message": "保存に失敗しました（データベースエラー）。"}, status_code=500)
    logger.info(f"アナウンスの{'部品' if row['kind'] == 'snippet' else 'テンプレート'} #{row['id']}「{row['name']}」を保存しました。")
    return {
        "success": True,
        "message": "保存しました。",
        "id": row["id"],
        "template": _template_for_editor(row),
        "edit_url": str(request.url_for("admin_announcement_template_edit", lang=lang, template_id=row["id"])),
    }


@router.post("/templates/{template_id:int}/delete", name="admin_announcement_template_delete")
async def admin_announcement_template_delete(
    request: Request, lang: str, template_id: int,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    row = await svc.get_template(db, template_id)
    if row is None or not await svc.delete_template(db, template_id):
        return JSONResponse({"success": False, "message": "テンプレートが見つかりません。"}, status_code=404)
    logger.info(f"アナウンスの{'部品' if row['kind'] == 'snippet' else 'テンプレート'} #{template_id}「{row['name']}」を削除しました（{current_user.id}）。")
    return {"success": True, "message": "削除しました。"}


@router.get("/{post_id:int}/view", name="admin_announcement_view")
async def admin_announcement_view(request: Request, lang: str, post_id: int):
    """ユーザー向けの個別ページへ（管理者には下書き・予約も見える）。"""
    return RedirectResponse(request.url_for("announcement_detail", lang=lang, post_id=post_id), status_code=302)
