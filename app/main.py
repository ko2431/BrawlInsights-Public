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
from app.core.cache import connect_redis, close_redis, set_cache, get_cache
from app.db.db import connect_to_db, close_db_connection, get_shared_db, get_db_connection_for_bg_task
#-from app.background_tasks.scheduler import start_scheduler, shutdown_scheduler
#-from app.background_tasks.tasks import player_update_task_loop
from app.routers import player, club, auth, account, stats, tools, boards, help, admin, billing
from app.core.templating import templates
from app.services.user_service import User, get_user, get_announcements
from app.services.brawl_service import get_player_name, get_player_icon_from_db, get_club_name, get_club_badge_id_from_db
from app.exceptions.custom_exceptions import DataBaseError
from app.services.meowapi import _api_client as _meow_api_client
from app.services.bsinfoapi import _api_client as _bsinfo_api_client
from app.utils.utils import get_normalized_ip, get_remote_ip


# プロジェクトのルートディレクトリを取得
BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = Path(__file__).resolve().parent
GENERATED_DIR = BASE_DIR / "generated"


#* /---*---*---*---*---*---*---*---*/
#* Lifespan イベントハンドラ
#* /---*---*---*---*---*---*---*---*/
@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logger()
    logger.info("Brawl Insights 起動処理を開始します (lifespan)...")
    
    # 共有データベース接続
    try:
        await connect_to_db() # DBコネクションプールを作成
    except Exception as e:
        logger.error(f"データベース接続の初期化に失敗しました: {e}", exc_info=True)
        raise RuntimeError("データベース接続エラー。PostgreSQLのコネクションプール作成に失敗しました。") from e
    
    # Redis接続処理
    try:
        await connect_redis() # Redis接続プールを初期化
    except Exception as e:
        logger.error(f"Redis接続の初期化に失敗しました: {e}", exc_info=True)
        raise RuntimeError("Redis接続の初期化に失敗しました") from e

    #* バックグラウンドタスクは、専用の別プログラム(worker.py)に移行
    # バックグラウンドタスクループを開始
    #-loop = asyncio.get_running_loop()
    #-update_task = loop.create_task(player_update_task_loop())
    #-app.state.update_task = update_task
    #-logger.info("プレイヤーデータのバックグラウンド更新タスクを開始しました。")

    # バックグラウンドスケジューラーを開始
    #-try:
    #-    await start_scheduler()
    #-    logger.info("バックグラウンドタスクスケジューラーを開始しました。")
    #-except Exception as e:
    #-    logger.error(f"バックグラウンドタスクスケジューラーの開始に失敗しました: {e}", exc_info=True)
    #-    raise RuntimeError("バックグラウンドタスクスケジューラーの開始に失敗しました") from e

    logger.info("Brawl Insights が起動しました (lifespan)。")
    #* ---
    yield
    #* ---
    logger.info("Brawl Insights 終了処理を開始します (lifespan)...")
    
    #-await shutdown_scheduler() # スケジューラーをシャットダウンする
    
    # バックグラウンドタスクループをキャンセルする
    #-task = app.state.update_task
    #-if task and not task.done():
    #-    logger.info("バックグラウンド更新タスクをキャンセルします。")
    #-    task.cancel()
    #-    try:
    #-        # 5秒のタイムアウトを設けてタスクの終了を待つ
    #-        await asyncio.wait_for(task, timeout=5.0)
    #-    except asyncio.CancelledError:
    #-        # タスクが正常にキャンセルされた場合（これが理想的なパス）
    #-        logger.info("バックグラウンド更新タスクは正常にキャンセルされました。")
    #-    except asyncio.TimeoutError:
    #-        # タスクが5秒以内に終了しなかった場合
    #-        logger.warning("バックグラウンドタスクのシャットダウンがタイムアウトしました。応答がありません。")
    #-    except Exception as e:
    #-        # その他の予期せぬエラー
    #-        logger.error(f"バックグラウンドタスクのシャットダウン中に予期せぬエラーが発生しました: {e}", exc_info=True)

    await close_redis() # Redis接続プールを閉じる
    await close_db_connection() # DBコネクションプールを閉じる
    try:
        await _meow_api_client.aclose() # アプリケーション終了時にAPIクライアントを閉じる
        logger.info("MeowAPI クライアント接続を閉じました。")
    except Exception as e:
        logger.error(f"MeowAPI クライアントのクローズ中にエラーが発生しました: {e}", exc_info=True)
    try:
        await _bsinfo_api_client.aclose() # アプリケーション終了時にAPIクライアントを閉じる
        logger.info("BSInfo API クライアント接続を閉じました。")
    except Exception as e:
        logger.error(f"BSInfo API クライアントのクローズ中にエラーが発生しました: {e}", exc_info=True)
    logger.info("Brawl Insights が終了しました (lifespan)。")


