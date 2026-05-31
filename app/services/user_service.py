import asyncpg
import datetime
import json
from typing import Any, TypedDict
import bcrypt
import asyncio

from app.exceptions.custom_exceptions import DataBaseError
from app.core.logger import logger
from app.core.cache import get_cache, set_cache, delete_cache, get_redis
from app.utils.utils import format_utc_date, parse_utc_datetime, format_utc_datetime, is_expired, parse_utc_date
from app.models.missing import MISSING


def _current_token_claim_date() -> datetime.date:
    """デイリー報酬カウント判定に使う現在日付(UTC)を返す。"""
    return datetime.datetime.now(datetime.timezone.utc).date()


def _normalize_daily_claim_count(last_claim_date: datetime.date | None, claim_count: int | None) -> int:
    """最終受取日が今日でない場合、日次カウントを0として扱う。"""
    today: datetime.date = _current_token_claim_date()
    if today != last_claim_date:
        return 0
    return claim_count if claim_count is not None else 0


async def _clear_user_caches(user_id: int) -> None:
    await delete_cache(f"user:{user_id}")
    await delete_cache(f"user_include_invalid:{user_id}")

# [この部分は公開用リポジトリでは非公開にされています]
