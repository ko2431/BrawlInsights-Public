import re
import time
import datetime
import asyncpg
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, status, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse, Response, PlainTextResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.requests import Request
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Callable, Awaitable
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from pathlib import Path
from starlette.middleware.sessions import SessionMiddleware
from urllib.parse import quote_plus, urlparse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from user_agents import parse

from app.core.config import settings
from app.core.logging_config import setup_logger, get_log_extra_info
from app.core.logger import logger
from app.core.cache import connect_redis, close_redis, set_cache, get_cache, is_transient_redis_error, log_transient_redis_warning
from app.db.db import connect_to_db, close_db_connection, get_shared_db, get_shared_db_if_logged_in, get_db_connection_for_bg_task, is_transient_pg_error, log_transient_pg_warning, PoolAcquireTimeoutError
# [この部分は公開用リポジトリでは非公開にされています]
from app.core.page_integrity import integrity_router, IntegrityGateMiddleware
from app.core.templating import templates
from app.services.user_service import User, get_user, get_home_announcements
from app.services.special_reward_link_service import get_public_home_banners
from app.services import main_account_reward_service
from app.services import reaction_rules
from app.services.brawl_service import get_player_name, get_player_icon_from_db, get_player_summaries_from_db, get_club_name, get_club_badge_id_from_db
from app.services.notification_service import empty_board_notification_context, get_board_notification_context
from app.services.admin_notification_service import (
    apply_admin_page_visit,
    empty_admin_notification_badge_context,
    get_admin_notification_badge_context,
)
from app.core.admin_permissions import is_staff
from app.exceptions.custom_exceptions import DataBaseError
from app.services.meowapi import _api_client as _meow_api_client
from app.services.bsinfoapi import _api_client as _bsinfo_api_client
from app.utils.utils import get_normalized_ip, get_remote_ip


# [この部分は公開用リポジトリでは非公開にされています]

    # [この部分は公開用リポジトリでは非公開にされています]
    await close_redis() # [この部分は公開用リポジトリでは非公開にされています]
app.include_router(integrity_router)


# [この部分は公開用リポジトリでは非公開にされています]

    # [この部分は公開用リポジトリでは非公開にされています]
                    # [この部分は公開用リポジトリでは非公開にされています]
            except Exception as e:
                _log_middleware_exception(
                    f"ミドルウェアでの通知バッジ取得中にエラー (User: {current_user_for_state.name})",
                    e,
                )

        if should_load_page_extras:
            # [この部分は公開用リポジトリでは非公開にされています]

    context = {
        "request": request,
        "lang": lang,
        "current_page": "home", # 下部タブバーのハイライト用
        "announcements": announcements,
        "special_reward_banners": special_reward_banners,
        "tournament_home_banner": tournament_home_banner,
        "bookmarked_players": bookmarked_players_for_display, # テンプレートでの変数名変更
        "sub_account_players": sub_account_players_for_display,
        "available_bookmark_slots": available_bookmark_slots,
        "can_extend_bookmark_slots": can_extend_bookmark_slots,
        "viewed_players": viewed_players_for_display,       # テンプレートでの変数名変更
        "actual_viewed_players_count": actual_viewed_players_count, # フィルタリング後の実際の閲覧履歴数
        "bookmark_display_limit": bookmark_display_limit, # ブックマークの表示上限
        "viewed_display_limit": viewed_display_limit,      # 閲覧履歴の表示上限
        "bookmarked_clubs": bookmarked_clubs_for_display,
        "available_club_bookmark_slots": available_club_bookmark_slots,
        "can_extend_club_bookmark_slots": can_extend_club_bookmark_slots,
        "viewed_clubs": viewed_clubs_for_display,
        "actual_viewed_clubs_count": actual_viewed_clubs_count,
        "club_bookmark_display_limit": club_bookmark_display_limit,
        "club_viewed_display_limit": club_viewed_display_limit
    }
    return templates.TemplateResponse("index.html", context)


#* /---*---*---*---*---*---*---*---*/
#* 利用規約エンドポイント
#* /---*---*---*---*---*---*---*---*/
@app.get("/{lang}/terms", name="terms")
async def terms(request: Request, lang: str):
    # テンプレートに渡すコンテキスト
    context = {
        "request": request,
        "lang": lang
    }
    return templates.TemplateResponse("terms.html", context)


