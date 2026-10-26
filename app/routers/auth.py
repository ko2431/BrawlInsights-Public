from fastapi import APIRouter, Request, Depends, Form, status, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel, field_validator, Field
import asyncpg
from urllib.parse import urlparse

from app.core.templating import templates
from app.services.user_service import (
    verify_password, get_user_id_by_name, get_user,
    create_user, is_user_name_used, get_all_secret_questions, get_secret_question
)
from app.services.brawl_service import (
    get_player_name, get_player_icon_from_db, get_player, get_player_from_db, check_verify
)
from app.utils.utils import format_tag, confirm_tag
from app.services import player_verification_service
from app.db.db import get_shared_db
from app.core.logger import logger
from app.exceptions.custom_exceptions import DataBaseError, BrawlStarsAPIError


router = APIRouter(
    tags=["Authentication"] # APIドキュメント用タグ
)


# /---*---*---*---*---*---*---*---*/
# ヘルパー関数
# /---*---*---*---*---*---*---*---*/
def is_safe_redirect_url(target_url: str | None, request: Request) -> bool:
    """リダイレクト先URLが安全かどうかを判定する"""
    if not target_url:
        return False
    try:
        parsed_target = urlparse(target_url)
        # スキームがHTTP/HTTPSであるか、またはスキームがない(相対パス)
        if parsed_target.scheme and parsed_target.scheme not in ['http', 'https']:
            return False
        # netloc (ドメイン) がある場合、現在のリクエストのドメインと一致するか
        if parsed_target.netloc and parsed_target.netloc != request.url.netloc:
            return False
        # / で始まらない相対パスや、// で始まるスキーマ相対URLは安全でないとみなす
        if not parsed_target.scheme and not parsed_target.netloc and not target_url.startswith('/'):
            return False
        if target_url.startswith('//'): # スキーマ相対URLは禁止
             return False
        # フラグメントAPIなど、ページとして表示できないパスへのリダイレクトを禁止
        redirect_path = parsed_target.path or ''
        if redirect_path.startswith('/api/'):
            return False
        return True
    except Exception:
        return False


# /---*---*---*---*---*---*---*---*/
# ログインページ
# /---*---*---*---*---*---*---*---*/
@router.get("/{lang}/login", response_class=HTMLResponse, name="login_page")
async def login_page(request: Request, lang: str, next: str | None = Query(None), username: str | None = Query(None)):
    # 既にログイン済みの場合はホームページへリダイレクト (オプション)
    if request.session.get("user_id"):
        return RedirectResponse(url=request.url_for("home", lang=lang), status_code=status.HTTP_303_SEE_OTHER)
    
    # current_user をテンプレートに渡す (未ログインなので None になるはず)
    # user_processor は自動的にコンテキストに追加されるので、明示的にここで取得する必要はない
    return templates.TemplateResponse("auth/login.html", {"request": request, "lang": lang, "next": next, "username": username})


# /---*---*---*---*---*---*---*---*/
# ログイン処理
# /---*---*---*---*---*---*---*---*/
class LoginForm(BaseModel):
    username: str
    password: str

@router.post("/{lang}/login", name="login_process")
async def login_process(
    request: Request,
    lang: str,
    username: str = Form(...),
    password: str = Form(...),
    next: str | None = Form(None),
    db: asyncpg.Connection = Depends(get_shared_db)
):
    # [この部分は公開用リポジトリでは非公開にされています]
    return RedirectResponse(url=str(redirect_url_str), status_code=status.HTTP_303_SEE_OTHER)


# /---*---*---*---*---*---*---*---*/
# ログアウト処理
# /---*---*---*---*---*---*---*---*/
# [この部分は公開用リポジトリでは非公開にされています]

# /---*---*---*---*---*---*---*---*/
# アカウント作成ページ表示
# /---*---*---*---*---*---*---*---*/
# [この部分は公開用リポジトリでは非公開にされています]

# /---*---*---*---*---*---*---*---*/
# ユーザー名バリデーションエンドポイント (HTMX/Fetch用)
# /---*---*---*---*---*---*---*---*/
class UsernameValidateRequest(BaseModel):
    username: str

