import asyncpg
import json
from fastapi import APIRouter, Request, HTTPException, Depends, Query, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.websockets import WebSocketState
from pydantic import BaseModel, Field

from app.core.templating import censor_filter
from app.db.db import get_shared_db, get_db_connection_for_bg_task
from app.db import db as db_module
from app.core.logger import logger
from app.core.text_search import GeneralBoardSearch, parse_general_board_search
from app.core.templating import templates
from app.core import cache as cache_module
from app.core.realtime import hub as realtime_hub
from app.services.brawl_service import Player, get_player, get_player_from_db, get_player_name, get_brawler
from app.services.user_service import User, get_user, get_blocked_ids, create_user_block, delete_user_block, try_progress_tutorial_board
from app.services.board_service import get_post, get_posts, get_trending_general_posts, get_messages, get_reactions, check_post_permitted, check_invitation_link, create_post, get_last_post, create_report, create_message, get_message, add_reaction, Reaction, get_player_icon_from_db, get_general_post_vote_summary, toggle_general_post_up_vote, attach_reply_to_previews, TEAM_POST_CLOSE_COOLDOWN_SECONDS, get_theme_board_posts, is_theme_post_type, get_or_create_theme_map_post, get_theme_brawler_board_colors, resolve_theme_map_display, invalidate_theme_board_posts_cache, get_theme_brawler_index_json, get_or_create_theme_brawler_post, THEME_CATEGORY_MAP, resolve_board_mentions, get_cached_board_mention_candidates
from app.services.notification_service import (
    NOTIFICATION_PAGE_SIZE,
    VALID_NOTIFICATION_FILTERS,
    BRAWLER_GUIDE_PARTICIPATED_THREAD_NOTIFICATION_LIMIT,
    create_message_notifications,
    create_token_gift_notification,
    VALID_THREAD_NOTIFICATION_MODES,
    empty_board_notification_context,
    get_board_notification_context,
    get_notification_settings,
    get_notifications_for_display,
    get_thread_notification_state,
    handle_message_reaction_notification,
    handle_post_like_notification,
    keep_reaction_notification_on_remove,
    mark_all_notifications_as_read,
    mark_thread_notifications_as_read,
    set_thread_notification_mode,
    set_thread_viewing,
)
from app.services.token_gift_service import (
    TOKEN_GIFT_COMMENT_MAX_LENGTH,
    TOKEN_GIFT_MESSAGE_TYPE,
    TOKEN_GIFT_OPTIONS,
    TokenGiftError,
    attach_token_gift_payloads,
    create_token_gift,
    delete_token_gift_comment,
    format_token_amount,
    get_remaining_give_count,
    validate_token_gift,
)
from app.utils.utils import get_icon_path, get_remote_ip
from app.services.all_maps_service import build_map_board_index_json
from app.services.map_mode_catalog import ensure_catalog
from app.services import reaction_rules
from app.exceptions.custom_exceptions import BrawlStarsAPIError, DataBaseError

router = APIRouter(
    prefix="/{lang}/boards",
    tags=["Boards"]
)

CHAT_MESSAGES_INITIAL_LOAD = 100
CHAT_MESSAGES_OLDER_LOAD = 200