#* /---*---*---*---*---*---*---*---*/
#* プライバシーポリシーエンドポイント
#* /---*---*---*---*---*---*---*---*/
@app.get("/{lang}/privacy_policy", name="privacy_policy")
async def privacy_policy(request: Request, lang: str):
    # テンプレートに渡すコンテキスト
    context = {
        "request": request,
        "lang": lang
    }
    return templates.TemplateResponse("privacy_policy.html", context)

#-@app.get("/test_db")
#-async def test_db_connection(db: asyncpg.Connection = Depends(get_shared_db)):
#-    """共有DB接続テスト用エンドポイント"""
#-    try:
#-        async with db.execute("SELECT sqlite_version()") as cur:
#-            version = await cur.fetchone()
#-            return {"sqlite_version": version[0] if version else "N/A"}
#-    except Exception as e:
#-        logger.error(f"共有DB接続テスト中にエラー: {e}")
#-        return {"error": str(e)}


#* /---*---*---*---*---*---*---*---*/
#* StarletteHTTPException を捕捉する例外ハンドラ
#* /---*---*---*---*---*---*---*---*/
@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    lang = request.path_params.get("lang", request.headers.get("accept-language", "ja").split(',')[0].split('-')[0]) # lang取得を改善
    if lang not in ['ja', 'en']: lang = 'ja' # サポート外言語はデフォルトへ

    if exc.status_code == status.HTTP_401_UNAUTHORIZED:
        original_url = str(request.url)
        try:
            login_url_path = request.app.url_path_for("login_page", lang=lang)
            next_param_encoded = quote_plus(original_url)
            final_redirect_url = f"{login_url_path}?next={next_param_encoded}"
            logger.debug(f"401エラー: ログインページへリダイレクト ({final_redirect_url})")
            return RedirectResponse(url=final_redirect_url, status_code=status.HTTP_303_SEE_OTHER)
        except Exception as e_url:
            logger.error(f"ログインページへのリダイレクトURL生成に失敗: {e_url}")
            try:
                fallback_login_url = request.app.url_path_for("login_page", lang=lang)
                return RedirectResponse(url=fallback_login_url, status_code=status.HTTP_303_SEE_OTHER)
            except Exception:
                return RedirectResponse(url=f"/{lang}", status_code=status.HTTP_303_SEE_OTHER)

    if exc.status_code == status.HTTP_403_FORBIDDEN:
        path = request.url.path
        is_admin_path = "/admin" in path
        wants_json = (
            request.method != "GET"
            or "/api/" in path
            or "application/json" in (request.headers.get("accept") or "")
        )
        logger.info(f"HTTPクライアントエラー: Code: 403, Path: {path}, Detail: {exc.detail}")
        if is_admin_path and wants_json:
            return JSONResponse(
                {"success": False, "detail": exc.detail, "message": exc.detail},
                status_code=403,
            )
        if is_admin_path:
            return templates.TemplateResponse(
                "admin/forbidden.html",
                {
                    "request": request,
                    "lang": lang,
                    "current_page": "account",
                    "error_detail": exc.detail,
                    "is_error_page": True,
                },
                status_code=403,
            )
        return templates.TemplateResponse(
            "error/server_error.html",
            {"request": request, "lang": lang, "error_code": 403, "error_detail": exc.detail, "is_error_page": True},
            status_code=403,
        )

    elif exc.status_code == 404:
        logger.debug(f"404エラーが発生しました: Path: {request.url.path}, Detail: {exc.detail}")
        return templates.TemplateResponse(
            "error/generic_404.html", # 汎用404エラーページ
            {"request": request, "lang": lang, "is_error_page": True},
            status_code=404
        )
    elif exc.status_code == 405:
        logger.info(f"許可されていないメソッドでのアクセス: Code: {exc.status_code}, Path: {request.url.path}, Detail: {exc.detail}")
        return templates.TemplateResponse(
            "error/server_error.html", # サーバーエラーページ
            {"request": request, "lang": lang, "error_code": exc.status_code, "error_detail": exc.detail, "is_error_page": True},
            status_code=exc.status_code
        )
    # バリデーションエラー（タグ形式不正）の場合はWARNINGレベルで1行だけ出力
    if exc.status_code == 400 and exc.detail in ["Invalid player tag format.", "Invalid club tag format."]:
        tag_type = "プレイヤー" if "player" in exc.detail.lower() else "クラブ"
        # URLパスからタグ部分を推測してログに含める（便宜上、パスの最後をタグと見なす）
        path_segments = [s for s in request.url.path.split("/") if s]
        target_tag = path_segments[-1] if path_segments else "unknown"
        logger.warning(f"不正なタグ \"{target_tag}\" の{tag_type}ページへのアクセスをブロックしました")
        return templates.TemplateResponse(
            "error/server_error.html",
            {"request": request, "lang": lang, "error_code": 400, "error_detail": exc.detail, "is_error_page": True},
            status_code=400
        )

    # 4xxはクライアント起因の想定内エラー。トレースバック付きERRORは過剰なので短く記録する
    if 400 <= exc.status_code < 500:
        logger.info(f"HTTPクライアントエラー: Code: {exc.status_code}, Path: {request.url.path}, Detail: {exc.detail}")
        return templates.TemplateResponse(
            "error/server_error.html",
            {"request": request, "lang": lang, "error_code": exc.status_code, "error_detail": exc.detail, "is_error_page": True},
            status_code=exc.status_code
        )

    # 500系やその他のHTTPエラー
    logger.error(f"HTTPエラーが発生しました: Code: {exc.status_code}, Path: {request.url.path}, Detail: {exc.detail}", exc_info=True)
    return templates.TemplateResponse(
        "error/server_error.html", # サーバーエラーページ
        {"request": request, "lang": lang, "error_code": exc.status_code, "error_detail": exc.detail, "is_error_page": True},
        status_code=exc.status_code
    )

