from fastapi import APIRouter, Request, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
import asyncpg
import math
import datetime
from typing import Optional, Any

from app.exceptions.custom_exceptions import BrawlStarsAPIError
from app.core.logger import logger
from app.core.logging_config import add_log_info
from app.db.db import get_shared_db
from app.services.brawl_service import get_player, get_player_from_db, calc_num_of_available_brawlers, get_club_name, search_players_fast, get_player_log_trends, PlayerStatsPageData, Battles, search_battles, add_auto_tracking_time, extend_battle_log_retention, get_battle_log_retention_months, get_max_accessory_counts, get_skin_catalog_stats, get_all_titles
from app.services.rating_service import build_player_rating_data
from app.core.cache import get_cache, set_cache
from app.services.user_service import User
from app.utils.utils import format_tag, confirm_tag, get_icon_path, get_ranked_seasons_for_filter
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


# 推移グラフフラグメント
@api_router.get("/{tag}/stats", name="get_player_stats_fragment")
async def get_player_stats_fragment(
    request: Request,
    lang: str,
    tag: str,
    period: str | None = Query("30d"),
    db: asyncpg.Connection = Depends(get_shared_db)
):
    tag = format_tag(tag)
    if not confirm_tag(tag):
        raise HTTPException(status_code=400, detail="Invalid player tag format.")

    try:
        player = await get_player(tag, db)
    except BrawlStarsAPIError as e:
        logger.debug(f"{tag}のプレイヤーデータ取得中にAPIエラー。DBフォールバック: {e}")
        player = await get_player_from_db(tag, db)
        if not player:
            return templates.TemplateResponse(
                "player/fragments/stats_error.html",
                {"request": request, "lang": lang, "tag": tag, "error_type": "not_found"},
                status_code=404
            )
    except Exception as e:
        logger.error(f"{tag}のプレイヤーデータ取得中にエラー: {e}", exc_info=True)
        return templates.TemplateResponse(
            "player/fragments/stats_error.html",
            {"request": request, "lang": lang, "tag": tag, "error_type": "server_error"},
            status_code=500
        )

    # 期間パラメータを解釈
    period_days_for_query: int | None = None
    if period == "all":
        period_days_for_query = None
    elif period.endswith("d"):
        try:
            period_days_for_query = int(period[:-1])
        except ValueError:
            period_days_for_query = 30
            period = "30d"
    elif period.endswith("y"):
        try:
            period_days_for_query = int(period[:-1]) * 365
        except ValueError:
            period_days_for_query = 30
            period = "30d"
    else:
        period_days_for_query = 30
        period = "30d"

    try:
        player_stats_data: PlayerStatsPageData = await get_player_log_trends(db, player.tag, period_days_for_query)
    except DataBaseError as e:
        logger.error(f"プレイヤー({player.tag})の統計推移データ取得中にエラー: {e}", exc_info=True)
        player_stats_data = {"log_trends": None, "oldest_log_date": None, "latest_log_date": None, "total_log_days": 0}
    except Exception as e:
        logger.error(f"プレイヤー({player.tag})の統計推移データ取得中に予期せぬエラー: {e}", exc_info=True)
        player_stats_data = {"log_trends": None, "oldest_log_date": None, "latest_log_date": None, "total_log_days": 0}

    is_auto_activate = await player.is_auto_activate()
    if is_auto_activate: await player.activate_automatic_acquisition(activate_time=player.auto_activate_time)

    icon_path_rel = get_icon_path(player.icon_id)
    add_log_info(request, f"プレイヤー(stats fragment): {player.name} - {player.tag}")

    # 期間選択メニューの選択肢を生成
    available_periods_options = []
    actual_total_days = 0

    if player_stats_data["oldest_log_date"]:
        try:
            oldest_date_obj = datetime.date.fromisoformat(player_stats_data["oldest_log_date"])
            today = datetime.datetime.now(datetime.timezone.utc).date()
            actual_total_days = (today - oldest_date_obj).days + 1
            if actual_total_days < 0: actual_total_days = 0
        except ValueError:
            actual_total_days = 0

    def format_all_period_label(total_days: int, lang: str, is_default: bool) -> str:
        years = total_days // 365
        days_remaining = total_days % 365
        default_suffix_ja = " (デフォルト)" if is_default else ""
        default_suffix_en = " (Default)" if is_default else ""
        if lang == "ja":
            if years > 0 and days_remaining > 0:
                return f"全期間 (過去{years}年{days_remaining}日){default_suffix_ja}"
            elif years > 0:
                return f"全期間 (過去{years}年){default_suffix_ja}"
            else:
                return f"全期間 (過去{days_remaining}日){default_suffix_ja}"
        else:
            if years > 0 and days_remaining > 0:
                return f"All Time ({years}y {days_remaining}d){default_suffix_en}"
            elif years > 0:
                return f"All Time ({years}y){default_suffix_en}"
            else:
                return f"All Time ({days_remaining}d){default_suffix_en}"

    is_default_for_all = actual_total_days < 30 and actual_total_days > 0
    period_definitions_master = []

    if actual_total_days > 1:
        period_definitions_master.append(
            (f"{actual_total_days}d", f"過去{actual_total_days}日", f"Last {actual_total_days} Days", actual_total_days, is_default_for_all)
        )

    fixed_periods_config = [
        (30, "30d", "過去30日", "Last 30 Days"),
        (90, "90d", "過去90日", "Last 90 Days"),
        (180, "180d", "過去180日", "Last 180 Days"),
        (365, "1y", "過去1年", "Last 1 Year"),
    ]
    for i in range(2, 6):
        fixed_periods_config.append((365 * i, f"{i}y", f"過去{i}年", f"Last {i} Years"))

    for days, val, label_ja, label_en in fixed_periods_config:
        if actual_total_days >= days:
            is_default = (days == 30 and actual_total_days >= 30)
            label_ja_final = label_ja + (" (デフォルト)" if is_default and not is_default_for_all else "")
            label_en_final = label_en + (" (Default)" if is_default and not is_default_for_all else "")
            period_definitions_master.append((val, label_ja_final, label_en_final, days, is_default))

    is_all_period_default = actual_total_days < 30
    all_label_ja = format_all_period_label(actual_total_days, "ja", is_all_period_default)
    all_label_en = format_all_period_label(actual_total_days, "en", is_all_period_default)
    period_definitions_master.append(("all", all_label_ja, all_label_en, actual_total_days, is_all_period_default))

    unique_options_dict = {}
    for val, l_ja, l_en, d, is_def in sorted(period_definitions_master, key=lambda x: (x[3], len(x[0]))):
        if d not in unique_options_dict or (val != "all" and len(val) < len(unique_options_dict[d][0])):
            unique_options_dict[d] = (val, l_ja, l_en, d, is_def)
        elif val == "all" and d not in unique_options_dict:
            unique_options_dict[d] = (val, l_ja, l_en, d, is_def)

    temp_options_from_dict = []
    for _val, _l_ja, _l_en, _d, _is_def in unique_options_dict.values():
        temp_options_from_dict.append((_val, _l_ja, _l_en, _d))
    available_periods_options = sorted(temp_options_from_dict, key=lambda x: x[3])

    current_period_is_valid = any(opt[0] == period for opt in available_periods_options)
    if not current_period_is_valid or not available_periods_options:
        if actual_total_days >= 30 and any(opt[0] == "30d" for opt in available_periods_options):
            period = "30d"
        elif actual_total_days > 0 and any(opt[0] == f"{actual_total_days}d" for opt in available_periods_options):
            period = f"{actual_total_days}d"
        elif available_periods_options:
            period = available_periods_options[0][0]
        else:
            period = "all"

    current_login_user: Optional[User] = getattr(request.state, "current_user", None)
    if current_login_user:
        try:
            await current_login_user.add_to_viewed_accounts(player.tag, db)
        except Exception as e:
            logger.error(f"閲覧履歴追加エラー: {e}", exc_info=True)

    context = {
        "request": request,
        "lang": lang,
        "player": player,
        "player_icon_path": "/" + icon_path_rel,
        "current_page": "home",
        "player_stats_data": player_stats_data,
        "current_period": period,
        "available_periods": available_periods_options,
        "current_user": current_login_user,
    }
    return templates.TemplateResponse("player/fragments/stats_fragment.html", context)


