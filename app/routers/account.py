from fastapi import APIRouter, Request, Depends, HTTPException, status, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
import asyncpg
from pathlib import Path
from pydantic import BaseModel, Field, field_validator
import re
import datetime
import json

from app.core.templating import templates, censor_filter
from app.core.cache import set_cache, delete_cache, get_cache
from app.exceptions.custom_exceptions import DataBaseError, BrawlStarsAPIError, PlayerNotFoundError
from app.services.brawl_service import get_player_name, get_player, check_verify, get_hide_history_settings, get_player_from_db, get_player_summaries_from_db
from app.services.user_service import SUB_ACCOUNTS_MAX_LIMIT, SUB_ACCOUNT_EXPAND_COST, User, get_user, is_user_name_used, verify_password, get_all_secret_questions, get_gift_code, get_active_giveaway_code, get_giveaway_user_entry_count, get_giveaway_total_stats, has_user_used_gift_code, reset_user_blocks_by_blocker, get_user_block_list, get_ticket_sell_options, get_elixir_sell_options, TICKET_SELL_TOKEN_RATE, ELIXIR_SELL_DIVISOR, TUTORIAL_MISSIONS, TUTORIAL_MISSION_KEYS, TUTORIAL_MISSION_REWARD, tutorial_mission_token_total, try_claim_tutorial_mission, APP_LOGIN_MISSION_REWARD
# [この部分は公開用リポジトリでは非公開にされています]
from app.services import main_account_reward_service
from app.services import player_verification_service
from app.services.user_service import _current_token_claim_date, _normalize_daily_claim_count
from app.services.board_service import get_today_post_count_by_user
from app.services.notification_service import get_notification_settings, update_notification_setting
from app.services import push_rules, push_service
from app.services import feedback_service
# [この部分は公開用リポジトリでは非公開にされています]
from app.utils.utils import format_tag, confirm_tag, get_remote_ip, get_icon_path
from app.db.db import get_shared_db
from app.core.logger import logger

router = APIRouter(
    prefix="/{lang}/account", # このルーターのURLは /{lang}/account から始まる
    tags=["Account"] # APIドキュメント用タグ
)

# [この部分は公開用リポジトリでは非公開にされています]

# --- ログイン必須の依存関係 ---
async def get_current_active_user(request: Request) -> User:
    """
    現在のリクエストからアクティブなログインユーザーを取得する。
    未ログインの場合はHTTP 401エラーを発生させる。
    request.state.current_user は UserToStateMiddleware で設定済みと想定。
    """
    user = getattr(request.state, "current_user", None)
    if not user:
        # 適切なエラーメッセージとリダイレクト先を検討
        # ここでは汎用的な401を返す
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"}, # APIならBearerだが、Webページなら不要かも
        )
    return user


# [この部分は公開用リポジトリでは非公開にされています]

