"""事前応募型企画の管理画面（一覧・作成とコピー・設定・応募の状況・当選者・景品の状況）。

検討メモ notes_private/giveaway_campaign_plan_2026-11-04.md の 7章。ミニゲーム型の管理画面とは分ける（GD22）。
権限は admin_permissions の resolve_admin_route_permission（GET は閲覧、当選者・当選発表は当選者、ほかの POST は編集）。
"""
from __future__ import annotations

from datetime import datetime, timezone

import asyncpg
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.core.admin_permissions import PERM_ANNOUNCEMENTS_EDIT, PERM_GIVEAWAYS_DRAW, PERM_GIVEAWAYS_EDIT
from app.core.templating import templates
from app.db.db import get_shared_db
from app.routers.admin import _notify_admin_action, enforce_admin_route_permission, format_admin_jst_datetime, get_current_active_user
from app.services import giveaway_prizes as prizes
from app.services import giveaway_service as gs
from app.services.board_campaign_service import fmt_jst
from app.services.user_service import User

router = APIRouter(
    prefix="/{lang}/admin/giveaways",
    tags=["Admin Giveaways"],
    dependencies=[Depends(enforce_admin_route_permission)],
)

TABS = ("settings", "status", "winners", "prizes")


def _error(e: gs.GiveawayError) -> JSONResponse:
    return JSONResponse({"success": False, "message": e.message_ja}, status_code=400)