# プレイヤー検索結果ページのエンドポイント（シェルHTML即時返却）
@router.get("/search", name="search_players_page")
async def search_players_page(
    request: Request,
    lang: str,
    q: str | None = Query(None, min_length=1, max_length=50, description="プレイヤー検索語句"),
    page: int = Query(1, ge=1, description="表示ページ番号"),
    exact: bool | None = Query(False, description="完全一致検索のみ"),
    old: bool | None = Query(False, description="改名前の名前も検索対象"),
):
    # 検索語句のチェックとリダイレクト
    query = q.strip() if q else None
    if not query:
        logger.debug("Player search query is empty. Redirecting to homepage.")
        try:
            home_url = request.url_for('home', lang=lang)
        except Exception as e:
            logger.warning(f"Could not generate URL for 'home', falling back to '/': {e}")
            home_url = f"/{lang}"
        return RedirectResponse(url=home_url, status_code=status.HTTP_302_FOUND)

    # シェルHTMLを即時返却（検索処理なし）
    context = {
        "request": request,
        "lang": lang,
        "query": query,
        "page": page,
        "exact_match": exact,
        "include_old_names": old,
        "current_page": "home",
    }
    return templates.TemplateResponse("player/search_results.html", context)


# プレイヤー検索結果フラグメント（実際の検索処理はここで行う）
@api_router.get("/search", name="get_player_search_fragment")
async def get_player_search_fragment(
    request: Request,
    lang: str,
    q: str | None = Query(None, min_length=1, max_length=50),
    page: int = Query(1, ge=1),
    exact: bool | None = Query(False),
    old: bool | None = Query(False),
    db: asyncpg.Connection = Depends(get_shared_db),
):
    query = q.strip() if q else None
    if not query:
        return templates.TemplateResponse(
            "player/fragments/search_error.html",
            {"request": request, "lang": lang, "error_type": "no_query"},
            status_code=400
        )

    players_on_page: list[dict[str, Any]] = []
    total_players = 0
    per_page = 60
    pagination_info = {}
    result_capped = False

    try:
        players_on_page, total_players, result_capped = await search_players_fast(
            query=query,
            db=db,
            page=page,
            per_page=per_page,
            exact_match=exact,
            include_previous_names=old
        )
        for player in players_on_page:
            player["icon_path"] = "/" + get_icon_path(player["icon_id"])

        total_pages = math.ceil(total_players / per_page) if total_players > 0 else 1
        current_page = min(page, total_pages) if total_pages > 0 else 1
        current_page = max(1, current_page)
        pagination_info = {
            "current_page": current_page,
            "total_pages": total_pages,
            "per_page": per_page,
            "total_items": total_players,
            "has_prev": current_page > 1,
            "has_next": current_page < total_pages,
            "prev_num": current_page - 1 if current_page > 1 else None,
            "next_num": current_page + 1 if current_page < total_pages else None,
            "page_numbers": [p for p in range(max(1, current_page - 2), min(total_pages, current_page + 2) + 1)]
        }

    except DataBaseError as db_err:
        logger.error(f"Database error during player search fragment: {db_err}")
        return templates.TemplateResponse(
            "player/fragments/search_error.html",
            {"request": request, "lang": lang, "error_type": "server_error"},
            status_code=500
        )
    except Exception as e:
        logger.error(f"Error in player search fragment for query '{query}': {e}", exc_info=True)
        return templates.TemplateResponse(
            "player/fragments/search_error.html",
            {"request": request, "lang": lang, "error_type": "server_error"},
            status_code=500
        )

    add_log_info(request, f"クエリ: {query} | ページ: {pagination_info.get('current_page', 1)}/{pagination_info.get('total_pages', 1)}")
    if exact: add_log_info(request, "完全一致: オン")
    if old: add_log_info(request, "改名前を含める: オン")

    context = {
        "request": request,
        "lang": lang,
        "query": query,
        "players": players_on_page,
        "pagination": pagination_info,
        "exact_match": exact,
        "include_old_names": old,
        "result_capped": result_capped,
        "current_page": "home",
    }
    return templates.TemplateResponse("player/fragments/search_fragment.html", context)



