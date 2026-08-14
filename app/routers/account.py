from fastapi import APIRouter, Request, Depends, HTTPException, status, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
import asyncpg
from pathlib import Path
from pydantic import BaseModel, Field, field_validator
import re
import datetime
import json

from app.core.templating import templates
from app.core.cache import set_cache, delete_cache, get_cache
from app.exceptions.custom_exceptions import DataBaseError, BrawlStarsAPIError
from app.services.brawl_service import get_player_name, get_player, check_verify, get_hide_history_settings, get_player_from_db
from app.services.user_service import User, is_user_name_used, verify_password, get_all_secret_questions, get_gift_code, create_feedback, get_active_giveaway_code, get_giveaway_user_entry_count, get_giveaway_total_stats, has_user_used_gift_code, reset_user_blocks_by_blocker, get_ticket_sell_options, TICKET_SELL_TOKEN_RATE
from app.services import minigame_service
from app.services.minigame_service import (
    AD_SKIP_TICKET_COST,
    DEFAULT_AD_DAILY_LIMIT,
    MINIGAME_AD_PLAY_CUTOFF_SECONDS,
    USER_HISTORY_LIMIT,
    resolve_prices,
)
from app.services.user_service import _current_token_claim_date, _normalize_daily_claim_count
from app.services.board_service import get_today_post_count_by_user
from app.services.notification_service import get_notification_settings, update_notification_setting
from app.utils.utils import format_tag, confirm_tag
from app.db.db import get_shared_db
from app.core.logger import logger

router = APIRouter(
    prefix="/{lang}/account", # [この部分は公開用リポジトリでは非公開にされています]

# [この部分は公開用リポジトリでは非公開にされています]

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
        "error": "処理中にエラーが発生しました。"
    }
    message_dict_en = {
        "added": "Added to bookmarks.",
        "overwritten": f"Overwrote bookmark.",
        "limit_reached": "Bookmark limit reached.",
        "already_exists": "Already bookmarked.",
        "is_main": "Main account cannot be bookmarked.",
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
        instructed_icon_id = await player.request_verify()

        # 正常終了のレスポンス
        return JSONResponse({
            "success": True,
            "message": "プレイヤーアイコンの変更指示を行いました。" if lang == "ja" else "Player icon change instruction issued.",
            "instructed_icon_id": instructed_icon_id,
            "player_tag": formatted_tag # 確認用
        })
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
        is_verified, error_reason_key = await check_verify(formatted_tag)

        if is_verified:
            # 認証成功時の処理
            verification_status_cache_key = f"player_verify_status:{formatted_tag}"
            await set_cache(verification_status_cache_key, "success", ttl=900) # 15分間有効な認証済みフラグ
            
            # 指示アイコンIDのキャッシュを削除
            await delete_cache(f"player_verify:{formatted_tag}")
            
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
    comment: str

@router.post("/send-feedback", name="account_send_feedback")
async def send_feedback_process(
    request: Request,
    payload: FeedbackRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user)
):
    lang = request.path_params.get("lang", "ja")
    
    # バリデーション
    if not payload.type or not payload.comment.strip():
        return JSONResponse(
            {"success": False, "message": "フィードバックの種類を選択し、内容を入力してください。" if lang == "ja" else "Please select a feedback type and enter a comment."},
            status_code=status.HTTP_400_BAD_REQUEST
        )

    try:
        await create_feedback(
            db=db,
            user_id=current_user.id,
            feedback_type=payload.type,
            comment=payload.comment
        )
        message = "送信が完了しました。" if lang == "ja" else "Thank you for your feedback!"
        return JSONResponse({"success": True, "message": message})
    except ValueError as e:
        # ユーザーが存在しない、投稿禁止、クールダウン中など
        return JSONResponse({"success": False, "message": str(e)}, status_code=status.HTTP_400_BAD_REQUEST)
    except DataBaseError as e:
        logger.error(f"フィードバック送信中にデータベースエラー (User ID: {current_user.id}): {e}")
        message = "データベースエラーのため、フィードバックを送信できませんでした。" if lang == "ja" else "Could not send feedback due to a database error."
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    except Exception as e:
        logger.error(f"フィードバック送信中に予期せぬエラー (User ID: {current_user.id}): {e}", exc_info=True)
        message = "予期せぬエラーのため、フィードバックを送信できませんでした。" if lang == "ja" else "An unexpected error occurred while sending feedback."
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    