@router.post("/{lang}/validate/username", name="validate_username")
async def validate_username_endpoint(
    lang: str,
    payload: UsernameValidateRequest,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    username = payload.username.strip()
    is_valid = False
    message = ""

    if not username:
        message = '{% if lang == "ja" %}日本語も利用できます。ほかのユーザーと重複できません。{% else %}Emojis are also available. Cannot duplicate other user names.{% endif %}' # Jinja展開はサーバーではできない
        message = "日本語も利用できます。ほかのユーザーと重複できません。" if lang == "ja" else "Emojis are also available. Cannot duplicate other user names."
    elif len(username) < 2:
        message = f'<span class="text-red-500">❌ {"ユーザー名は2文字以上で入力してください" if lang == "ja" else "Username must be at least 2 characters"}</span>'
    elif len(username) > 32:
        message = f'<span class="text-red-500">❌ {"ユーザー名は32文字以下で入力してください" if lang == "ja" else "Username must be 32 characters or less"}</span>'
    elif await is_user_name_used(db, username):
        message = f'<span class="text-red-500">❌ {"このユーザー名は使用されています" if lang == "ja" else "This username is already taken"}</span>'
    else:
        message = f'<span class="text-green-500">✅ {"このユーザー名は使用できます" if lang == "ja" else "This username is available"}</span>'
        is_valid = True
    return JSONResponse({"is_valid": is_valid, "message": message})


# /---*---*---*---*---*---*---*---*/
# プレイヤータグバリデーションエンドポイント (HTMX/Fetch用)
# /---*---*---*---*---*---*---*---*/
class PlayerTagValidateRequest(BaseModel):
    player_tag: str

@router.post("/{lang}/validate/player-tag", name="validate_player_tag")
async def validate_player_tag_endpoint(
    lang: str,
    payload: PlayerTagValidateRequest,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    raw_tag = payload.player_tag.strip()
    formatted_tag_only = format_tag(raw_tag).replace("#", "") # #なしの形式に

    if not confirm_tag(f"#{formatted_tag_only}"): # confirm_tagは#付きを期待
        message = "無効なプレイヤータグ形式です。" if lang == "ja" else "Invalid player tag format."
        return JSONResponse({"is_valid": False, "message": message})

    try:
        full_tag = f"#{formatted_tag_only}"
        # [この部分は公開用リポジトリでは非公開にされています]
        name: str = await get_player_name(full_tag, db) # get_playerは#付きタグを期待
        if name: # プレイヤーが存在し、無効でない
            icon_id: int = await get_player_icon_from_db(full_tag, db) or 0
            icon_ext = "webp" if icon_id else "png"
            icon_url = f"/static/images/player_icon/{icon_id}.{icon_ext}"
            return JSONResponse({
                "is_valid": True,
                "message": f"{name} (#{formatted_tag_only})",
                "player_name": name,
                "formatted_tag": full_tag,
                "icon_id": icon_id,
                "icon_url": icon_url,
            })
        else: # is_invalid の場合など
            message = "指定されたタグのプレイヤーは存在しないか、現在利用できません。" if lang == "ja" else "Player with the specified tag does not exist or is currently unavailable."
            return JSONResponse({"is_valid": False, "message": message})

    except Exception as e: # BrawlStarsAPIError や DataBaseError を含む
        logger.debug(f"プレイヤータグ '{raw_tag}' の検証中にエラー: {e}")
        message = "指定されたタグのプレイヤーは存在しないようです。" if lang == "ja" else "Player with the specified tag does not exist or is currently unavailable."
        return JSONResponse({"is_valid": False, "message": message})


# /---*---*---*---*---*---*---*---*/
# アカウント作成処理
# /---*---*---*---*---*---*---*---*/
# [この部分は公開用リポジトリでは非公開にされています]

# /---*---*---*---*---*---*---*---*/
# パスワードリセット
# /---*---*---*---*---*---*---*---*/
# [この部分は公開用リポジトリでは非公開にされています]
