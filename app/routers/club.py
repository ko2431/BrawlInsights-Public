# app/routers/club.py
from fastapi import APIRouter, Request, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
import asyncpg
import math # ページ数計算 (ceil) のため

from app.core.logger import logger
from app.core.logging_config import add_log_info
from app.db.db import get_shared_db # DB接続
from app.services.brawl_service import get_club, search_clubs # クラブデータ取得関数
from app.services.user_service import User
from app.utils.utils import format_tag, confirm_tag, get_icon_path # タグ整形・確認関数
from app.exceptions.custom_exceptions import BrawlStarsAPIError, DataBaseError # カスタム例外
from app.core.templating import templates

# ルーターインスタンスを作成
router = APIRouter(
    prefix="/{lang}/club",      # このルーター内のパスは /club から始まる
    tags=["Clubs"]       # APIドキュメントでのグループ化用
)

api_router = APIRouter(
    prefix="/api/{lang}/club",
    tags=["Club Fragments"]
)

# --- クラブプロフィールページのエンドポイント ---
@router.get("/profile/{tag}", name="get_club_profile")
async def get_club_profile(
    request: Request,
    lang: str,
    tag: str
):
    # 1. タグの整形とバリデーション
    if not tag:
        logger.debug("Club profile tag is empty. Redirecting to homepage.")
        try:
            home_url = request.url_for('home', lang=lang)
        except Exception as e:
            logger.warning(f"Could not generate URL for 'home', falling back to '/': {e}")
            home_url = f"/{lang}" # url_forが失敗した場合のフォールバック
        # 302 Found (一時的なリダイレクト) でリダイレクトレスポンスを返す
        return RedirectResponse(url=home_url, status_code=status.HTTP_302_FOUND)
    tag = format_tag(tag)
    if not confirm_tag(tag):
        raise HTTPException(status_code=400, detail="Invalid club tag format.") # 400 Bad Request

    # シェルHTMLを即時返却（クラブデータ取得なし）
    context = {
        "request": request,
        "lang": lang,
        "tag": tag,
        "current_page": "home" # または "clubs" など、タブバーのハイライトに合わせて設定
    }

    return templates.TemplateResponse("club/profile.html", context)


@api_router.get("/{tag}/profile", name="get_club_profile_fragment")
async def get_club_profile_fragment(
    request: Request,
    lang: str,
    tag: str,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    formatted_tag = format_tag(tag)
    if not confirm_tag(formatted_tag):
        raise HTTPException(status_code=400, detail="Invalid club tag format.")

    try:
        club = await get_club(formatted_tag, db)
    except BrawlStarsAPIError as e:
        logger.debug(f"{formatted_tag}のクラブデータ取得中にAPIエラーが発生しました: {e}", exc_info=True)
        return templates.TemplateResponse(
            "club/fragments/profile_error.html",
            {"request": request, "lang": lang, "tag": formatted_tag, "error_type": "not_found"},
            status_code=404
        )
    except Exception as e:
        logger.debug(f"{formatted_tag}のクラブデータ取得中にその他のエラーが発生しました: {e}", exc_info=True)
        return templates.TemplateResponse(
            "club/fragments/profile_error.html",
            {"request": request, "lang": lang, "tag": formatted_tag, "error_type": "server_error"},
            status_code=500
        )

    for member in club.members:
        try:
            member.icon_path = "/" + get_icon_path(member.icon_id)
        except Exception as icon_err:
            logger.error(f"Error getting icon path for member {member.tag} (icon_id={member.icon_id}): {icon_err}")
            member.icon_path = "/" + get_icon_path(0)

    current_login_user: User | None = getattr(request.state, "current_user", None)
    if current_login_user:
        try:
            await current_login_user.add_to_viewed_clubs(club.tag, db)
        except Exception as e:
            logger.error(f"クラブ閲覧履歴追加エラー: {e}", exc_info=True)

    add_log_info(request, f"クラブ(fragment): {club.name} - {club.tag}")

    context = {
        "request": request,
        "lang": lang,
        "club": club,
        "current_user": current_login_user,
        "current_page": "home"
    }
    return templates.TemplateResponse("club/fragments/profile_fragment.html", context)


# --- クラブ検索結果ページのエンドポイント（シェルHTML即時返却）---
@router.get("/search", name="search_clubs_page")
async def search_clubs_page(
    request: Request,
    lang: str,
    q: str | None = Query(None, min_length=1, max_length=50, description="クラブ検索語句"),
    page: int = Query(1, ge=1, description="表示ページ番号"),
):
    query = q.strip() if q else None
    if not query:
        logger.debug("Club search query is empty. Redirecting to homepage.")
        try:
            home_url = request.url_for('home', lang=lang)
        except Exception as e:
            logger.warning(f"Could not generate URL for 'home', falling back to '/': {e}")
            home_url = f"/{lang}"
        return RedirectResponse(url=home_url, status_code=status.HTTP_302_FOUND)

    # シェルHTMLを即時返却（検索処理なし）
    context = {
        "request": request,
        "lang": lang,
        "query": query,
        "page": page,
        "current_page": "home",
    }
    return templates.TemplateResponse("club/search_results.html", context)


# --- クラブ検索結果フラグメント（実際の検索処理）---
@api_router.get("/search", name="get_club_search_fragment")
async def get_club_search_fragment(
    request: Request,
    lang: str,
    q: str | None = Query(None, min_length=1, max_length=50),
    page: int = Query(1, ge=1),
    db: asyncpg.Connection = Depends(get_shared_db),
):
    query = q.strip() if q else None
    if not query:
        return templates.TemplateResponse(
            "club/fragments/search_error.html",
            {"request": request, "lang": lang, "error_type": "no_query"},
            status_code=400
        )

    clubs_on_page: list[dict[str, str]] = []
    total_clubs = 0
    per_page = 60
    pagination_info = {}
    result_capped = False

    try:
        clubs_on_page, total_clubs, result_capped = await search_clubs(
            query=query,
            db=db,
            page=page,
            per_page=per_page
        )

        total_pages = math.ceil(total_clubs / per_page) if total_clubs > 0 else 1
        current_page = min(page, total_pages) if total_pages > 0 else 1
        current_page = max(1, current_page)
        pagination_info = {
            "current_page": current_page,
            "total_pages": total_pages,
            "per_page": per_page,
            "total_items": total_clubs,
            "has_prev": current_page > 1,
            "has_next": current_page < total_pages,
            "prev_num": current_page - 1 if current_page > 1 else None,
            "next_num": current_page + 1 if current_page < total_pages else None,
            "page_numbers": [p for p in range(max(1, current_page - 2), min(total_pages, current_page + 2) + 1)]
        }

    except Exception as e:
        logger.error(f"Error in club search fragment for query '{query}': {e}", exc_info=True)
        return templates.TemplateResponse(
            "club/fragments/search_error.html",
            {"request": request, "lang": lang, "error_type": "server_error"},
            status_code=500
        )

    add_log_info(request, f"クラブ検索: {query} | ページ: {pagination_info.get('current_page', 1)}/{pagination_info.get('total_pages', 1)}")

    context = {
        "request": request,
        "lang": lang,
        "query": query,
        "clubs": clubs_on_page,
        "pagination": pagination_info,
        "result_capped": result_capped,
        "current_page": "home",
    }
    return templates.TemplateResponse("club/fragments/search_fragment.html", context)