# [この部分は公開用リポジトリでは非公開にされています]

    # [この部分は公開用リポジトリでは非公開にされています]

    mode_count = sum(
        1 for value in (before_message_id, after_message_id, around_message_id, edge) if value is not None
    )
    if mode_count != 1:
        raise HTTPException(
            status_code=400,
            detail="Specify exactly one of before_message_id, after_message_id, around_message_id, edge",
        )

    anchor_message = None
    if edge is None:
        anchor_id = before_message_id or after_message_id or around_message_id
        anchor_message = await get_message(db, anchor_id, include_deleted_message=True)
        if not anchor_message or anchor_message.thread_id != thread_id:
            raise HTTPException(status_code=400, detail="Invalid message anchor")

    user: User | None = getattr(request.state, "current_user", None)
    blocker_user_id = user.id if user else None
    blocker_anonymous_id = request.cookies.get("brawlanonid") if not user else None

    if blocker_user_id or blocker_anonymous_id:
        blocked_ids = await get_blocked_ids(
            db,
            blocker_user_id=blocker_user_id,
            blocker_anonymous_id=blocker_anonymous_id,
        )
    else:
        blocked_ids = {"user_ids": [], "anonymous_ids": []}

    try:
        if edge is not None:
            messages_payload, has_more_older, has_more_newer = await _fetch_chat_messages_payload(
                db,
                thread_id=thread_id,
                blocked_user_ids=blocked_ids["user_ids"],
                from_oldest=(edge == "oldest"),
                per_page=min(per_page, CHAT_MESSAGES_INITIAL_LOAD),
            )
        elif around_message_id is not None:
            if not anchor_message or anchor_message.is_deleted:
                raise HTTPException(status_code=400, detail="Invalid around_message_id")
            messages_payload, has_more_older, has_more_newer = await _fetch_chat_messages_around(
                db,
                thread_id=thread_id,
                blocked_user_ids=blocked_ids["user_ids"],
                around_message_id=around_message_id,
                per_page=min(per_page, CHAT_MESSAGES_INITIAL_LOAD),
            )
        elif before_message_id is not None:
            messages_payload, has_more_older, has_more_newer = await _fetch_chat_messages_payload(
                db,
                thread_id=thread_id,
                blocked_user_ids=blocked_ids["user_ids"],
                before_message_id=before_message_id,
                per_page=per_page,
            )
        else:
            messages_payload, has_more_older, has_more_newer = await _fetch_chat_messages_payload(
                db,
                thread_id=thread_id,
                blocked_user_ids=blocked_ids["user_ids"],
                after_message_id=after_message_id,
                per_page=per_page,
            )
    except DataBaseError as e:
        logger.error(f"メッセージ取得中にデータベースエラー (thread: {thread_id}): {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error")

    return {
        "messages": messages_payload,
        "has_more": has_more_older,
        "has_more_older": has_more_older,
        "has_more_newer": has_more_newer,
    }


# [この部分は公開用リポジトリでは非公開にされています]
        except Exception as e:
            logger.error(f"WebSocket初期化中にエラー (スレッド: {thread_id}): {e}", exc_info=True)
            # [この部分は公開用リポジトリでは非公開にされています]

        # 3. 接続処理とリスナー開始
        await manager.connect(websocket, thread_id)
        logger.debug(f"WebSocket接続確立 (スレッド: {thread_id}, ユーザー: {user_info['name']})")

        # 4. メッセージ受信ループ (ここではDB接続を保持しない)
        while True:
            try:
                payload = await websocket.receive_json()
                event_type = payload.get("type")
                logger.debug(f"受信データ (スレッド: {thread_id}): {payload}")

                # メッセージ処理後、manager.broadcastでRedisに発行する
                if event_type == "typing_start":
                    await manager.broadcast(
                        thread_id,
                        {"type": "user_typing", "data": user_info}
                    )
                elif event_type == "typing_stop":
                    await manager.broadcast(
                        thread_id,
                        {"type": "user_stopped_typing", "data": {"id": user_info["id"]}}
                    )
                # 画面を表示している間（viewing は45秒おきに届く）は、このスレッドの通知を作らない
                elif event_type in ("viewing", "away"):
                    if user_info["id"]:
                        await set_thread_viewing(user_info["id"], thread_id, event_type == "viewing")
                else:
                    logger.warning(f"未定義のイベントタイプを受信: {event_type}")

            except WebSocketDisconnect:
                break # ループを抜けてfinallyへ
            except json.JSONDecodeError:
                logger.warning(f"不正なJSONデータを受信しました。")
                continue
            except RuntimeError as e:
                if "WebSocket is not connected" in str(e):
                    logger.debug(f"WebSocket接続が既に閉じられています (スレッド: {thread_id})")
                    break
                logger.error(
                    f"WebSocketメッセージ処理中に予期せぬエラー (スレッド: {thread_id}): {e}",
                    exc_info=True
                )
                continue
            except Exception as e:
                logger.error(
                    f"WebSocketメッセージ処理中に予期せぬエラー (スレッド: {thread_id}): {e}",
                    exc_info=True
                )
                continue

    finally:
        # 終了処理 ---
        logger.debug(f"WebSocket接続終了 (スレッド: {thread_id}, ユーザー: {user_info['name']})")
        if not denied:
            # タイピング終了を通知
            await manager.broadcast(
                thread_id,
                {"type": "user_stopped_typing", "data": {"id": user_info["id"]}}
            )
            if user_info["id"]:
                await set_thread_viewing(user_info["id"], thread_id, False)
            await manager.disconnect(websocket, thread_id)

