from fastapi import APIRouter, Request, HTTPException, Depends, status, Query, Body
from fastapi.responses import JSONResponse
import asyncpg
from pydantic import BaseModel, Field, field_validator
import json
import datetime
import math
import os
import re
import itertools
import httpx
import asyncio
import base64
from app.core.config import settings

from app.services.brawl_service import get_all_maps, get_all_modes, upsert_map, upsert_mode, get_available_brawlers, update_brawler, get_player_name
from app.services.user_service import User, get_all_regions, update_region, delete_region, insert_region, get_all_secret_questions, insert_secret_question, update_secret_question, get_all_gift_codes, create_gift_code, get_gift_code, get_user, get_user_include_invalid, get_feedbacks, get_feedback, get_all_announcements, insert_announcement, update_announcement, delete_announcement, search_users, get_usage_stat_trend
from app.services.board_service import get_reports, get_post, get_message, get_report, EMOJIS
from app.utils.utils import parse_utc_datetime, format_tag, get_normalized_ip
from app.db.db import get_shared_db
from app.exceptions.custom_exceptions import DataBaseError
from app.core.logger import logger
from app.core.templating import templates
from app.routers.billing import SUPPORT_PRODUCT_PRICE_TEXT
from app.core.cache import get_cache, set_cache, delete_cache
from app.services.minigame_service import (
    GAME_TYPES,
    create_campaign,
    format_prize_label,
    update_campaign,
    validate_prizes,
)
from app.services.minigame_assets import CARD_ASSETS


router = APIRouter(
    prefix="/{lang}/admin",
    tags=["Admin"]
)

# [この部分は公開用リポジトリでは非公開にされています]