# FastAPI の HTTPException も上記ハンドラで処理するように
@app.exception_handler(HTTPException)
async def fastapi_http_exception_handler(request: Request, exc: HTTPException):
    return await custom_http_exception_handler(request, StarletteHTTPException(status_code=exc.status_code, detail=exc.detail, headers=exc.headers))

# FastAPI標準の422ハンドラは、UTF-8として不正なリクエストボディ(bytes)をそのままデコードしようとして500になるため、置換文字でデコードする
@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": jsonable_encoder(exc.errors(), custom_encoder={bytes: lambda b: b.decode("utf-8", errors="replace")})},
    )

#* /---*---*---*---*---*---*---*---*/
#* 一般的な Exception を捕捉するハンドラ (予期せぬエラー)
#* /---*---*---*---*---*---*---*---*/
def _transient_error_response(request: Request, exc: Exception) -> Response:
    """DB接続枯渇などの一時障害。ミドルウェアが重なってトレースバックが1件数百行になるため、間引いて1行で記録する。"""
    lang = request.path_params.get("lang", "ja")
    message = f"一時的なDB/Redis障害のためリクエストを処理できませんでした (503): Path: {request.url.path}, Error: {exc}"
    if is_transient_pg_error(exc):
        log_transient_pg_warning(message)
    else:
        log_transient_redis_warning(message)
    return templates.TemplateResponse(
        "error/server_error.html",
        {"request": request, "lang": lang, "error_code": 503, "error_detail": "一時的にアクセスが集中しています。", "is_error_page": True},
        status_code=503,
        headers={"Retry-After": "30"},
    )


# Exception 用ハンドラの後は ServerErrorMiddleware が例外を再送出し、uvicorn も同じトレースバックを出力する。
# 接続枯渇はクラスを指定して内側の ExceptionMiddleware で処理を終え、再送出させない。
async def transient_db_exception_handler(request: Request, exc: Exception):
    return _transient_error_response(request, exc)

for _transient_exc_class in (PoolAcquireTimeoutError, asyncpg.TooManyConnectionsError, asyncpg.CannotConnectNowError):
    app.add_exception_handler(_transient_exc_class, transient_db_exception_handler)


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    lang = request.path_params.get("lang", "ja") # URLから言語コードを取得、なければデフォルト
    root_exc: BaseException = exc
    while isinstance(root_exc, BaseExceptionGroup) and len(root_exc.exceptions) == 1:
        root_exc = root_exc.exceptions[0]
    if isinstance(root_exc, Exception) and (is_transient_pg_error(root_exc) or is_transient_redis_error(root_exc)):
        return _transient_error_response(request, root_exc)
    logger.warning(f"予期せぬエラーが発生しました: Path: {request.url.path}, Error: {exc}", exc_info=True)
    return templates.TemplateResponse(
        "error/server_error.html", # サーバーエラーページ
        {"request": request, "lang": lang, "error_code": 500, "error_detail": "予期せぬサーバーエラーが発生しました。", "is_error_page": True},
        status_code=500
    )