#* /---*---*---*---*---*---*---*---*/
#* FastAPI アプリケーション設定
#* /---*---*---*---*---*---*---*---*/
# lifespanパラメータに上で定義したコンテキストマネージャ関数を指定
app = FastAPI(
    title="Brawl Insights",
    description="ブロスタの戦績Webアプリ",
    version="14.5_V11",
    lifespan=lifespan, # lifespan を指定
    docs_url=None,    # Swagger UI を無効化
    redoc_url=None    # ReDoc を無効化
)
# テンプレート内でアプリのバージョンを使えるようにグローバル変数へ登録
templates.env.globals['app_version'] = app.version


#* /---*---*---*---*---*---*---*---*/
#* 作成したルーターをアプリに登録
#* /---*---*---*---*---*---*---*---*/
app.include_router(player.router)
app.include_router(player.api_router)
app.include_router(club.router)
app.include_router(club.api_router)
app.include_router(auth.router)
app.include_router(account.router)
app.include_router(billing.router)
app.include_router(billing.lang_router)
app.include_router(stats.router)
app.include_router(tools.router)
app.include_router(boards.router)
app.include_router(help.router)
app.include_router(admin.router)


#* /---*---*---*---*---*---*---*---*/
#* 静的ファイルとテンプレート設定
#* /---*---*---*---*---*---*---*---*/
GENERATED_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.mount("/generated", StaticFiles(directory=str(GENERATED_DIR)), name="generated")