@router.post("/extend-tracking/{tag}", name="extend_player_tracking")
async def extend_player_tracking(
    request: Request,
    tag: str,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user) # ログイン必須
):
    lang = request.path_params.get("lang", "ja") # リクエストから言語を取得

    # 1. プレイヤータグを検証
    formatted_tag = format_tag(tag)
    if not confirm_tag(formatted_tag):
        raise HTTPException(status_code=400, detail="Invalid player tag format.")

    # 2. プレイヤー情報を取得
    try:
        player = await get_player(formatted_tag, db)
    except BrawlStarsAPIError:
        raise HTTPException(status_code=404, detail="Player not found")

    # 3. トークンが足りるかチェック
    COST = 10
    if current_user.tokens < COST:
        message = f"トークンが足りません。(現在所持: {current_user.tokens})" if lang == "ja" else f"Not enough tokens. (You have: {current_user.tokens})"
        return JSONResponse({"success": False, "message": message}, status_code=400)

    # 4. トランザクションを開始 (同時実行時の競合を防ぐため)
    try:
        async with db.transaction():
            # 5. トークンを消費
            # 呼び出し先の spend_tokens もasyncpgに対応している必要がある
            success_spend = await current_user.spend_tokens(db, COST)
            if not success_spend:
                # transactionブロック内で例外を発生させると自動でロールバックされる
                raise ValueError("トークン不足により処理を中断しました。")

            # 6. 自動追跡時間を延長
            await add_auto_tracking_time(player, 24)

    except ValueError as e: # 自分で発生させた例外を捕捉
        logger.warning(f"自動追跡時間の延長処理を中断 (User: {current_user.name}): {e}")
        message = "トークンが足りません。" if lang == "ja" else "Not enough tokens."
        return JSONResponse({"success": False, "message": message}, status_code=400)
    except Exception as e:
        # その他の予期せぬエラーはここで捕捉される
        logger.error(f"自動追跡時間の延長処理中にエラー (User: {current_user.name}, Player: {player.tag}): {e}", exc_info=True)
        message = "処理中にエラーが発生しました。" if lang == "ja" else "An error occurred during processing."
        return JSONResponse({"success": False, "message": message}, status_code=500)

    # 7. 成功レスポンス
    message = f"プレイヤー「{player.name}」の自動追跡時間を24時間延長しました。" if lang == "ja" else f"Successfully extended auto-tracking for '{player.name}' by 24 hours."
    return JSONResponse({"success": True, "message": message})


