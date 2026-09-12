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

from app.services.brawl_service import get_available_brawlers, update_brawler
from app.services.map_mode_catalog import (
    collect_unresolved_report,
    get_all_maps,
    get_all_modes,
    sync_maps_and_modes_from_bsinfo,
    update_map_from_admin,
    update_mode_from_admin,
)
from app.services.user_service import User, get_all_regions, update_region, delete_region, insert_region, get_all_secret_questions, insert_secret_question, update_secret_question, get_all_gift_codes, create_gift_code, get_gift_code, get_user, get_user_include_invalid, get_feedback, get_all_announcements, insert_announcement, update_announcement, delete_announcement, search_users, get_usage_stat_trend
from app.services.board_service import get_report, EMOJIS, TEAM_POST_AUTO_CLOSE_SECONDS, TEAM_POST_LATER_AUTO_CLOSE_SECONDS, is_team_post_effectively_closed
from app.utils.utils import parse_utc_datetime, format_tag, get_normalized_ip, get_remote_ip
from app.db.db import get_shared_db
from app.exceptions.custom_exceptions import DataBaseError
from app.core.logger import logger
from app.core.templating import templates
from app.routers.billing import SUPPORT_PRODUCT_PRICE_TEXT
from app.core.cache import get_cache, set_cache, delete_cache
from app.core.admin_permissions import (
    HOME_ADMIN_PAGES,
    HOME_EXTERNAL_LINKS,
    PERM_MESSAGES_EDIT_DELETED,
    PERM_MESSAGES_VIEW_IP,
    PERM_POSTS_EDIT_CLOSED,
    PERM_POSTS_EDIT_DELETED,
    PERM_POSTS_VIEW_IP,
    PERM_REPORTS_VIEW_IP,
    PERM_USERS_EDIT_ADS,
    PERM_USERS_EDIT_CUSTOM,
    PERM_USERS_EDIT_ECONOMY,
    PERM_USERS_EDIT_INVALID,
    PERM_USERS_EDIT_PROHIBIT,
    PERM_USERS_VIEW_SECRET,
    PERM_WORKER_RUN,
    catalog_groups_for_ui,
    expand_granted_keys,
    home_links_for_user,
    is_staff,
    presets_for_ui,
    resolve_admin_route_permission,
    user_has_perm,
    visible_notification_categories,
)
from app.services.admin_audit_service import list_admin_audit_logs, record_admin_audit
from app.services.minigame_service import (
    GAME_TYPES,
    create_campaign,
    format_prize_label,
    update_campaign,
    validate_prizes,
)
from app.services.minigame_assets import CARD_ASSETS
from app.services.admin_notification_service import (
    ADMIN_NOTIFICATION_PAGE_SIZE,
    category_options,
    emit_admin_notification,
    ensure_event_settings,
    events_grouped_for_settings,
    get_effective_display_levels,
    list_admin_notifications,
    save_event_levels,
    save_user_event_levels,
)


router = APIRouter(
    prefix="/{lang}/admin",
    tags=["Admin"]
)

# [この部分は公開用リポジトリでは非公開にされています]