async def _campaign_or_404(db: asyncpg.Connection, campaign_id: int) -> gs.Campaign:
    c = await gs.get_campaign(db, campaign_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return c


def _period_text(c: gs.Campaign) -> str:
    return f"{fmt_jst(c.starts_at, relative=False)} 〜 {fmt_jst(c.ends_at, relative=False)}"


def _days_text(c: gs.Campaign) -> str:
    hours = round((c.ends_at - c.starts_at).total_seconds() / 3600)
    days, rest = divmod(hours, 24)
    return f"{days}日" + (f"{rest}時間" if rest else "") if days else f"{rest}時間"


@router.get("", name="admin_giveaways")
async def admin_giveaways(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    now = datetime.now(timezone.utc)
    campaigns = await gs.list_campaigns(db)
    counts = {}
    if campaigns:
        counts = {r["campaign_id"]: r for r in await db.fetch(
            """
            SELECT campaign_id, count(DISTINCT user_id)::int AS users, sum(count)::int AS entries
            FROM giveaway_entries WHERE campaign_id = ANY($1::int[]) GROUP BY campaign_id
            """,
            [c.id for c in campaigns],
        )}
    items = []
    for c in campaigns:
        state = c.state(now)
        items.append({
            "c": c, "state": state, "state_label": gs.STATE_LABELS[state], "period": _period_text(c), "days": _days_text(c),
            "left": gs.fmt_left(c.ends_at - now, "ja") if state == "live" else "",
            "users": counts[c.id]["users"] if c.id in counts else 0,
            "entries": counts[c.id]["entries"] if c.id in counts else 0,
        })
    return templates.TemplateResponse("admin/giveaways.html", {
        "request": request, "lang": lang, "current_page": "account", "items": items,
        "next_round": await gs.next_round_no(db),
        "can_edit": current_user.has_perm(PERM_GIVEAWAYS_EDIT),
    })


@router.post("/create", name="admin_giveaway_create")
async def admin_giveaway_create(
    request: Request,
    payload: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    source = None
    if payload.get("copy_from"):
        try:
            source = await gs.get_campaign(db, int(payload["copy_from"]))
        except (TypeError, ValueError):
            source = None
        if source is None:
            return JSONResponse({"success": False, "message": "コピー元の企画が見つかりません。"}, status_code=404)
    campaign_id = await gs.create_campaign(db, current_user.id, source)
    c = await gs.get_campaign(db, campaign_id)
    await _notify_admin_action(
        db, current_user, "giveaway_created", "事前応募型企画を作成しました",
        f"管理者操作 / 事前応募型企画 #{campaign_id}「{c.name_ja}」を下書きで作成" + (f"（#{source.id} をコピー）" if source else ""),
        {"campaign_id": campaign_id}, request=request, target_type="giveaway", target_id=campaign_id,
    )
    return {"success": True, "id": campaign_id}


@router.get("/{campaign_id:int}", name="admin_giveaway")
async def admin_giveaway(
    request: Request,
    lang: str,
    campaign_id: int,
    tab: str = Query("settings"),
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    c = await _campaign_or_404(db, campaign_id)
    now = datetime.now(timezone.utc)
    state = c.state(now)
    if tab not in TABS:
        tab = "settings"
    rows = await gs.applicants(db, c) if c.starts_at <= now else []
    valid = [r["entries"] for r in rows if r["invalid_reason"] is None]
    live_stats = {
        "users": len(rows), "entries": sum(r["entries"] for r in rows),
        "valid_users": len(valid), "valid_entries": sum(valid),
        "participation": sum(gs.participation_tokens(c.settings, v) for v in valid),
    }
    announcements = {}
    for key, post_id in (("start", c.start_announcement_id), ("result", c.result_announcement_id)):
        if post_id:
            announcements[key] = await db.fetchrow(
                "SELECT id, title_ja, status, published_at, deleted_at FROM announcement_posts WHERE id = $1", post_id,
            )
    return templates.TemplateResponse("admin/giveaway.html", {
        "request": request, "lang": lang, "current_page": "account",
        "c": c, "state": state, "state_label": gs.STATE_LABELS[state], "tab": tab,
        "can_edit": current_user.has_perm(PERM_GIVEAWAYS_EDIT),
        "can_draw": current_user.has_perm(PERM_GIVEAWAYS_DRAW),
        "can_announce": current_user.has_perm(PERM_GIVEAWAYS_DRAW) and current_user.has_perm(PERM_ANNOUNCEMENTS_EDIT),
        "can_start_announce": current_user.has_perm(PERM_GIVEAWAYS_EDIT) and current_user.has_perm(PERM_ANNOUNCEMENTS_EDIT),
        "period_text": _period_text(c), "days_text": _days_text(c),
        "left": gs.fmt_left(c.ends_at - now, "ja") if state == "live" else "",
        "rules_locked": state not in ("draft", "scheduled"),
        "prizes_locked": c.winners_fixed_at is not None,
        "form": gs.form_values(c),
        "live_stats": live_stats,
        "daily": await gs.daily_rows(db, c),
        "distribution": gs.distribution(valid, c.total_limit),
        "top_rows": rows[:50],
        "invalid_rows": [r for r in rows if r["invalid_reason"]][:200],
        "exclusions": await gs.list_exclusions(db, c),
        "exclusion_reasons": gs.EXCLUSION_REASONS,
        "winners": await prizes.winners(db, c) if c.winners_fixed_at else [],
        "prize_status": await prizes.prize_status(db, c) if c.winners_fixed_at else None,
        "gift_status_labels": prizes.GIFT_STATUS_LABELS,
        "announcements": announcements,
        "start_template_ok": await prizes.get_start_template(db) is not None,
        "result_template_ok": await prizes.get_result_template(db) is not None,
        "tier_name": gs.tier_name, "tier_label": gs.tier_label,
        "participation_tokens": gs.participation_tokens,
        "format_admin_jst_datetime": format_admin_jst_datetime, "now": now,
    })


@router.post("/{campaign_id:int}/save", name="admin_giveaway_save")
async def admin_giveaway_save(
    request: Request,
    campaign_id: int,
    payload: dict = Body(...),
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    c = await _campaign_or_404(db, campaign_id)
    try:
        await gs.save_campaign(db, c, payload, current_user.id)
    except gs.GiveawayError as e:
        return _error(e)
    await _notify_admin_action(
        db, current_user, "giveaway_updated", "事前応募型企画を変更しました",
        f"管理者操作 / 事前応募型企画 #{c.id}「{payload.get('name_ja') or c.name_ja}」の設定を保存",
        {"campaign_id": c.id}, request=request, target_type="giveaway", target_id=c.id,
    )
    return {"success": True, "message": "保存しました。"}


@router.post("/{campaign_id:int}/publish", name="admin_giveaway_publish")
async def admin_giveaway_publish(
    request: Request,
    campaign_id: int,
    payload: dict = Body(...),
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    c = await _campaign_or_404(db, campaign_id)
    published = bool(payload.get("published"))
    try:
        await gs.set_published(db, c, published, current_user.id)
    except gs.GiveawayError as e:
        return _error(e)
    await _notify_admin_action(
        db, current_user, "giveaway_published", "事前応募型企画を公開しました" if published else "事前応募型企画を取り下げました",
        f"管理者操作 / 事前応募型企画 #{c.id}「{c.name_ja}」を{'公開' if published else '取り下げ'}",
        {"campaign_id": c.id, "published": published}, request=request, target_type="giveaway", target_id=c.id,
    )
    return {"success": True}


@router.post("/{campaign_id:int}/delete", name="admin_giveaway_delete")
async def admin_giveaway_delete(
    request: Request,
    campaign_id: int,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    c = await _campaign_or_404(db, campaign_id)
    try:
        await gs.delete_draft(db, c)
    except gs.GiveawayError as e:
        return _error(e)
    await _notify_admin_action(
        db, current_user, "giveaway_updated", "事前応募型企画を削除しました",
        f"管理者操作 / 事前応募型企画 #{c.id}「{c.name_ja}」（下書き）を削除",
        {"campaign_id": c.id}, request=request, target_type="giveaway", target_id=c.id,
    )
    return {"success": True}


@router.post("/{campaign_id:int}/start-announcement", name="admin_giveaway_start_announcement")
async def admin_giveaway_start_announcement(
    request: Request,
    lang: str,
    campaign_id: int,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    if not current_user.has_perm(PERM_ANNOUNCEMENTS_EDIT):
        return JSONResponse({"success": False, "message": "開始のお知らせを作るには、アナウンス管理の編集の権限も要ります。"}, status_code=403)
    c = await _campaign_or_404(db, campaign_id)
    try:
        post_id = await prizes.create_start_announcement(db, c.id, current_user.id)
    except gs.GiveawayError as e:
        return _error(e)
    await _notify_admin_action(
        db, current_user, "giveaway_updated", "事前応募型企画の開始のお知らせの下書きを作りました",
        f"管理者操作 / 事前応募型企画 #{c.id}「{c.name_ja}」の開始のお知らせ #{post_id} の下書きを作成",
        {"campaign_id": c.id, "post_id": post_id}, request=request, target_type="giveaway", target_id=c.id,
    )
    return {"success": True, "post_id": post_id, "edit_url": str(request.url_for("admin_announcement_edit", lang=lang, post_id=post_id))}


@router.get("/{campaign_id:int}/applicants", name="admin_giveaway_applicants")
async def admin_giveaway_applicants(
    campaign_id: int,
    db: asyncpg.Connection = Depends(get_shared_db),
):
    """抽選用の応募者のリスト（notes_private/giveaways.py の applicants に貼る形と、ID・口数の表）。"""
    c = await _campaign_or_404(db, campaign_id)
    rows = await gs.applicants(db, c)
    valid = [r for r in rows if r["invalid_reason"] is None]
    return {
        "success": True,
        "list_text": gs.applicants_list_text(rows),
        "table_text": "\n".join(f"{r['user_id']}\t{r['entries']}" for r in sorted(valid, key=lambda r: r["user_id"])),
        "users": len(valid),
        "entries": sum(r["entries"] for r in valid),
        "invalid": [{"user_id": r["user_id"], "name": r["name"], "entries": r["entries"], "reason": r["invalid_label"]}
                    for r in rows if r["invalid_reason"]],
    }


@router.post("/{campaign_id:int}/exclusions", name="admin_giveaway_exclusion_add")
async def admin_giveaway_exclusion_add(
    request: Request,
    campaign_id: int,
    payload: dict = Body(...),
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    c = await _campaign_or_404(db, campaign_id)
    try:
        user_id = int(payload.get("user_id"))
    except (TypeError, ValueError):
        return JSONResponse({"success": False, "message": "ユーザー ID を数で入れてください。"}, status_code=400)
    try:
        await gs.add_exclusion(db, c, user_id, str(payload.get("reason") or ""), str(payload.get("note") or ""), current_user.id)
    except gs.GiveawayError as e:
        return _error(e)
    await _notify_admin_action(
        db, current_user, "giveaway_exclusion", "事前応募型企画の除外のリストに足しました",
        f"管理者操作 / 事前応募型企画 #{c.id} の除外のリストにユーザー {user_id} を足す（{gs.EXCLUSION_REASONS.get(payload.get('reason'), '')}）",
        {"campaign_id": c.id, "user_id": user_id}, request=request, target_type="user", target_id=user_id,
    )
    return {"success": True, "message": "除外のリストに足しました。"}


@router.post("/{campaign_id:int}/exclusions/remove", name="admin_giveaway_exclusion_remove")
async def admin_giveaway_exclusion_remove(
    request: Request,
    campaign_id: int,
    payload: dict = Body(...),
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    c = await _campaign_or_404(db, campaign_id)
    try:
        user_id = int(payload.get("user_id"))
    except (TypeError, ValueError):
        return JSONResponse({"success": False, "message": "ユーザー ID が正しくありません。"}, status_code=400)
    try:
        await gs.remove_exclusion(db, c, user_id)
    except gs.GiveawayError as e:
        return _error(e)
    await _notify_admin_action(
        db, current_user, "giveaway_exclusion", "事前応募型企画の除外のリストから外しました",
        f"管理者操作 / 事前応募型企画 #{c.id} の除外のリストからユーザー {user_id} を外す",
        {"campaign_id": c.id, "user_id": user_id}, request=request, target_type="user", target_id=user_id,
    )
    return {"success": True}


def _tiers_text(payload: dict) -> list[str]:
    return [str(t or "") for t in (payload.get("tiers") or [])][:20]


@router.post("/{campaign_id:int}/check-winners", name="admin_giveaway_check_winners")
async def admin_giveaway_check_winners(
    campaign_id: int,
    payload: dict = Body(...),
    db: asyncpg.Connection = Depends(get_shared_db),
):
    c = await _campaign_or_404(db, campaign_id)
    return {"success": True, **await prizes.check_winners(db, c, _tiers_text(payload))}


@router.post("/{campaign_id:int}/fix", name="admin_giveaway_fix")
async def admin_giveaway_fix(
    request: Request,
    campaign_id: int,
    payload: dict = Body(...),
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    c = await _campaign_or_404(db, campaign_id)
    try:
        result = await prizes.fix_winners(db, c.id, _tiers_text(payload), current_user.id)
    except gs.GiveawayError as e:
        return _error(e)
    if not result["ok"]:
        return JSONResponse({"success": False, "message": "確定できませんでした。下の内容を確かめてください。", **result}, status_code=400)
    winners_count = sum(len(t["items"]) for t in result["tiers"])
    await _notify_admin_action(
        db, current_user, "giveaway_winners", "事前応募型企画の当選者を確定しました",
        f"管理者操作 / 事前応募型企画 #{c.id}「{c.name_ja}」の当選者（{winners_count}名）を確定",
        {"campaign_id": c.id}, request=request, target_type="giveaway", target_id=c.id,
    )
    return {"success": True, **result}


@router.post("/{campaign_id:int}/unfix", name="admin_giveaway_unfix")
async def admin_giveaway_unfix(
    request: Request,
    campaign_id: int,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    c = await _campaign_or_404(db, campaign_id)
    try:
        await prizes.unfix_winners(db, c.id)
    except gs.GiveawayError as e:
        return _error(e)
    await _notify_admin_action(
        db, current_user, "giveaway_winners", "事前応募型企画の当選者の確定を取り消しました",
        f"管理者操作 / 事前応募型企画 #{c.id}「{c.name_ja}」の当選者の確定を取り消し",
        {"campaign_id": c.id}, request=request, target_type="giveaway", target_id=c.id,
    )
    return {"success": True}


@router.post("/{campaign_id:int}/announcement", name="admin_giveaway_announcement")
async def admin_giveaway_announcement(
    request: Request,
    lang: str,
    campaign_id: int,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    if not current_user.has_perm(PERM_ANNOUNCEMENTS_EDIT):
        return JSONResponse({"success": False, "message": "当選発表のお知らせを作るには、アナウンス管理の編集の権限も要ります。"}, status_code=403)
    c = await _campaign_or_404(db, campaign_id)
    try:
        post_id = await prizes.create_result_announcement(db, c.id, current_user.id)
    except gs.GiveawayError as e:
        return _error(e)
    await _notify_admin_action(
        db, current_user, "giveaway_winners", "事前応募型企画の当選発表の下書きを作りました",
        f"管理者操作 / 事前応募型企画 #{c.id}「{c.name_ja}」の当選発表のお知らせ #{post_id} の下書きを作成",
        {"campaign_id": c.id, "post_id": post_id}, request=request, target_type="giveaway", target_id=c.id,
    )
    return {"success": True, "post_id": post_id, "edit_url": str(request.url_for("admin_announcement_edit", lang=lang, post_id=post_id))}


@router.post("/{campaign_id:int}/gift-status", name="admin_giveaway_gift_status")
async def admin_giveaway_gift_status(
    request: Request,
    campaign_id: int,
    payload: dict = Body(...),
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    c = await _campaign_or_404(db, campaign_id)
    try:
        prize_id = int(payload.get("prize_id"))
        user_id = await prizes.set_gift_status(db, c, prize_id, str(payload.get("status") or ""), str(payload.get("note") or ""))
    except (TypeError, ValueError):
        return JSONResponse({"success": False, "message": "景品の指定が正しくありません。"}, status_code=400)
    except gs.GiveawayError as e:
        return _error(e)
    await _notify_admin_action(
        db, current_user, "giveaway_winners", "事前応募型企画のギフトの連絡の状態を変えました",
        f"管理者操作 / 事前応募型企画 #{c.id} の景品 {prize_id}（ユーザー {user_id}）を「{prizes.GIFT_STATUS_LABELS[payload.get('status')][0]}」に",
        {"campaign_id": c.id, "prize_id": prize_id, "user_id": user_id}, request=request, target_type="user", target_id=user_id,
    )
    return {"success": True, "message": "保存しました。"}
