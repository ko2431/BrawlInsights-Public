"""ミニゲーム企画管理（一覧・作成とコピー・設定・景品と還元率・絵柄・状況・開始のお知らせ）。

検討メモ notes_private/minigame_campaign_plan_2026-11-09.md の 7a。参加履歴は今までどおり admin.py の /minigame-plays。
権限は admin_permissions の resolve_admin_route_permission（GET は閲覧、POST は編集）。
"""
from __future__ import annotations

from datetime import datetime, timezone

import asyncpg
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.core.admin_permissions import PERM_ANNOUNCEMENTS_EDIT, PERM_MINIGAME_CAMPAIGNS_EDIT, PERM_MINIGAME_PLAYS_VIEW
from app.core.logger import logger
from app.core.templating import templates
from app.db.db import get_shared_db
from app.routers.admin import _notify_admin_action, enforce_admin_route_permission, format_admin_jst_datetime, get_current_active_user
from app.services import minigame_assets as ma
from app.services import minigame_campaign_service as mcs
from app.services import minigame_service as ms
from app.services.board_campaign_service import fmt_jst
from app.services.giveaway_service import fmt_left
from app.services.special_reward_link_service import list_links_for_minigame_select
from app.services.user_service import User

router = APIRouter(
    prefix="/{lang}/admin/minigame-campaigns",
    tags=["Admin Minigame Campaigns"],
    dependencies=[Depends(enforce_admin_route_permission)],
)

TABS = ("settings", "prizes", "assets", "status")


def _error(e: mcs.MinigameAdminError) -> JSONResponse:
    return JSONResponse({"success": False, "message": e.message_ja}, status_code=400)