# プレイヤー自動追跡時間延長(10日間)のエンドポイント
@router.post("/extend-tracking-10days/{tag}", name="extend_player_tracking_10days")
async def extend_player_tracking_10days(
    request: Request,
    tag: str,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user) # ログイン必須
):
    lang = request.path_params.get("lang", "ja") # リクエストから言語を取得

    # 1. プレイヤータグを検証
    formatted_tag = format_tag(tag)
    if not confirm_tag(formatted_tag):
        raise HTTPException(status_code=400, detail="Invalid player tag format.")

    # 2. プレイヤー情報を取得
    try:
        player = await get_player(formatted_tag, db)
    except BrawlStarsAPIError:
        raise HTTPException(status_code=404, detail="Player not found")

    # 3. トークンが足りるかチェック
    COST = 90
    if current_user.tokens < COST:
        message = f"トークンが足りません。(現在所持: {current_user.tokens})" if lang == "ja" else f"Not enough tokens. (You have: {current_user.tokens})"
        return JSONResponse({"success": False, "message": message}, status_code=400)

    # 4. トランザクションを開始 (同時実行時の競合を防ぐため)
    try:
        async with db.transaction():
            # 5. トークンを消費
            success_spend = await current_user.spend_tokens(db, COST)
            if not success_spend:
                raise ValueError("トークン不足により処理を中断しました。")

            # 6. 自動追跡時間を延長 (10日間 = 240時間)
            await add_auto_tracking_time(player, 240)

    except ValueError as e:
        logger.warning(f"自動追跡時間の延長処理を中断 (User: {current_user.name}): {e}")
        message = "トークンが足りません。" if lang == "ja" else "Not enough tokens."
        return JSONResponse({"success": False, "message": message}, status_code=400)
    except Exception as e:
        logger.error(f"自動追跡時間の延長処理中にエラー (User: {current_user.name}, Player: {player.tag}): {e}", exc_info=True)
        message = "処理中にエラーが発生しました。" if lang == "ja" else "An error occurred during processing."
        return JSONResponse({"success": False, "message": message}, status_code=500)

    # 7. 成功レスポンス
    message = f"プレイヤー「{player.name}」の自動追跡時間を10日間追加しました。" if lang == "ja" else f"Successfully extended auto-tracking for '{player.name}' by 10 days."
    return JSONResponse({"success": True, "message": message})


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
