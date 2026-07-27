import asyncpg
import json
import copy
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException, Query, Depends
from fastapi.responses import JSONResponse, RedirectResponse
import datetime
from itertools import groupby
from pydantic import BaseModel, Field

from app.core.logger import logger
from app.core.templating import templates
from app.db.db import get_shared_db
from app.services.brawl_service import (Player, get_player, get_player_from_db, calc_num_of_available_brawlers, get_available_brawlers,
                                        get_brawler_analysis, get_current_ranked_pool, get_brawler,
                                        get_ban_suggestions, get_pick_suggestions, predict_win_rate,
                                        get_accessory_stats, get_max_accessory_counts, get_all_skins,
                                        get_player_name, get_player_icon_from_db)
from app.services import bsinfoapi
from app.services.image_generation_service import (
    IMAGE_REGENERATE_AFTER,
    INITIAL_PROFILE_IMAGE_TYPES,
    ImageGenerationJobData,
    build_image_generation_cache_key,
    create_image_generation_job,
    get_image_generation_job,
    get_image_generation_jobs_ahead_count,
    get_image_job_min_wait_until,
    get_image_job_priority,
    get_latest_cached_image_generation_job,
)
from app.services.user_service import User
from app.services.board_service import get_or_create_brawler_guide_post, get_messages
from app.exceptions.custom_exceptions import BrawlStarsAPIError, DataBaseError
from app.utils.utils import confirm_tag, format_tag, format_utc_date, format_utc_datetime
from app.core.cache import get_cache, set_cache

router = APIRouter(
    prefix="/{lang}/tools",
    tags=["Tools"]
)

DROP_BOXES_PATH = Path(__file__).resolve().parent.parent / "data" / "drop_boxes.json"
TROPHY_REWARDS_PATH = Path(__file__).resolve().parent.parent / "data" / "trophy_rewards.json"
LANDSCAPE_ONLY_PROFILE_IMAGE_TYPES: set[str] = {"equipment_skins"}


def load_drop_boxes_data() -> dict:
    with DROP_BOXES_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_trophy_rewards_data() -> dict:
    with TROPHY_REWARDS_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def normalize_trophy_rewards_data(request: Request, raw_data: dict) -> dict:
    data = copy.deepcopy(raw_data)
    for reward_type in data.get("reward_types", {}).values():
        reward_type["icon"] = build_static_url(request, reward_type.get("icon"))
    return data