# --- 保存枠拡張エンドポイント ---
class ExpandSlotsRequest(BaseModel):
    target: str # 'bookmarks' / 'history' / 'club_bookmarks' / 'club_history'

@router.post("/expand-slots", name="account_expand_slots")
async def expand_slots_process(
    request: Request,
    payload: ExpandSlotsRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user)
):
    lang = request.path_params.get("lang", "ja")
    target = payload.target

    if current_user.tokens < 10:
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

    try:
        # Userクラスのclaim_tokensメソッドを呼び出す (claimed=15, daily_limit=5)
        success = await current_user.claim_tokens(db, claimed=15, daily_limit=5)

        if success:
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

    try:
        before_ticket_balance = current_user.ad_skip_tickets
        before_token_balance = current_user.tokens
        success = await current_user.claim_tickets(db, claimed=2, daily_limit=1)

        if not success:
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


#^ プライバシー設定関連のエンドポイント
# 履歴の公開設定リクエストモデル ---
class HistoryPrivacyRequest(BaseModel):
    history_type: str  # "name" または "club"
    is_hidden: bool

@router.post("/update-history-privacy", name="account_update_history_privacy")
async def update_history_privacy_process(
    request: Request,
    payload: HistoryPrivacyRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    メインアカウントの改名履歴・クラブ履歴の公開設定を更新するエンドポイント。
    """
    lang = request.path_params.get("lang", "ja")
    history_type = payload.history_type
    is_hidden = payload.is_hidden

    if history_type not in ["name", "club"]:
        return JSONResponse(
            {"success": False, "message": "無効なリクエストです。" if lang == "ja" else "Invalid request."},
            status_code=status.HTTP_400_BAD_REQUEST
        )

    player_tag = current_user.main_account
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
        "notification_message_reaction_enabled",
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


class MinigameTicketSettingRequest(BaseModel):
    enabled: bool


@router.post("/update-minigame-ticket-setting", name="account_update_minigame_ticket_setting")
async def update_minigame_ticket_setting_process(
    request: Request,
    payload: MinigameTicketSettingRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    lang = request.path_params.get("lang", "ja")
    if current_user.is_delete_ads:
        return JSONResponse(
            {
                "success": False,
                "message": "広告削除購入済みのため、この設定は変更できません。"
                if lang == "ja"
                else "This setting is unavailable because you purchased Remove Ads.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    try:
        await db.execute(
            "UPDATE users SET minigame_use_ad_skip_ticket = $1 WHERE id = $2",
            payload.enabled,
            current_user.id,
        )
        current_user.minigame_use_ad_skip_ticket = payload.enabled
        await delete_cache(f"user:{current_user.id}")
        await delete_cache(f"user_include_invalid:{current_user.id}")
        logger.info(
            f"{current_user.name} (ID: {current_user.id}) が minigame_use_ad_skip_ticket を {payload.enabled} に変更しました。"
        )
        return JSONResponse({
            "success": True,
            "message": "設定を更新しました。" if lang == "ja" else "Settings updated successfully.",
        })
    except Exception as e:
        logger.error(
            f"ミニゲームチケット設定更新中にエラー (User ID: {current_user.id}): {e}",
            exc_info=True,
        )
        message = (
            "データベースエラーのため、設定を更新できませんでした。"
            if lang == "ja"
            else "Could not update settings due to a database error."
        )
        return JSONResponse({"success": False, "message": message}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post("/reset-board-blocks", name="account_reset_board_blocks")
async def reset_board_blocks_process(
    request: Request,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    lang = request.path_params.get("lang", "ja")
    try:
        deleted_count = await reset_user_blocks_by_blocker(db, current_user.id)
        logger.info(
            f"{current_user.name} (ID: {current_user.id}) が掲示板のブロックをリセットしました。"
            f" 削除件数: {deleted_count}"
        )
        return JSONResponse({
            "success": True,
            "deleted_count": deleted_count,
            "message": "ブロックをリセットしました。" if lang == "ja" else "Board blocks have been reset.",
        })
    except DataBaseError as e:
        logger.error(f"掲示板ブロックのリセット中にエラー (User ID: {current_user.id}): {e}", exc_info=True)
        message = "データベースエラーのため、ブロックをリセットできませんでした。" if lang == "ja" else "Could not reset blocks due to a database error."
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
            
        # user_service.py の claim_tokens メソッドを呼び出す
        # 10トークン付与、daily_limitは関係ないのでNone
        success = await current_user.claim_tokens(db, claimed=10, daily_limit=None)
        
        if success:
            # last_bonus_mission_date の更新
            current_user.last_bonus_mission_date = today_utc
            update_query = "UPDATE users SET last_bonus_mission_date = $1 WHERE id = $2"
            await db.execute(update_query, today_utc, current_user.id)
            
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


# --- ミニゲーム ---
class MinigamePlayRequest(BaseModel):
    method: str = Field(..., pattern="^(ad|token)$")
    use_tickets: bool = False


class MinigameCompleteRequest(BaseModel):
    play_id: int
    skip: bool = False


@router.post("/minigame/play", name="account_minigame_play")
async def account_minigame_play(
    request: Request,
    payload: MinigamePlayRequest,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    platform = getattr(request.state, "platform", "web")
    try:
        play = await minigame_service.start_play(
            db,
            current_user,
            method=payload.method,
            platform=platform,
            lang=lang,
            require_tickets=bool(payload.use_tickets),
        )
        result_prizes = play["result_prizes"]
        animation_payload = play["animation_payload"]
        if isinstance(result_prizes, str):
            result_prizes = json.loads(result_prizes)
        if isinstance(animation_payload, str):
            animation_payload = json.loads(animation_payload)
        return JSONResponse({
            "success": True,
            "play": {
                "id": play["id"],
                "result_rank": play["result_rank"],
                "result_prizes": result_prizes,
                "animation_payload": animation_payload,
                "tokens_spent": play["tokens_spent"],
                "tickets_spent": play.get("tickets_spent", 0) or 0,
                "play_method": play["play_method"],
            },
            "user_tokens": current_user.tokens,
            "user_tickets": current_user.ad_skip_tickets,
        })
    except ValueError as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=400)
    except Exception as e:
        logger.error(f"ミニゲーム開始エラー (User: {current_user.id}): {e}", exc_info=True)
        message = "エラーが発生しました。" if lang == "ja" else "An error occurred."
        return JSONResponse({"success": False, "message": message}, status_code=500)


@router.post("/minigame/complete", name="account_minigame_complete")
async def account_minigame_complete(
    request: Request,
    payload: MinigameCompleteRequest,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        result = await minigame_service.complete_play(
            db, current_user, payload.play_id, skip=payload.skip, lang=lang
        )
        if payload.skip or result.get("status") == "skipped":
            return JSONResponse({
                "success": True,
                "message": result.get("message")
                or ("プレイを中止しました。" if lang == "ja" else "Play abandoned."),
                "is_none": True,
                "is_gift": False,
                "result_rank": result.get("result_rank"),
                "user_tokens": current_user.tokens,
                "reload_after_ms": 3000,
            })
        result_prizes = result.get("result_prizes") or {}
        if isinstance(result_prizes, str):
            result_prizes = json.loads(result_prizes)
        items = result_prizes.get("items", []) if isinstance(result_prizes, dict) else []
        label = minigame_service.format_prize_label(items, lang)
        is_none = len(items) == 1 and items[0].get("type") == "none"
        is_gift = any(i.get("type") == "gift" for i in items)
        grant_log = result.get("grant_log") or {}
        if isinstance(grant_log, str):
            grant_log = json.loads(grant_log)
        grant_items = grant_log.get("items") or [] if isinstance(grant_log, dict) else []

        if is_none:
            message = "ご参加ありがとうございました。" if lang == "ja" else "Thank you for playing."
        elif is_gift:
            message = (
                f"おめでとうございます！<b>{label}</b>が当選しました。"
                if lang == "ja"
                else f"Congratulations! You won <b>{label}</b>."
            )
        else:
            message = None
            for item in grant_items:
                if item.get("type") == "auto_track_extend":
                    remaining = (
                        minigame_service._format_duration_ja(hours=int(item.get("remaining_hours", 0) or 0))
                        if lang == "ja"
                        else minigame_service._format_duration_en(hours=int(item.get("remaining_hours", 0) or 0))
                    )
                    duration = minigame_service.format_prize_label([item], lang)
                    duration = duration.replace("プレイヤー自動追跡 ", "").replace("Player auto-tracking ", "")
                    name = item.get("player_name") or ""
                    message = (
                        f"<b>{name}</b>のプレイヤー自動追跡機能の有効期限を"
                        f"<b>{duration}</b>延長しました (現在の残り期間: {remaining})"
                        if lang == "ja"
                        else f"Extended auto-tracking for <b>{name}</b> by <b>{duration}</b>"
                        f" (remaining: {remaining})"
                    )
                    break
                if item.get("type") == "battle_log_retention":
                    name = item.get("player_name") or ""
                    duration = (
                        minigame_service._format_months_ja(int(item.get("months", 0)))
                        if lang == "ja"
                        else minigame_service._format_months_en(int(item.get("months", 0)))
                    )
                    after = (
                        minigame_service._format_months_ja(int(item.get("after_months", 0)))
                        if lang == "ja"
                        else minigame_service._format_months_en(int(item.get("after_months", 0)))
                    )
                    message = (
                        f"<b>{name}</b>のバトル履歴保存期間を<b>{duration}</b>延長しました"
                        f" (現在の保存期間: {after})"
                        if lang == "ja"
                        else f"Extended battle log retention for <b>{name}</b> by <b>{duration}</b>"
                        f" (current retention: {after})"
                    )
                    if item.get("compensation_tokens"):
                        message += (
                            f"<br>上限超過分として {item['compensation_tokens']}トークンを補填しました"
                            if lang == "ja"
                            else f"<br>Compensated {item['compensation_tokens']} tokens for the excess period"
                        )
                    break

            if (message is None):
                grant_parts: list[str] = []
                for item in grant_items:
                    if item.get("type") == "ad_skip_ticket":
                        if item.get("converted_to_tokens"):
                            grant_parts.append(
                                f"<br>広告削除設定のためチケットをトークンに変換しました"
                                f"（トークン: {item.get('before_tokens')} → {item.get('after_tokens')}）"
                                if lang == "ja"
                                else f"<br>Tickets were converted to tokens because ads are removed"
                                f" (tokens: {item.get('before_tokens')} → {item.get('after_tokens')})"
                            )
                        else:
                            grant_parts.append(
                                f"<br>チケットを{item.get('amount', 0)}枚受け取りました"
                                f"（チケット数: {item.get('before_tickets')} → {item.get('after_tickets')}）"
                                if lang == "ja"
                                else f"<br>Received {item.get('amount', 0)} ticket(s)"
                                f" (tickets: {item.get('before_tickets')} → {item.get('after_tickets')})"
                            )
                    elif item.get("type") == "token":
                        grant_parts.append(
                            f"<br>{item.get('amount', 0)}トークンを受け取りました"
                            f"（トークン: {item.get('before_tokens')} → {item.get('after_tokens')}）"
                            if lang == "ja"
                            else f"<br>Received {item.get('amount', 0)} tokens"
                            f" (tokens: {item.get('before_tokens')} → {item.get('after_tokens')})"
                        )
                grant_detail = "".join(grant_parts)
                message = (
                    f"おめでとうございます！<b>{label}</b>が当選しました。{grant_detail}"
                    if lang == "ja"
                    else f"Congratulations! You won <b>{label}</b>.{grant_detail}"
                )
        return JSONResponse({
            "success": True,
            "message": message,
            "is_none": is_none,
            "is_gift": is_gift,
            "result_rank": result.get("result_rank"),
            "user_tokens": current_user.tokens,
            "reload_after_ms": 3000,
        })
    except ValueError as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=400)
    except Exception as e:
        logger.error(f"ミニゲーム完了エラー (User: {current_user.id}): {e}", exc_info=True)
        message = "エラーが発生しました。" if lang == "ja" else "An error occurred."
        return JSONResponse({"success": False, "message": message}, status_code=500)
