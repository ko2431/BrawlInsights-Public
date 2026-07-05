from fastapi import APIRouter, Request, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
import asyncpg
import math
import datetime
import uuid
from typing import Optional, Any

from app.exceptions.custom_exceptions import BrawlStarsAPIError
from app.core.logger import logger
from app.core.logging_config import add_log_info
from app.db.db import get_shared_db
from app.services.brawl_service import get_player, get_player_from_db, get_player_for_tracking_extension, calc_num_of_available_brawlers, get_club_name, search_players_fast, get_player_log_trends, PlayerStatsPageData, Battles, search_battles, add_auto_tracking_time, extend_battle_log_retention, get_battle_log_retention_months, get_max_accessory_counts, get_skin_catalog_stats, get_all_titles
from app.services.rating_service import build_player_rating_data
from app.core.cache import get_cache, get_redis, set_cache
from app.services.user_service import User, _current_token_claim_date
from app.utils.utils import format_tag, confirm_tag, get_icon_path, get_ranked_seasons_for_filter, get_remote_ip, get_normalized_ip
from app.exceptions.custom_exceptions import BrawlStarsAPIError, DataBaseError
from app.core.templating import templates

router = APIRouter(
    prefix="/{lang}/player",
    tags=["Players"]
)

# [この部分は公開用リポジトリでは非公開にされています]

    context = {
        "request": request,
        "lang": lang,
        "player": player,
        "player_icon_path": "/" + icon_path_rel,
        "current_tab": tab,
        "selected_brawler_id": current_brawler_id_for_tab,
        "battles_on_page": battles_on_page,
        "win": battles_on_page_win,
        "lose": battles_on_page_lose,
        "draw": battles_on_page_draw,
        "stats_data": stats_data,
        "graphs_data": graphs_data,
        "pagination": pagination_info,
        "current_page_num": page,
        "before_date_filter": before_date_str,
        "after_date_filter": after_date_str,
        "current_page": "home",
        "should_display_ranked_score_trend": should_display_ranked_score_trend,
        "selected_season_number": selected_season_number,
        "seasons": seasons_for_filter,
        "current_user": current_login_user,
    }
    return templates.TemplateResponse("player/fragments/battles_fragment.html", context)


# [この部分は公開用リポジトリでは非公開にされています]


# バトル履歴保存期間延長のエンドポイント
DEFAULT_BATTLE_LOG_RETENTION_MONTHS = 4
MAX_BATTLE_LOG_RETENTION_MONTHS = 120

@router.post("/extend-retention/{tag}", name="extend_player_retention")
async def extend_player_retention(
    request: Request,
    tag: str,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user) # ログイン必須
):
    lang = request.path_params.get("lang", "ja")

    # 1. プレイヤータグを検証
    formatted_tag = format_tag(tag)
    if not confirm_tag(formatted_tag):
        raise HTTPException(status_code=400, detail="Invalid player tag format.")

    # 2. プレイヤー情報を取得
    try:
        player = await get_player(formatted_tag, db)
    except BrawlStarsAPIError:
        raise HTTPException(status_code=404, detail="Player not found")

    # 3. 現在の保存期間を取得し、上限チェック
    current_retention = await get_battle_log_retention_months(db=db, tag=formatted_tag)
    current_months = current_retention if current_retention else DEFAULT_BATTLE_LOG_RETENTION_MONTHS

    if current_months >= MAX_BATTLE_LOG_RETENTION_MONTHS:
        message = "保存期間は既に上限に達しています。" if lang == "ja" else "Storage period has already reached the maximum."
        return JSONResponse({"success": False, "message": message}, status_code=400)

    # 4. トークンが足りるかチェック
    COST = 100
    if current_user.tokens < COST:
        message = f"トークンが足りません。(現在所持: {current_user.tokens})" if lang == "ja" else f"Not enough tokens. (You have: {current_user.tokens})"
        return JSONResponse({"success": False, "message": message}, status_code=400)

    # 5. トランザクションを開始
    new_months = min(current_months + 1, MAX_BATTLE_LOG_RETENTION_MONTHS)
    try:
        async with db.transaction():
            # 6. トークンを消費
            success_spend = await current_user.spend_tokens(db, COST)
            if not success_spend:
                raise ValueError("トークン不足により処理を中断しました。")

            # 7. 保存期間を更新
            await extend_battle_log_retention(db=db, tag=formatted_tag, new_months=new_months)

    except ValueError as e:
        logger.warning(f"バトル履歴保存期間の延長処理を中断 (User: {current_user.name}): {e}")
        message = "トークンが足りません。" if lang == "ja" else "Not enough tokens."
        return JSONResponse({"success": False, "message": message}, status_code=400)
    except Exception as e:
        logger.error(f"バトル履歴保存期間の延長処理中にエラー (User: {current_user.name}, Player: {player.tag}): {e}", exc_info=True)
        message = "処理中にエラーが発生しました。" if lang == "ja" else "An error occurred during processing."
        return JSONResponse({"success": False, "message": message}, status_code=500)

    # 8. プレイヤーキャッシュ内の保存期間を部分更新（API再呼び出し不要）
    try:
        player_cache_key = f"player:{formatted_tag}"
        cached_player_data = await get_cache(player_cache_key)
        if cached_player_data and isinstance(cached_player_data, dict):
            cached_player_data["battle_log_retention_months"] = new_months
            await set_cache(key=player_cache_key, value=cached_player_data, ttl=1800)
    except Exception as e:
        logger.warning(f"プレイヤーキャッシュの部分更新に失敗 (Player: {formatted_tag}): {e}")

    # 9. 成功レスポンス
    # 延長後の期間を表示用にフォーマット
    new_years = new_months // 12
    new_remaining = new_months % 12
    if lang == "ja":
        if new_years > 0 and new_remaining > 0:
            period_str = f"{new_years}年 {new_remaining}ヶ月間"
        elif new_years > 0:
            period_str = f"{new_years}年間"
        else:
            period_str = f"{new_remaining}ヶ月間"
        message = f"プレイヤー「{player.name}」のバトル履歴保存期間を{period_str}に延長しました。"
    else:
        if new_years > 0 and new_remaining > 0:
            period_str = f"{new_years} year{'s' if new_years > 1 else ''} {new_remaining} month{'s' if new_remaining > 1 else ''}"
        elif new_years > 0:
            period_str = f"{new_years} year{'s' if new_years > 1 else ''}"
        else:
            period_str = f"{new_remaining} month{'s' if new_remaining > 1 else ''}"
        message = f"Successfully extended battle log storage for '{player.name}' to {period_str}."

    return JSONResponse({"success": True, "message": message})