def build_static_url(request: Request, path: str | None) -> str:
    if not path:
        return str(request.url_for("static", path="images/ui/starrdrop.png"))
    if path.startswith("http:// [この部分は公開用リポジトリでは非公開にされています]

    # テンプレートに渡すコンテキスト
    context = {
        "request": request,
        "lang": lang,
        "user": user,
        "main_account": main_account,
        "analysis": analysis,
        "grouped_matchups": grouped_matchups,
        "pool_data": pool_data,
        "brawler": brawler_data,
        "accessory_stats": accessory_stats,
        "bsinfo_accessory_levels": bsinfo_accessory_levels,
        "accessory_db_fallback": accessory_db_fallback,
        "start_date": format_utc_date(start_date) if start_date else None,
        "end_date": format_utc_date(end_date) if end_date else None,
        "use_cache": use_cache,
        "current_page": "tools" if not is_stats_tab else "stats",
        "brawler_thread_id": brawler_thread_id,
        "brawler_preview_messages": brawler_preview_messages,
    }

    try:
        return templates.TemplateResponse("tools/brawler_guide.html", context)
    except Exception as render_err: # テンプレートレンダリングエラーも捕捉
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")


#* /---*---*---*---*---*---*---*---*/
#* キャラクタールーレット
#* /---*---*---*---*---*---*---*---*/
@router.get("/random_brawler", name="random_brawler")
async def random_brawler(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    try:
        # キャラクターの一覧を取得
        brawlers = await get_available_brawlers(db)
    except Exception as e:
        logger.error(f"Error in random_brawler: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    
    #^ メインアカウントのデータを取得
    user: User | None = getattr(request.state, "current_user", None)
    main_account: Player | None = None

    if user:
        try:
            main_account = await get_player_from_db(user.main_account, db)
        except BrawlStarsAPIError as e:
            logger.debug(f"{user.name}のメインアカウントのプレイヤーデータ取得中にAPIエラーが発生しました: {e}。スキップします。", exc_info=True)
        except Exception as e:
            logger.debug(f"{user.name}のメインアカウントのプレイヤーデータ取得中にその他のエラーが発生しました: {e}", exc_info=True)
    

    # テンプレートに渡すコンテキスト
    context = {
        "request": request,
        "lang": lang,
        "brawlers": brawlers,
        "user": user,
        "main_account": main_account,
        "current_page": "tools",
    }

    try:
        return templates.TemplateResponse("tools/random_brawler.html", context)
    except Exception as render_err:
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")


#* /---*---*---*---*---*---*---*---*/
#* 育成計算機
#* /---*---*---*---*---*---*---*---*/
@router.get("/cost_calc", name="cost_calc")
async def cost_calc(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db) # DB接続が必要な場合
):
    user: User | None = getattr(request.state, "current_user", None)
    main_account: Player | None = None
    num_of_available_brawlers: int | None = None
    required_pps: int = 0
    required_coins: int = 0

    # ログイン済みの場合は、プレイヤーデータを取得
    if user:
        try:
            main_account = await get_player_from_db(user.main_account, db)
            if not main_account:
                main_account = await get_player(user.main_account, db) # DBから取れなかった場合はAPIから取得
        except BrawlStarsAPIError as e:
            logger.debug(f"{user.name}のメインアカウントのプレイヤーデータ取得中にAPIエラーが発生しました: {e}。スキップします。", exc_info=True)
        except Exception as e:
            logger.debug(f"{user.name}のメインアカウントのプレイヤーデータ取得中にその他のエラーが発生しました: {e}", exc_info=True)

    try:
        num_of_available_brawlers = await calc_num_of_available_brawlers(db)
    except Exception as e:
        logger.error(f"Error getting num_of_available_brawlers: {e}", exc_info=True)
        num_of_available_brawlers = 85 # fallback
    
    # 現在のアクセサリの最大数を取得
    max_accessory_counts = await get_max_accessory_counts(db)
    
    # メインアカウントのデータが取得できた場合は、追加データを計算する
    if main_account:
        # 必要なパワーポイントとコインを計算
        for b in main_account.brawlers:
            match b.power:
                case 1:
                    required_pps += 3740
                    required_coins += 7765
                case 2:
                    required_pps += 3720
                    required_coins += 7745
                case 3:
                    required_pps += 3690
                    required_coins += 7710
                case 4:
                    required_pps += 3640
                    required_coins += 7635
                case 5:
                    required_pps += 3560
                    required_coins += 7495
                case 6:
                    required_pps += 3430
                    required_coins += 7205
                case 7:
                    required_pps += 3220
                    required_coins += 6725
                case 8:
                    required_pps += 2880
                    required_coins += 5925
                case 9:
                    required_pps += 2330
                    required_coins += 4675
                case 10:
                    required_pps += 1440
                    required_coins += 2800
    
    # テンプレートに渡すコンテキスト
    context = {
        "request": request,
        "lang": lang,
        "main_account": main_account,
        "required_pps": required_pps,
        "required_coins": required_coins,
        "num_of_available_brawlers": num_of_available_brawlers,
        "max_accessory_counts": max_accessory_counts,
        "current_page": "tools"
    }

    try:
        return templates.TemplateResponse("tools/cost_calc.html", context)
    except Exception as render_err: # テンプレートレンダリングエラーも捕捉
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")


#* /---*---*---*---*---*---*---*---*/
#* 報酬量計算機
#* /---*---*---*---*---*---*---*---*/
@router.get("/reward_calc", name="reward_calc")
async def reward_calc(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db) # DB接続が必要な場合
):
    # テンプレートに渡すコンテキスト
    context = {
        "request": request,
        "lang": lang,
        "current_page": "tools"
    }

    try:
        return templates.TemplateResponse("tools/reward_calc.html", context)
    except Exception as render_err: # テンプレートレンダリングエラーも捕捉
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")

@router.get("/trophy_reward_calc", name="trophy_reward_calc")
async def trophy_reward_calc(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db),
):
    trophy_rewards_data = normalize_trophy_rewards_data(request, load_trophy_rewards_data())

    context = {
        "request": request,
        "lang": lang,
        "trophy_rewards_data": trophy_rewards_data,
        "current_page": "tools",
    }

    try:
        return templates.TemplateResponse("tools/trophy_reward_calc.html", context)
    except Exception as render_err:
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")