# --- アカウント設定ページ表示 ---
@router.get("", response_class=HTMLResponse, name="account_page")
async def account_page(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    tutorial_missions = []
    tutorial_all_complete = False
    tutorial_token_total = 0
    tutorial_mission_reward = 15
    current_user_for_counts: User | None = getattr(request.state, "current_user", None)
    token_ad_claimed_today = (current_user_for_counts.token_claim_count or 0) if current_user_for_counts else 0
    ticket_ad_claimed_today = (current_user_for_counts.ticket_claim_count or 0) if current_user_for_counts else 0
    # [この部分は公開用リポジトリでは非公開にされています]

    # メイン・サブアカウントの一覧 (アカウント情報の表示用。名前・アイコンは1回のクエリでまとめて取得)
    player_accounts = []
    if current_user_obj:
        player_summaries = await get_player_summaries_from_db(current_user_obj.player_tags, db)
        # 改名履歴・クラブ履歴の非公開設定 (サブアカウントがある場合のみ、まとめて1回のクエリで取得する)
        hide_settings: dict[str, tuple[bool, bool]] = {}
        if current_user_obj.sub_accounts:
            try:
                rows = await db.fetch(
                    "SELECT tag, hide_name_history, hide_club_history FROM players WHERE tag = ANY($1::text[])",
                    current_user_obj.player_tags,
                )
                hide_settings = {r["tag"]: (bool(r["hide_name_history"]), bool(r["hide_club_history"])) for r in rows}
            except asyncpg.PostgresError as e:
                logger.warning(f"登録アカウントの非公開設定の取得に失敗しました (ID: {current_user_obj.id}): {e}")
        for index, tag in enumerate(current_user_obj.player_tags):
            summary = player_summaries.get(tag, {})
            hide_name, hide_club = hide_settings.get(tag, (False, False))
            player_accounts.append({
                "tag": tag,
                "name": summary.get("name") or tag,
                "icon_id": summary.get("icon_id"),
                "is_main": index == 0,
                "hide_name_history": hide_name,
                "hide_club_history": hide_club,
            })
        if player_accounts:
            # メインアカウントは既存の取得結果(キャッシュあり)を使う
            player_accounts[0]["hide_name_history"] = bool(current_user_main_account_hide_name_history)
            player_accounts[0]["hide_club_history"] = bool(current_user_main_account_hide_club_history)

    # テンプレートに渡すコンテキスト
    context = {
        "request": request,
        "lang": lang,
        "current_page": "account", # 下部タブバーのハイライト用
        "secret_questions_list_for_edit": secret_questions_list, # テンプレートに渡す
        "secret_questions_list_json": secret_questions_list_json_str, # Alpine.jsの初期データ用
        "current_user_main_account_name": current_user_main_account_name, # メインアカウント名
        "current_user_main_account_hide_name_history": current_user_main_account_hide_name_history,
        "current_user_main_account_hide_club_history": current_user_main_account_hide_club_history,
        "basic_mission": basic_mission,
        "advance_mission": advance_mission,
        "giveaway_info": giveaway_info,
        # [この部分は公開用リポジトリでは非公開にされています]
        "is_prohibit_giveaway": bool(getattr(current_user_obj, "is_prohibit_giveaway", False)) if current_user_obj else False,
        "is_survey_cleared": is_survey_cleared,
        "is_discord_joined": is_discord_joined,
        "is_ios_app_login_cleared": bool(getattr(current_user_obj, "is_ios_app_login_cleared", False)) if current_user_obj else False,
        "is_android_app_login_cleared": bool(getattr(current_user_obj, "is_android_app_login_cleared", False)) if current_user_obj else False,
        "notification_badge_enabled": notification_badge_enabled,
        "notification_post_like_enabled": notification_post_like_enabled,
        "notification_own_post_message_enabled": notification_own_post_message_enabled,
        "notification_participated_thread_message_enabled": notification_participated_thread_message_enabled,
        "notification_message_reply_enabled": notification_message_reply_enabled,
        "notification_message_mention_enabled": notification_message_mention_enabled,
        "notification_message_reaction_enabled": notification_message_reaction_enabled,
        "notification_token_gift_enabled": notification_token_gift_enabled,
        "push_settings": push_settings,
        "admin_push_level": admin_push_level,
        "feedback_form": feedback_service.form_context(request, lang, site_version=request.app.version),
        # [この部分は公開用リポジトリでは非公開にされています]
        "ticket_sell_options": get_ticket_sell_options(
            current_user_obj.ad_skip_tickets
            if current_user_obj and not current_user_obj.is_delete_ads
            else 0
        ),
        "ticket_sell_token_rate": TICKET_SELL_TOKEN_RATE,
        "elixir_sell_options": get_elixir_sell_options(
            (current_user_obj.auto_track_elixirs or 0) if current_user_obj else 0
        ),
        "elixir_sell_divisor": ELIXIR_SELL_DIVISOR,
        "tutorial_missions": tutorial_missions,
        "tutorial_all_complete": tutorial_all_complete,
        "tutorial_token_total": tutorial_token_total,
        "tutorial_mission_reward": tutorial_mission_reward,
        "token_ad_claimed_today": token_ad_claimed_today,
        "ticket_ad_claimed_today": ticket_ad_claimed_today,
        "player_accounts": player_accounts,
        "sub_accounts_limit": (current_user_obj.sub_accounts_limit or 1) if current_user_obj else 1,
        "sub_accounts_max_limit": SUB_ACCOUNTS_MAX_LIMIT,
        "sub_account_expand_cost": SUB_ACCOUNT_EXPAND_COST,
    }
    return templates.TemplateResponse("account_settings.html", context)

@router.post("/change-language", name="account_change_language")
async def change_language_process(
    request: Request,
    lang: str,
    new_lang: str = Form(...),
    db: asyncpg.Connection = Depends(get_shared_db)
):
    if new_lang not in ["ja", "en"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid language code")

    current_user_from_state: User | None = getattr(request.state, "current_user", None)

    if current_user_from_state and current_user_from_state.lang != new_lang:
        try:
            current_user_from_state.lang = new_lang
            await current_user_from_state.update(db)
            logger.info(f"ユーザー '{current_user_from_state.name}' (ID: {current_user_from_state.id}) の言語設定を '{new_lang}' に更新しました。")
        except Exception as e:
            logger.error(f"ユーザー '{current_user_from_state.name}' の言語設定更新中にエラー: {e}")
            # エラーが発生した場合は、DB更新は失敗するが画面遷移は試みる
    
    # Refererヘッダーから元のページのパスを取得する
    referer = request.headers.get("referer")
    redirect_url_str = f"/{new_lang}/" # デフォルトのフォールバック先

    if referer:
        # RefererのURLからパス部分を取得
        from urllib.parse import urlparse
        referer_path = urlparse(referer).path
        path_parts = list(Path(referer_path).parts)

        # パスが /ja/... や /en/... の形式かチェック
        if len(path_parts) > 1 and path_parts[1] in ["ja", "en"]:
            path_parts[1] = new_lang # 言語部分を置き換える
            redirect_url_str = str(Path(*path_parts))
        else:
            # 言語コードで始まらないパスの場合は、先頭に新しい言語コードを付与
            redirect_url_str = f"/{new_lang}{referer_path}".replace("//", "/") # /が二重になるのを防ぐ

    # クエリパラメータを維持する
    if request.url.query:
        redirect_url_str += f"?{request.url.query}"

    # JSONResponseの代わりにRedirectResponseを返す
    # POSTリクエスト後のリダイレクトには 303 See Other を使うのが一般的
    return RedirectResponse(url=redirect_url_str, status_code=status.HTTP_303_SEE_OTHER)

# ユーザー名変更 ---
class UsernameChangeRequest(BaseModel):
    new_username: str = Field(min_length=2, max_length=32)

@router.post("/change-username", name="account_change_username")
async def change_username_process(
    request: Request,
    lang: str,
    payload: UsernameChangeRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user) # ログイン必須
):
    new_username = payload.new_username.strip()

    if new_username == current_user.name:
        return JSONResponse({
            "success": True, # 実質変更なしなので成功扱い
            "message": "現在のユーザー名と同じです。" if lang == "ja" else "Same as current username."
        })

    if await is_user_name_used(db, new_username):
        return JSONResponse({
            "success": False,
            "message": "このユーザー名は既に使用されています。" if lang == "ja" else "This username is already taken."
        }, status_code=status.HTTP_400_BAD_REQUEST)

    try:
        old_name = current_user.name
        current_user.name = new_username
        await current_user.update(db)
        # セッションのusernameも更新
        request.session["username"] = new_username
        logger.info(f"ユーザー '{old_name}' (ID: {current_user.id}) がユーザー名を '{new_username}' に変更しました。")
        return JSONResponse({
            "success": True,
            "message": "ユーザー名を更新しました。" if lang == "ja" else "Username updated successfully.",
            "new_username": new_username # フロントエンドで表示を更新するため
        })
    except DataBaseError as e:
        logger.error(f"ユーザー名変更中 (ID: {current_user.id}) にDBエラー: {e}")
        return JSONResponse({"success": False, "message": "データベースエラーが発生しました。" if lang == "ja" else "A database error occurred."}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    except Exception as e:
        logger.error(f"ユーザー名変更中 (ID: {current_user.id}) に予期せぬエラー: {e}")
        return JSONResponse({"success": False, "message": "予期せぬエラーが発生しました。" if lang == "ja" else "An unexpected error occurred."}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

# --- 現在のパスワード検証 ---
class VerifyCurrentPasswordRequest(BaseModel):
    current_password: str

@router.post("/verify-current-password", name="account_verify_current_password")
async def verify_current_password_process(
    request: Request,
    lang: str,
    payload: VerifyCurrentPasswordRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user)
):
    if await verify_password(db, current_user.id, payload.current_password):
        return JSONResponse({"verified": True, "message": "現在のパスワードが確認されました。" if lang == "ja" else "Current password verified."})
    else:
        return JSONResponse({
            "verified": False,
            "message": "現在のパスワードが正しくありません。" if lang == "ja" else "Incorrect current password."
        }, status_code=status.HTTP_400_BAD_REQUEST) # 400 Bad Request or 401 Unauthorized


# --- パスワード変更 ---
class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=32, pattern=r"^[a-zA-Z0-9]+$")
    # new_password_confirm はPydanticモデル内ではなく、エンドポイントで直接比較する

@router.post("/change-password", name="account_change_password")
async def change_password_process(
    request: Request,
    lang: str,
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password_confirm: str = Form(...), # フォームから直接受け取る
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user)
):
    # 1. 現在のパスワードを確認
    if not await verify_password(db, current_user.id, current_password):
        return JSONResponse({
            "success": False,
            "message": "現在のパスワードが正しくありません。" if lang == "ja" else "Incorrect current password.",
            "reverify_current": True # フロントエンドに現在のパスワードの再入力を促す
        }, status_code=status.HTTP_400_BAD_REQUEST)

    # 2. 新しいパスワードのバリデーション (Pydanticモデルでも行えるが、ここでは明示的に)
    if new_password != new_password_confirm:
        return JSONResponse({"success": False, "message": "新しいパスワードが一致しません。" if lang == "ja" else "New passwords do not match."}, status_code=status.HTTP_400_BAD_REQUEST)
    
    password_pattern = r"^[a-zA-Z0-9]+$"
    if not (8 <= len(new_password) <= 32 and re.match(password_pattern, new_password)):
        return JSONResponse({"success": False, "message": "新しいパスワードは8～32文字の英数字である必要があります。" if lang == "ja" else "New password must be 8-32 alphanumeric characters."}, status_code=status.HTTP_400_BAD_REQUEST)

    # 3. パスワードを更新
    try:
        await current_user.update_password(db, new_password)
        logger.info(f"ユーザー '{current_user.name}' (ID: {current_user.id}) がパスワードを変更しました。")
        # パスワード変更後は現在のセッションを無効化し、再ログインを促すのが一般的
        request.session.clear() # セッションをクリア
        return JSONResponse({
            "success": True,
            "message": "パスワードを変更しました。セキュリティのため、再ログインしてください。" if lang == "ja" else "Password changed successfully. Please log in again for security."
        })
    except DataBaseError as e:
        logger.error(f"パスワード変更中 (ID: {current_user.id}) にDBエラー: {e}")
        return JSONResponse({"success": False, "message": "データベースエラーが発生しました。" if lang == "ja" else "A database error occurred."}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    except Exception as e:
        logger.error(f"パスワード変更中 (ID: {current_user.id}) に予期せぬエラー: {e}")
        return JSONResponse({"success": False, "message": "予期せぬエラーが発生しました。" if lang == "ja" else "An unexpected error occurred."}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

# --- 秘密の質問変更リクエストモデル ---
class SecretQuestionsChangeRequest(BaseModel):
    current_password: str
    new_questions: dict[int, str] # {question_id: answer_text} の形式を期待

    @field_validator('new_questions')
    @classmethod
    def questions_must_be_two_and_different(cls, v: dict[int, str]):
        if len(v) != 2:
            raise ValueError('Two security questions must be provided.')
        question_ids = list(v.keys())
        if question_ids[0] == question_ids[1]:
            raise ValueError('Security questions must be different.')
        if not all(answer.strip() for answer in v.values()): # 回答が空でないか
            raise ValueError('Answers cannot be empty.')
        return v

@router.post("/change-secret-questions", name="account_change_secret_questions") # 既存のエンドポイントを修正
async def change_secret_questions_process( # 関数名を変更
    request: Request,
    lang: str,
    payload: SecretQuestionsChangeRequest, # リクエストボディを受け取る
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user) # ログイン必須
):
    # 1. 現在のパスワードを検証
    if not await verify_password(db, current_user.id, payload.current_password):
        return JSONResponse({
            "success": False,
            "message": "現在のパスワードが正しくありません。" if lang == "ja" else "Incorrect current password.",
            "reverify_current": True # フロントエンドに現在のパスワードの再入力を促す
        }, status_code=status.HTTP_400_BAD_REQUEST)

    # 2. 新しい秘密の質問と回答をDBに保存
    try:
        # Userオブジェクトの secret_questions 属性を更新し、User.update() を呼び出す
        # User.secret_questions は { "質問ID文字列": "回答文字列", ... } の形式なので合わせる
        new_secret_questions_for_db = {str(qid): ans for qid, ans in payload.new_questions.items()}
        current_user.secret_questions = new_secret_questions_for_db
        await current_user.update(db)
        logger.info(f"ユーザー '{current_user.name}' (ID: {current_user.id}) が秘密の質問を変更しました。")
        return JSONResponse({
            "success": True,
            "message": "秘密の質問を更新しました。" if lang == "ja" else "Security questions updated successfully."
        })
    except DataBaseError as e:
        logger.error(f"秘密の質問変更中 (ID: {current_user.id}) にDBエラー: {e}")
        return JSONResponse({"success": False, "message": "データベースエラーが発生しました。" if lang == "ja" else "A database error occurred."}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    except Exception as e:
        logger.error(f"秘密の質問変更中 (ID: {current_user.id}) に予期せぬエラー: {e}")
        return JSONResponse({"success": False, "message": "予期せぬエラーが発生しました。" if lang == "ja" else "An unexpected error occurred."}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

# --- メイン・サブアカウント(プレイヤーアカウント)操作 ---
class PlayerAccountRegisterRequest(BaseModel):
    player_tag: str = Field(max_length=20)
    as_main: bool = False # メインアカウントとして登録するか (これまでのメインはサブに移る)

class PlayerAccountRemoveRequest(BaseModel):
    player_tag: str = Field(max_length=20)

class PlayerAccountReorderRequest(BaseModel):
    ordered_tags: list[str] = Field(min_length=1, max_length=SUB_ACCOUNTS_MAX_LIMIT + 1)


def _player_account_error(message_ja: str, message_en: str, lang: str, status_code: int = status.HTTP_400_BAD_REQUEST, **extra) -> JSONResponse:
    return JSONResponse(
        {"success": False, "message": message_ja if lang == "ja" else message_en, **extra},
        status_code=status_code,
    )


@router.post("/player-accounts/register", name="account_register_player_account")
async def register_player_account_process(
    request: Request,
    lang: str,
    payload: PlayerAccountRegisterRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user)
):
    tag = format_tag(payload.player_tag.strip())
    if not confirm_tag(tag):
        return _player_account_error("プレイヤータグの形式が正しくありません。", "Invalid player tag format.", lang)
    if current_user.is_own_player_tag(tag):
        return _player_account_error("このプレイヤーは既に登録済みです。", "This player is already registered.", lang)

    # [この部分は公開用リポジトリでは非公開にされています]

    try:
        player_name = await get_player_name(tag, db)
        if not player_name:
            return _player_account_error("指定されたタグのプレイヤーが見つからないか、無効です。", "Player with the specified tag not found or is invalid.", lang)
    except Exception as e:
        logger.error(f"アカウント登録時のプレイヤー名取得中にエラー (タグ: {tag}): {e}")
        return _player_account_error("プレイヤー情報の取得中にエラーが発生しました。", "An error occurred while fetching player information.", lang, status.HTTP_500_INTERNAL_SERVER_ERROR)

    if not await player_verification_service.is_verified(request, tag):
        return _player_account_error(
            "所有者確認が完了していません。プレイヤータグ入力後、表示される指示に従って認証を完了してください。",
            "Ownership verification is not complete. Please follow the instructions displayed after entering the player tag to complete verification.",
            lang,
        )

    try:
        result = await current_user.add_player_account(db, tag, as_main=payload.as_main)
    except DataBaseError as e:
        logger.error(f"アカウント登録中 (ID: {current_user.id}) にDBエラー: {e}")
        return _player_account_error("データベースエラーが発生しました。", "A database error occurred.", lang, status.HTTP_500_INTERNAL_SERVER_ERROR)

    if result == "already_registered":
        return _player_account_error("このプレイヤーは既に登録済みです。", "This player is already registered.", lang)
    if result == "limit_reached":
        return _player_account_error(
            "登録枠に空きがありません。登録済みのアカウントを解除するか、登録枠を拡張してください。",
            "No registration slots are available. Remove a registered account or expand your slots.",
            lang,
        )
    if result not in ("added_sub", "set_main"):
        return _player_account_error("登録できませんでした。", "Could not register the player.", lang, status.HTTP_500_INTERNAL_SERVER_ERROR)

    # 同じ認証を使い回せないようにする
    await player_verification_service.consume_verification(request, tag)
    if result == "set_main":
        logger.info(f"ユーザー '{current_user.name}' (ID: {current_user.id}) がメインアカウントを '{tag}' ({player_name}) に変更しました。")
        message = f"「{player_name}」をメインアカウントに登録しました。" if lang == "ja" else f'Registered "{player_name}" as your main account.'
    else:
        logger.info(f"ユーザー '{current_user.name}' (ID: {current_user.id}) がサブアカウント '{tag}' ({player_name}) を登録しました。")
        message = f"「{player_name}」をサブアカウントに登録しました。" if lang == "ja" else f'Registered "{player_name}" as a sub account.'
    return JSONResponse({"success": True, "message": message})


@router.post("/player-accounts/remove", name="account_remove_sub_account")
async def remove_sub_account_process(
    request: Request,
    lang: str,
    payload: PlayerAccountRemoveRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user)
):
    tag = format_tag(payload.player_tag.strip())
    if tag == current_user.main_account:
        return _player_account_error(
            "メインアカウントは登録解除できません。先に別のアカウントをメインにしてください。",
            "The main account cannot be unregistered. Make another account your main account first.",
            lang,
        )
    try:
        removed = await current_user.remove_sub_account(db, tag)
    except DataBaseError as e:
        logger.error(f"サブアカウント解除中 (ID: {current_user.id}) にDBエラー: {e}")
        return _player_account_error("データベースエラーが発生しました。", "A database error occurred.", lang, status.HTTP_500_INTERNAL_SERVER_ERROR)
    if not removed:
        return _player_account_error("このプレイヤーはサブアカウントに登録されていません。", "This player is not registered as a sub account.", lang)

    logger.info(f"ユーザー '{current_user.name}' (ID: {current_user.id}) がサブアカウント '{tag}' を登録解除しました。")
    return JSONResponse({
        "success": True,
        "message": "サブアカウントの登録を解除しました。" if lang == "ja" else "Sub account unregistered.",
    })


@router.post("/player-accounts/reorder", name="account_reorder_player_accounts")
async def reorder_player_accounts_process(
    request: Request,
    lang: str,
    payload: PlayerAccountReorderRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user)
):
    ordered_tags = [format_tag(tag.strip()) for tag in payload.ordered_tags]
    previous_main = current_user.main_account
    try:
        result = await current_user.reorder_player_accounts(db, ordered_tags)
    except DataBaseError as e:
        logger.error(f"アカウント並び替え中 (ID: {current_user.id}) にDBエラー: {e}")
        return _player_account_error("データベースエラーが発生しました。", "A database error occurred.", lang, status.HTTP_500_INTERNAL_SERVER_ERROR)

    if result == "mismatch":
        return _player_account_error(
            "登録中のアカウントが変わっています。ページを再読み込みしてからもう一度お試しください。",
            "Your registered accounts have changed. Please reload the page and try again.",
            lang, status.HTTP_409_CONFLICT,
        )
    if result == "updated" and current_user.main_account != previous_main:
        logger.info(f"ユーザー '{current_user.name}' (ID: {current_user.id}) がメインアカウントを '{previous_main}' から '{current_user.main_account}' に入れ替えました。")
    return JSONResponse({
        "success": True,
        "message": "並び順を保存しました。" if lang == "ja" else "Order saved.",
        "main_account": current_user.main_account,
    })

