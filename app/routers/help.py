from fastapi import APIRouter, Request, Depends, HTTPException
import asyncpg

from app.core.logger import logger
from app.core.templating import templates
from app.services.brawl_service import calc_num_of_available_brawlers
from app.services.user_service import get_announcements
from app.exceptions.custom_exceptions import DataBaseError
from app.db.db import get_shared_db

router = APIRouter(
    prefix="/{lang}/help",
    tags=["Helps"]
)

@router.get("/announcements", name="announcements")
async def announcements(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    # お知らせを取得。ただしプラットフォーム指定のお知らせは該当プラットフォームのみに表示
    platform = getattr(request.state, "platform", "unknown")
    try:
        all_announcements = await get_announcements(db)
    except DataBaseError as e:
        logger.error(f"アナウンス一覧取得中にデータベースエラー: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="データベースエラー")
    announcements = []
    for a in all_announcements:
        if a.id.lower().startswith("web") and platform != "web" or a.id.lower().startswith("ios") and platform != "ios" or \
            a.id.lower().startswith("android") and platform != "android" or a.id.lower().startswith("app") and platform not in ["ios", "android"]:
                continue
        announcements.append(a)
    
    # テンプレートに渡すコンテキスト
    context = {
        "request": request,
        "lang": lang,
        "current_page": "home",
        "announcements": announcements
    }

    return templates.TemplateResponse("announcements.html", context)

@router.get("/automatic_acquisition", name="automatic_acquisition")
async def automatic_acquisition(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    num_of_available_brawlers = await calc_num_of_available_brawlers(db)
    
    # テンプレートに渡すコンテキスト
    context = {
        "request": request,
        "lang": lang,
        "n": num_of_available_brawlers, # ヘルプ記事内で最大キャラ数を利用する
    }

    return templates.TemplateResponse("help/automatic_acquisition.html", context)

@router.get("/search_specification", name="search_specification")
async def search_specification(request: Request, lang: str):
    context = {"request": request, "lang": lang}
    return templates.TemplateResponse("help/search_specification.html", context)

@router.get("/about_the_technology", name="about_the_technology")
async def about_the_technology(request: Request, lang: str):
    context = {"request": request, "lang": lang}
    return templates.TemplateResponse("help/about_the_technology.html", context)

@router.get("/version_history", name="version_history")
async def version_history(request: Request, lang: str):
    context = {"request": request, "lang": lang}
    return templates.TemplateResponse("help/version_history.html", context)

@router.get("/sponsor_info", name="sponsor_info")
async def sponsor_info(request: Request, lang: str):
    context = {"request": request, "lang": lang}
    return templates.TemplateResponse("help/sponsor_info.html", context)