@router.get("/starrdrop_calc", name="starrdrop_calc")
async def starrdrop_calc(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db), # DB接続が必要な場合
    box: str | None = Query(None, description="初期表示するドロップ/ボックス")
):
    drop_boxes_data = normalize_drop_boxes_data(request, load_drop_boxes_data())

    # テンプレートに渡すコンテキスト
    context = {
        "request": request,
        "lang": lang,
        "drop_boxes_data": drop_boxes_data,
        "initial_box": box,
        "current_page": "tools"
    }

    try:
        return templates.TemplateResponse("tools/starrdrop_calc.html", context)
    except Exception as render_err: # テンプレートレンダリングエラーも捕捉
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")


#* /---*---*---*---*---*---*---*---*/
#* マップ周期
#* /---*---*---*---*---*---*---*---*/
@router.get("/map_rotation", name="map_rotation")
async def map_rotation(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db) # DB接続が必要な場合
):
    # テンプレートに渡すコンテキスト
    context = {
        "request": request,
        "lang": lang,
        "current_page": "tools"
    }

    try:
        return templates.TemplateResponse("tools/map_rotation.html", context)
    except Exception as render_err: # テンプレートレンダリングエラーも捕捉
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")


#* /---*---*---*---*---*---*---*---*/
#* ピック提案ツール
#* /---*---*---*---*---*---*---*---*/
@router.get("/pick_tool", name="pick_tool")
async def pick_tool(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    try:
        #^ 1. モードとマップの情報を取得
        pool_data = await get_current_ranked_pool(db)
    except Exception as e:
        logger.error(f"Error in pick_tool: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    
    #^ メインアカウントのデータを取得
    user: User | None = getattr(request.state, "current_user", None)
    main_account: Player | None = None

    if user:
        try:
            main_account = await get_player_from_db(user.main_account, db)
        except BrawlStarsAPIError as e:
            logger.debug(f"{user.name}のメインアカウントのプレイヤーデータ取得中にAPIエラーが発生しました: {e}。スキップします。", exc_info=True)
        except Exception as e:
            logger.debug(f"{user.name}のメインアカウントのプレイヤーデータ取得中にその他のエラーが発生しました: {e}", exc_info=True)
    
    # テンプレートに渡すコンテキスト
    context = {
        "request": request,
        "lang": lang,
        "user": user,
        "main_account": main_account,
        "pool_data": pool_data,
        "current_page": "tools",
        "hide_navigation_controls": True,
    }

    try:
        return templates.TemplateResponse("tools/pick_tool.html", context)
    except Exception as render_err: # テンプレートレンダリングエラーも捕捉
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")

@router.get("/api/ban_suggestions", name="get_ban_suggestions_api")
async def get_ban_suggestions_api(
    db: asyncpg.Connection = Depends(get_shared_db),
    mode: str | None = Query(None),
    map_name: str | None = Query(None),
    banned_brawlers: list[int] | None = Query(None)
):
    """BAN候補のキャラクターリストを脅威度順で取得するAPI"""
    try:
        # brawl_serviceの関数を呼び出す
        suggestions = await get_ban_suggestions(
            db,
            mode=mode if mode else None,
            map_name=map_name if map_name else None,
            rank_tier=None, # ランク帯は考慮しない
            banned_brawlers=banned_brawlers
        )
        # BrawlerStatオブジェクトのリストを辞書のリストに変換して返す
        return JSONResponse([s.to_dict() for s in suggestions])
    except Exception as e:
        logger.error(f"Error in get_ban_suggestions_api: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get ban suggestions")

@router.get("/api/pick_suggestions", name="get_pick_suggestions_api")
async def get_pick_suggestions_api(
    db: asyncpg.Connection = Depends(get_shared_db),
    mode: str | None = Query(None),
    map_name: str | None = Query(None),
    my_team_picks: list[int] | None = Query(None),
    enemy_team_picks: list[int] | None = Query(None),
    banned_brawlers: list[int] | None = Query(None)
):
    """ピック候補のキャラクターリストをおすすめ度順で取得するAPI"""
    try:
        suggestions = await get_pick_suggestions(
            db,
            mode=mode if mode else None,
            map_name=map_name if map_name else None,
            rank_tier=None, # ランク帯は考慮しない
            my_team_picks=my_team_picks,
            enemy_team_picks=enemy_team_picks,
            banned_brawlers=banned_brawlers
        )
        # PickSuggestionオブジェクトのリストを辞書のリストに変換して返す
        return JSONResponse([s.to_dict() for s in suggestions])
    except Exception as e:
        logger.error(f"Error in get_pick_suggestions_api: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get pick suggestions")

@router.get("/api/predict_win_rate", name="predict_win_rate_api")
async def predict_win_rate_api(
    db: asyncpg.Connection = Depends(get_shared_db),
    mode: str | None = Query(None),
    map_name: str | None = Query(None),
    team_1_picks: list[int] = Query(...),
    team_2_picks: list[int] = Query(...)
):
    """チーム1の予想勝率を算出するAPI"""
    try:
        win_rate = await predict_win_rate(
            db,
            mode=mode if mode else None,
            map_name=map_name if map_name else None,
            rank_tier=None, # ランク帯は考慮しない
            team_1_picks=team_1_picks,
            team_2_picks=team_2_picks
        )
        # 計算結果をJSON形式で返す
        return JSONResponse({"team_1_win_rate": win_rate})
    except Exception as e:
        logger.error(f"Error in predict_win_rate_api: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to predict win rate")


#* /---*---*---*---*---*---*---*---*/
#* ガチバトルマップ一覧
#* /---*---*---*---*---*---*---*---*/
@router.get("/ranked_maps", name="ranked_maps")
async def ranked_maps(
    request: Request,
    lang: str
):
    
    # テンプレートに渡すコンテキスト
    context = {
        "request": request,
        "lang": lang,
        "current_page": "tools"
    }

    try:
        return templates.TemplateResponse("tools/ranked_map_list.html", context)
    except Exception as render_err: # テンプレートレンダリングエラーも捕捉
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")


#* /---*---*---*---*---*---*---*---*/
#* トロフィー増減表
#* /---*---*---*---*---*---*---*---*/
@router.get("/trophy_table", name="trophy_table")
async def trophy_table(
    request: Request,
    lang: str
):
    
    # テンプレートに渡すコンテキスト
    context = {
        "request": request,
        "lang": lang,
        "current_page": "tools"
    }

    try:
        return templates.TemplateResponse("tools/trophy_table.html", context)
    except Exception as render_err: # テンプレートレンダリングエラーも捕捉
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")
    

#* /---*---*---*---*---*---*---*---*/
#* スタードロップ
#* /---*---*---*---*---*---*---*---*/
@router.get("/starrdrop_chances", name="starrdrop_chances")
async def starrdrop_chances(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db),
    box: str | None = Query(None, description="初期表示するドロップ/ボックス")
):
    skins_dict = await get_all_skins(db)
    drop_boxes_data = normalize_drop_boxes_data(request, load_drop_boxes_data(), skins_dict)

    # テンプレートに渡すコンテキスト
    context = {
        "request": request,
        "lang": lang,
        "drop_boxes_data": drop_boxes_data,
        "initial_box": box,
        "current_page": "tools"
    }

    try:
        return templates.TemplateResponse("tools/starrdrop_chances.html", context)
    except Exception as render_err: # テンプレートレンダリングエラーも捕捉
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")


@router.get("/chaosdrop_chances", name="chaosdrop_chances")
async def chaosdrop_chances(
    request: Request,
    lang: str
):
    return RedirectResponse(
        url=router.url_path_for("starrdrop_chances", lang=lang) + "?box=chaosdrop",
        status_code=307
    )


#* /---*---*---*---*---*---*---*---*/
#* よくある質問
#* /---*---*---*---*---*---*---*---*/
@router.get("/app_faq", name="app_faq")
async def app_faq(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    try:
        rows = await db.fetch(
            "SELECT * FROM faqs WHERE is_deleted = FALSE ORDER BY priority ASC, id ASC"
        )
    except Exception as e:
        logger.error(f"FAQ取得中にエラー: {e}", exc_info=True)
        rows = []

    # カテゴリごとにグルーピング
    # 各カテゴリの並び順は、カテゴリ内で最もpriorityが小さいFAQのpriority値で決定
    category_key = 'category_ja' if lang == 'ja' else 'category_en'
    categories_map: dict[str, list[dict]] = {}
    category_min_priority: dict[str, int] = {}

    for row in rows:
        cat = row[category_key]
        faq_item = dict(row)
        if cat not in categories_map:
            categories_map[cat] = []
            category_min_priority[cat] = row['priority']
        categories_map[cat].append(faq_item)
        if row['priority'] < category_min_priority[cat]:
            category_min_priority[cat] = row['priority']

    # カテゴリを最小priority値順にソート
    sorted_categories = sorted(categories_map.keys(), key=lambda c: category_min_priority[c])

    faq_groups = []
    for cat in sorted_categories:
        faq_groups.append({
            "category": cat,
            "faqs": categories_map[cat]
        })

    context = {
        "request": request,
        "lang": lang,
        "faq_groups": faq_groups,
        "current_page": "tools"
    }

    try:
        return templates.TemplateResponse("tools/app_faq.html", context)
    except Exception as render_err:
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")


#* /---*---*---*---*---*---*---*---*/
#* マップ画像表示ページ
#* /---*---*---*---*---*---*---*---*/
@router.get("/map/{id}", name="get_map")
async def get_map(
    request: Request,
    lang: str,
    id: int,
    name: str | None = Query(None, description="ページに表示するマップ名"),
    is_tools_tab: bool = False
):
    
    # テンプレートに渡すコンテキスト
    context = {
        "request": request,
        "lang": lang,
        "id": id,
        "name": name,
        "current_page": "tools" if is_tools_tab else None
    }

    try:
        return templates.TemplateResponse("tools/map.html", context)
    except Exception as render_err: # テンプレートレンダリングエラーも捕捉
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")


#* /---*---*---*---*---*---*---*---*/
#* ブロスタ動画
#* /---*---*---*---*---*---*---*---*/
_BRAWL_VIDEOS_CACHE_KEY = "brawl_videos:active_list"
_BRAWL_VIDEOS_CACHE_TTL = 60  # 秒

@router.get("/brawl_videos", name="brawl_videos")
async def brawl_videos(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db),
):
    try:
        cached = await get_cache(_BRAWL_VIDEOS_CACHE_KEY)
        if cached is not None:
            videos = cached
        else:
            rows = await db.fetch(
                """
                SELECT id, title_ja, title_en, platform, video_id,
                       thumbnail_url, is_sponsored, sponsor_name
                FROM brawl_videos
                WHERE is_active = TRUE
                ORDER BY display_order ASC, created_at DESC
                """
            )
            videos = [dict(r) for r in rows]
            await set_cache(_BRAWL_VIDEOS_CACHE_KEY, videos, ttl=_BRAWL_VIDEOS_CACHE_TTL)
    except Exception as e:
        logger.error(f"brawl_videosの取得中にエラー: {e}", exc_info=True)
        videos = []

    context = {
        "request": request,
        "lang": lang,
        "videos": videos,
        "current_page": "tools",
    }

    try:
        return templates.TemplateResponse("tools/brawl_videos.html", context)
    except Exception as render_err:
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")
# PUBLIC_EXCLUDE_END