# [この部分は公開用リポジトリでは非公開にされています]

# --- ブックマーク操作リクエストモデル ---
class BookmarkRequest(BaseModel):
    player_tag: str
    overwrite: bool | None = False # 追加時に上書きを許可するか

# --- ブックマーク追加エンドポイント ---
@router.post("/bookmark/add", name="account_add_bookmark")
async def add_bookmark_endpoint(
    request: Request,
    payload: BookmarkRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user) # ログイン必須
):
    player_tag_to_add = format_tag(payload.player_tag) # タグを整形
    if not confirm_tag(player_tag_to_add):
        return JSONResponse(
            {"success": False, "message": "無効なプレイヤータグ形式です。" if current_user.lang == "ja" else "Invalid player tag format.", "action": "validation_error"},
            status_code=status.HTTP_400_BAD_REQUEST
        )

    success, message_key, action = await current_user.add_bookmark(player_tag_to_add, db, payload.overwrite)
    
    # メッセージの多言語対応 (簡易版)
    message_dict_ja = {
        "added": "ブックマークに追加しました。",
        "overwritten": f"ブックマークを上書きしました。", # 上書きされたタグの情報は別途取得・表示が必要
        "limit_reached": "ブックマークの上限に達しています。",
        "already_exists": "既にブックマーク済みです。",
        "is_main": "メインアカウントはブックマークできません。",
        "is_sub": "サブアカウントはブックマークできません。",
        "error": "処理中にエラーが発生しました。"
    }
    message_dict_en = {
        "added": "Added to bookmarks.",
        "overwritten": f"Overwrote bookmark.",
        "limit_reached": "Bookmark limit reached.",
        "already_exists": "Already bookmarked.",
        "is_main": "Main account cannot be bookmarked.",
        "is_sub": "Sub accounts cannot be bookmarked.",
        "error": "An error occurred."
    }
    
    message = ""
    if current_user.lang == "ja":
        message = message_dict_ja.get(action, message_dict_ja["error"])
        if action == "overwritten": # 上書き時のメッセージ調整
            # 実際には削除されたタグ名を service 層で返すか、ここで取得する必要がある
            message = f"ブックマークを上書きしました。" # 簡略化
    else:
        message = message_dict_en.get(action, message_dict_en["error"])
        if action == "overwritten":
            message = f"Overwrote bookmark."


    status_code = status.HTTP_200_OK if success else status.HTTP_400_BAD_REQUEST
    if action == "error":
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR

    return JSONResponse({"success": success, "message": message, "action": action}, status_code=status_code)

# --- ブックマーク削除エンドポイント ---
@router.post("/bookmark/remove", name="account_remove_bookmark")
async def remove_bookmark_endpoint(
    request: Request,
    payload: BookmarkRequest, # player_tag のみ使用
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user) # ログイン必須
):
    player_tag_to_remove = format_tag(payload.player_tag) # タグを整形
    if not confirm_tag(player_tag_to_remove):
        return JSONResponse(
            {"success": False, "message": "無効なプレイヤータグ形式です。" if current_user.lang == "ja" else "Invalid player tag format.", "action": "validation_error"},
            status_code=status.HTTP_400_BAD_REQUEST
        )

    success, message_key, action = await current_user.remove_bookmark(player_tag_to_remove, db)

    message_dict_ja = {
        "removed": "ブックマークから削除しました。",
        "not_found": "指定されたタグはブックマークされていません。",
        "error": "処理中にエラーが発生しました。"
    }
    message_dict_en = {
        "removed": "Removed from bookmarks.",
        "not_found": "The specified tag is not bookmarked.",
        "error": "An error occurred."
    }
    message = message_dict_ja.get(action, message_dict_ja["error"]) if current_user.lang == "ja" else message_dict_en.get(action, message_dict_en["error"])

    status_code = status.HTTP_200_OK if success else status.HTTP_400_BAD_REQUEST
    if action == "error":
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        
    return JSONResponse({"success": success, "message": message, "action": action}, status_code=status_code)

# --- ブックマーク編集リクエストモデル ---
class BookmarkUpdateRequest(BaseModel):
    updated_bookmarks: list[str] = Field(default_factory=list)

