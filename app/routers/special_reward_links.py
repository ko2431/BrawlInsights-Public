from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
import asyncpg

from app.db.db import get_shared_db
from app.core.logger import logger
from app.services.special_reward_link_service import SpecialRewardLinkError, open_home_banner
from app.services.user_service import User
from app.utils.utils import get_remote_ip

router = APIRouter(prefix="/{lang}", tags=["Special Reward Links"])


@router.post("/special-reward-banners/{banner_id}/open", name="special_reward_banner_open")
async def special_reward_banner_open(
    request: Request,
    banner_id: int,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db),
):
    user: User | None = getattr(request.state, "current_user", None)
    try:
        result = await open_home_banner(
            db, banner_id=banner_id, user=user, ip=get_remote_ip(request), lang=lang
        )
        return JSONResponse({
            "success": True,
            "url": result["url"],
            "already_claimed": result.get("already_claimed", False),
        })
    except SpecialRewardLinkError as e:
        status_code = 401 if e.code == "login_required" else 429 if e.code == "rate_limited" else 400
        return JSONResponse({
            "success": False,
            "message": e.message(lang),
            "code": e.code,
            "hide_banner": e.hide_banner,
        }, status_code=status_code)
    except Exception as e:
        logger.error("ホーム特別報酬バナーのオープンに失敗: %s", e, exc_info=True)
        message = "エラーが発生しました。" if lang == "ja" else "An error occurred."
        return JSONResponse({"success": False, "message": message}, status_code=500)