#* /---*---*---*---*---*---*---*---*/
#* robots.txt エンドポイント
#* /---*---*---*---*---*---*---*---*/
@app.get("/robots.txt", include_in_schema=False, name="robots_txt")
async def robots_txt():
    """
    robots.txt を返すエンドポイント
    app/static/robots.txt をプレーンテキストとして返します。
    """
    # app/static/robots.txt のパスを解決
    robots_path = Path(__file__).parent / "static" / "robots.txt"

    if not robots_path.is_file():
        # ファイルが存在しない場合は404エラーを返す
        logger.error(f"robots.txtファイルが見つかりません。パス: {robots_path}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="robots.txt not found")

    # ファイルの内容を読み込んでプレーンテキストとして返す
    content = robots_path.read_text(encoding="utf-8")
    return PlainTextResponse(content=content)


#* /---*---*---*---*---*---*---*---*/
#* ads.txt エンドポイント
#* /---*---*---*---*---*---*---*---*/
@app.get("/ads.txt", include_in_schema=False, name="ads_txt")
async def ads_txt():
    """
    ads.txt を返すエンドポイント
    app/static/ads.txt をプレーンテキストとして返します。
    """
    # app/static/ads.txt のパスを解決
    ads_path = Path(__file__).parent / "static" / "ads.txt"

    if not ads_path.is_file():
        # ファイルが存在しない場合は404エラーを返す
        logger.error(f"ads.txtファイルが見つかりません。パス: {ads_path}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ads.txt not found")

    # ファイルの内容を読み込んでプレーンテキストとして返す
    content = ads_path.read_text(encoding="utf-8")
    return PlainTextResponse(content=content)

@app.get("/app-ads.txt", include_in_schema=False, name="app_ads_txt")
async def app_ads_txt():
    """
    app-ads.txt を返すエンドポイント
    app/static/app-ads.txt をプレーンテキストとして返します。
    """
    # app/static/app-ads.txt のパスを解決
    ads_path = Path(__file__).parent / "static" / "app-ads.txt"

    if not ads_path.is_file():
        # ファイルが存在しない場合は404エラーを返す
        logger.error(f"app-ads.txtファイルが見つかりません。パス: {ads_path}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="app-ads.txt not found")

    # ファイルの内容を読み込んでプレーンテキストとして返す
    content = ads_path.read_text(encoding="utf-8")
    return PlainTextResponse(content=content)


#* /---*---*---*---*---*---*---*---*/
#* サイトマップエンドポイント
#* /---*---*---*---*---*---*---*---*/
@app.get("/sitemap.xml", include_in_schema=False)
@app.get("/sitemap.xml/", include_in_schema=False)
async def sitemap(request: Request, db: asyncpg.Connection = Depends(get_shared_db)):
    """
    サイトマップをXML形式で生成するエンドポイント
    """
    # サイトのベースURLを取得 (例: "https://brawlinsights.com")
    base_url = str(request.base_url)
    if base_url.endswith('/'):
        base_url = base_url[:-1]

    # 静的なページのパスをリストアップ
    static_paths = [
        "/ja/", "/en/",
        "/ja/terms", "/en/terms",
        "/ja/privacy_policy", "/en/privacy_policy",
        "/ja/help/search_specification", "/en/help/search_specification",
        "/ja/help/about_the_technology", "/en/help/about_the_technology",
        "/ja/help/version_history", "/en/help/version_history",
        "/ja/stats", "/en/stats",
        "/ja/stats/prestige", "/en/stats/prestige",
        "/ja/stats/ranked_tier_list", "/en/stats/ranked_tier_list",
        "/ja/stats/skin_ranking", "/en/stats/skin_ranking",
        "/ja/stats/pins_ranking", "/en/stats/pins_ranking",
        "/ja/stats/player_icon_ranking", "/en/stats/player_icon_ranking",
        "/ja/stats/ranking/player", "/en/stats/ranking/player",
        "/ja/stats/ranking/brawler", "/en/stats/ranking/brawler",
        "/ja/stats/ranking/club", "/en/stats/ranking/club",
        "/ja/stats/ranking/ranked", "/en/stats/ranking/ranked",
        "/ja/stats/ranking/custom", "/en/stats/ranking/custom"
        "/ja/tools", "/en/tools",
        "/ja/tools/ranked_maps", "/en/tools/ranked_maps",
        "/ja/tools/cost_calc", "/en/tools/cost_calc",
        "/ja/tools/reward_calc", "/en/tools/reward_calc",
        "/ja/tools/trophy_reward_calc", "/en/tools/trophy_reward_calc",
        "/ja/tools/starrdrop_calc", "/en/tools/starrdrop_calc",
        "/ja/tools/trophy_table", "/en/tools/trophy_table",
        "/ja/tools/starrdrop_chances", "/en/tools/starrdrop_chances",
        "/ja/tools/pick_tool", "/en/tools/pick_tool",
        "/ja/tools/brawler_guide/menu", "/en/tools/brawler_guide/menu",
        "/ja/tools/map_rotation", "/en/tools/map_rotation",
        "/ja/tools/random_brawler", "/en/tools/random_brawler",
        "/ja/tools/app_faq", "/en/tools/app_faq",
        "/ja/tools/profile_image", "/en/tools/profile_image",
        "/ja/tools/brawl_videos", "/en/tools/brawl_videos",
        "/ja/boards/team", "/en/boards/team",
        "/ja/boards/friend", "/en/boards/friend",
        "/ja/boards/club", "/en/boards/club",
        "/ja/boards/general", "/en/boards/general",
        #> 静的ページを実装したら、ここに追加する
    ]

    # キャラクター図鑑の個別ページを動的に追加
    try:
        brawler_rows = await db.fetch("SELECT id FROM brawlers ORDER BY id")
        for row in brawler_rows:
            bid = row["id"]
            static_paths.append(f"/ja/tools/brawler_guide/stats/{bid}")
            static_paths.append(f"/en/tools/brawler_guide/stats/{bid}")
    except Exception as e:
        logger.warning(f"サイトマップ生成時にキャラクターID取得に失敗: {e}")

    # XMLコンテンツを生成
    urlset_content = ""
    for path in static_paths:
        url = f"{base_url}{path}"
        # 現在の日付を取得し、YYYY-MM-DD形式に
        lastmod = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

        urlset_content += f"""
        <url>
            <loc>{url}</loc>
            <lastmod>{lastmod}</lastmod>
            <changefreq>weekly</changefreq>
            <priority>0.8</priority>
        </url>
        """

    # 完全なXMLドキュメントを作成
    xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        {urlset_content}
    </urlset>
    """
    # XMLレスポンスを返す
    return Response(content=xml_content, media_type="application/xml")


#* /---*---*---*---*---*---*---*---*/
#* 言語判定関数
#* /---*---*---*---*---*---*---*---*/
def detect_language(request: Request) -> str:
    accept_language = request.headers.get("accept-language")
    if accept_language:
        # 簡単な判定: 'ja' が含まれていれば 'ja'、そうでなければ 'en'
        if 'ja' in accept_language.lower().split(','):
            return "ja"
    return "en" # デフォルトは英語 (または日本語でも可)


#* /---*---*---*---*---*---*---*---*/
#* ミドルウェアの定義
#* /---*---*---*---*---*---*---*---*/
class PlatformDetectionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[StarletteRequest], Awaitable[Response]]):
        """
        User-Agentを元にプラットフォームとアプリのクライアントバージョンを判定し、
        request.state に格納するミドルウェア。
        """
        user_agent = request.headers.get("user-agent", "")

        # デフォルト値を設定
        request.state.platform = "web"
        request.state.app_client_version = None

        if "BrawlInsightsApp" in user_agent:
            # プラットフォーム判定
            if "iPhone" in user_agent or "iPad" in user_agent:
                request.state.platform = "ios"
            elif "Android" in user_agent:
                request.state.platform = "android"
            else:
                request.state.platform = "app"
            
            # バージョン番号の解析
            # "BrawlInsightsApp/12.0" のような形式から "12.0" を抽出
            match = re.search(r"BrawlInsightsApp/([\d.]+)", user_agent)
            if match:
                request.state.app_client_version = match.group(1)

        response = await call_next(request)
        return response

class IPSecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[StarletteRequest], Awaitable[Response]]):
        # チェック対象のヘッダー
        # NOTE: これらのヘッダーにIPアドレス以外の文字列が含まれる場合は遮断する
        check_headers = ["X-Forwarded-For", "CF-Connecting-IP"]
        for header in check_headers:
            value = request.headers.get(header)
            if value:
                # X-Forwarded-For はカンマ区切りの可能性があるので分割してチェック
                ips = [ip.strip() for ip in value.split(",")]
                for ip in ips:
                    if get_normalized_ip(ip) is None:
                        # IPアドレスとして不正な文字列が含まれている場合は攻撃（インジェクション等）とみなして遮断
                        logger.warning(f"不正なIPヘッダー値を検出したため遮断しました: {header}={value}")
                        return Response(content="Forbidden: Invalid IP format in headers", status_code=403)
        
        return await call_next(request)

class LanguageRedirectMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[StarletteRequest], Awaitable[Response]]
    ) -> Response:
        path = request.url.path

        #< Botの場合は、プレイヤーおよびクラブおよびチャットページをブロック
        user_agent_string = request.headers.get("User-Agent", "Unknown")
        user_agent = parse(user_agent_string)
        if (user_agent.is_bot or "bot" in user_agent_string.lower()) and ("/player/" in path or "/club/" in path or "/chat/" in path):
            logger.info(f'Bot "{user_agent.browser.family}" のプレイヤー/クラブページ "{request.url.path}" へのアクセスをブロックしました')
            return Response(
                content="Access to this page is restricted for bots.",
                status_code=403
            )

        # 1. 常にリダイレクト処理から除外するパス (静的ファイル、APIドキュメントなど)
        excluded_exact_paths = [
            "/openapi.json",
            "/favicon.ico",
            "/sitemap.xml",
            "/robots.txt"
        ]
        excluded_prefixes = [
            "/static/",
            "/docs",
            "/.well-known/",
        ]
        if path in excluded_exact_paths or any(path.startswith(prefix) for prefix in excluded_prefixes):
            logger.debug(f"Path '{path}' is excluded from language redirection (static/docs).")
            return await call_next(request)

        # 2. 既に有効な言語コードで始まっているパスはそのまま処理
        lang_match = re.match(r"^/(ja|en)($|/.*)", path)
        if lang_match:
            logger.debug(f"Path '{path}' already has a valid language code. Passing through.")
            return await call_next(request)
        
        # 2b. (オプション) 無効な言語コードで始まっているパスはデフォルト言語にリダイレクト
        # invalid_lang_match = re.match(r"^/([a-zA-Z]{2,3})($|/.*)", path) # ja, en 以外の2-3文字コード
        # if invalid_lang_match and invalid_lang_match.group(1) not in ['ja', 'en']:
        #     lang_code = invalid_lang_match.group(1)
        #     preferred_lang = "ja" # デフォルト言語
        #     new_path = f"/{preferred_lang}{invalid_lang_match.group(2) or ''}"
        #     redirect_url = request.url.replace(path=new_path)
        #     logger.warning(f"Redirecting invalid language code path '{path}' to '{redirect_url}'")
        #     return RedirectResponse(url=str(redirect_url), status_code=status.HTTP_307_TEMPORARY_REDIRECT)


        # 3. リダイレクト処理の対象となるパスパターンを定義
        #    ルートパス /
        #    /player... (ただし /player/ や /player のみは含めない方が良いかも。具体的なエンドポイントがあるパス)
        #    /club...  (同様)
        #   正規表現で具体的に指定する方が良い
        
        # 対象とするプレフィックスのリスト (言語コードなしの状態)
        # これらにマッチする場合のみ言語プレフィックス付与リダイレクトを検討する
        target_prefixes_for_lang_redirect = ["/player/", "/club/"] 
        is_target_path = False
        if path == "/": # ルートパスは常にリダイレクト対象
            is_target_path = True
        else:
            for prefix in target_prefixes_for_lang_redirect:
                if path.startswith(prefix):
                    is_target_path = True
                    break
        
        if is_target_path:
            # 4. 言語を判定
            preferred_lang = detect_language(request) # ja or en

            # 5. 新しいパスを構築
            # ルートパス / の場合は /{lang} に、それ以外は /{lang}{元のパス} に
            new_path = f"/{preferred_lang}{path if path != '/' else ''}"

            redirect_url = request.url.replace(path=new_path)
            logger.debug(f"Path '{path}' is a target for language redirection. Redirecting to '{redirect_url}'.")
            return RedirectResponse(url=str(redirect_url), status_code=status.HTTP_307_TEMPORARY_REDIRECT)
        else:
            # 6. 上記のいずれにも該当しないパスは、そのまま次の処理へ (または404)
            #    例えば、/api/v1/... や他のプレフィックスを持たないページなど
            logger.debug(f"Path '{path}' is not targeted for language redirection. Passing through.")
            return await call_next(request)

class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[StarletteRequest], Awaitable[Response]]):
        path = request.url.path
        # /ja または /en で始まるパス以外は、このミドルウェアの処理をスキップ
        if not (path.startswith('/ja') or path.startswith('/en')):
            return await call_next(request)
        
        #^ ログから除外するAPIパスの断片を定義
        EXCLUDED_LOG_PATHS = [
            "/tools/api/ban_suggestions",
            "/tools/api/pick_suggestions",
            "/tools/api/predict_win_rate",
            "/tools/api/profile_image/"
        ]

        # 現在のリクエストパスが除外リストのいずれかを含むかチェック
        if any(excluded_path in path for excluded_path in EXCLUDED_LOG_PATHS):
            return await call_next(request) #> 含まれていたらログを出力せずに次の処理へ
        
        start_time = time.time()
        # クライアントのIPアドレスを取得
        client_ip = get_remote_ip(request)
        response = await call_next(request)
        
        user_agent_string = request.headers.get("User-Agent", "Unknown") # ブラウザやデバイス情報
        
        referer_header = request.headers.get("Referer", "Direct") # どのページからアクセスしてきたか
        referer = referer_header

        # Refererが "Direct" ではなく、かつ有効なURL形式の場合
        if referer_header != "Direct":
            try:
                parsed_referer = urlparse(referer_header)
                # リファラのホスト名とリクエストのホスト名が一致する場合（自サイト内からの遷移）
                if parsed_referer.hostname == request.url.hostname:
                    # パスとクエリパラメータだけを記録する
                    referer = parsed_referer.path
                    if parsed_referer.query:
                        referer += f"?{parsed_referer.query}"
                    # ルートパスからの遷移の場合、空文字ではなく "/" を設定
                    if not referer:
                        referer = "/"
            except (ValueError, AttributeError):
                # 不正なURL形式などでパースに失敗した場合は、元のヘッダー値をそのまま使用
                pass
        
        # 処理時間を計算
        process_time = time.time() - start_time
        # アクセスログを記録
        extra_info = get_log_extra_info(request)
        extra_log = f" | [追加情報] {extra_info}" if extra_info else ""
        
        # ログインしているユーザーの情報を取得してログに追加
        current_user = getattr(request.state, 'current_user', None)
        user_info = f" | ユーザー: {current_user.name} ({current_user.id})" if current_user else ""
        
        platform = getattr(request.state, "platform", "unknown")
        
        user_agent = parse(user_agent_string)
        device_type = "Unknown"
        if user_agent.is_pc:
            device_type = "PC"
        elif user_agent.is_mobile:
            device_type = "Mobile"
        elif user_agent.is_tablet:
            device_type = "Tablet"
        elif user_agent.is_bot:
            device_type = "Bot"
        short_ua = (
            f"{device_type}, "
            f"{user_agent.os.family}, "
            f"{user_agent.browser.family}"
        )
        
        logger.info(
            f"IP: {client_ip} | "
            f"Platform: {platform} | "
            f"UA: {short_ua} | "
            f"Referer: {referer} | "
            f"Method: {request.method} | "
            f"Path: {request.url.path} | "
            f"Status: {response.status_code} | "
            f"処理時間: {process_time:.4f}秒"
            f"{user_info}"
            f"{extra_log}"
        )
        return response

#* ユーザー情報を request.state に格納するミドルウェア
class UserToStateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[StarletteRequest], Awaitable[Response]]):
        path = request.url.path
        # /ja, /en に加えて、フラグメントAPI (/api/ja, /api/en) でも実行する
        if not (
            path.startswith('/ja')
            or path.startswith('/en')
            or path.startswith('/api/ja')
            or path.startswith('/api/en')
        ):
            return await call_next(request)
        
        current_user_for_state: User | None = None # 変数名を変更して明確化
        user_id_str = request.session.get("user_id")

        if user_id_str:
            try:
                user_id = int(user_id_str)
                # ミドルウェア内ではDependsは使えないため、バックグラウンドタスク用のコンテキストマネージャを使用
                async with get_db_connection_for_bg_task() as db_conn:
                    user_obj_from_db = await get_user(db=db_conn, id=user_id)

                    if user_obj_from_db and user_obj_from_db.is_invalid:
                        current_user_for_state = None
                        request.session.clear()
                    elif user_obj_from_db:
                        current_user_for_state = user_obj_from_db
                    else:
                        request.session.clear()

            except ValueError: # user_id_str が整数に変換できない
                logger.warning(f"UserToStateMiddleware: セッションのuser_id '{user_id_str}' が不正です。")
                request.session.pop("user_id", None)
                if "username" in request.session: request.session.pop("username", None)
            except Exception as e:
                logger.error(f"UserToStateMiddleware でユーザー情報取得中にエラー: {e}", exc_info=True)
                request.session.pop("user_id", None) # エラー時もセッションクリア
                if "username" in request.session: request.session.pop("username", None)
        
        request.state.current_user = current_user_for_state
        #- logger.debug(f"UserToStateMiddleware: request.state.current_user is set to type: {type(current_user_for_state)}")

        # PVカウントから除外するAPIパスの断片を定義
        API_PATH_SEGMENTS_TO_EXCLUDE = [
            "/check-permission/",
            "/last-post/",
            "/api/"
            #= その他、PVカウントから除外したいgetメソッドのパスを実装したらここに追加する
        ]
        # 現在のリクエストパスが除外リストのいずれかを含むかチェック
        is_api_call = any(segment in path for segment in API_PATH_SEGMENTS_TO_EXCLUDE)

        if current_user_for_state and request.method == "GET" and not is_api_call:
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
                logger.error(f"ミドルウェアでのトークン付与処理中にエラー (User: {current_user_for_state.name}): {e}", exc_info=True)

            # PVカウントと最終閲覧日時更新 ---
            try:
                await current_user_for_state.record_view_in_redis()
            except Exception as e:
                logger.error(f"ミドルウェアでのPVカウント記録中にエラー (User: {current_user_for_state.name}): {e}", exc_info=True)
        
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