# --- ブックマーク編集エンドポイント ---
@router.post("/bookmark/update", name="account_update_bookmarks")
async def update_bookmarks_endpoint(
    request: Request,
    payload: BookmarkUpdateRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user) # ログイン必須
):
    if not current_user:
        # このケースは get_current_active_user で処理されるはずだが念のため
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    success = await current_user.update_saved_accounts(payload.updated_bookmarks, db)

    if success:
        return JSONResponse({
            "success": True,
            "message": "ブックマークを更新しました。" if current_user.lang == "ja" else "Bookmarks updated successfully."
        })
    else:
        return JSONResponse({
            "success": False,
            "message": "ブックマークの更新に失敗しました。" if current_user.lang == "ja" else "Failed to update bookmarks."
        }, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

# --- 履歴操作リクエストモデル ---
class HistoryUpdateRequest(BaseModel):
    updated_history: list[str] = Field(default_factory=list)

# --- 閲覧履歴編集エンドポイント ---
@router.post("/history/update", name="account_update_history")
async def update_history_endpoint(
    request: Request,
    payload: HistoryUpdateRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user)
):
    if not current_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    success = await current_user.update_viewed_accounts(payload.updated_history, db)

    if success:
        return JSONResponse({
            "success": True,
            "message": "閲覧履歴を更新しました。" if current_user.lang == "ja" else "Viewing history updated successfully."
        })
    else:
        return JSONResponse({
            "success": False,
            "message": "閲覧履歴の更新に失敗しました。" if current_user.lang == "ja" else "Failed to update viewing history."
        }, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

# --- クラブブックマーク操作リクエストモデル ---
class ClubBookmarkRequest(BaseModel):
    club_tag: str
    overwrite: bool | None = False


@router.post("/club-bookmark/add", name="account_add_club_bookmark")
async def add_club_bookmark_endpoint(
    request: Request,
    payload: ClubBookmarkRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user)
):
    club_tag_to_add = format_tag(payload.club_tag)
    if not confirm_tag(club_tag_to_add):
        return JSONResponse(
            {"success": False, "message": "無効なクラブタグ形式です。" if current_user.lang == "ja" else "Invalid club tag format.", "action": "validation_error"},
            status_code=status.HTTP_400_BAD_REQUEST
        )

    success, message_key, action = await current_user.add_club_bookmark(club_tag_to_add, db, payload.overwrite)

    message_dict_ja = {
        "added": "ブックマークに追加しました。",
        "overwritten": "ブックマークを上書きしました。",
        "limit_reached": "ブックマークの上限に達しています。",
        "already_exists": "既にブックマーク済みです。",
        "error": "処理中にエラーが発生しました。"
    }
    message_dict_en = {
        "added": "Added to bookmarks.",
        "overwritten": "Overwrote bookmark.",
        "limit_reached": "Bookmark limit reached.",
        "already_exists": "Already bookmarked.",
        "error": "An error occurred."
    }

    message = message_dict_ja.get(action, message_dict_ja["error"]) if current_user.lang == "ja" else message_dict_en.get(action, message_dict_en["error"])

    status_code = status.HTTP_200_OK if success else status.HTTP_400_BAD_REQUEST
    if action == "error":
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR

    return JSONResponse({"success": success, "message": message, "action": action}, status_code=status_code)


@router.post("/club-bookmark/remove", name="account_remove_club_bookmark")
async def remove_club_bookmark_endpoint(
    request: Request,
    payload: ClubBookmarkRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user)
):
    club_tag_to_remove = format_tag(payload.club_tag)
    if not confirm_tag(club_tag_to_remove):
        return JSONResponse(
            {"success": False, "message": "無効なクラブタグ形式です。" if current_user.lang == "ja" else "Invalid club tag format.", "action": "validation_error"},
            status_code=status.HTTP_400_BAD_REQUEST
        )

    success, message_key, action = await current_user.remove_club_bookmark(club_tag_to_remove, db)

    message_dict_ja = {
        "removed": "ブックマークから削除しました。",
        "not_found": "指定されたタグはブックマークされていません。",
        "error": "処理中にエラーが発生しました。"
    }
    message_dict_en = {
        "removed": "Removed from bookmarks.",
        "not_found": "The specified tag is not bookmarked.",
        "error": "An error occurred."
    }
    message = message_dict_ja.get(action, message_dict_ja["error"]) if current_user.lang == "ja" else message_dict_en.get(action, message_dict_en["error"])

    status_code = status.HTTP_200_OK if success else status.HTTP_400_BAD_REQUEST
    if action == "error":
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR

    return JSONResponse({"success": success, "message": message, "action": action}, status_code=status_code)


class ClubBookmarkUpdateRequest(BaseModel):
    updated_bookmarks: list[str] = Field(default_factory=list)


