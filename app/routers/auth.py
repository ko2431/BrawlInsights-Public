from fastapi import APIRouter, Request, Depends, Form, status, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel, field_validator, Field
import asyncpg
from urllib.parse import urlparse

from app.core.templating import templates
from app.core.cache import get_cache, delete_cache
from app.services.user_service import (
    verify_password, get_user_id_by_name, get_user,
    create_user, is_user_name_used, get_all_secret_questions, get_secret_question
)
from app.services.brawl_service import get_player_name, get_player_icon_from_db
from app.utils.utils import format_tag, confirm_tag
from app.db.db import get_shared_db
from app.core.logger import logger
from app.exceptions.custom_exceptions import DataBaseError


router = APIRouter(
    tags=["Authentication"] # [この部分は公開用リポジトリでは非公開にされています]
    return RedirectResponse(url=str(redirect_url_str), status_code=status.HTTP_303_SEE_OTHER)


# [この部分は公開用リポジトリでは非公開にされています]

# [この部分は公開用リポジトリでは非公開にされています]

# [この部分は公開用リポジトリでは非公開にされています]

# [この部分は公開用リポジトリでは非公開にされています]
