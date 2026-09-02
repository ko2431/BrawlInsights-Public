import re
import time
import datetime
import asyncpg
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, status, Depends, HTTPException
from fastapi.responses import RedirectResponse, Response, PlainTextResponse
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
from app.db.db import connect_to_db, close_db_connection, get_shared_db, get_db_connection_for_bg_task, is_transient_pg_error, log_transient_pg_warning
# [この部分は公開用リポジトリでは非公開にされています]

    # [この部分は公開用リポジトリでは非公開にされています]
    await close_redis() # [この部分は公開用リポジトリでは非公開にされています]
            except Exception as e:
                _log_middleware_exception(
                    f"ミドルウェアでの通知バッジ取得中にエラー (User: {current_user_for_state.name})",
                    e,
                )

        if should_load_page_extras:
            # デイリー初回アクセスのトークン付与処理 ---
            try:
                # Redisを使い、本日すでにトークン付与チェックを行ったかを確認
                cache_key = f"daily_token_claimed:{current_user_for_state.id}"
                already_checked_today = await get_cache(cache_key)

                # Redisにキーがなければ、DBを確認してトークンを付与する
                if not already_checked_today:
                    async with get_db_connection_for_bg_task() as db_conn:
                        claimed = await current_user_for_state.claim_tokens(db=db_conn, claimed=5)
                        if claimed:
                            logger.debug(f"ユーザー '{current_user_for_state.name}' にデイリー初回アクセストークンを5付与しました。")
                        else:
                            logger.debug(f"ユーザー '{current_user_for_state.name}' はトークン所持数が上限に達しているため、デイリー初回アクセストークンを付与できません。")

                    # チェック済みフラグをRedisにセット。UTCの深夜0時に失効させる。
                    now_utc = datetime.datetime.now(datetime.timezone.utc)
                    tomorrow_utc = now_utc + datetime.timedelta(days=1)
                    midnight_utc = tomorrow_utc.replace(hour=0, minute=0, second=0, microsecond=0)
                    seconds_until_midnight = int((midnight_utc - now_utc).total_seconds())
                    await set_cache(cache_key, True, seconds_until_midnight)

            except Exception as e:
                _log_middleware_exception(
                    f"ミドルウェアでのトークン付与処理中にエラー (User: {current_user_for_state.name})",
                    e,
                )

            # PVカウントと最終閲覧日時更新 ---
            try:
                await current_user_for_state.record_view_in_redis()
            except Exception as e:
                _log_middleware_exception(
                    f"ミドルウェアでのPVカウント記録中にエラー (User: {current_user_for_state.name})",
                    e,
                )
        
        response = await call_next(request)
        return response

#* 匿名IDを生成する
class AnonymousIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[StarletteRequest], Awaitable[Response]]):
        # 匿名ID用のCookie名
        ANONYMOUS_ID_COOKIE_KEY = "brawlanonid"

        # リクエストからCookieを取得
        anonymous_id = request.cookies.get(ANONYMOUS_ID_COOKIE_KEY)
        
        # 次の処理（エンドポイントなど）を呼び出す
        response = await call_next(request)

        # Cookieが存在しなかった場合のみ、レスポンスに新しいCookieを設定する
        if not anonymous_id:
            new_id = str(uuid.uuid4())
            # 1年間有効なCookieを設定
            response.set_cookie(
                key=ANONYMOUS_ID_COOKIE_KEY,
                value=new_id,
                max_age=365 * 24 * 60 * 60, # 1年間 (秒)
                httponly=True, # JavaScriptからのアクセスを禁止
                samesite='lax',
                secure=settings.SESSION_HTTPS_ONLY # 本番環境(HTTPS)ではTrueに
            )
        
        return response


#* /---*---*---*---*---*---*---*---*/
#* ミドルウェアをアプリケーションに追加 (下から上に実行される)
#* /---*---*---*---*---*---*---*---*/
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
app.add_middleware(AccessLogMiddleware)
app.add_middleware(IPSecurityMiddleware)
app.add_middleware(AnonymousIdMiddleware)
# 後から add したものが先に実行される。Gate は Platform / User の内側（よりアプリ側）に置く。
app.add_middleware(IntegrityGateMiddleware)
app.add_middleware(UserToStateMiddleware)
app.add_middleware(PlatformDetectionMiddleware)
app.add_middleware(LanguageRedirectMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SESSION_SECRET_KEY,
    session_cookie="brawlsession",  # クッキー名を変更する場合 (オプション)
    max_age= 365 * 24 * 60 * 60,  # セッションの有効期限 (秒単位。開発環境ではセッション切れ確認のため短めに設定することもある)
    https_only=settings.SESSION_HTTPS_ONLY
)