async def _campaign_or_404(db: asyncpg.Connection, campaign_id: int) -> mcs.Campaign:
    c = await mcs.get_campaign(db, campaign_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return c


def _period_text(c: mcs.Campaign) -> str:
    return f"{fmt_jst(c.starts_at, relative=False)} 〜 {fmt_jst(c.ends_at, relative=False)}"


def _days_text(c: mcs.Campaign) -> str:
    hours = round((c.ends_at - c.starts_at).total_seconds() / 3600)
    days, rest = divmod(hours, 24)
    return f"{days}日" + (f"{rest}時間" if rest else "") if days else f"{rest}時間"


def _asset_specs() -> dict:
    """ゲームの種類×等級の数ごとの絵柄の枠と標準の絵柄（画面で枠を作り直すため）。"""
    specs: dict = {}
    for game in ma.GAME_LABELS:
        specs[game] = {}
        for n in range(ma.MIN_TIERS, ma.MAX_TIERS + 1):
            if (game, n) not in ma.CARD_ASSETS:
                continue
            spec = ma.asset_spec(game, n)
            specs[game][n] = {"ranks": spec["ranks"], "decoys": spec["decoys"], "pool": spec["pool"], "standard": ma.standard_assets(game, n)}
    return specs


@router.get("", name="admin_minigame_campaigns")
async def admin_minigame_campaigns(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    now = datetime.now(timezone.utc)
    campaigns = await mcs.list_campaigns(db)
    counts = await mcs.campaign_counts(db, [c.id for c in campaigns])
    warnings = await mcs.unpublished_warning_ids(db)
    items = []
    for c in campaigns:
        state = c.state(now)
        n = counts.get(c.id, {})
        items.append({
            "c": c, "state": state, "state_label": mcs.STATE_LABELS[state], "period": _period_text(c), "days": _days_text(c),
            "left": fmt_left(c.ends_at - now, "ja") if state == "live" else "",
            "game": ma.GAME_LABELS.get(c.game_type, c.game_type), "tiers": c.tier_count,
            "users": n.get("users", 0), "plays": n.get("plays", 0), "admin_plays": n.get("admin_plays", 0),
            "gift_pending": n.get("gift_pending", 0), "unpublished_warning": c.id in warnings,
        })
    return templates.TemplateResponse("admin/minigame_campaigns.html", {
        "request": request, "lang": lang, "current_page": "account", "items": items,
        "next_round": await mcs.gs.next_round_no(db),
        "can_edit": current_user.has_perm(PERM_MINIGAME_CAMPAIGNS_EDIT),
        "can_view_plays": current_user.has_perm(PERM_MINIGAME_PLAYS_VIEW),
    })


@router.post("/create", name="admin_minigame_campaign_create")
async def admin_minigame_campaign_create(
    request: Request,
    payload: dict = Body(default={}),
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    source = None
    if payload.get("copy_from"):
        try:
            source = await mcs.get_campaign(db, int(payload["copy_from"]))
        except (TypeError, ValueError):
            source = None
        if source is None:
            return JSONResponse({"success": False, "message": "コピー元の企画が見つかりません。"}, status_code=404)
    campaign_id = await mcs.create_campaign(db, current_user.id, source)
    c = await mcs.get_campaign(db, campaign_id)
    await _notify_admin_action(
        db, current_user, "minigame_campaign_created", "ミニゲーム企画を作成しました",
        f"管理者操作 / ミニゲーム企画 #{campaign_id}「{c.name_ja}」を下書きで作成" + (f"（#{source.id} をコピー）" if source else ""),
        {"campaign_id": campaign_id}, request=request, target_type="minigame_campaign", target_id=campaign_id,
    )
    return {"success": True, "id": campaign_id}


@router.get("/assets", name="admin_minigame_assets")
async def admin_minigame_assets(
    kind: str = Query("spray"),
    q: str = Query("", max_length=20),
    offset: int = Query(0, ge=0),
):
    """絵柄の選択の一覧（スプレー・ピン。新しい順。ID の一部で絞る）。"""
    if kind not in ma.ASSET_KINDS:
        return JSONResponse({"success": False, "message": "種類が正しくありません。"}, status_code=400)
    return {"success": True, **ma.asset_catalog(kind, query="".join(ch for ch in q if ch.isdigit()), offset=offset)}


@router.get("/{campaign_id:int}", name="admin_minigame_campaign")
async def admin_minigame_campaign(
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
    can_edit = current_user.has_perm(PERM_MINIGAME_CAMPAIGNS_EDIT)
    special_reward_links = []
    if can_edit:
        try:
            special_reward_links = await list_links_for_minigame_select(db)
        except Exception as e:
            logger.warning(f"ミニゲーム企画用の特別報酬リンクの一覧の取得に失敗: {e}", exc_info=True)
    start_announcement = None
    if c.start_announcement_id:
        start_announcement = await db.fetchrow(
            "SELECT id, title_ja, status, published_at, deleted_at FROM announcement_posts WHERE id = $1", c.start_announcement_id,
        )
    price_ad, price_token, _ = c.prices
    return templates.TemplateResponse("admin/minigame_campaign.html", {
        "request": request, "lang": lang, "current_page": "account",
        "c": c, "state": state, "state_label": mcs.STATE_LABELS[state], "tab": tab,
        "can_edit": can_edit,
        "can_start_announce": can_edit and current_user.has_perm(PERM_ANNOUNCEMENTS_EDIT),
        "can_view_plays": current_user.has_perm(PERM_MINIGAME_PLAYS_VIEW),
        "period_text": _period_text(c), "days_text": _days_text(c),
        "left": fmt_left(c.ends_at - now, "ja") if state == "live" else "",
        "started": c.started(now),
        "form": mcs.form_values(c),
        "rates": mcs.return_rates(c.prizes, price_token, price_ad, c.expected_total_plays or ms.DEFAULT_EXPECTED_TOTAL_PLAYS, c.settings),
        "status": await mcs.campaign_status(db, c),
        "start_announcement": start_announcement,
        "start_template_ok": await mcs.get_start_template(db) is not None,
        "start_template_name": mcs.START_TEMPLATE_NAME,
        "special_reward_links": special_reward_links,
        "game_labels": ma.GAME_LABELS,
        "asset_specs": _asset_specs(),
        "unpublished_warning": c.id in await mcs.unpublished_warning_ids(db),
        "format_admin_jst_datetime": format_admin_jst_datetime, "now": now,
    })


@router.post("/{campaign_id:int}/preview", name="admin_minigame_campaign_preview")
async def admin_minigame_campaign_preview(
    lang: str,
    campaign_id: int,
    payload: dict = Body(...),
    db: asyncpg.Connection = Depends(get_shared_db),
):
    """編集中の設定から、還元率・絵柄の検査・遊び方の表を作る（保存はしない）。"""
    c = await _campaign_or_404(db, campaign_id)
    out: dict = {"success": True}
    try:
        prizes = mcs._clean_prizes(payload.get("prizes") if not c.started() else c.prizes)
        steps, repeat = mcs._clean_bonuses(payload)
        settings = {**c.settings, "bonus_steps": steps, "bonus_repeat": repeat}
        price_token = mcs._int_in(payload.get("price_token_tokens"), 1, 10000, "トークン参加の価格")
        price_ad = mcs._int_in(payload.get("price_ad_tokens"), 1, 10000, "割引参加の価格")
        expected = mcs._int_in(payload.get("expected_total_plays"), 1, 10**7, "想定総参加数")
    except mcs.MinigameAdminError as e:
        return {"success": False, "message": e.message_ja}
    out["rates"] = mcs.return_rates(prizes, price_token, price_ad, expected, settings)
    game_type = payload.get("game_type") if not c.started() else c.game_type
    tier_count = len(prizes["tiers"])
    if (game_type, tier_count) not in ma.CARD_ASSETS:
        out["asset_errors"] = ["このゲームでは、この等級の数は使えません。"]
        return out
    assets = None
    if payload.get("assets_custom"):
        assets = ma.normalize_assets(game_type, tier_count, payload.get("assets"))
        out["asset_errors"] = list(dict.fromkeys(ma.validate_assets(game_type, tier_count, assets) or ms.verify_assets(game_type, tier_count, assets)))
    else:
        out["asset_errors"] = []
    if not out["asset_errors"]:
        out["howto"] = ms.build_howto({"game_type": game_type, "prizes": prizes, "settings": {"assets": assets}}, lang, "")
    return out


@router.post("/{campaign_id:int}/save", name="admin_minigame_campaign_save")
async def admin_minigame_campaign_save(
    request: Request,
    campaign_id: int,
    payload: dict = Body(...),
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    c = await _campaign_or_404(db, campaign_id)
    try:
        await mcs.save_campaign(db, c, payload, current_user.id)
    except mcs.MinigameAdminError as e:
        return _error(e)
    await _notify_admin_action(
        db, current_user, "minigame_campaign_updated", "ミニゲーム企画を変更しました",
        f"管理者操作 / ミニゲーム企画 #{c.id}「{payload.get('name_ja') or c.name_ja}」の設定を保存",
        {"campaign_id": c.id}, request=request, target_type="minigame_campaign", target_id=c.id,
    )
    return {"success": True, "message": "保存しました。"}


@router.post("/{campaign_id:int}/publish", name="admin_minigame_campaign_publish")
async def admin_minigame_campaign_publish(
    request: Request,
    campaign_id: int,
    payload: dict = Body(...),
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    c = await _campaign_or_404(db, campaign_id)
    published = bool(payload.get("published"))
    try:
        await mcs.set_published(db, c, published, current_user.id)
    except mcs.MinigameAdminError as e:
        return _error(e)
    await _notify_admin_action(
        db, current_user, "minigame_campaign_updated", "ミニゲーム企画を公開しました" if published else "ミニゲーム企画を取り下げました",
        f"管理者操作 / ミニゲーム企画 #{c.id}「{c.name_ja}」を{'公開' if published else '取り下げ'}",
        {"campaign_id": c.id, "published": published}, request=request, target_type="minigame_campaign", target_id=c.id,
    )
    return {"success": True}


@router.post("/{campaign_id:int}/cancel", name="admin_minigame_campaign_cancel")
async def admin_minigame_campaign_cancel(
    request: Request,
    campaign_id: int,
    payload: dict = Body(...),
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    c = await _campaign_or_404(db, campaign_id)
    canceled = bool(payload.get("canceled"))
    try:
        await mcs.set_canceled(db, c, canceled, current_user.id)
    except mcs.MinigameAdminError as e:
        return _error(e)
    await _notify_admin_action(
        db, current_user, "minigame_campaign_updated", "ミニゲーム企画を中止しました" if canceled else "ミニゲーム企画の中止を取り消しました",
        f"管理者操作 / ミニゲーム企画 #{c.id}「{c.name_ja}」を{'中止' if canceled else '中止の取り消し'}",
        {"campaign_id": c.id, "canceled": canceled}, request=request, target_type="minigame_campaign", target_id=c.id,
    )
    return {"success": True}


@router.post("/{campaign_id:int}/delete", name="admin_minigame_campaign_delete")
async def admin_minigame_campaign_delete(
    request: Request,
    campaign_id: int,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    c = await _campaign_or_404(db, campaign_id)
    try:
        await mcs.delete_draft(db, c)
    except mcs.MinigameAdminError as e:
        return _error(e)
    await _notify_admin_action(
        db, current_user, "minigame_campaign_updated", "ミニゲーム企画を削除しました",
        f"管理者操作 / ミニゲーム企画 #{c.id}「{c.name_ja}」（下書き）を削除",
        {"campaign_id": c.id}, request=request, target_type="minigame_campaign", target_id=c.id,
    )
    return {"success": True}


@router.post("/{campaign_id:int}/start-announcement", name="admin_minigame_campaign_start_announcement")
async def admin_minigame_campaign_start_announcement(
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
        post_id = await mcs.create_start_announcement(db, c.id, current_user.id)
    except mcs.MinigameAdminError as e:
        return _error(e)
    await _notify_admin_action(
        db, current_user, "minigame_campaign_updated", "ミニゲーム企画の開始のお知らせの下書きを作りました",
        f"管理者操作 / ミニゲーム企画 #{c.id}「{c.name_ja}」の開始のお知らせ #{post_id} の下書きを作成",
        {"campaign_id": c.id, "post_id": post_id}, request=request, target_type="minigame_campaign", target_id=c.id,
    )
    return {"success": True, "post_id": post_id, "edit_url": str(request.url_for("admin_announcement_edit", lang=lang, post_id=post_id))}