@router.post("/chat/{thread_id}/messages", name="create_chat_message")
async def create_chat_message(
    request: Request,
    thread_id: int,
    message_data: MessageCreateRequest,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    """チャットスレッドに新しいメッセージを送信する。
    """
    user: User | None = getattr(request.state, "current_user", None)
    
    # 投稿(スレッド)の存在確認
    post = await get_post(db, id=thread_id)
    if not post:
        raise HTTPException(status_code=404, detail="Chat thread not found")

    # 権限チェック ---
    # フロントエンドの制御だけでなく、バックエンドでも必ず検証します。
    is_permitted = await post.is_permitted_to_chat(db, user.id if user else None)
    if not is_permitted:
        raise HTTPException(status_code=403, detail="You do not have permission to post in this chat")

    try:
        # 本文の「@名前」から、メンションした人を読み取る（範囲はスレッド主と最近書いた人。ゲストは数えない）
        mentions = await resolve_board_mentions(db, post, user.id if user else None, message_data.message)
        message_id = await create_message(
            db=db,
            thread_id=thread_id,
            ip=get_ip(request),
            message=message_data.message,
            user_id=user.id if user else None,
            reply_to_message_id=message_data.reply_to_message_id,
            mentions=mentions,
        )
        
        # トークンとアドバンスミッション
        if user:
            if post.type == "general":
                await user.check_and_claim_advance_mission(db, "chat_general")
            elif is_theme_post_type(post.type):
                await user.check_and_claim_advance_mission(db, "chat_brawler")
        if is_theme_post_type(post.type):
            await invalidate_theme_board_posts_cache()
        
        # WebSocketで新しいメッセージをブロードキャスト
        new_message = await get_message(db, message_id)
        if new_message:
            await attach_reply_to_previews(db, [new_message])
            # まず、ブロードキャスト用のデータを辞書として準備
            broadcast_message_data = _censor_message_dict(new_message.to_dict())

            # フィルタリング済みのデータをブロードキャストする
            await manager.broadcast(
                thread_id,
                {
                    "type": "new_message",
                    "data": {
                        "message": broadcast_message_data, # フィルタリング済みの辞書を使用
                        "reactions": []
                    }
                }
            )
        # キャッシュを更新: message_count をインクリメントし、post キャッシュを削除
        try:
            cache_key = f"message_count:{thread_id}"
            await cache_module.adjust_cache_counter_if_exists(cache_key, delta=1, ttl=15)
            await cache_module.delete_cache(f"post:{thread_id}")
        except Exception as e:
            logger.debug(f"キャッシュ更新に失敗しました (thread: {thread_id}): {e}")

        if user:
            await create_message_notifications(
                db,
                thread_id=thread_id,
                message_id=message_id,
                sender_user_id=user.id,
                reply_to_message_id=message_data.reply_to_message_id,
                mention_user_ids=[mention["id"] for mention in mentions],
            )
        
        return {"success": True, "message_id": message_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except DataBaseError as e:
        logger.error(f"メッセージ作成中(スレッド:{thread_id})にDBエラー: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error")


@router.get("/chat/{thread_id}/mention-candidates", name="get_chat_mention_candidates")
async def get_chat_mention_candidates(
    request: Request,
    thread_id: int,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    """入力欄で「@」を打ったときの候補（スレッド主と、最近書いた人）。画面ごとに最初の1回だけ呼ばれる。
    """
    user: User | None = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login required")

    post = await get_post(db, id=thread_id)
    if not post:
        raise HTTPException(status_code=404, detail="Chat thread not found")
    if not await post.is_permitted_to_chat(db, user.id):
        raise HTTPException(status_code=403, detail="You do not have permission to post in this chat")

    try:
        return {"ok": True, "items": await get_cached_board_mention_candidates(db, post)}
    except DataBaseError as e:
        logger.error(f"メンションの候補の取得中(スレッド:{thread_id}, User ID: {user.id})にDBエラー: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error")


@router.get("/chat/{thread_id}/notification-setting", name="get_thread_notification_setting")
async def get_thread_notification_setting(
    request: Request,
    thread_id: int,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    """チャット画面のメニュー用に、このスレッドの通知状態を返す（メニューを開いた時のみ呼ばれる）。
    """
    user: User | None = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login required")

    post = await get_post(db, id=thread_id)
    if not post:
        raise HTTPException(status_code=404, detail="Chat thread not found")

    try:
        return await get_thread_notification_state(db, user.id, post)
    except DataBaseError as e:
        logger.error(f"スレッド通知設定の取得中(スレッド:{thread_id}, User ID: {user.id})にDBエラー: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error")


@router.post("/chat/{thread_id}/notification-setting", name="update_thread_notification_setting")
async def update_thread_notification_setting(
    request: Request,
    thread_id: int,
    setting_data: ThreadNotificationSettingRequest,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    """このスレッドの通知オン/オフ（テーマ掲示板では直近のみに戻すも可）を設定する。
    """
    user: User | None = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login required")
    if setting_data.mode not in VALID_THREAD_NOTIFICATION_MODES:
        raise HTTPException(status_code=400, detail="Invalid mode")

    post = await get_post(db, id=thread_id)
    if not post or post.is_deleted:
        raise HTTPException(status_code=404, detail="Chat thread not found")

    try:
        return await set_thread_notification_mode(db, user.id, post, setting_data.mode)
    except DataBaseError as e:
        logger.error(f"スレッド通知設定の更新中(スレッド:{thread_id}, User ID: {user.id})にDBエラー: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error")


def _token_gift_error_response(exc: TokenGiftError, lang: str) -> JSONResponse:
    status_code = 400
    if exc.code in {"prohibit_posting", "permission", "forbidden"}:
        status_code = 403
    elif exc.code == "not_found":
        status_code = 404
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "code": exc.code, "detail": exc.client_message(lang)},
    )


@router.post("/chat/{thread_id}/token-gifts/validate", name="validate_chat_token_gift")
async def validate_chat_token_gift(
    request: Request,
    thread_id: int,
    gift_data: TokenGiftCreateRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
):
    lang = request.path_params.get("lang", "ja")
    user: User | None = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    post = await get_post(db, thread_id, include_deleted_post=True)
    if not post or post.is_deleted:
        raise HTTPException(status_code=404, detail="Thread not found")

    try:
        preview = await validate_token_gift(
            db,
            post=post,
            giver=user,
            recipient_user_id=gift_data.recipient_user_id,
            amount=gift_data.amount,
            comment=gift_data.comment,
            giver_ip=get_ip(request),
            lang=lang,
        )
    except TokenGiftError as e:
        return _token_gift_error_response(e, lang)
    except DataBaseError as e:
        logger.error(f"トークン進呈判定中(スレッド:{thread_id})にDBエラー: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error")

    recipient_name = preview["recipient_name"]
    amount_text = format_token_amount(preview["amount"])
    confirm_message = (
        f"{recipient_name}さんに{amount_text}トークンを贈りますか？"
        if lang == "ja"
        else f"Gift {amount_text} tokens to {recipient_name}?"
    )
    return {
        "success": True,
        "confirm_message": confirm_message,
        "amount": preview["amount"],
        "fee": preview["fee"],
        "recipient_name": recipient_name,
        "giver_tokens_before": preview["giver_tokens_before"],
        "giver_tokens_after": preview["giver_tokens_after"],
    }


@router.post("/chat/{thread_id}/token-gifts", name="create_chat_token_gift")
async def create_chat_token_gift(
    request: Request,
    thread_id: int,
    gift_data: TokenGiftCreateRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
):
    lang = request.path_params.get("lang", "ja")
    user: User | None = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    post = await get_post(db, thread_id, include_deleted_post=True)
    if not post or post.is_deleted:
        raise HTTPException(status_code=404, detail="Thread not found")

    try:
        result = await create_token_gift(
            db,
            post=post,
            giver=user,
            recipient_user_id=gift_data.recipient_user_id,
            amount=gift_data.amount,
            comment=gift_data.comment,
            giver_ip=get_ip(request),
            lang=lang,
        )
        new_message = await get_message(db, result["message_id"])
        if new_message:
            await attach_token_gift_payloads(db, [new_message])
            broadcast_message_data = _censor_message_dict(new_message.to_dict())
            await manager.broadcast(
                thread_id,
                {
                    "type": "new_message",
                    "data": {
                        "message": broadcast_message_data,
                        "reactions": [],
                    },
                },
            )
        try:
            cache_key = f"message_count:{thread_id}"
            await cache_module.adjust_cache_counter_if_exists(cache_key, delta=1, ttl=15)
            await cache_module.delete_cache(f"post:{thread_id}")
        except Exception as e:
            logger.debug(f"キャッシュ更新に失敗しました (thread: {thread_id}): {e}")

        await create_token_gift_notification(
            db,
            thread_id=thread_id,
            message_id=result["message_id"],
            giver_user_id=user.id,
            recipient_user_id=result["recipient_user_id"],
        )

        amount_text = format_token_amount(result["amount"])
        before_text = format_token_amount(result["giver_tokens_before"])
        after_text = format_token_amount(result["giver_tokens_after"])
        success_message = (
            f"{result['recipient_name']}さんに{amount_text}トークンを贈りました。\n"
            f"(自分のトークン数: {before_text} → {after_text})"
            if lang == "ja"
            else (
                f"You gifted {amount_text} tokens to {result['recipient_name']}.\n"
                f"(Your tokens: {before_text} → {after_text})"
            )
        )
        return {
            "success": True,
            "message": success_message,
            "message_id": result["message_id"],
            "tokens": result["giver_tokens_after"],
        }
    except TokenGiftError as e:
        return _token_gift_error_response(e, lang)
    except DataBaseError as e:
        logger.error(f"トークン進呈中(スレッド:{thread_id})にDBエラー: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error")


@router.delete("/messages/{message_id}/token-gift-comment", name="delete_token_gift_comment")
async def delete_token_gift_comment_endpoint(
    request: Request,
    message_id: int,
    db: asyncpg.Connection = Depends(get_shared_db),
):
    lang = request.path_params.get("lang", "ja")
    user: User | None = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        thread_id = await delete_token_gift_comment(
            db,
            message_id=message_id,
            actor=user,
            lang=lang,
        )
        await manager.broadcast(
            thread_id,
            {"type": "token_gift_comment_deleted", "data": {"message_id": message_id}},
        )
        return {"success": True}
    except TokenGiftError as e:
        return _token_gift_error_response(e, lang)
    except DataBaseError as e:
        logger.error(f"トークン進呈コメント削除中(メッセージ:{message_id})にDBエラー: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error")


@router.delete("/messages/{message_id}", name="delete_message")
async def delete_message(
    request: Request,
    message_id: int,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    """メッセージを削除する。
    """
    user: User | None = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    message = await get_message(db, id=message_id, include_deleted_message=True)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    if message.message_type == TOKEN_GIFT_MESSAGE_TYPE:
        raise HTTPException(status_code=400, detail="Token gift comments must be deleted via the gift comment endpoint")

    # 権限チェック ---
    if not user.has_perm("chat.delete_messages") and message.user_id != user.id:
        raise HTTPException(status_code=403, detail="You do not have permission to delete this message")
        
    try:
        thread_id = message.thread_id # 削除前にスレッドIDを保持
        await message.delete(db)
        
        # メッセージが削除されたことをブロードキャスト
        await manager.broadcast(
            thread_id,
            {"type": "delete_message", "data": {"message_id": message_id}}
        )
        # キャッシュを更新: message_count をデクリメントし、post キャッシュを削除
        try:
            cache_key = f"message_count:{thread_id}"
            await cache_module.adjust_cache_counter_if_exists(cache_key, delta=-1, ttl=15)
            await cache_module.delete_cache(f"post:{thread_id}")
        except Exception as e:
            logger.debug(f"キャッシュ更新に失敗しました (thread: {thread_id}): {e}")

        return {"success": True}
    except DataBaseError as e:
        logger.error(f"メッセージ(ID:{message_id})の削除中にDBエラー: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error")

@router.post("/messages/{message_id}/report", name="report_message")
async def report_message(
    request: Request,
    message_id: int,
    report_data: ReportCreateRequest,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    """メッセージを通報する。
    """
    user: User | None = getattr(request.state, "current_user", None)

    # メッセージの存在確認
    message = await get_message(db, id=message_id)
    if not message:
        return JSONResponse(status_code=404, content={"detail": "Message to report not found"}) # 例外をJSONResponseで返すように変更
    if message.message_type == "warning":
        return JSONResponse(status_code=400, content={"detail": "This message cannot be reported"})
    if message.message_type == TOKEN_GIFT_MESSAGE_TYPE:
        try:
            gift_row = await db.fetchrow(
                "SELECT comment, is_comment_deleted FROM token_gifts WHERE message_id = $1",
                message_id,
            )
        except asyncpg.PostgresError as e:
            logger.error(f"トークン進呈の通報前確認中にDBエラー (メッセージ:{message_id}): {e}", exc_info=True)
            return JSONResponse(status_code=500, content={"detail": "Database error"})
        if not gift_row or gift_row["is_comment_deleted"] or not (gift_row["comment"] or "").strip():
            return JSONResponse(status_code=400, content={"detail": "This gift cannot be reported"})
        
    try:
        await create_report(
            db=db,
            user_ip=get_ip(request),
            target_type="message",
            target_id=message_id,
            category=report_data.category,
            user_id=user.id if user else None,
            text=report_data.text
        )
        return {"success": True, "message": "Report submitted successfully"}
    except ValueError as e: # クールダウン中などのエラー
        return JSONResponse(status_code=429, content={"detail": str(e)})
    except DataBaseError as e:
        logger.error(f"メッセージ(ID:{message_id})の通報中にDBエラー: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"detail": "Database error"})

def _reaction_error(status_code: int, code: str, detail: str) -> JSONResponse:
    """リアクションのエラー。画面は code で文言を出し分ける。"""
    return JSONResponse(status_code=status_code, content={"detail": detail, "code": code})


@router.post("/messages/{message_id}/reactions", name="add_message_reaction")
async def add_message_reaction(
    request: Request,
    message_id: int,
    reaction_data: ReactionCreateRequest,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    """メッセージにリアクションを追加する（複数の種類を付けられる。1メッセージの種類数には上限がある）。
    """
    user: User | None = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    emoji = reaction_rules.normalize_emoji(reaction_data.emoji)
    if not emoji:
        return _reaction_error(400, "invalid_emoji", "This reaction is not available")
    if not await reaction_rules.consume_rate_limit(user.id):
        return _reaction_error(429, "rate_limited", "Too many reactions")

    # メッセージの存在確認
    message = await get_message(db, id=message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    if message.message_type not in ("message", TOKEN_GIFT_MESSAGE_TYPE, "warning"):
        raise HTTPException(status_code=400, detail="Reactions are not allowed on this message")

    try:
        if not await reaction_rules.has_room_for(db, message_id, emoji):
            return _reaction_error(400, "too_many_kinds", "This message has too many kinds of reactions")
        reaction_id = await add_reaction(db, message_id, user.id, emoji)

        # 新しいリアクションをブロードキャスト
        # リアクション情報をDBから取得
        row = await db.fetchrow("SELECT * FROM reactions WHERE id = $1", reaction_id)
        reaction_dict = None
        if row:
            new_reaction = await Reaction.from_db(row, db)
            reaction_dict = new_reaction.to_dict()
            await manager.broadcast(
                message.thread_id,
                {"type": "new_reaction", "data": reaction_dict}
            )
            await handle_message_reaction_notification(
                db,
                message_id=message_id,
                actor_user_id=user.id,
                reaction_id=reaction_id,
                is_added=True,
            )

        return {"success": True, "reaction_id": reaction_id, "reaction": reaction_dict}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except asyncpg.UniqueViolationError:
        # 連打・競合による想定内の重複。ERRORログも例外ハンドラも経由させない
        return JSONResponse(
            status_code=409,
            content={"detail": "You have already reacted with this emoji"},
        )
    except (DataBaseError, asyncpg.PostgresError) as e:
        logger.error(f"リアクション追加中(メッセージ:{message_id})にDBエラー: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error")


async def _delete_reaction_and_broadcast(db: asyncpg.Connection, reaction: Reaction) -> None:
    """リアクションを削除して配信する。同じ人がほかの絵文字も付けている間は、相手への通知を残す。"""
    await keep_reaction_notification_on_remove(db, reaction_id=reaction.id, message_id=reaction.message_id, actor_user_id=reaction.user_id)
    await reaction.delete(db)
    thread_row = await db.fetchrow("SELECT thread_id FROM messages WHERE id = $1", reaction.message_id)
    if thread_row:
        await manager.broadcast(
            thread_row['thread_id'],
            {
                "type": "delete_reaction",
                "data": {
                    "reaction_id": reaction.id,
                    "message_id": reaction.message_id
                }
            }
        )


@router.delete("/messages/{message_id}/reactions", name="delete_my_message_reaction")
async def delete_my_message_reaction(
    request: Request,
    message_id: int,
    emoji: str = Query(..., max_length=32),
    db: asyncpg.Connection = Depends(get_shared_db)
):
    """自分が付けたリアクションを絵文字で指定して外す（付いていなければ何もしない）。
    """
    user: User | None = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    normalized = reaction_rules.normalize_emoji(emoji) or emoji
    if not await reaction_rules.consume_rate_limit(user.id):
        return _reaction_error(429, "rate_limited", "Too many reactions")
    try:
        row = await db.fetchrow(
            "SELECT * FROM reactions WHERE message_id = $1 AND user_id = $2 AND emoji = $3",
            message_id, user.id, normalized,
        )
        if not row:
            return {"success": True, "deleted": False}
        await _delete_reaction_and_broadcast(db, await Reaction.from_db(row, db))
        return {"success": True, "deleted": True}
    except (DataBaseError, asyncpg.PostgresError) as e:
        logger.error(f"リアクションの削除中(メッセージ:{message_id})にDBエラー: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error")


@router.delete("/reactions/{reaction_id}", name="delete_reaction")
async def delete_reaction(
    request: Request,
    reaction_id: int,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    """リアクションを削除する。
    """
    user: User | None = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    # リアクション情報をDBから直接取得
    try:
        row = await db.fetchrow("SELECT * FROM reactions WHERE id = $1", reaction_id)
    except asyncpg.PostgresError as e:
        raise HTTPException(status_code=500, detail="Database error")
    if not row:
        raise HTTPException(status_code=404, detail="Reaction not found")
        
    reaction = await Reaction.from_db(row, db)

    # 権限チェック ---
    if not user.has_perm("chat.delete_messages") and reaction.user_id != user.id:
        raise HTTPException(status_code=403, detail="You do not have permission to delete this reaction")
    if reaction.user_id == user.id and not await reaction_rules.consume_rate_limit(user.id):
        return _reaction_error(429, "rate_limited", "Too many reactions")

    try:
        await _delete_reaction_and_broadcast(db, reaction)
        return {"success": True}
    except (DataBaseError, asyncpg.PostgresError) as e:
        logger.error(f"リアクション(ID:{reaction_id})の削除中にDBエラー: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error")


#* /---*---*---*---*---*---*---*---*/
#* 許可ID/禁止ID関連のエンドポイント
#* /---*---*---*---*---*---*---*---*/
@router.post("/posts/{post_id}/permitted-users", name="add_permitted_user")
async def add_permitted_user(
    request: Request,
    post_id: int,
    manage_data: UserManageRequest,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    """投稿の許可IDリストにユーザーを追加する"""
    user: User | None = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    post = await get_post(db, id=post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    # 権限チェック: 投稿主のみが操作可能 ---
    if post.host_id != user.id:
        raise HTTPException(status_code=403, detail="Only the post host can perform this action")

    await post.add_permitted_id(db, manage_data.user_id)
    return {"success": True}

@router.delete("/posts/{post_id}/permitted-users/{user_to_manage_id}", name="remove_permitted_user")
async def remove_permitted_user(
    request: Request,
    post_id: int,
    user_to_manage_id: int,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    """投稿の許可IDリストからユーザーを削除する"""
    user: User | None = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    post = await get_post(db, id=post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if post.host_id != user.id:
        raise HTTPException(status_code=403, detail="Only the post host can perform this action")

    await post.remove_permitted_id(db, user_to_manage_id)
    return {"success": True}

@router.post("/posts/{post_id}/prohibited-users", name="add_prohibited_user")
async def add_prohibited_user(
    request: Request,
    post_id: int,
    manage_data: UserManageRequest,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    """投稿の禁止IDリストにユーザーを追加する"""
    user: User | None = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    post = await get_post(db, id=post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if post.host_id != user.id:
        raise HTTPException(status_code=403, detail="Only the post host can perform this action")

    await post.add_prohibited_id(db, manage_data.user_id)
    return {"success": True}

@router.delete("/posts/{post_id}/prohibited-users/{user_to_manage_id}", name="remove_prohibited_user")
async def remove_prohibited_user(
    request: Request,
    post_id: int,
    user_to_manage_id: int,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    """投稿の禁止IDリストからユーザーを削除する"""
    user: User | None = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    post = await get_post(db, id=post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if post.host_id != user.id:
        raise HTTPException(status_code=403, detail="Only the post host can perform this action")

    await post.remove_prohibited_id(db, user_to_manage_id)
    return {"success": True}


#* /---*---*---*---*---*---*---*---*/
#* ブロック関連のエンドポイント
#* /---*---*---*---*---*---*---*---*/
class BlockUserRequest(BaseModel):
    blocked_user_id: int | None = None
    blocked_anonymous_id: str | None = None
    # ブロックのきっかけになった投稿 (ゲストをブロックした場合に、ブロック一覧での表示に使う)
    source_post_id: int | None = None

@router.post("/users/block", name="block_user")
async def block_user(
    request: Request,
    block_data: BlockUserRequest,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    """指定したユーザーをブロックする"""
    user: User | None = getattr(request.state, "current_user", None)
    
    try:
        success = await create_user_block(
            db,
            blocker_user_id=user.id if user else None,
            blocker_anonymous_id=request.cookies.get('brawlanonid') if not user else None,
            blocked_user_id=block_data.blocked_user_id,
            blocked_anonymous_id=block_data.blocked_anonymous_id,
            source_post_id=block_data.source_post_id,
        )
        return {"success": success}
    except (ValueError, DataBaseError) as e:
        logger.error(f"ブロック処理中にエラー: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/users/unblock", name="unblock_user")
async def unblock_user(
    request: Request,
    block_data: BlockUserRequest,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    """指定したユーザーのブロックを解除する"""
    user: User | None = getattr(request.state, "current_user", None)
    
    try:
        success = await delete_user_block(
            db,
            blocker_user_id=user.id if user else None,
            blocker_anonymous_id=request.cookies.get('brawlanonid') if not user else None,
            blocked_user_id=block_data.blocked_user_id,
            blocked_anonymous_id=block_data.blocked_anonymous_id
        )
        return {"success": success}
    except (ValueError, DataBaseError) as e:
        logger.error(f"ブロック解除処理中にエラー: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