@router.post("/club-bookmark/update", name="account_update_club_bookmarks")
async def update_club_bookmarks_endpoint(
    request: Request,
    payload: ClubBookmarkUpdateRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user)
):
    success = await current_user.update_saved_clubs(payload.updated_bookmarks, db)

    if success:
        return JSONResponse({
            "success": True,
            "message": "クラブブックマークを更新しました。" if current_user.lang == "ja" else "Club bookmarks updated successfully."
        })
    else:
        return JSONResponse({
            "success": False,
            "message": "クラブブックマークの更新に失敗しました。" if current_user.lang == "ja" else "Failed to update club bookmarks."
        }, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ClubHistoryUpdateRequest(BaseModel):
    updated_history: list[str] = Field(default_factory=list)


@router.post("/club-history/update", name="account_update_club_history")
async def update_club_history_endpoint(
    request: Request,
    payload: ClubHistoryUpdateRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user)
):
    success = await current_user.update_viewed_clubs(payload.updated_history, db)

    if success:
        return JSONResponse({
            "success": True,
            "message": "クラブ閲覧履歴を更新しました。" if current_user.lang == "ja" else "Club viewing history updated successfully."
        })
    else:
        return JSONResponse({
            "success": False,
            "message": "クラブ閲覧履歴の更新に失敗しました。" if current_user.lang == "ja" else "Failed to update club viewing history."
        }, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

        
# リクエストボディのモデル
class PlayerTagPayload(BaseModel):
    player_tag: str

@router.post("/request-verification", name="request_player_verification")
async def request_player_verification_endpoint(
    request: Request,
    lang: str,
    payload: PlayerTagPayload,
    db: asyncpg.Connection = Depends(get_shared_db),
    # current_user: User = Depends(get_current_active_user) # ログインユーザーのみ操作可能にする場合
):
    raw_tag = payload.player_tag
    formatted_tag = format_tag(raw_tag)

    if not confirm_tag(formatted_tag):
        return JSONResponse(
            {"success": False, "message": "無効なプレイヤータグ形式です。" if lang == "ja" else "Invalid player tag format."},
            status_code=status.HTTP_400_BAD_REQUEST
        )

    try:
        # Playerインスタンスを取得 (brawl_service.get_player を使用)
        player = await get_player_from_db(formatted_tag, db) # DBからの取得で高速化
        if not player:
            player = await get_player(formatted_tag, db) # DBからの取得ではDBにないプレイヤーはNoneになるため、その場合は普通にget_playerする
        if not player or player.is_invalid:
            return JSONResponse(
                {"success": False, "message": "指定されたタグのプレイヤーが見つからないか、無効です。" if lang == "ja" else "Player not found or invalid."},
                status_code=status.HTTP_404_NOT_FOUND
            )

        # Playerクラスのrequest_verifyメソッドを呼び出し
        instructed_icon_id = await player.request_verify(player_verification_service.get_verification_scope(request))

        # 正常終了のレスポンス
        return JSONResponse({
            "success": True,
            "message": "プレイヤーアイコンの変更指示を行いました。" if lang == "ja" else "Player icon change instruction issued.",
            "instructed_icon_id": instructed_icon_id,
            "player_tag": formatted_tag # 確認用
        })
    except PlayerNotFoundError as e:
        # 存在しないタグの入力ミスのため、APIエラーとは区別してinfoレベルで記録する
        logger.info(f"認証リクエストで存在しないプレイヤータグが指定されました (タグ: {formatted_tag}): {e}")
        return JSONResponse(
            {"success": False, "message": "指定されたタグのプレイヤーが見つからないか、無効です。" if lang == "ja" else "Player not found or invalid."},
            status_code=status.HTTP_404_NOT_FOUND
        )
    except BrawlStarsAPIError as e:
        logger.error(f"認証リクエスト中にAPIエラー (タグ: {formatted_tag}): {e}")
        return JSONResponse(
            {"success": False, "message": "APIエラーにより認証を開始できませんでした。" if lang == "ja" else "Could not start verification due to API error."},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR # API側の問題なので500系
        )
    except Exception as e:
        logger.error(f"認証リクエスト中に予期せぬエラー (タグ: {formatted_tag}): {e}", exc_info=True)
        return JSONResponse(
            {"success": False, "message": "予期せぬエラーが発生しました。" if lang == "ja" else "An unexpected error occurred."},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        
@router.post("/check-verification", name="check_player_verification")
async def check_player_verification_endpoint(
    request: Request,
    lang: str,
    payload: PlayerTagPayload, # PlayerTagPayload を再利用
    db: asyncpg.Connection = Depends(get_shared_db),
    # current_user: User = Depends(get_current_active_user) # ログインユーザーのみ操作可能にする場合
):
    formatted_tag = format_tag(payload.player_tag)

    if not confirm_tag(formatted_tag):
        return JSONResponse(
            {"success": False, "verified": False, "message": "無効なプレイヤータグ形式です。" if lang == "ja" else "Invalid player tag format."},
            status_code=status.HTTP_400_BAD_REQUEST
        )

    try:
        is_verified, error_reason_key = await check_verify(
            formatted_tag, player_verification_service.get_verification_scope(request)
        )

        if is_verified:
            # 認証成功時の処理 (このセッションに15分間有効な認証済みフラグを記録)
            await player_verification_service.mark_verified(request, formatted_tag)
            
            message = "認証に成功しました。" if lang == "ja" else "Verification successful."
            return JSONResponse({"success": True, "verified": True, "message": message})
        else:
            # 認証失敗時のメッセージ分岐
            if error_reason_key == "icon_mismatch":
                message = "指定されたアイコンへの変更が確認できませんでした。ゲーム内でアイコンを変更してから反映に最大3-5分程度かかることがあります。変更しても反映されない場合は、お手数ですが少し待って再度ボタンを押してください。" if lang == "ja" else "The icon is different from the one instructed. Please check if it has been changed in the game and try again after a few minutes (it may take up to 3-5 minutes for API changes to reflect)."
            elif error_reason_key == "no_cached_id":
                message = "認証セッションの有効期限が切れました。お手数ですが、最初からやり直してください。" if lang == "ja" else "Verification session expired. Please start over."
            elif error_reason_key == "api_error":
                message = "APIエラーにより確認できませんでした。しばらくしてから再度お試しください。ブロスタがメンテナンス中の場合は、ブロスタのメンテナンスが終了してからお試しください。" if lang == "ja" else "Could not verify due to an API error. Please try again later."
            else: # unknown_error やその他の予期せぬケース
                message = "認証に失敗しました。再度お試しください。" if lang == "ja" else "Verification failed. Please try again."
            
            return JSONResponse({"success": True, "verified": False, "message": message})

    except Exception as e: # check_verify自体が予期せぬエラーを投げる可能性も考慮
        logger.error(f"認証確認処理中に予期せぬエラー (タグ: {formatted_tag}): {e}", exc_info=True)
        return JSONResponse(
            {"success": False, "verified": False, "message": "予期せぬエラーが発生しました。" if lang == "ja" else "An unexpected error occurred."},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

class GiftCodeRequest(BaseModel):
    gift_code: str


class GiveawayBulkApplyError(Exception):
    def __init__(self, msg_ja: str, msg_en: str):
        self.msg_ja = msg_ja
        self.msg_en = msg_en
        super().__init__(msg_ja)


class GiveawayBulkEntryRequest(BaseModel):
    gift_code: str
    entry_count: int = Field(ge=2, le=200)

@router.post("/use-gift-code", name="account_use_gift_code")
async def use_gift_code_process(
    request: Request,
    payload: GiftCodeRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user) # ログイン必須
):
    code_to_use = payload.gift_code.strip().upper() # 大文字に統一して比較
    lang = request.path_params.get("lang", "ja")

    try:
        gift_code_obj = await get_gift_code(db, code_to_use)

        # ギフトコードを使用
        success, msg_ja, msg_en = await gift_code_obj.use(db=db, user_id=current_user.id)

        if success:
            # giveawayコードの場合、ユーザーの応募回数キャッシュをクリア
            if gift_code_obj.reward.get("giveaway"):
                await delete_cache(f"giveaway:entries:{code_to_use}:{current_user.id}")
                # [この部分は公開用リポジトリでは非公開にされています]
            else:
                # [この部分は公開用リポジトリでは非公開にされています]
            message = msg_ja if lang == "ja" and msg_ja else msg_en
            return JSONResponse({"success": True, "message": message})
        else:
            # useメソッド内で使用失敗の理由が返される
            message = msg_ja if lang == "ja" and msg_ja else msg_en
            return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_400_BAD_REQUEST)

    except ValueError:
        # get_gift_codeでコードが見つからなかった場合
        message = "このギフトコードは存在しません。" if lang == "ja" else "This gift code does not exist."
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_404_NOT_FOUND)
    except DataBaseError as e:
        logger.error(f"ギフトコード適用中にデータベースエラー (User ID: {current_user.id}, Code: {code_to_use}): {e}")
        message = "データベースエラーが発生しました。" if lang == "ja" else "A database error occurred."
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    except Exception as e:
        logger.error(f"ギフトコード適用中に予期せぬエラー (User ID: {current_user.id}, Code: {code_to_use}): {e}", exc_info=True)
        message = "予期せぬエラーが発生しました。" if lang == "ja" else "An unexpected error occurred."
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post("/use-giveaway-max-entries", name="account_use_giveaway_max_entries")
async def use_giveaway_max_entries_process(
    request: Request,
    payload: GiveawayBulkEntryRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user)
):
    code_to_use = payload.gift_code.strip().upper()
    lang = request.path_params.get("lang", "ja")

    try:
        async with db.transaction():
            # 同時実行時の競合を避けるために対象行をロックする
            await db.fetchrow("SELECT id FROM users WHERE id = $1 FOR UPDATE", current_user.id)
            await db.fetchrow("SELECT code FROM gift_codes WHERE UPPER(code) = UPPER($1) FOR UPDATE", code_to_use)

            gift_code_obj = await get_gift_code(db, code_to_use)

            if not gift_code_obj.reward.get("giveaway"):
                raise GiveawayBulkApplyError(
                    "このコードはプレゼント企画応募コードではありません。",
                    "This code is not a giveaway entry code.",
                )

            spend_tokens_raw = gift_code_obj.reward.get("spend_tokens", 0)
            try:
                spend_tokens = int(spend_tokens_raw)
            except (TypeError, ValueError) as e:
                logger.error(f"プレゼント企画コードの spend_tokens が不正です (Code: {code_to_use}, Value: {spend_tokens_raw}): {e}")
                raise GiveawayBulkApplyError(
                    "このコードの設定が不正なため応募できません。",
                    "This code cannot be used due to invalid configuration.",
                )

            if spend_tokens <= 0:
                raise GiveawayBulkApplyError(
                    "このコードの設定が不正なため応募できません。",
                    "This code cannot be used due to invalid configuration.",
                )

            user_tokens_before = await db.fetchval("SELECT tokens FROM users WHERE id = $1", current_user.id)
            current_entry_count = gift_code_obj.usage_log.count(current_user.id)

            remaining_entries = payload.entry_count
            if gift_code_obj.usage_limit_per_user:
                remaining_entries = max(gift_code_obj.usage_limit_per_user - current_entry_count, 0)

            max_by_tokens = user_tokens_before // spend_tokens
            max_entries_now = min(remaining_entries, max_by_tokens)

            if payload.entry_count > max_entries_now:
                raise GiveawayBulkApplyError(
                    "トークン不足または応募可能回数不足のため、まとめて応募を完了できませんでした。",
                    "Could not complete bulk entry due to insufficient tokens or remaining entry capacity.",
                )

            start_entry = current_entry_count + 1

            for _ in range(payload.entry_count):
                success, msg_ja, msg_en = await gift_code_obj.use(db=db, user_id=current_user.id)
                if not success:
                    raise GiveawayBulkApplyError(
                        msg_ja or "応募処理に失敗しました。",
                        msg_en or "Failed to submit giveaway entry.",
                    )

            user_tokens_after = await db.fetchval("SELECT tokens FROM users WHERE id = $1", current_user.id)
            end_entry = start_entry + payload.entry_count - 1
            consumed_tokens = user_tokens_before - user_tokens_after

        await delete_cache(f"giveaway:entries:{code_to_use}:{current_user.id}")
        # [この部分は公開用リポジトリでは非公開にされています]

        if lang == "ja":
            message = (
                f"プレゼント企画への<b>{start_entry}〜{end_entry}口目</b>の応募が完了しました。"
                f"最大で{gift_code_obj.usage_limit_per_user}口まで応募できます！"
                f"<br>トークンを{consumed_tokens}個消費しました ({user_tokens_before} → {user_tokens_after})"
            )
        else:
            message = (
                f"Your <b>entries #{start_entry} to #{end_entry}</b> for the giveaway have been submitted. "
                f"You can submit up to {gift_code_obj.usage_limit_per_user} entries!"
                f"<br>Consumed {consumed_tokens} Token(s) ({user_tokens_before} → {user_tokens_after})"
            )

        return JSONResponse({
            "success": True,
            "message": message,
            "applied_entries": payload.entry_count,
        })

    except GiveawayBulkApplyError as e:
        message = e.msg_ja if lang == "ja" else e.msg_en
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_400_BAD_REQUEST)
    except ValueError:
        message = "このギフトコードは存在しません。" if lang == "ja" else "This gift code does not exist."
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_404_NOT_FOUND)
    except DataBaseError as e:
        logger.error(f"プレゼント企画の複数口応募中にデータベースエラー (User ID: {current_user.id}, Code: {code_to_use}): {e}")
        message = "データベースエラーが発生しました。" if lang == "ja" else "A database error occurred."
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    except Exception as e:
        logger.error(f"プレゼント企画の複数口応募中に予期せぬエラー (User ID: {current_user.id}, Code: {code_to_use}): {e}", exc_info=True)
        message = "予期せぬエラーが発生しました。" if lang == "ja" else "An unexpected error occurred."
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
class FeedbackRequest(BaseModel):
    type: str
    comment: str = Field("", max_length=feedback_service.COMMENT_MAX_CHARS * 2)
    media_ids: list[int] = Field(default_factory=list, max_length=10)

@router.post("/send-feedback", name="account_send_feedback")
async def send_feedback_process(
    request: Request,
    payload: FeedbackRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user)
):
    lang = request.path_params.get("lang", "ja")

    try:
        env = feedback_service.client_env(request, lang, site_version=request.app.version)
        await feedback_service.create_feedback(
            db,
            user_id=current_user.id,
            feedback_type=payload.type,
            comment=payload.comment,
            env=env,
            media_ids=payload.media_ids,
        )
        message = "送信されました。ありがとうございます！" if lang == "ja" else "Thank you for your feedback!"
        return JSONResponse({"success": True, "message": message})
    except feedback_service.FeedbackError as e:
        # 種類・本文・画像の数、送信の間隔（3分）など
        return JSONResponse({"success": False, "message": e.message(lang)}, status_code=status.HTTP_400_BAD_REQUEST)
    except asyncpg.PostgresError as e:
        logger.error(f"フィードバック送信中にデータベースエラー (User ID: {current_user.id}): {e}")
        message = "データベースエラーのため、フィードバックを送信できませんでした。" if lang == "ja" else "Could not send feedback due to a database error."
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    except Exception as e:
        logger.error(f"フィードバック送信中に予期せぬエラー (User ID: {current_user.id}): {e}", exc_info=True)
        message = "予期せぬエラーのため、フィードバックを送信できませんでした。" if lang == "ja" else "An unexpected error occurred while sending feedback."
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    

# --- 保存枠拡張エンドポイント ---
class ExpandSlotsRequest(BaseModel):
    target: str # 'bookmarks' / 'history' / 'club_bookmarks' / 'club_history' / 'sub_accounts'

