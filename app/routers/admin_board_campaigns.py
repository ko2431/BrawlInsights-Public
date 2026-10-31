"""掲示板企画の管理画面（一覧・企画の設定・集計・確定賞の順位・ユーザーの内訳・無効のリスト）。

検討メモ notes_private/board_campaign_plan_2026-10-31.md の 10.3（モック 15.5）。確定・抽選・景品の状況は 5b。
admin.py が大きいため分けている。権限は admin_permissions の resolve_admin_route_permission（GET は閲覧、POST は編集）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import asyncpg
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.core.admin_permissions import PERM_BOARD_CAMPAIGNS_EDIT
from app.core.logger import logger
from app.core.templating import templates
from app.db.db import get_shared_db
from app.routers.admin import _notify_admin_action, enforce_admin_route_permission, format_admin_jst_datetime, get_current_active_user
from app.services import board_campaign_service as bcs
from app.services.user_service import User

router = APIRouter(
    prefix="/{lang}/admin/board-campaigns",
    tags=["Admin Board Campaigns"],
    dependencies=[Depends(enforce_admin_route_permission)],
)

STATE_LABELS = {"draft": "下書き", "scheduled": "予約", "live": "開催中", "counting": "集計中"}
TABS = ("settings", "live", "ranking", "user", "exclude")


def _error(e: bcs.CampaignError) -> JSONResponse:
    return JSONResponse({"success": False, "message": e.message_ja}, status_code=400)


async def _campaign_or_404(db: asyncpg.Connection, campaign_id: int) -> bcs.Campaign:
    c = await bcs.get_campaign(db, campaign_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return c


def _period_text(c: bcs.Campaign) -> str:
    return f"{bcs.fmt_jst(c.starts_at, relative=False)} 〜 {bcs.fmt_jst(c.ends_at, relative=False)}"


@router.get("", name="admin_board_campaigns")
async def admin_board_campaigns(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    now = datetime.now(timezone.utc)
    campaigns = []
    for c in await bcs.list_campaigns(db):
        state = c.state(now)
        campaigns.append({
            "c": c,
            "state": state,
            "state_label": STATE_LABELS[state],
            "period": _period_text(c),
            "days": c.days,
            "remaining": bcs.fmt_remaining(c.ends_at - now, "ja") if state == "live" else "",
        })
    return templates.TemplateResponse("admin/board_campaigns.html", {
        "request": request, "lang": lang, "current_page": "account", "campaigns": campaigns,
        "can_edit": current_user.has_perm(PERM_BOARD_CAMPAIGNS_EDIT),
    })


@router.post("/create", name="admin_board_campaign_create")
async def admin_board_campaign_create(
    request: Request,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    campaign_id = await bcs.create_next_campaign(db, current_user.id)
    c = await bcs.get_campaign(db, campaign_id)
    await _notify_admin_action(
        db, current_user, "board_campaign_created", "掲示板企画を作成しました",
        f"管理者操作 / 掲示板企画 #{campaign_id}「{c.name_ja}」を下書きで作成",
        {"campaign_id": campaign_id}, request=request, target_type="board_campaign", target_id=campaign_id,
    )
    return {"success": True, "id": campaign_id}


@router.get("/{campaign_id:int}", name="admin_board_campaign")
async def admin_board_campaign(
    request: Request,
    lang: str,
    campaign_id: int,
    tab: str = Query("settings"),
    award: str = Query("single_like"),
    user_id: int | None = Query(None),
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    c = await _campaign_or_404(db, campaign_id)
    now = datetime.now(timezone.utc)
    state = c.state(now)
    if tab not in TABS:
        tab = "settings"
    if award not in bcs.AWARD_BY_KEY:
        award = "single_like"

    rankings: dict[str, list[dict[str, Any]]] = {}
    flags: dict[str, dict[int, dict[str, int]]] = {}
    for a in c.enabled_awards():
        rankings[a.key] = await bcs.award_ranking(db, c, a.key)
    for key in ("single_like", "total_like"):
        if rankings.get(key):
            try:
                flags[key] = await bcs.self_like_flags(db, c, key, rankings[key])
            except Exception as e:
                logger.warning(f"掲示板企画 #{c.id} の自演いいねの印の取得に失敗しました: {e}", exc_info=True)

    breakdown = None
    if user_id:
        breakdown = await bcs.user_breakdown(db, c, user_id, now)
        tab = "user"

    stats = c.stats or {}
    awards_settings = c.settings["awards"]
    return templates.TemplateResponse("admin/board_campaign.html", {
        "request": request, "lang": lang, "current_page": "account",
        "c": c, "state": state, "state_label": STATE_LABELS[state], "tab": tab, "award_tab": award,
        "can_edit": current_user.has_perm(PERM_BOARD_CAMPAIGNS_EDIT),
        "period_locked": state in ("live", "counting"),
        "period_text": _period_text(c),
        "remaining": bcs.fmt_remaining(c.ends_at - now, "ja") if state == "live" else "",
        "form": {
            "name_ja": c.name_ja, "name_en": c.name_en,
            "starts_at": bcs.to_jst_local(c.starts_at), "ends_at": bcs.to_jst_local(c.ends_at),
            "start_announcement_id": c.start_announcement_id or "",
            "methods": c.settings["methods"], "tiers": c.settings["tiers"],
            "awards": {a.key: awards_settings[a.key] for a in bcs.AWARDS},
            "bonus": c.settings["bonus"],
            "participation_tokens": c.settings["participation_tokens"],
            "claim_hours": c.settings["claim_hours"], "gift_contact_hours": c.settings["gift_contact_hours"],
        },
        "methods": bcs.METHODS, "awards": bcs.AWARDS,
        "stats": stats, "counted_at": format_admin_jst_datetime(c.counted_at) if c.counted_at else "",
        "locked_through": c.locked_through,
        "rankings": rankings, "flags": flags,
        "exclusions": await bcs.list_exclusions(db, c),
        "auto_invalid": await bcs.list_auto_invalid(db, c),
        "exclusion_reasons": bcs.EXCLUSION_REASONS,
        "breakdown": breakdown, "breakdown_user_id": user_id,
        "start_announcement": await db.fetchrow(
            "SELECT id, title_ja FROM announcement_posts WHERE id = $1", c.start_announcement_id
        ) if c.start_announcement_id else None,
        "format_admin_jst_datetime": format_admin_jst_datetime,
    })


@router.post("/{campaign_id:int}/save", name="admin_board_campaign_save")
async def admin_board_campaign_save(
    request: Request,
    campaign_id: int,
    payload: dict = Body(...),
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    c = await _campaign_or_404(db, campaign_id)
    try:
        await bcs.save_campaign(db, c, payload, current_user.id)
    except bcs.CampaignError as e:
        return _error(e)
    await _notify_admin_action(
        db, current_user, "board_campaign_updated", "掲示板企画を変更しました",
        f"管理者操作 / 掲示板企画 #{c.id}「{payload.get('name_ja') or c.name_ja}」の設定を保存",
        {"campaign_id": c.id}, request=request, target_type="board_campaign", target_id=c.id,
    )
    return {"success": True, "message": "保存しました。"}


@router.post("/{campaign_id:int}/publish", name="admin_board_campaign_publish")
async def admin_board_campaign_publish(
    request: Request,
    campaign_id: int,
    payload: dict = Body(...),
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    c = await _campaign_or_404(db, campaign_id)
    published = bool(payload.get("published"))
    try:
        await bcs.set_published(db, c, published, current_user.id)
    except bcs.CampaignError as e:
        return _error(e)
    await _notify_admin_action(
        db, current_user, "board_campaign_published", "掲示板企画を公開しました" if published else "掲示板企画を取り下げました",
        f"管理者操作 / 掲示板企画 #{c.id}「{c.name_ja}」を{'公開' if published else '取り下げ'}",
        {"campaign_id": c.id, "published": published}, request=request, target_type="board_campaign", target_id=c.id,
    )
    if published and c.starts_at <= datetime.now(timezone.utc):
        # 始まっている企画（11月のように後から公開したもの）は、すぐ数えて帯に出す
        try:
            await bcs.recount_due_campaigns(db, campaign_id=c.id)
        except Exception as e:
            logger.error(f"掲示板企画 #{c.id} の公開の後の数え直しに失敗しました: {e}", exc_info=True)
    return {"success": True}


@router.post("/{campaign_id:int}/delete", name="admin_board_campaign_delete")
async def admin_board_campaign_delete(
    request: Request,
    campaign_id: int,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    c = await _campaign_or_404(db, campaign_id)
    try:
        await bcs.delete_draft(db, c)
    except bcs.CampaignError as e:
        return _error(e)
    await _notify_admin_action(
        db, current_user, "board_campaign_updated", "掲示板企画を削除しました",
        f"管理者操作 / 掲示板企画 #{c.id}「{c.name_ja}」（下書き）を削除",
        {"campaign_id": c.id}, request=request, target_type="board_campaign", target_id=c.id,
    )
    return {"success": True}


@router.post("/{campaign_id:int}/recount", name="admin_board_campaign_recount")
async def admin_board_campaign_recount(
    campaign_id: int,
    db: asyncpg.Connection = Depends(get_shared_db),
):
    c = await _campaign_or_404(db, campaign_id)
    if c.state() not in ("live", "counting"):
        return JSONResponse({"success": False, "message": "公開中で、始まっている企画だけ数え直せます。"}, status_code=400)
    done = await bcs.recount_due_campaigns(db, campaign_id=c.id)
    if not done:
        return JSONResponse({"success": False, "message": "ほかで数え直しているところです。少し待ってからもう一度押してください。"}, status_code=409)
    return {"success": True, "message": "数え直しました。"}


@router.post("/{campaign_id:int}/exclusions", name="admin_board_campaign_exclusion_add")
async def admin_board_campaign_exclusion_add(
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
        await bcs.add_exclusion(db, c, user_id, str(payload.get("reason") or ""), str(payload.get("note") or ""), current_user.id)
    except bcs.CampaignError as e:
        return _error(e)
    await _notify_admin_action(
        db, current_user, "board_campaign_exclusion", "掲示板企画の無効のリストに足しました",
        f"管理者操作 / 掲示板企画 #{c.id} の無効のリストにユーザー {user_id} を足す（{bcs.EXCLUSION_REASONS.get(payload.get('reason'), '')}）",
        {"campaign_id": c.id, "user_id": user_id}, request=request, target_type="user", target_id=user_id,
    )
    return {"success": True, "message": "無効のリストに足しました（全体の数には次の集計で反映されます）。"}


@router.post("/{campaign_id:int}/exclusions/remove", name="admin_board_campaign_exclusion_remove")
async def admin_board_campaign_exclusion_remove(
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
    await bcs.remove_exclusion(db, c, user_id)
    await _notify_admin_action(
        db, current_user, "board_campaign_exclusion", "掲示板企画の無効のリストから外しました",
        f"管理者操作 / 掲示板企画 #{c.id} の無効のリストからユーザー {user_id} を外す",
        {"campaign_id": c.id, "user_id": user_id}, request=request, target_type="user", target_id=user_id,
    )
    return {"success": True}
