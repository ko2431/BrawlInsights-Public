from fastapi import APIRouter, Request, HTTPException, Depends, Query
from fastapi.responses import JSONResponse
import asyncpg
import datetime
import statistics
import math
from itertools import groupby

from app.core.logger import logger
from app.core.templating import templates
from app.db.db import get_shared_db
from app.services.brawl_service import (get_prestige_borders, get_available_brawlers, get_player_ranking, get_player_alltime_ranking,
    get_brawler_ranking, get_brawler_alltime_ranking, get_club_ranking, get_ranked_ranking, get_custom_ranking, get_ranked_stats,
    get_ban_suggestions, get_current_ranked_pool, get_brawler_analysis, get_pick_suggestions, predict_win_rate, get_japanese_mode_name,
    get_japanese_map_name, get_brawler, get_play_time_ranking, get_all_skins, get_all_pins, get_all_player_icons, get_all_titles, get_all_frames,
    get_player, get_player_from_db)
from app.services.user_service import get_all_regions
from app.utils.utils import get_first_thursdays, format_utc_date, get_icon_path, calc_ranked_season
from app.exceptions.custom_exceptions import DataBaseError, BrawlStarsAPIError


router = APIRouter(
    prefix="/{lang}/stats",
    tags=["Stats"]
)


# [この部分は公開用リポジトリでは非公開にされています]