@router.post("/expand-slots", name="account_expand_slots")
async def expand_slots_process(
    request: Request,
    payload: ExpandSlotsRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user)
):
    lang = request.path_params.get("lang", "ja")
    target = payload.target

    if current_user.tokens < 15:
        message = "トークンが足りないため拡張できません。" if lang == "ja" else "Not enough tokens to expand."
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_400_BAD_REQUEST)

    if target == "sub_accounts":
        try:
            result = await current_user.expand_sub_account_slots(db)
        except DataBaseError as e:
            logger.error(f"サブアカウント枠拡張中 (ID: {current_user.id}) にDBエラー: {e}")
            message = "拡張処理に失敗しました。" if lang == "ja" else "Failed to expand slots."
            return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
        if result == "expanded":
            # 表示上はメインアカウントを含めた登録枠数で表す
            message = f"登録枠を{current_user.sub_accounts_limit + 1}枠に拡張しました。" if lang == "ja" else f"Expanded your account slots to {current_user.sub_accounts_limit + 1}."
            return JSONResponse({"success": True, "message": message})
        if result == "max_reached":
            message = "登録枠はこれ以上拡張できません。" if lang == "ja" else "Account slots cannot be expanded further."
        else:
            message = "トークンが足りないため拡張できません。" if lang == "ja" else "Not enough tokens to expand."
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_400_BAD_REQUEST)

    if target == "bookmarks":
        success = await current_user.expand_bookmark_slots(db)
        if success:
            message = "ブックマーク枠を2枠拡張しました。" if lang == "ja" else "Expanded bookmark slots by 2."
            return JSONResponse({"success": True, "message": message})
        else:
            # spend_tokensが内部で失敗することは考えにくいが念のため
            message = "拡張処理に失敗しました。" if lang == "ja" else "Failed to expand slots."
            return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

    elif target == "history":
        success = await current_user.expand_history_slots(db)
        if success:
            message = "閲覧履歴の表示枠を3枠拡張しました。" if lang == "ja" else "Expanded history slots by 3."
            return JSONResponse({"success": True, "message": message})
        else:
            message = "拡張処理に失敗しました。" if lang == "ja" else "Failed to expand slots."
            return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

    elif target == "club_bookmarks":
        success = await current_user.expand_club_bookmark_slots(db)
        if success:
            message = "クラブブックマーク枠を2枠拡張しました。" if lang == "ja" else "Expanded club bookmark slots by 2."
            return JSONResponse({"success": True, "message": message})
        else:
            message = "クラブブックマーク枠はこれ以上拡張できないか、拡張処理に失敗しました。" if lang == "ja" else "Club bookmark slots cannot be expanded further or expansion failed."
            return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_400_BAD_REQUEST)

    elif target == "club_history":
        success = await current_user.expand_club_history_slots(db)
        if success:
            message = "クラブ閲覧履歴の表示枠を3枠拡張しました。" if lang == "ja" else "Expanded club history slots by 3."
            return JSONResponse({"success": True, "message": message})
        else:
            message = "クラブ閲覧履歴枠はこれ以上拡張できないか、拡張処理に失敗しました。" if lang == "ja" else "Club history slots cannot be expanded further or expansion failed."
            return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_400_BAD_REQUEST)

    else:
        message = "無効なリクエストです。" if lang == "ja" else "Invalid request."
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_400_BAD_REQUEST)


# --- 報酬付き広告のトークン付与エンドポイント ---
@router.post("/claim-rewarded-token", name="account_claim_rewarded_token")
async def claim_rewarded_token_process(
    request: Request,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user) # ログイン必須
):
    """
    報酬付き動画広告の視聴完了後にトークンを付与するエンドポイント。
    """
    lang = request.path_params.get("lang", "ja")

    platform = getattr(request.state, "platform", "web")
    if platform not in ("ios", "android"):
        logger.warning(f"報酬トークン付与を非アプリから拒否 (platform: {platform}, user: {current_user.id})")
        message = "この報酬はアプリ版でのみ利用できます。" if lang == "ja" else "This reward is available only in the app."
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_403_FORBIDDEN)

    try:
        # Userクラスのclaim_tokensメソッドを呼び出す (claimed=15, daily_limit=5)
        # 1日の上限は同じタグ(メイン・サブ)を登録しているアカウント全体でも共有する
        claim_status = await main_account_reward_service.claim_with_main_account_limit(
            db,
            player_tags=current_user.player_tags,
            reward_key=main_account_reward_service.REWARD_AD_TOKEN,
            limit=5,
            daily=True,
            user_id=current_user.id,
            grant=lambda: current_user.claim_tokens(db, claimed=15, daily_limit=5),
        )

        if claim_status == main_account_reward_service.CLAIM_MAIN_ACCOUNT_LIMIT:
            message = main_account_reward_service.MAIN_ACCOUNT_DAILY_CLAIMED_JA if lang == "ja" else main_account_reward_service.MAIN_ACCOUNT_DAILY_CLAIMED_EN
            return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_400_BAD_REQUEST)

        if claim_status == main_account_reward_service.CLAIM_SUCCESS:
            message = "15トークンを獲得しました。" if lang == "ja" else "You earned 15 tokens!"
            return JSONResponse({
                "success": True, 
                "message": message,
                "new_token_balance": current_user.tokens,
                "new_claim_count": current_user.token_claim_count
            })
        else:
            # 上限に達していた場合
            message = "今日の報酬上限に達しています。" if lang == "ja" else "You have reached the daily reward limit."
            return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_400_BAD_REQUEST)

    except DataBaseError as e:
        logger.error(f"トークン付与中にデータベースエラー (User ID: {current_user.id}): {e}")
        message = "データベースエラーが発生しました。" if lang == "ja" else "A database error occurred."
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    except Exception as e:
        logger.error(f"トークン付与中に予期せぬエラー (User ID: {current_user.id}): {e}", exc_info=True)
        message = "予期せぬエラーが発生しました。" if lang == "ja" else "An unexpected error occurred."
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post("/claim-rewarded-ticket", name="account_claim_rewarded_ticket")
async def claim_rewarded_ticket_process(
    request: Request,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user)
):
    """報酬付き動画広告の視聴完了後にチケットを付与するエンドポイント。"""
    lang = request.path_params.get("lang", "ja")

    platform = getattr(request.state, "platform", "web")
    if platform not in ("ios", "android"):
        logger.warning(f"報酬チケット付与を非アプリから拒否 (platform: {platform}, user: {current_user.id})")
        message = "この報酬はアプリ版でのみ利用できます。" if lang == "ja" else "This reward is available only in the app."
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_403_FORBIDDEN)

    try:
        before_ticket_balance = current_user.ad_skip_tickets
        before_token_balance = current_user.tokens
        claim_status = await main_account_reward_service.claim_with_main_account_limit(
            db,
            player_tags=current_user.player_tags,
            reward_key=main_account_reward_service.REWARD_AD_TICKET,
            limit=1,
            daily=True,
            user_id=current_user.id,
            grant=lambda: current_user.claim_tickets(db, claimed=2, daily_limit=1),
        )

        if claim_status == main_account_reward_service.CLAIM_MAIN_ACCOUNT_LIMIT:
            message = main_account_reward_service.MAIN_ACCOUNT_DAILY_CLAIMED_JA if lang == "ja" else main_account_reward_service.MAIN_ACCOUNT_DAILY_CLAIMED_EN
            return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_400_BAD_REQUEST)

        if claim_status != main_account_reward_service.CLAIM_SUCCESS:
            message = "今日はすでに視聴済みです。" if lang == "ja" else "You have already watched today's ad."
            return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_400_BAD_REQUEST)

        if current_user.is_delete_ads:
            converted_tokens = 20
            message = (
                f"チケット2枚の代替報酬として、トークンを{converted_tokens}個受け取りました。\n"
                f"(トークン数: {before_token_balance} → {current_user.tokens})"
                if lang == "ja"
                else f"You received {converted_tokens} tokens instead of 2 tickets.\n(Tokens: {before_token_balance} -> {current_user.tokens})"
            )
        else:
            message = (
                f"チケットを2枚受け取りました。\n(チケット数: {before_ticket_balance} → {current_user.ad_skip_tickets})"
                if lang == "ja"
                else f"You received 2 tickets.\n(Tickets: {before_ticket_balance} -> {current_user.ad_skip_tickets})"
            )

        return JSONResponse({
            "success": True,
            "message": message,
            "new_ticket_balance": current_user.ad_skip_tickets,
            "new_ticket_claim_count": current_user.ticket_claim_count,
            "new_token_balance": current_user.tokens,
        })

    except DataBaseError as e:
        logger.error(f"チケット付与中にデータベースエラー (User ID: {current_user.id}): {e}")
        message = "データベースエラーが発生しました。" if lang == "ja" else "A database error occurred."
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    except Exception as e:
        logger.error(f"チケット付与中に予期せぬエラー (User ID: {current_user.id}): {e}", exc_info=True)
        message = "予期せぬエラーが発生しました。" if lang == "ja" else "An unexpected error occurred."
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


class TicketPackPurchaseRequest(BaseModel):
    ticket_count: int
    token_cost: int


