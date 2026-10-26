from fastapi import APIRouter, Request, HTTPException, Depends, Query
from fastapi.responses import JSONResponse
import asyncpg
import datetime
import statistics
import math

from app.services.player_account_service import build_player_account_options, resolve_user_player_tag
from app.utils.utils import format_tag
from app.core.logger import logger
# [この部分は公開用リポジトリでは非公開にされています]
from app.core.templating import templates
from app.db.db import get_shared_db
from app.services.brawl_service import (get_prestige_borders, get_available_brawlers, get_player_ranking, get_player_alltime_ranking,
    get_brawler_ranking, get_brawler_alltime_ranking, get_club_ranking, get_ranked_ranking, get_custom_ranking, get_ranked_stats,
    get_ban_suggestions, get_current_ranked_pool, resolve_ranked_filter, get_brawler_analysis, get_pick_suggestions, predict_win_rate, get_japanese_mode_name,
    get_japanese_map_name, get_brawler, get_play_time_ranking, get_all_skins, get_all_pins, get_all_player_icons, get_all_titles, get_all_frames,
    get_player, get_player_from_db)
from app.services.trophy_stats_service import (
    get_trophy_filter_pool,
    get_trophy_stats,
    parse_map_id_param,
    parse_mode_id_param,
    resolve_trophy_filter_ids,
)
from app.services.user_service import get_all_regions
from app.utils.utils import get_first_thursdays, format_utc_date, get_icon_path, calc_ranked_season, group_brawlers_by_rarity
from app.utils.utils import build_brawler_tier_groups, brawler_name_sort_key, get_brawler_display_name
from app.utils.nav_context import build_nav_query
from app.exceptions.custom_exceptions import DataBaseError, BrawlStarsAPIError
from app.services.map_mode_catalog import mode_icon_candidates


router = APIRouter(
    prefix="/{lang}/stats",
    tags=["Stats"]
)


def _validate_region(region: str, regions: list[dict]) -> str:
    """地域一覧にない地域コードは "global" として扱う。スキャナー等の不正な値を外部APIやDBに渡さないため。"""
    if region.lower() == "global":
        return "global"
    if any(r["code"] == region.upper() for r in regions):
        return region.upper()
    logger.info(f"不正な地域コード \"{region}\" が指定されたため、グローバルとして扱います")
    return "global"


#* /---*---*---*---*---*---*---*---*/
#* 統計データタブメインページ
#* /---*---*---*---*---*---*---*---*/
@router.get("", name="stats")
async def stats(
    request: Request,
    lang: str,
    # db: asyncpg.Connection = Depends(get_shared_db) # DB接続が必要な場合
):
    
    # テンプレートに渡すコンテキスト
    context = {
        "request": request,
        "lang": lang,
        "current_page": "stats"
    }

    try:
        return templates.TemplateResponse("stats/stats.html", context)
    except Exception as render_err: # テンプレートレンダリングエラーも捕捉
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")

# [この部分は公開用リポジトリでは非公開にされています]