#* /---*---*---*---*---*---*---*---*/
#* ホームページエンドポイント
#* /---*---*---*---*---*---*---*---*/
@app.get("/{lang}", name="home") # name="home" を確認 (url_for用)
async def home(request: Request, lang: str, db: asyncpg.Connection = Depends(get_shared_db)):
    if lang not in ["ja", "en"]:
        # サポート外言語はデフォルト(例: ja)へリダイレクト
        return RedirectResponse(url=request.url_for('home', lang='ja'), status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    
    # お知らせ(最初の3件)を取得。ただしプラットフォーム指定のお知らせは該当プラットフォームのみに表示
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
        if len(announcements) >= 3:
            break
    
    current_login_user: User | None = getattr(request.state, "current_user", None)
    
    bookmarked_players_for_display = []
    can_extend_bookmark_slots = False # 将来の拡張用フラグ (今は常にFalse)
    viewed_players_for_display = []
    actual_viewed_players_count = 0
    available_bookmark_slots = 0
    bookmarked_clubs_for_display = []
    can_extend_club_bookmark_slots = False
    viewed_clubs_for_display = []
    actual_viewed_clubs_count = 0
    available_club_bookmark_slots = 0
    
    # 表示するブックマークの上限数 (最大25件とする)
    bookmark_display_limit = 25 
    # 表示する閲覧履歴の上限数 (最大25件とする)
    viewed_display_limit = 25
    # クラブのブックマーク表示上限 (最大25件)
    club_bookmark_display_limit = 25
    # クラブの閲覧履歴表示上限 (最大25件)
    club_viewed_display_limit = 25
    
    if current_login_user:
        # --- メインアカウントの情報を取得 ---
        main_account_tag = current_login_user.main_account
        main_account_name = main_account_tag # デフォルトはタグ
        main_account_icon = 0 # デフォルトアイコン
        try:
            name_result = await get_player_name(main_account_tag, db)
            if name_result:
                main_account_name = name_result
            icon_result = await get_player_icon_from_db(main_account_tag, db)
            if icon_result is not None:
                main_account_icon = icon_result
        except Exception as e:
            logger.warning(f"メインアカウントの情報取得中にエラー (タグ: {main_account_tag}): {e}")
        
        bookmarked_players_for_display.append({
            "tag": main_account_tag,
            "name": main_account_name,
            "icon_id": main_account_icon,
            "is_main": True # メインアカウントであることを示すフラグ
        })
        
        # ブックマーク表示用データの準備 ---
        # Userオブジェクトの saved_accounts_limit を参照する(limitが0になっている場合はUser側のlimitを無視)
        tags_to_fetch_names_bookmarked = current_login_user.saved_accounts[:(min(bookmark_display_limit - 1, current_login_user.saved_accounts_limit if current_login_user.saved_accounts_limit else (bookmark_display_limit - 1)))]
        for tag in tags_to_fetch_names_bookmarked:
            name = tag # デフォルトはタグ
            icon_id = 0
            try:
                name_result = await get_player_name(tag, db)
                if name_result:
                    name = name_result
                icon_result = await get_player_icon_from_db(tag, db)
                if icon_result is not None:
                    icon_id = icon_result
            except Exception as e:
                logger.warning(f"ブックマークのプレイヤー情報取得中にエラー (タグ: {tag}): {e}")
            bookmarked_players_for_display.append({
                "tag": tag,
                "name": name,
                "icon_id": icon_id,
                "is_main": False
            })
            
        # 空きスロット数の計算 (ユーザー設定上限 - 現在のブックマーク数)
        # ただし、表示上の空きは (effective_bookmark_limit_for_user - 表示中のブックマーク数)
        # ここでは「実際にあといくつ追加できるか」を表示するため、ユーザー設定の上限を基準とする
        available_bookmark_slots = current_login_user.saved_accounts_limit - len(current_login_user.saved_accounts)
        if available_bookmark_slots < 0: # 念のため
            available_bookmark_slots = 0

        # 拡張可能性の判定 (現在は未実装なので常にFalse)
        # 将来的に、例えば current_user.saved_accounts_limit がシステム上限(24)未満の場合に True にするなど
        if current_login_user.saved_accounts_limit < bookmark_display_limit - 1: # システム上の上限を24と仮定
            can_extend_bookmark_slots = True # 例：あとで広告視聴などの条件を追加する

        # 閲覧履歴表示用データの準備 ---
        # メインアカウントとブックマークを除外した閲覧履歴タグリストを作成
        filtered_viewed_tags = [
            tag for tag in current_login_user.viewed_accounts 
            if tag != current_login_user.main_account and tag not in current_login_user.saved_accounts
        ]
        actual_viewed_players_count = len(filtered_viewed_tags)
        
        # Userオブジェクトの viewed_accounts_limit を参照(limitが0になっている場合はUser側のlimitを無視)
        tags_to_fetch_names_viewed = filtered_viewed_tags[:(min(viewed_display_limit, current_login_user.viewed_accounts_limit if current_login_user.viewed_accounts_limit else viewed_display_limit))]
        
        for tag in tags_to_fetch_names_viewed:
            name = tag
            icon_id = 0
            try:
                name_result = await get_player_name(tag, db)
                if name_result:
                    name = name_result
                icon_result = await get_player_icon_from_db(tag, db)
                if icon_result is not None:
                    icon_id = icon_result
                viewed_players_for_display.append({"tag": tag, "name": name, "icon_id": icon_id})
            except Exception as e:
                logger.warning(f"閲覧履歴のプレイヤー情報取得中にエラー (タグ: {tag}): {e}")
                viewed_players_for_display.append({"tag": tag, "name": tag, "icon_id": 0})

        # クラブブックマーク表示用データの準備 ---
        tags_to_fetch_names_bookmarked_clubs = current_login_user.saved_clubs[:(min(club_bookmark_display_limit, current_login_user.saved_clubs_limit if current_login_user.saved_clubs_limit else club_bookmark_display_limit))]
        for tag in tags_to_fetch_names_bookmarked_clubs:
            name = tag
            badge_id = None
            try:
                name_result = await get_club_name(tag, db)
                if name_result:
                    name = name_result
                badge_result = await get_club_badge_id_from_db(tag, db)
                if badge_result is not None:
                    badge_id = badge_result
            except Exception as e:
                logger.warning(f"ブックマークのクラブ情報取得中にエラー (タグ: {tag}): {e}")
            bookmarked_clubs_for_display.append({"tag": tag, "name": name, "badge_id": badge_id})

        available_club_bookmark_slots = current_login_user.saved_clubs_limit - len(current_login_user.saved_clubs)
        if available_club_bookmark_slots < 0:
            available_club_bookmark_slots = 0

        if current_login_user.saved_clubs_limit < club_bookmark_display_limit:
            can_extend_club_bookmark_slots = True

        # クラブ閲覧履歴表示用データの準備 ---
        filtered_viewed_club_tags = [
            tag for tag in current_login_user.viewed_clubs
            if tag not in current_login_user.saved_clubs
        ]
        actual_viewed_clubs_count = len(filtered_viewed_club_tags)

        tags_to_fetch_names_viewed_clubs = filtered_viewed_club_tags[:(min(club_viewed_display_limit, current_login_user.viewed_clubs_limit if current_login_user.viewed_clubs_limit else club_viewed_display_limit))]

        for tag in tags_to_fetch_names_viewed_clubs:
            name = tag
            badge_id = None
            try:
                name_result = await get_club_name(tag, db)
                if name_result:
                    name = name_result
                badge_result = await get_club_badge_id_from_db(tag, db)
                if badge_result is not None:
                    badge_id = badge_result
                viewed_clubs_for_display.append({"tag": tag, "name": name, "badge_id": badge_id})
            except Exception as e:
                logger.warning(f"閲覧履歴のクラブ情報取得中にエラー (タグ: {tag}): {e}")
                viewed_clubs_for_display.append({"tag": tag, "name": tag, "badge_id": badge_id})

    context = {
        "request": request,
        "lang": lang,
        "current_page": "home", # 下部タブバーのハイライト用
        "announcements": announcements,
        "bookmarked_players": bookmarked_players_for_display, # テンプレートでの変数名変更
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

#* /---*---*---*---*---*---*---*---*/
#* 一般的な Exception を捕捉するハンドラ (予期せぬエラー)
#* /---*---*---*---*---*---*---*---*/
@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    lang = request.path_params.get("lang", "ja") # URLから言語コードを取得、なければデフォルト
    logger.warning(f"予期せぬエラーが発生しました: Path: {request.url.path}, Error: {exc}", exc_info=True)
    return templates.TemplateResponse(
        "error/server_error.html", # サーバーエラーページ
        {"request": request, "lang": lang, "error_code": 500, "error_detail": "予期せぬサーバーエラーが発生しました。", "is_error_page": True},
        status_code=500
    )