@router.post("/purchase-ticket-pack", name="account_purchase_ticket_pack")
async def purchase_ticket_pack_process(
    request: Request,
    payload: TicketPackPurchaseRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user)
):
    lang = request.path_params.get("lang", "ja")
    valid_packs = {
        (5, 35),
        (15, 100),
    }

    if (payload.ticket_count, payload.token_cost) not in valid_packs:
        return JSONResponse(
            {"success": False, "message": "無効なリクエストです。" if lang == "ja" else "Invalid request."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if current_user.is_delete_ads:
        return JSONResponse(
            {
                "success": False,
                "message": "すでに広告の削除が有効なため、チケットを購入する必要はありません。" if lang == "ja" else "You do not need to purchase tickets because Remove Ads is already active."
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if current_user.tokens < payload.token_cost:
        return JSONResponse(
            {
                "success": False,
                "message": "トークンが足りないため、購入できません。この上の「トークン」セクションより獲得方法をご確認ください。" if lang == "ja" else "You do not have enough tokens to make this purchase."
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    before_tokens = current_user.tokens
    before_tickets = current_user.ad_skip_tickets

    query = """
        UPDATE users
        SET
            tokens = tokens - $1,
            ad_skip_tickets = ad_skip_tickets + $2
        WHERE id = $3
          AND tokens >= $1
        RETURNING tokens, ad_skip_tickets
    """

    try:
        result = await db.fetchrow(query, payload.token_cost, payload.ticket_count, current_user.id)
    except asyncpg.PostgresError as e:
        logger.error(f"チケット購入中にデータベースエラー (User ID: {current_user.id}): {e}")
        message = "データベースエラーが発生しました。" if lang == "ja" else "A database error occurred."
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

    if not result:
        return JSONResponse(
            {
                "success": False,
                "message": "トークンが足りないため、購入できません。この上の「トークン」セクションより獲得方法をご確認ください。" if lang == "ja" else "You do not have enough tokens to make this purchase."
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    current_user.tokens = result["tokens"]
    current_user.ad_skip_tickets = result["ad_skip_tickets"]
    await delete_cache(f"user:{current_user.id}")
    await delete_cache(f"user_include_invalid:{current_user.id}")

    logger.info(
        f"{current_user.name} (ID: {current_user.id}) が {payload.token_cost}トークンでチケット{payload.ticket_count}枚を購入しました。"
    )
    await try_claim_tutorial_mission(current_user, db, "spend_tokens")

    message = (
        f"チケットを{payload.ticket_count}枚購入しました。\n(チケット数: {before_tickets} → {current_user.ad_skip_tickets})"
        if lang == "ja"
        else f"Purchased {payload.ticket_count} tickets.\n(Tickets: {before_tickets} -> {current_user.ad_skip_tickets})"
    )
    return JSONResponse(
        {
            "success": True,
            "message": message,
            "before_tokens": before_tokens,
            "after_tokens": current_user.tokens,
            "before_tickets": before_tickets,
            "after_tickets": current_user.ad_skip_tickets,
        }
    )


class TicketSellRequest(BaseModel):
    ticket_count: int


@router.post("/sell-tickets", name="account_sell_tickets")
async def sell_tickets_process(
    request: Request,
    payload: TicketSellRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user)
):
    lang = request.path_params.get("lang", "ja")

    if payload.ticket_count < 1:
        return JSONResponse(
            {"success": False, "message": "無効なリクエストです。" if lang == "ja" else "Invalid request."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if current_user.is_delete_ads:
        return JSONResponse(
            {
                "success": False,
                "message": "すでに広告の削除が有効なため、チケットを売却する必要はありません。" if lang == "ja" else "You do not need to sell tickets because Remove Ads is already active."
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if current_user.ad_skip_tickets < payload.ticket_count:
        return JSONResponse(
            {
                "success": False,
                "message": "チケットが足りないため、売却できません。" if lang == "ja" else "You do not have enough tickets to sell."
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    before_tokens = current_user.tokens

    try:
        sold_tickets, converted_tokens = await current_user.convert_tickets_to_tokens(
            db, payload.ticket_count, TICKET_SELL_TOKEN_RATE
        )
    except DataBaseError as e:
        logger.error(f"チケット売却中にデータベースエラー (User ID: {current_user.id}): {e}")
        message = "データベースエラーが発生しました。" if lang == "ja" else "A database error occurred."
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    except Exception as e:
        logger.error(f"チケット売却中に予期せぬエラー (User ID: {current_user.id}): {e}", exc_info=True)
        message = "予期せぬエラーが発生しました。" if lang == "ja" else "An unexpected error occurred."
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

    if sold_tickets < 1:
        return JSONResponse(
            {
                "success": False,
                "message": "チケットが足りないため、売却できません。" if lang == "ja" else "You do not have enough tickets to sell."
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    logger.info(
        f"{current_user.name} (ID: {current_user.id}) がチケット{sold_tickets}枚を{converted_tokens}トークンに売却しました。"
    )

    message = (
        f"チケット{sold_tickets}枚を{converted_tokens}トークンに売却しました。\n(トークン数: {before_tokens} → {current_user.tokens})"
        if lang == "ja"
        else f"Sold {sold_tickets} tickets for {converted_tokens} tokens.\n(Tokens: {before_tokens} -> {current_user.tokens})"
    )
    return JSONResponse(
        {
            "success": True,
            "message": message,
            "before_tokens": before_tokens,
            "after_tokens": current_user.tokens,
            "sold_tickets": sold_tickets,
            "earned_tokens": converted_tokens,
        }
    )


class ElixirSellRequest(BaseModel):
    elixir_count: int


@router.post("/sell-elixirs", name="account_sell_elixirs")
async def sell_elixirs_process(
    request: Request,
    payload: ElixirSellRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user)
):
    lang = request.path_params.get("lang", "ja")

    if payload.elixir_count < ELIXIR_SELL_DIVISOR or payload.elixir_count % ELIXIR_SELL_DIVISOR != 0:
        return JSONResponse(
            {"success": False, "message": "無効なリクエストです。" if lang == "ja" else "Invalid request."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if (current_user.auto_track_elixirs or 0) < payload.elixir_count:
        return JSONResponse(
            {
                "success": False,
                "message": "エリクサーが足りないため、売却できません。" if lang == "ja" else "You do not have enough elixir to sell."
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    before_tokens = current_user.tokens

    try:
        sold_elixirs, converted_tokens = await current_user.convert_elixirs_to_tokens(
            db, payload.elixir_count, ELIXIR_SELL_DIVISOR
        )
    except ValueError:
        return JSONResponse(
            {"success": False, "message": "無効なリクエストです。" if lang == "ja" else "Invalid request."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except DataBaseError as e:
        logger.error(f"エリクサー売却中にデータベースエラー (User ID: {current_user.id}): {e}")
        message = "データベースエラーが発生しました。" if lang == "ja" else "A database error occurred."
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    except Exception as e:
        logger.error(f"エリクサー売却中に予期せぬエラー (User ID: {current_user.id}): {e}", exc_info=True)
        message = "予期せぬエラーが発生しました。" if lang == "ja" else "An unexpected error occurred."
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

    if sold_elixirs < 1:
        return JSONResponse(
            {
                "success": False,
                "message": "エリクサーが足りないため、売却できません。" if lang == "ja" else "You do not have enough elixir to sell."
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    logger.info(
        f"{current_user.name} (ID: {current_user.id}) が{sold_elixirs}エリクサーを{converted_tokens}トークンに売却しました。"
    )

    message = (
        f"{sold_elixirs}エリクサーを{converted_tokens}トークンに売却しました。\n(トークン数: {before_tokens} → {current_user.tokens})"
        if lang == "ja"
        else f"Sold {sold_elixirs} elixir for {converted_tokens} tokens.\n(Tokens: {before_tokens} -> {current_user.tokens})"
    )
    return JSONResponse(
        {
            "success": True,
            "message": message,
            "before_tokens": before_tokens,
            "after_tokens": current_user.tokens,
            "sold_elixirs": sold_elixirs,
            "after_elixirs": current_user.auto_track_elixirs,
            "earned_tokens": converted_tokens,
        }
    )


#^ プライバシー設定関連のエンドポイント
# 履歴の公開設定リクエストモデル ---
class HistoryPrivacyRequest(BaseModel):
    history_type: str  # "name" または "club"
    is_hidden: bool
    player_tag: str | None = Field(default=None, max_length=20)  # 対象の登録アカウント。省略時はメインアカウント

@router.post("/update-history-privacy", name="account_update_history_privacy")
async def update_history_privacy_process(
    request: Request,
    payload: HistoryPrivacyRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    登録アカウント(メイン・サブ)の改名履歴・クラブ履歴の公開設定を更新するエンドポイント。
    """
    lang = request.path_params.get("lang", "ja")
    history_type = payload.history_type
    is_hidden = payload.is_hidden

    if history_type not in ["name", "club"]:
        return JSONResponse(
            {"success": False, "message": "無効なリクエストです。" if lang == "ja" else "Invalid request."},
            status_code=status.HTTP_400_BAD_REQUEST
        )

    # 本人の登録アカウント以外のタグが指定された場合は拒否する
    player_tag = format_tag(payload.player_tag) if payload.player_tag else current_user.main_account
    if payload.player_tag and not current_user.is_own_player_tag(player_tag):
        return JSONResponse(
            {"success": False, "message": "無効なリクエストです。" if lang == "ja" else "Invalid request."},
            status_code=status.HTTP_400_BAD_REQUEST
        )
    if not player_tag:
        # ログインユーザーであれば通常このエラーは発生しない
        return JSONResponse(
            {"success": False, "message": "メインアカウントが設定されていません。" if lang == "ja" else "Main account is not set."},
            status_code=status.HTTP_400_BAD_REQUEST
        )

    try:
        # 更新するカラムを動的に決定
        column_to_update = "hide_name_history" if history_type == "name" else "hide_club_history"

        # SQLインジェクションを防ぐため、カラム名は文字列結合ではなくif文で分岐させています
        query = f"UPDATE players SET {column_to_update} = $1 WHERE tag = $2"
        await db.execute(query, is_hidden, player_tag)

        # プレイヤーデータのキャッシュを削除して、次回のプロフィール表示時に最新情報が読み込まれるようにする
        await delete_cache(f"player:{player_tag}")
        await delete_cache(f"player_hide_history_settings:{player_tag}")

        logger.info(f"{current_user.name} (ID: {current_user.id}) がプレイヤー {player_tag} の{column_to_update}を{is_hidden}に変更しました。")
        return JSONResponse({
            "success": True,
            "message": "設定を更新しました。" if lang == "ja" else "Settings updated successfully."
        })
    except Exception as e:
        logger.error(f"履歴の公開設定更新中にエラー (User ID: {current_user.id}, Player: {player_tag}): {e}", exc_info=True)
        message = "データベースエラーのため、設定を更新できませんでした。" if lang == "ja" else "Could not update settings due to a database error."
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

class NotificationSettingRequest(BaseModel):
    setting_key: str
    enabled: bool

@router.post("/update-notification-settings", name="account_update_notification_settings")
async def update_notification_settings_process(
    request: Request,
    payload: NotificationSettingRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    lang = request.path_params.get("lang", "ja")
    allowed_keys = {
        "notification_badge_enabled",
        "notification_post_like_enabled",
        "notification_own_post_message_enabled",
        "notification_participated_thread_message_enabled",
        "notification_message_reply_enabled",
        "notification_message_mention_enabled",
        "notification_message_reaction_enabled",
        "notification_token_gift_enabled",
    }
    if payload.setting_key not in allowed_keys:
        return JSONResponse(
            {"success": False, "message": "無効なリクエストです。" if lang == "ja" else "Invalid request."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        await update_notification_setting(
            db,
            current_user.id,
            payload.setting_key,
            payload.enabled,
        )
        logger.info(
            f"{current_user.name} (ID: {current_user.id}) が通知設定 {payload.setting_key} を {payload.enabled} に変更しました。"
        )
        return JSONResponse({
            "success": True,
            "message": "設定を更新しました。" if lang == "ja" else "Settings updated successfully.",
        })
    except Exception as e:
        logger.error(f"通知設定更新中にエラー (User ID: {current_user.id}): {e}", exc_info=True)
        message = "データベースエラーのため、設定を更新できませんでした。" if lang == "ja" else "Could not update settings due to a database error."
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


# [この部分は公開用リポジトリでは非公開にされています]


def _board_block_owner(request: Request) -> tuple[int | None, str | None]:
    """ブロック一覧の持ち主を返す。ログイン中はアカウント、未ログインなら端末の匿名ID。"""
    user: User | None = getattr(request.state, "current_user", None)
    if user:
        return user.id, None
    return None, request.cookies.get("brawlanonid")


@router.get("/board-blocks", name="account_board_blocks")
async def get_board_blocks(
    request: Request,
    before_id: int | None = None,
    db: asyncpg.Connection = Depends(get_shared_db),
):
    """掲示板でブロックしたユーザー・ゲストの一覧を返す。未ログインでも利用できる。"""
    lang = request.path_params.get("lang", "ja")
    blocker_user_id, blocker_anonymous_id = _board_block_owner(request)
    if blocker_user_id is None and not blocker_anonymous_id:
        # 匿名IDのクッキーがまだない端末は、ブロックもまだしていない
        return JSONResponse({"success": True, "items": [], "has_more": False})

    try:
        items, has_more = await get_user_block_list(
            db,
            blocker_user_id=blocker_user_id,
            blocker_anonymous_id=blocker_anonymous_id,
            before_id=before_id,
        )
    except DataBaseError as e:
        logger.error(f"掲示板ブロック一覧の取得中にエラー (User ID: {blocker_user_id}): {e}", exc_info=True)
        message = "データベースエラーのため、ブロック一覧を読み込めませんでした。" if lang == "ja" else "Could not load your block list due to a database error."
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

    for item in items:
        item["icon_path"] = "/" + get_icon_path(item.pop("icon_id"))
        if item.get("post_comment"):
            item["post_comment"] = censor_filter(item["post_comment"])
        if item.get("post_club_badge_id") is not None:
            item["post_club_badge_path"] = f"/static/images/club_badges/{item.pop('post_club_badge_id')}.png"
    return JSONResponse({"success": True, "items": items, "has_more": has_more})


@router.post("/reset-board-blocks", name="account_reset_board_blocks")
async def reset_board_blocks_process(
    request: Request,
    db: asyncpg.Connection = Depends(get_shared_db),
):
    """掲示板のブロックをすべて解除する。未ログインの場合は、この端末でのブロックが対象。"""
    lang = request.path_params.get("lang", "ja")
    blocker_user_id, blocker_anonymous_id = _board_block_owner(request)
    if blocker_user_id is None and not blocker_anonymous_id:
        return JSONResponse({
            "success": True,
            "deleted_count": 0,
            "message": "ブロックをすべて解除しました。" if lang == "ja" else "All blocks have been removed.",
        })

    try:
        deleted_count = await reset_user_blocks_by_blocker(
            db,
            blocker_user_id=blocker_user_id,
            blocker_anonymous_id=blocker_anonymous_id,
        )
        user: User | None = getattr(request.state, "current_user", None)
        who = f"{user.name} (ID: {user.id})" if user else f"ゲスト (匿名ID: {blocker_anonymous_id})"
        logger.info(f"{who} が掲示板のブロックをすべて解除しました。 削除件数: {deleted_count}")
        return JSONResponse({
            "success": True,
            "deleted_count": deleted_count,
            "message": "ブロックをすべて解除しました。" if lang == "ja" else "All blocks have been removed.",
        })
    except DataBaseError as e:
        logger.error(f"掲示板ブロックの一括解除中にエラー (User ID: {blocker_user_id}): {e}", exc_info=True)
        message = "データベースエラーのため、ブロックを解除できませんでした。" if lang == "ja" else "Could not remove blocks due to a database error."
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

# --- ボーナスミッションクリアエンドポイント ---
@router.post("/bonus-mission/claim", response_class=JSONResponse, name="account_claim_basic_mission")
async def claim_bonus_mission(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user)
):
    try:
        today_utc = datetime.datetime.now(datetime.timezone.utc).date()
        if current_user.last_bonus_mission_date == today_utc:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"success": False, "message": "本日分のベーシックミッションは既にクリア済みです。" if lang == "ja" else "Today's basic mission is already cleared."}
            )
            
        # 10トークン付与と last_bonus_mission_date の更新を原子的に行う
        # 1日1回の上限は同じタグ(メイン・サブ)を登録しているアカウント全体でも共有する
        claim_status = await main_account_reward_service.claim_with_main_account_limit(
            db,
            player_tags=current_user.player_tags,
            reward_key=main_account_reward_service.REWARD_BASIC_MISSION,
            limit=1,
            daily=True,
            user_id=current_user.id,
            grant=lambda: current_user.claim_basic_mission(db, claimed=10, today=today_utc),
        )

        if claim_status == main_account_reward_service.CLAIM_MAIN_ACCOUNT_LIMIT:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"success": False, "message": main_account_reward_service.MAIN_ACCOUNT_DAILY_CLAIMED_JA if lang == "ja" else main_account_reward_service.MAIN_ACCOUNT_DAILY_CLAIMED_EN}
            )

        if claim_status == main_account_reward_service.CLAIM_SUCCESS:
            # ログ出力用: どのミッションをクリアしたか名前を取得する
            offset_day = ((today_utc.day - 1 + current_user.id) % 31) + 1
            mission_name = BASIC_MISSIONS.get(offset_day, {}).get("ja", "不明なミッション")

            logger.info(f"{current_user.name} (ID: {current_user.id})がベーシックミッション「{mission_name}」をクリアしました。")

            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={"success": True, "message": "ベーシックミッションをクリアし、10トークンを獲得しました！" if lang == "ja" else "Cleared the basic mission and earned 10 tokens!"}
            )
        else:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"success": False, "message": "所持上限に達しているためトークンを受け取れません。" if lang == "ja" else "Cannot claim tokens because you have reached the maximum limit."}
            )
            
    except Exception as e:
        logger.error(f"ベーシックミッション・クリア処理でエラー: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": "エラーが発生しました。時間を置いて再度お試しください。" if lang == "ja" else "An error occurred. Please try again later."}
        )


@router.post("/special-mission/app-login", response_class=JSONResponse, name="account_claim_app_login_mission")
async def claim_app_login_mission(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    platform = getattr(request.state, "platform", "web")
    if platform not in ("ios", "android"):
        logger.warning(
            f"アプリ版ログインミッション達成を非アプリから拒否 (platform: {platform}, user: {current_user.id})"
        )
        message = (
            "このミッションはアプリ版でのみ達成できます。"
            if lang == "ja"
            else "This mission can only be completed in the app."
        )
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_403_FORBIDDEN)

    try:
        result = await current_user.claim_app_login_mission(db, platform)
        if result == "success":
            message = (
                f"ミッションを達成しました。{APP_LOGIN_MISSION_REWARD}トークンを獲得しました。"
                if lang == "ja"
                else f"Mission completed. You earned {APP_LOGIN_MISSION_REWARD} tokens."
            )
            return JSONResponse(status_code=status.HTTP_200_OK, content={"success": True, "message": message})
        if result == "main_account_claimed":
            # ミッションは達成扱い(トークンのみ付与なし)のため success を返し、画面を達成済み表示にする
            message = (
                "ミッションを達成しました。登録中のメイン・サブアカウントのいずれかで、別のアカウントが報酬を受け取り済みのため、トークンは付与されません。"
                if lang == "ja"
                else "Mission completed. No tokens were given because the reward has already been claimed by another account linked to one of your main or sub accounts."
            )
            return JSONResponse(status_code=status.HTTP_200_OK, content={"success": True, "message": message, "rewarded": False})
        if result == "already_cleared":
            message = (
                "このミッションは既にクリア済みです。"
                if lang == "ja"
                else "This mission has already been cleared."
            )
            return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"success": False, "message": message})
        message = (
            "所持上限に達しているためトークンを受け取れません。"
            if lang == "ja"
            else "Cannot claim tokens because you have reached the maximum limit."
        )
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"success": False, "message": message})
    except Exception as e:
        logger.error(
            f"アプリ版ログインミッション・クリア処理でエラー (platform: {platform}, user: {current_user.id}): {e}",
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "success": False,
                "message": "エラーが発生しました。時間を置いて再度お試しください。"
                if lang == "ja"
                else "An error occurred. Please try again later.",
            },
        )


# [この部分は公開用リポジトリでは非公開にされています]
