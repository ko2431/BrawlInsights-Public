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
from app.exceptions.custom_exceptions import BrawlStarsAPIError, DataBaseError
from app.utils.utils import confirm_tag, format_tag, format_utc_date, format_utc_datetime
from app.core.cache import get_cache, set_cache

router = APIRouter(
    prefix="/{lang}/tools",
    tags=["Tools"]
)

DROP_BOXES_PATH = Path(__file__).resolve().parent.parent / "data" / "drop_boxes.json"
LANDSCAPE_ONLY_PROFILE_IMAGE_TYPES: set[str] = {"equipment_skins"}


def load_drop_boxes_data() -> dict:
    with DROP_BOXES_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_static_url(request: Request, path: str | None) -> str:
    if not path:
        return str(request.url_for("static", path="images/ui/starrdrop.png"))
    if path.startswith("http:// [この部分は公開用リポジトリでは非公開にされています]
