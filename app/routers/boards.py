import asyncio
import asyncpg
import json
from fastapi import APIRouter, Request, HTTPException, Depends, Query, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.websockets import WebSocketState
from pydantic import BaseModel, Field

from app.core.templating import censor_filter
from app.db.db import get_shared_db, get_db_connection_for_bg_task
from app.db import db as db_module
from app.core.logger import logger
from app.core.templating import templates
from app.core import cache as cache_module
from app.services.brawl_service import Player, get_player, get_player_from_db, get_brawler
from app.services.user_service import User, get_user, get_blocked_ids, create_user_block, delete_user_block
from app.services.board_service import get_post, get_posts, get_trending_general_posts, get_messages, get_reactions, check_post_permitted, check_invitation_link, create_post, get_last_post, create_report, create_message, get_message, add_reaction, Reaction, get_player_icon_from_db, get_general_post_vote_summary, toggle_general_post_up_vote, attach_reply_to_previews, TEAM_POST_CLOSE_COOLDOWN_SECONDS
from app.services.notification_service import (
    NOTIFICATION_PAGE_SIZE,
    VALID_NOTIFICATION_FILTERS,
    BRAWLER_GUIDE_PARTICIPATED_THREAD_NOTIFICATION_LIMIT,
    create_message_notifications,
    empty_board_notification_context,
    get_board_notification_context,
    get_notifications_for_display,
    handle_message_reaction_notification,
    handle_post_like_notification,
    mark_all_notifications_as_read,
)
from app.utils.utils import get_icon_path, get_remote_ip
from app.exceptions.custom_exceptions import BrawlStarsAPIError, DataBaseError

router = APIRouter(
    prefix="/{lang}/boards",
    tags=["Boards"]
)

CHAT_MESSAGES_INITIAL_LOAD = 100
CHAT_MESSAGES_OLDER_LOAD = 200


#* /---*---*---*---*---*---*---*---*/
#* ヘルパー関数
#* /---*---*---*---*---*---*---*---*/
async def _append_board_notification_context(
    context: dict,
    db: asyncpg.Connection,
    user: User | None,
) -> None:
    # ミドルウェアで注入済みなら再取得しない（全ページ共通のナビバッジ用）
    request = context.get("request")
    if request is not None:
        existing = getattr(request.state, "board_notification_context", None)
        if isinstance(existing, dict):
            context.update(existing)
            return

    if not user:
        context.update(empty_board_notification_context())
        return
    context.update(await get_board_notification_context(db, user.id))


async def _attach_fragment_notification_badge(
    context: dict,
    db: asyncpg.Connection,
    user: User | None,
    *,
    page: int,
) -> None:
    """投稿 fragment（page=1）に通知バッジ情報を載せる。

    ミドルウェアは /fragment をバッジ取得から除外しているため、既存の DB 接続を再利用する。
    コンテキストプロセッサが show_notification_badge を空で上書きするので、別キーを使う。
    """
    if page > 1:
        return

    badge = empty_board_notification_context()
    if user:
        try:
            badge = await get_board_notification_context(db, user.id)
        except Exception as e:
            logger.error(
                f"掲示板 fragment での通知バッジ取得中にエラー (User: {user.name}): {e}",
                exc_info=True,
            )

    context["fragment_show_notification_badge"] = bool(badge.get("show_notification_badge"))
    context["fragment_notification_badge_text"] = badge.get("notification_badge_text") or ""


def get_ip(request: Request) -> str:
    return get_remote_ip(request)


async def _fetch_chat_messages_payload(
    db: asyncpg.Connection,
    *,
    thread_id: int,
    blocked_user_ids: list[int],
    before_message_id: int | None = None,
    after_message_id: int | None = None,
    per_page: int = CHAT_MESSAGES_OLDER_LOAD,
) -> tuple[list[dict], bool, bool]:
    """チャット表示用のメッセージとリアクションを取得する。

    Returns:
        payload, has_more_older, has_more_newer
    """
    fetch_kwargs: dict = {
        "db": db,
        "per_page": per_page,
        "thread_id": thread_id,
    }
    if before_message_id is not None:
        fetch_kwargs["before_message_id"] = before_message_id
    if after_message_id is not None:
        fetch_kwargs["after_message_id"] = after_message_id

    messages_data, _ = await get_messages(**fetch_kwargs)
    # get_messages は常に新しい順で返すため、表示用に古い→新しいへ反転
    messages_data.reverse()

    await attach_reply_to_previews(db, messages_data)

    payload: list[dict] = []
    for message in messages_data:
        if message.is_deleted or message.user_id in blocked_user_ids:
            continue
        reactions, _ = await get_reactions(db, per_page=1000, message_id=message.id)
        message_dict = _censor_message_dict(message.to_dict())
        payload.append({"data": message_dict, "reactions": reactions})

    has_more_older = False
    has_more_newer = False
    if messages_data:
        oldest_id = messages_data[0].id
        newest_id = messages_data[-1].id
        older_exists = await db.fetchrow(
            "SELECT 1 FROM messages WHERE thread_id = $1 AND is_deleted = FALSE AND id < $2 LIMIT 1",
            thread_id,
            oldest_id,
        )
        newer_exists = await db.fetchrow(
            "SELECT 1 FROM messages WHERE thread_id = $1 AND is_deleted = FALSE AND id > $2 LIMIT 1",
            thread_id,
            newest_id,
        )
        has_more_older = older_exists is not None
        has_more_newer = newer_exists is not None

    return payload, has_more_older, has_more_newer


def _censor_message_dict(message_dict: dict) -> dict:
    message_dict["message"] = censor_filter(message_dict.get("message") or "")
    reply_to = message_dict.get("reply_to")
    if reply_to and not reply_to.get("is_deleted") and reply_to.get("message") is not None:
        reply_to = dict(reply_to)
        reply_to["message"] = censor_filter(reply_to["message"])
        message_dict["reply_to"] = reply_to
    return message_dict


async def _fetch_chat_messages_around(
    db: asyncpg.Connection,
    *,
    thread_id: int,
    blocked_user_ids: list[int],
    around_message_id: int,
    per_page: int = CHAT_MESSAGES_INITIAL_LOAD,
) -> tuple[list[dict], bool, bool]:
    """指定メッセージを中心にしたウィンドウを取得する。"""
    half = max(per_page // 2, 1)

    older_desc, _ = await get_messages(
        db,
        per_page=half,
        thread_id=thread_id,
        before_message_id=around_message_id,
    )
    older_asc = list(reversed(older_desc))

    focus = await get_message(db, around_message_id, include_deleted_message=True)
    focus_list = []
    if focus and focus.thread_id == thread_id and not focus.is_deleted and focus.user_id not in blocked_user_ids:
        focus_list = [focus]
    elif focus and focus.thread_id == thread_id and not focus.is_deleted:
        # ブロックユーザーのメッセージでも、周辺ウィンドウ自体は返す（フォーカス対象はスキップされうる）
        focus_list = [focus]

    newer_desc, _ = await get_messages(
        db,
        per_page=max(per_page - len(older_asc) - 1, 1),
        thread_id=thread_id,
        after_message_id=around_message_id,
    )
    newer_asc = list(reversed(newer_desc))

    messages_data = older_asc + focus_list + newer_asc
    # 重複除去（念のため）
    seen: set[int] = set()
    unique_messages = []
    for message in messages_data:
        if message.id in seen:
            continue
        seen.add(message.id)
        unique_messages.append(message)
    messages_data = unique_messages

    await attach_reply_to_previews(db, messages_data)

    payload: list[dict] = []
    for message in messages_data:
        if message.is_deleted or message.user_id in blocked_user_ids:
            continue
        reactions, _ = await get_reactions(db, per_page=1000, message_id=message.id)
        message_dict = _censor_message_dict(message.to_dict())
        payload.append({"data": message_dict, "reactions": reactions})

    has_more_older = False
    has_more_newer = False
    if messages_data:
        oldest_id = messages_data[0].id
        newest_id = messages_data[-1].id
        older_exists = await db.fetchrow(
            "SELECT 1 FROM messages WHERE thread_id = $1 AND is_deleted = FALSE AND id < $2 LIMIT 1",
            thread_id,
            oldest_id,
        )
        newer_exists = await db.fetchrow(
            "SELECT 1 FROM messages WHERE thread_id = $1 AND is_deleted = FALSE AND id > $2 LIMIT 1",
            thread_id,
            newest_id,
        )
        has_more_older = older_exists is not None
        has_more_newer = newer_exists is not None

    return payload, has_more_older, has_more_newer


BOARD_POST_LIMIT_QUERY = Query(60, ge=1, le=100, description="1回あたりの投稿表示数")
BOARD_PAGE_QUERY = Query(1, ge=1, description="取得ページ")


def _board_has_more(page: int, limit: int, fetched_count: int, total: int) -> bool:
    if fetched_count <= 0:
        return False
    return (page - 1) * limit + fetched_count < total


def _board_pagination_context(page: int, limit: int, fetched_count: int, total: int) -> dict:
    return {
        "page": page,
        "has_more": _board_has_more(page, limit, fetched_count, total),
        "total_posts": total,
        "append_mode": page > 1,
    }


#* /---*---*---*---*---*---*---*---*/
#* WebSocket接続管理
#* /---*---*---*---*---*---*---*---*/
class ConnectionManager:
    def __init__(self):
        # 各スレッドIDごとに、接続中のWebSocketオブジェクトを管理する
        # {thread_id: {websocket_object, ...}}
        self.active_connections: dict[int, set[WebSocket]] = {}
        # 各WebSocketごとに、Redisリスナーのタスクを管理する
        self.listener_tasks: dict[WebSocket, asyncio.Task] = {}

    async def connect(self, websocket: WebSocket, thread_id: int):
        """クライアントを接続し、Redisのリスナーを開始する"""
        if websocket.client_state == WebSocketState.CONNECTING:
            await websocket.accept()
        if thread_id not in self.active_connections:
            self.active_connections[thread_id] = set()
        self.active_connections[thread_id].add(websocket)

        # この接続専用のRedisリスナータスクを作成して開始
        task = asyncio.create_task(self._redis_listener(websocket, thread_id))
        self.listener_tasks[websocket] = task
        logger.debug(f"Redisリスナータスクを開始しました (スレッド: {thread_id})")

    def disconnect(self, websocket: WebSocket, thread_id: int):
        """クライアントを切断し、Redisのリスナーを停止する"""
        if thread_id in self.active_connections and websocket in self.active_connections[thread_id]:
            self.active_connections[thread_id].remove(websocket)
            if not self.active_connections[thread_id]:
                del self.active_connections[thread_id]

        # この接続のリスナータスクをキャンセルして削除
        task = self.listener_tasks.pop(websocket, None)
        if task:
            task.cancel()
            logger.debug(f"Redisリスナータスクを停止しました (スレッド: {thread_id})")

    async def broadcast(self, thread_id: int, message: dict):
        """メッセージをRedisチャネルに発行(Publish)する"""
        # cache_module.redis_pool を参照
        if cache_module.redis_pool is None:
            logger.error("Redis接続が利用できないため、ブロードキャストをスキップします。")
            return
        channel = f"chat_{thread_id}"
        await cache_module.redis_pool.publish(channel, json.dumps(message))

    async def _redis_listener(self, websocket: WebSocket, thread_id: int):
        """Redisチャネルを購読(Subscribe)し、メッセージをクライアントに送信する"""
        # cache_module.redis_pool を参照
        if cache_module.redis_pool is None:
            logger.error("Redis接続が利用できないため、リスナーを開始できません。")
            return
            
        channel_name = f"chat_{thread_id}"
        async with cache_module.redis_pool.pubsub() as pubsub:
            await pubsub.subscribe(channel_name)
            logger.debug(f"Redisチャネル '{channel_name}' の購読を開始しました。")
            try:
                while True:
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=None)
                    if message and message.get("type") == "message":
                        # Redisから受け取ったメッセージをWebSocketクライアントに送信
                        await websocket.send_text(message["data"].decode())
            except asyncio.CancelledError:
                # タスクがキャンセルされた場合は正常終了
                logger.debug(f"Redisリスナーが正常にキャンセルされました (スレッド: {thread_id})")
            except WebSocketDisconnect:
                # クライアント切断後にRedisメッセージを転送しようとした場合（サスペンド等）
                logger.debug(f"WebSocket接続が既に閉じられています (スレッド: {thread_id})")
            except RuntimeError as e:
                # WebSocketが既に閉じられている場合のエラーは無視する
                if (
                    "Unexpected ASGI message" in str(e)
                    or "websocket.close" in str(e)
                    or "WebSocket is not connected" in str(e)
                ):
                    logger.debug(f"WebSocket接続が既に閉じられています (スレッド: {thread_id})")
                else:
                    logger.error(f"Redisリスナーで予期せぬRuntimeError (スレッド: {thread_id}): {e}", exc_info=True)
            except Exception as e:
                logger.error(f"Redisリスナーで予期せぬエラー (スレッド: {thread_id}): {e}", exc_info=True)
            finally:
                logger.debug(f"Redisチャネル '{channel_name}' の購読を終了します。")

manager = ConnectionManager()


#* /---*---*---*---*---*---*---*---*/
#* Pydanticモデルの定義
#* /---*---*---*---*---*---*---*---*/
class LinkParseRequest(BaseModel):
    text: str
    type: str

class PostCreateRequest(BaseModel):
    type: str
    host_player_tag: str | None = None
    host_club_tag: str | None = None
    link: str | None = None
    comment: str | None = None
    region: str | None = None
    conditions_application_type: str = Field(default="and")
    chat_permission_level: int = Field(default=30)
    category: str | None = None
    mode: str | None = None
    hashtags: list[str] = Field(default_factory=list)
    custom_settings: dict = Field(default_factory=dict)
    permitted_ids: list[int] = Field(default_factory=list)
    prohibited_ids: list[int] = Field(default_factory=list)
    required_highest_trophies: int | None = None
    required_current_trophies: int | None = None
    required_ranked_highest_rank: int | None = None
    required_ranked_current_rank: int | None = None
    required_ranked_highest_score: int | None = None
    required_ranked_current_score: int | None = None
    required_solo_pl_rank: int | None = None
    required_max_power_brawlers: int | None = None
    required_prestige: int | None = None
    other_conditions: dict = Field(default_factory=dict)
    is_later_recruitment: bool = False
    
class ReportCreateRequest(BaseModel):
    category: str
    text: str | None = None

class MessageCreateRequest(BaseModel):
    message: str
    reply_to_message_id: int | None = None

class ReactionCreateRequest(BaseModel):
    emoji: str

class UserManageRequest(BaseModel):
    user_id: int


class GoodToggleResponse(BaseModel):
    success: bool
    up_vote_count: int
    is_up_voted_by_current_user: bool


#* /---*---*---*---*---*---*---*---*/
#* チーム募集タブ
#* /---*---*---*---*---*---*---*---*/
TEAM_BOARD_CATEGORIES = frozenset({"all", "trophy", "ranked", "friendly", "event"})


async def _fetch_team_board_posts(
    db: asyncpg.Connection,
    request: Request,
    *,
    page: int,
    limit: int,
    category: str,
    mode: str,
    filter: str,
    region: str,
    eliminate_duplicates: bool,
    only_joinable: bool = False,
) -> tuple[dict, dict, int]:
    """チーム募集掲示板の投稿一覧とブロックリストを取得する。"""
    user: User | None = getattr(request.state, "current_user", None)
    main_account: Player | None = None

    if user:
        try:
            main_account = await get_player_from_db(user.main_account, db)
        except BrawlStarsAPIError as e:
            logger.debug(f"{user.name}のメインアカウントのプレイヤーデータ取得中にAPIエラーが発生しました: {e}。スキップします。", exc_info=True)
        except Exception as e:
            logger.debug(f"{user.name}のメインアカウントのプレイヤーデータ取得中にその他のエラーが発生しました: {e}", exc_info=True)

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

    if category not in TEAM_BOARD_CATEGORIES:
        category = "all"

    # 旧フィルター値の互換: only_can_participate → only_joinable
    if filter == "only_can_participate":
        only_joinable = True
        filter = "all"

    needs_target_user = only_joinable or filter in ("only_participated_threads",)
    needs_target_player = only_joinable

    try:
        posts_data, total_posts = await get_posts(
            db,
            page=page,
            per_page=limit,
            type="team",
            region=None if region.lower() == "all" else region,
            target_user=user if needs_target_user else None,
            target_player=main_account if needs_target_player and main_account else None,
            category=None if category.lower() == "all" else category,
            mode=None if mode.lower() == "all" or category != "trophy" else mode,
            eliminate_duplicates=eliminate_duplicates,
            author_user_id=user.id if filter == "only_own_posts" and user else None,
            author_ip=get_ip(request) if filter == "only_own_posts" else None,
            filter=filter,
            only_joinable=only_joinable,
            viewer_ip=get_ip(request) if only_joinable else None,
        )
    except BrawlStarsAPIError:
        posts_data = []
        total_posts = 0
    except DataBaseError as e:
        logger.error(f"投稿取得中にデータベースエラー: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page") from e

    posts = {
        post.id: {
            "data": post.to_dict(),
            "is_permitted_to_chat": await post.is_permitted_to_chat(db, user_id=user.id if user else None),
            "is_permitted_to_click_link": await post.is_permitted_to_click_link(db, user_id=user.id if user else None),
        }
        for post in posts_data
    }

    return posts, blocked_ids, total_posts


@router.get("/team/fragment", name="team_recruitment_board_fragment")
async def team_recruitment_board_fragment(
    request: Request,
    lang: str,
    limit: int = BOARD_POST_LIMIT_QUERY,
    page: int = BOARD_PAGE_QUERY,
    category: str = Query("all", description="表示するカテゴリー"),
    mode: str = Query("all", description="表示するモード"),
    region: str = Query("all", description="表示する地域"),
    filter: str = Query("all", description="フィルター"),
    eliminate_duplicates: bool = Query(False, description="重複を排除するかどうか"),
    only_joinable: bool = Query(False, description="参加可能な募集のみ表示するかどうか"),
    db: asyncpg.Connection = Depends(get_shared_db),
):
    user: User | None = getattr(request.state, "current_user", None)
    main_account: Player | None = None

    if user:
        try:
            main_account = await get_player_from_db(user.main_account, db)
        except BrawlStarsAPIError as e:
            logger.debug(f"{user.name}のメインアカウントのプレイヤーデータ取得中にAPIエラーが発生しました: {e}。スキップします。", exc_info=True)
        except Exception as e:
            logger.debug(f"{user.name}のメインアカウントのプレイヤーデータ取得中にその他のエラーが発生しました: {e}", exc_info=True)

    # 旧フィルター値の互換
    if filter == "only_can_participate":
        only_joinable = True
        filter = "all"

    posts, blocked_ids, total_posts = await _fetch_team_board_posts(
        db,
        request,
        page=page,
        limit=limit,
        category=category,
        mode=mode,
        filter=filter,
        region=region,
        eliminate_duplicates=eliminate_duplicates,
        only_joinable=only_joinable,
    )

    fetched_count = len(posts)
    context = {
        "request": request,
        "lang": lang,
        "ip": get_ip(request),
        "limit": limit,
        "region": region,
        "category": category if category in TEAM_BOARD_CATEGORIES else "all",
        "mode": mode,
        "filter": filter,
        "eliminate_duplicates": eliminate_duplicates,
        "only_joinable": only_joinable,
        "main_account": main_account,
        "posts": posts,
        "blocked_ids": blocked_ids,
        "team_post_close_cooldown_seconds": TEAM_POST_CLOSE_COOLDOWN_SECONDS,
        **_board_pagination_context(page, limit, fetched_count, total_posts),
    }
    await _attach_fragment_notification_badge(context, db, user, page=page)

    template_name = (
        "recruitment_board/fragments/team_posts_append.html"
        if page > 1
        else "recruitment_board/fragments/team_posts_fragment.html"
    )

    try:
        return templates.TemplateResponse(template_name, context)
    except Exception as render_err:
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page") from render_err


@router.get("/team", name="team_recruitment_board")
async def team_recruitment_board(
    request: Request,
    lang: str,
    limit: int = Query(60, ge=1, le=100, description="1回あたりの投稿表示数"),
    category: str = Query("all", description="表示するカテゴリー"),
    mode: str = Query("all", description="表示するモード"),
    region: str = Query("all", description="表示する地域"),
    filter: str = Query("all", description="only_participated_threads / only_own_posts"),
    eliminate_duplicates: bool = Query(False, description="重複を排除するかどうか"),
    only_joinable: bool = Query(False, description="参加可能な募集のみ表示するかどうか"),
    db: asyncpg.Connection = Depends(get_shared_db)
):
    user: User | None = getattr(request.state, "current_user", None)
    main_account: Player | None = None

    if user:
        try:
            main_account = await get_player_from_db(user.main_account, db)
        except BrawlStarsAPIError as e:
            logger.debug(f"{user.name}のメインアカウントのプレイヤーデータ取得中にAPIエラーが発生しました: {e}。スキップします。", exc_info=True)
        except Exception as e:
            logger.debug(f"{user.name}のメインアカウントのプレイヤーデータ取得中にその他のエラーが発生しました: {e}", exc_info=True)

    is_permitted_to_post, cooldown_seconds = await check_post_permitted(db, "team", ip=get_ip(request), user_id=user.id if user else None)

    if category not in TEAM_BOARD_CATEGORIES:
        category = "all"

    # 旧フィルター値の互換
    if filter == "only_can_participate":
        only_joinable = True
        filter = "all"

    context = {
        "request": request,
        "lang": lang,
        "ip": get_ip(request),
        "limit": limit,
        "region": region,
        "category": category,
        "mode": mode,
        "filter": filter,
        "eliminate_duplicates": eliminate_duplicates,
        "only_joinable": only_joinable,
        "main_account": main_account,
        "is_permitted_to_post": is_permitted_to_post,
        "cooldown_seconds": int(cooldown_seconds),
        "current_page": "board",
    }
    await _append_board_notification_context(context, db, user)

    try:
        return templates.TemplateResponse("recruitment_board/team.html", context)
    except Exception as render_err:
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page") from render_err


#* /---*---*---*---*---*---*---*---*/
#* フレンド募集タブ / クラブ募集タブ
#* /---*---*---*---*---*---*---*---*/


async def _fetch_recruitment_board_posts(
    db: asyncpg.Connection,
    request: Request,
    *,
    post_type: str,
    page: int,
    limit: int,
    filter: str,
    region: str,
    eliminate_duplicates: bool,
    only_joinable: bool = False,
) -> tuple[dict, dict, int]:
    """フレンド/クラブ募集掲示板の投稿一覧とブロックリストを取得する。"""
    user: User | None = getattr(request.state, "current_user", None)
    main_account: Player | None = None

    if user:
        try:
            main_account = await get_player_from_db(user.main_account, db)
        except BrawlStarsAPIError as e:
            logger.debug(f"{user.name}のメインアカウントのプレイヤーデータ取得中にAPIエラーが発生しました: {e}。スキップします。", exc_info=True)
        except Exception as e:
            logger.debug(f"{user.name}のメインアカウントのプレイヤーデータ取得中にその他のエラーが発生しました: {e}", exc_info=True)

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

    # 旧フィルター値の互換: only_can_participate → only_joinable
    if filter == "only_can_participate":
        only_joinable = True
        filter = "all"

    needs_target_user = only_joinable or filter in ("only_participated_threads",)
    needs_target_player = only_joinable

    try:
        posts_data, total_posts = await get_posts(
            db,
            page=page,
            per_page=limit,
            type=post_type,
            region=None if region.lower() == "all" else region,
            target_user=user if needs_target_user else None,
            target_player=main_account if needs_target_player and main_account else None,
            eliminate_duplicates=eliminate_duplicates,
            author_user_id=user.id if filter == "only_own_posts" and user else None,
            author_ip=get_ip(request) if filter == "only_own_posts" else None,
            filter=filter,
            only_joinable=only_joinable,
            viewer_ip=get_ip(request) if only_joinable else None,
        )
    except BrawlStarsAPIError:
        posts_data = []
        total_posts = 0
    except DataBaseError as e:
        logger.error(f"投稿取得中にデータベースエラー: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page") from e

    posts = {
        post.id: {
            "data": post.to_dict(),
            "is_permitted_to_chat": await post.is_permitted_to_chat(db, user_id=user.id if user else None),
            "is_permitted_to_click_link": await post.is_permitted_to_click_link(db, user_id=user.id if user else None),
        }
        for post in posts_data
    }

    return posts, blocked_ids, total_posts


@router.get("/friend/fragment", name="friend_recruitment_board_fragment")
async def friend_recruitment_board_fragment(
    request: Request,
    lang: str,
    limit: int = BOARD_POST_LIMIT_QUERY,
    page: int = BOARD_PAGE_QUERY,
    region: str = Query("all", description="表示する地域"),
    filter: str = Query("all", description="フィルター"),
    eliminate_duplicates: bool = Query(True, description="重複を排除するかどうか"),
    only_joinable: bool = Query(False, description="申請可能な募集のみ表示するかどうか"),
    db: asyncpg.Connection = Depends(get_shared_db),
):
    user: User | None = getattr(request.state, "current_user", None)
    main_account: Player | None = None

    if user:
        try:
            main_account = await get_player_from_db(user.main_account, db)
        except BrawlStarsAPIError as e:
            logger.debug(f"{user.name}のメインアカウントのプレイヤーデータ取得中にAPIエラーが発生しました: {e}。スキップします。", exc_info=True)
        except Exception as e:
            logger.debug(f"{user.name}のメインアカウントのプレイヤーデータ取得中にその他のエラーが発生しました: {e}", exc_info=True)

    # 旧フィルター値の互換
    if filter == "only_can_participate":
        only_joinable = True
        filter = "all"

    posts, blocked_ids, total_posts = await _fetch_recruitment_board_posts(
        db,
        request,
        post_type="friend",
        page=page,
        limit=limit,
        filter=filter,
        region=region,
        eliminate_duplicates=eliminate_duplicates,
        only_joinable=only_joinable,
    )

    fetched_count = len(posts)
    context = {
        "request": request,
        "lang": lang,
        "ip": get_ip(request),
        "limit": limit,
        "region": region,
        "filter": filter,
        "eliminate_duplicates": eliminate_duplicates,
        "only_joinable": only_joinable,
        "main_account": main_account,
        "posts": posts,
        "blocked_ids": blocked_ids,
        **_board_pagination_context(page, limit, fetched_count, total_posts),
    }
    await _attach_fragment_notification_badge(context, db, user, page=page)

    template_name = (
        "recruitment_board/fragments/friend_posts_append.html"
        if page > 1
        else "recruitment_board/fragments/friend_posts_fragment.html"
    )

    try:
        return templates.TemplateResponse(template_name, context)
    except Exception as render_err:
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page") from render_err


@router.get("/friend", name="friend_recruitment_board")
async def friend_recruitment_board(
    request: Request,
    lang: str,
    limit: int = Query(60, ge=1, le=100, description="1回あたりの投稿表示数"),
    region: str = Query("all", description="表示する地域"),
    filter: str = Query("all", description="only_participated_threads / only_own_posts"),
    eliminate_duplicates: bool = Query(True, description="重複を排除するかどうか"),
    only_joinable: bool = Query(False, description="申請可能な募集のみ表示するかどうか"),
    db: asyncpg.Connection = Depends(get_shared_db)
):
    user: User | None = getattr(request.state, "current_user", None)
    main_account: Player | None = None

    if user:
        try:
            main_account = await get_player_from_db(user.main_account, db)
        except BrawlStarsAPIError as e:
            logger.debug(f"{user.name}のメインアカウントのプレイヤーデータ取得中にAPIエラーが発生しました: {e}。スキップします。", exc_info=True)
        except Exception as e:
            logger.debug(f"{user.name}のメインアカウントのプレイヤーデータ取得中にその他のエラーが発生しました: {e}", exc_info=True)

    # 旧フィルター値の互換
    if filter == "only_can_participate":
        only_joinable = True
        filter = "all"

    is_permitted_to_post, cooldown_seconds = await check_post_permitted(db, "friend", ip=get_ip(request), user_id=user.id if user else None)

    context = {
        "request": request,
        "lang": lang,
        "ip": get_ip(request),
        "limit": limit,
        "region": region,
        "filter": filter,
        "eliminate_duplicates": eliminate_duplicates,
        "only_joinable": only_joinable,
        "main_account": main_account,
        "is_permitted_to_post": is_permitted_to_post,
        "cooldown_seconds": int(cooldown_seconds),
        "current_page": "board",
    }
    await _append_board_notification_context(context, db, user)

    try:
        return templates.TemplateResponse("recruitment_board/friend.html", context)
    except Exception as render_err:
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page") from render_err


@router.get("/club/fragment", name="club_recruitment_board_fragment")
async def club_recruitment_board_fragment(
    request: Request,
    lang: str,
    limit: int = BOARD_POST_LIMIT_QUERY,
    page: int = BOARD_PAGE_QUERY,
    region: str = Query("all", description="表示する地域"),
    filter: str = Query("all", description="フィルター"),
    eliminate_duplicates: bool = Query(True, description="重複を排除するかどうか"),
    only_joinable: bool = Query(False, description="参加可能な募集のみ表示するかどうか"),
    db: asyncpg.Connection = Depends(get_shared_db),
):
    user: User | None = getattr(request.state, "current_user", None)
    main_account: Player | None = None

    if user:
        try:
            main_account = await get_player_from_db(user.main_account, db)
        except BrawlStarsAPIError as e:
            logger.debug(f"{user.name}のメインアカウントのプレイヤーデータ取得中にAPIエラーが発生しました: {e}。スキップします。", exc_info=True)
        except Exception as e:
            logger.debug(f"{user.name}のメインアカウントのプレイヤーデータ取得中にその他のエラーが発生しました: {e}", exc_info=True)

    # 旧フィルター値の互換
    if filter == "only_can_participate":
        only_joinable = True
        filter = "all"

    posts, blocked_ids, total_posts = await _fetch_recruitment_board_posts(
        db,
        request,
        post_type="club",
        page=page,
        limit=limit,
        filter=filter,
        region=region,
        eliminate_duplicates=eliminate_duplicates,
        only_joinable=only_joinable,
    )

    fetched_count = len(posts)
    context = {
        "request": request,
        "lang": lang,
        "ip": get_ip(request),
        "limit": limit,
        "region": region,
        "filter": filter,
        "eliminate_duplicates": eliminate_duplicates,
        "only_joinable": only_joinable,
        "main_account": main_account,
        "posts": posts,
        "blocked_ids": blocked_ids,
        **_board_pagination_context(page, limit, fetched_count, total_posts),
    }
    await _attach_fragment_notification_badge(context, db, user, page=page)

    template_name = (
        "recruitment_board/fragments/club_posts_append.html"
        if page > 1
        else "recruitment_board/fragments/club_posts_fragment.html"
    )

    try:
        return templates.TemplateResponse(template_name, context)
    except Exception as render_err:
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page") from render_err


@router.get("/club", name="club_recruitment_board")
async def club_recruitment_board(
    request: Request,
    lang: str,
    limit: int = Query(60, ge=1, le=100, description="1回あたりの投稿表示数"),
    region: str = Query("all", description="表示する地域"),
    filter: str = Query("all", description="only_participated_threads / only_own_posts"),
    eliminate_duplicates: bool = Query(True, description="重複を排除するかどうか"),
    only_joinable: bool = Query(False, description="参加可能な募集のみ表示するかどうか"),
    db: asyncpg.Connection = Depends(get_shared_db)
):
    user: User | None = getattr(request.state, "current_user", None)
    main_account: Player | None = None

    if user:
        try:
            main_account = await get_player_from_db(user.main_account, db)
        except BrawlStarsAPIError as e:
            logger.debug(f"{user.name}のメインアカウントのプレイヤーデータ取得中にAPIエラーが発生しました: {e}。スキップします。", exc_info=True)
        except Exception as e:
            logger.debug(f"{user.name}のメインアカウントのプレイヤーデータ取得中にその他のエラーが発生しました: {e}", exc_info=True)

    # 旧フィルター値の互換
    if filter == "only_can_participate":
        only_joinable = True
        filter = "all"

    is_permitted_to_post, cooldown_seconds = await check_post_permitted(db, "club", ip=get_ip(request), user_id=user.id if user else None)

    context = {
        "request": request,
        "lang": lang,
        "ip": get_ip(request),
        "limit": limit,
        "region": region,
        "filter": filter,
        "eliminate_duplicates": eliminate_duplicates,
        "only_joinable": only_joinable,
        "main_account": main_account,
        "is_permitted_to_post": is_permitted_to_post,
        "cooldown_seconds": int(cooldown_seconds),
        "current_page": "board",
    }
    await _append_board_notification_context(context, db, user)

    try:
        return templates.TemplateResponse("recruitment_board/club.html", context)
    except Exception as render_err:
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page") from render_err


#* /---*---*---*---*---*---*---*---*/
#* なんでも掲示板タブ
#* /---*---*---*---*---*---*---*---*/
GENERAL_BOARD_CATEGORY_FILTERS = frozenset({
    "chat", "question", "offtopic", "brawl_info", "x", "discord", "youtube", "tiktok",
})
GENERAL_BOARD_ALL_FILTERS = frozenset({"all", "all_except_offtopic"})
GENERAL_BOARD_DEFAULT_FILTER = "all_except_offtopic"
GENERAL_BOARD_TABS = frozenset({"latest", "trending", "own", "participated", "liked"})
LEGACY_GENERAL_TAB_FILTERS = {"trending": "trending", "only_own_posts": "own", "only_liked_posts": "liked"}


def _normalize_general_board_query(
    filter: str,
    tab: str,
) -> tuple[str, str, str | None, str | None]:
    """なんでも掲示板の tab / filter クエリを正規化する。

    Returns:
        (tab, filter, category_filter, exclude_category)
    """
    if filter in LEGACY_GENERAL_TAB_FILTERS:
        tab = LEGACY_GENERAL_TAB_FILTERS[filter]
        filter = "all"
    if tab not in GENERAL_BOARD_TABS:
        tab = "latest"

    category_filter = filter if filter in GENERAL_BOARD_CATEGORY_FILTERS else None
    exclude_category: str | None = None
    if filter == "all_except_offtopic":
        exclude_category = "offtopic"
    elif filter not in GENERAL_BOARD_CATEGORY_FILTERS and filter not in GENERAL_BOARD_ALL_FILTERS:
        filter = GENERAL_BOARD_DEFAULT_FILTER
        exclude_category = "offtopic"

    return tab, filter, category_filter, exclude_category


async def _fetch_general_board_posts(
    db: asyncpg.Connection,
    request: Request,
    *,
    page: int,
    limit: int,
    tab: str,
    filter: str,
    region: str,
    eliminate_duplicates: bool,
) -> tuple[dict, dict, int]:
    """なんでも掲示板の投稿一覧とブロックリストを取得する。"""
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

    tab, filter, category_filter, exclude_category = _normalize_general_board_query(filter, tab)

    try:
        if tab == "trending":
            posts_data, total_posts = await get_trending_general_posts(
                db,
                per_page=limit,
                page=page,
                region=None if region.lower() == "all" else region,
                category=category_filter,
                exclude_category=exclude_category,
            )
        else:
            posts_filter = None
            target_user_for_posts = None
            author_user_id = None
            author_ip = None

            if tab == "own":
                author_user_id = user.id if user else None
                author_ip = get_ip(request)
            elif tab == "participated":
                posts_filter = "only_participated_threads"
                target_user_for_posts = user
            elif tab == "liked":
                posts_filter = "only_liked_posts"
                target_user_for_posts = user

            posts_data, total_posts = await get_posts(
                db,
                page=page,
                per_page=limit,
                type="general",
                region=None if region.lower() == "all" else region,
                target_user=target_user_for_posts,
                target_player=None,
                category=category_filter,
                exclude_category=exclude_category,
                eliminate_duplicates=eliminate_duplicates,
                author_user_id=author_user_id,
                author_ip=author_ip,
                filter=posts_filter,
            )
    except BrawlStarsAPIError:
        posts_data = []
        total_posts = 0
    except DataBaseError as e:
        logger.error(f"投稿取得中にデータベースエラー: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page") from e

    vote_summary = await get_general_post_vote_summary(
        db,
        [post.id for post in posts_data],
        user_id=user.id if user else None,
    )

    for post in posts_data:
        summary = vote_summary.get(post.id)
        if summary:
            post.up_vote_count = int(summary.get("up_vote_count", 0))
            post.down_vote_count = int(summary.get("down_vote_count", 0))
            post.is_up_voted_by_current_user = bool(summary.get("is_up_voted_by_current_user", False))

    posts = {
        post.id: {
            "data": post.to_dict(),
            "is_permitted_to_chat": await post.is_permitted_to_chat(db, user_id=user.id if user else None),
            "is_permitted_to_click_link": await post.is_permitted_to_click_link(db, user_id=user.id if user else None),
        }
        for post in posts_data
    }

    return posts, blocked_ids, total_posts


@router.get("/general/fragment", name="general_board_fragment")
async def general_board_fragment(
    request: Request,
    lang: str,
    limit: int = BOARD_POST_LIMIT_QUERY,
    page: int = BOARD_PAGE_QUERY,
    tab: str = Query("latest", description="表示タブ(latest/trending/own/participated/liked)"),
    filter: str = Query(GENERAL_BOARD_DEFAULT_FILTER, description="投稿タイプのカテゴリーフィルター"),
    region: str = Query("all", description="表示する地域"),
    eliminate_duplicates: bool = Query(False, description="重複を排除するかどうか"),
    db: asyncpg.Connection = Depends(get_shared_db),
):
    user: User | None = getattr(request.state, "current_user", None)
    main_account: Player | None = None

    if user:
        try:
            main_account = await get_player_from_db(user.main_account, db)
        except BrawlStarsAPIError as e:
            logger.debug(f"{user.name}のメインアカウントのプレイヤーデータ取得中にAPIエラーが発生しました: {e}。スキップします。", exc_info=True)
        except Exception as e:
            logger.debug(f"{user.name}のメインアカウントのプレイヤーデータ取得中にその他のエラーが発生しました: {e}", exc_info=True)

    tab, filter, _, _ = _normalize_general_board_query(filter, tab)
    posts, blocked_ids, total_posts = await _fetch_general_board_posts(
        db,
        request,
        page=page,
        limit=limit,
        tab=tab,
        filter=filter,
        region=region,
        eliminate_duplicates=eliminate_duplicates,
    )

    fetched_count = len(posts)
    context = {
        "request": request,
        "lang": lang,
        "ip": get_ip(request),
        "limit": limit,
        "region": region,
        "tab": tab,
        "filter": filter,
        "eliminate_duplicates": eliminate_duplicates,
        "main_account": main_account,
        "posts": posts,
        "blocked_ids": blocked_ids,
        **_board_pagination_context(page, limit, fetched_count, total_posts),
    }
    await _attach_fragment_notification_badge(context, db, user, page=page)

    template_name = (
        "recruitment_board/fragments/general_posts_append.html"
        if page > 1
        else "recruitment_board/fragments/general_posts_fragment.html"
    )

    try:
        return templates.TemplateResponse(template_name, context)
    except Exception as render_err:
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page") from render_err


@router.get("/general", name="general_board")
async def general_board(
    request: Request,
    lang: str,
    limit: int = Query(60, ge=1, le=100, description="1回あたりの投稿表示数"),
    tab: str = Query("latest", description="表示タブ(latest/trending/own/participated/liked)"),
    filter: str = Query(GENERAL_BOARD_DEFAULT_FILTER, description="投稿タイプのカテゴリーフィルター"),
    region: str = Query("all", description="表示する地域"),
    eliminate_duplicates: bool = Query(False, description="重複を排除するかどうか"),
    db: asyncpg.Connection = Depends(get_shared_db)
):
    # ユーザー情報とメインアカウント情報を取得
    user: User | None = getattr(request.state, "current_user", None)
    main_account: Player | None = None

    if user:
        try:
            main_account = await get_player_from_db(user.main_account, db)
        except BrawlStarsAPIError as e:
            logger.debug(f"{user.name}のメインアカウントのプレイヤーデータ取得中にAPIエラーが発生しました: {e}。スキップします。", exc_info=True)
        except Exception as e:
            logger.debug(f"{user.name}のメインアカウントのプレイヤーデータ取得中にその他のエラーが発生しました: {e}", exc_info=True)

    # 投稿が許可されているか確認
    is_permitted_to_post, cooldown_seconds = await check_post_permitted(db, "general", ip = get_ip(request), user_id = user.id if user else None)

    tab, filter, _, _ = _normalize_general_board_query(filter, tab)

    # テンプレートに渡すコンテキスト（投稿一覧はフラグメントで遅延読み込み）
    context = {
        "request": request,
        "lang": lang,
        "ip": get_ip(request),
        "limit": limit,
        "region": region,
        "tab": tab,
        "filter": filter,
        "eliminate_duplicates": eliminate_duplicates,
        "main_account": main_account,
        "is_permitted_to_post": is_permitted_to_post,
        "cooldown_seconds": int(cooldown_seconds),
        "current_page": "board"
    }
    await _append_board_notification_context(context, db, user)

    try:
        return templates.TemplateResponse("recruitment_board/general.html", context)
    except Exception as render_err:
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")


#* /---*---*---*---*---*---*---*---*/
#* チャット画面
#* /---*---*---*---*---*---*---*---*/
@router.get("/chat/{thread_id}", name="chat_thread")
async def chat_thread(
    request: Request,
    lang: str,
    thread_id: int,
    message_id: int | None = Query(None, ge=1, description="フォーカスするメッセージID"),
    db: asyncpg.Connection = Depends(get_shared_db)
):
    # 投稿情報を取得
    post = await get_post(db, id=thread_id, include_deleted_post=True)
    if not post: # 存在しないスレッドIDが指定された場合
        return templates.TemplateResponse(
            "error/generic_404.html", # 汎用404エラーページ
            {"request": request, "lang": lang, "current_page": "board"},
            status_code=404
        )
    
    # このスレッドについて、現在のユーザーがメッセージを送信する権限があるか確認する
    user: User | None = getattr(request.state, "current_user", None)
    is_permitted_to_chat = await post.is_permitted_to_chat(db, user.id if user else None)
    
    # ブロックリストを取得
    blocker_user_id = user.id if user else None
    blocker_anonymous_id = request.cookies.get('brawlanonid') if not user else None
    
    if blocker_user_id or blocker_anonymous_id:
        # ユーザーIDか匿名IDが存在する場合のみ、DBからブロックリストを取得
        blocked_ids = await get_blocked_ids(
            db,
            blocker_user_id=blocker_user_id,
            blocker_anonymous_id=blocker_anonymous_id
        )
    else:
        # どちらのIDも存在しない場合は、空の辞書を生成
        blocked_ids = {"user_ids": [], "anonymous_ids": []}
    
    blocked_user_ids = blocked_ids["user_ids"]
    focus_message_id: int | None = None
    has_more_older_messages = False
    has_more_newer_messages = False
    total_messages = 0
    messages: dict[int, dict] = {}

    # フォーカス対象が有効か確認
    if message_id is not None:
        focus_candidate = await get_message(db, message_id, include_deleted_message=True)
        if (
            focus_candidate
            and focus_candidate.thread_id == thread_id
            and not focus_candidate.is_deleted
        ):
            focus_message_id = message_id

    try:
        if focus_message_id is not None:
            # 最新ウィンドウに含まれるか確認
            latest_messages, total_messages = await get_messages(
                db, per_page=CHAT_MESSAGES_INITIAL_LOAD, thread_id=thread_id
            )
            latest_ids = {m.id for m in latest_messages}
            if focus_message_id in latest_ids:
                messages_data = list(reversed(latest_messages))
                await attach_reply_to_previews(db, messages_data)
                for message in messages_data:
                    if message.is_deleted or message.user_id in blocked_user_ids:
                        continue
                    reactions, _ = await get_reactions(db, per_page=1000, message_id=message.id)
                    messages[message.id] = {
                        "data": _censor_message_dict(message.to_dict()),
                        "reactions": reactions,
                    }
                has_more_older_messages = total_messages > len(messages_data)
                has_more_newer_messages = False
            else:
                payload, has_more_older_messages, has_more_newer_messages = await _fetch_chat_messages_around(
                    db,
                    thread_id=thread_id,
                    blocked_user_ids=blocked_user_ids,
                    around_message_id=focus_message_id,
                    per_page=CHAT_MESSAGES_INITIAL_LOAD,
                )
                for item in payload:
                    messages[item["data"]["id"]] = item
                total_messages = len(payload)
        else:
            messages_data, total_messages = await get_messages(
                db, per_page=CHAT_MESSAGES_INITIAL_LOAD, thread_id=thread_id
            )
            messages_data.reverse()
            await attach_reply_to_previews(db, messages_data)
            for message in messages_data:
                if message.is_deleted or message.user_id in blocked_user_ids:
                    continue
                reactions, _ = await get_reactions(db, per_page=1000, message_id=message.id)
                messages[message.id] = {
                    "data": _censor_message_dict(message.to_dict()),
                    "reactions": reactions,
                }
            has_more_older_messages = total_messages > len(messages_data)
            has_more_newer_messages = False
    except DataBaseError as e:
        logger.error(f"メッセージ取得中にデータベースエラー: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")
    
    # テンプレートに渡すコンテキスト
    # brawler_guide型の場合はキャラクター情報を取得し、current_pageをtoolsに変更する
    brawler_for_chat = None
    chat_current_page = "board"
    if post.type == "brawler_guide":
        chat_current_page = "tools"
        brawler_id_for_chat = post.custom_settings.get("brawler_id") if post.custom_settings else None
        if brawler_id_for_chat:
            try:
                brawler_obj = await get_brawler(int(brawler_id_for_chat), db)
                brawler_for_chat = brawler_obj.to_dict() if brawler_obj else None
            except Exception as e:
                logger.warning(f"chat_thread: brawler情報の取得に失敗: brawler_id={brawler_id_for_chat}, error={e}")

    context = {
        "request": request,
        "lang": lang,
        "post": post,
        "messages": messages,
        "total_messages": total_messages,
        "has_more_older_messages": has_more_older_messages,
        "has_more_newer_messages": has_more_newer_messages,
        "focus_message_id": focus_message_id,
        "is_permitted_to_chat": is_permitted_to_chat,
        "blocked_ids": blocked_ids,
        "current_page": chat_current_page,
        # チャットは下部入力欄があるため、AdMobバナーはヘッダー直下(上部)に出す
        "admob_banner_position": "top",
        "hide_navigation_controls": True,
        "brawler": brawler_for_chat,
    }

    try:
        return templates.TemplateResponse("recruitment_board/chat.html", context)
    except Exception as render_err: # テンプレートレンダリングエラーも捕捉
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")


@router.get("/chat/{thread_id}/messages", name="get_chat_messages")
async def get_chat_messages(
    request: Request,
    lang: str,
    thread_id: int,
    before_message_id: int | None = Query(None, ge=1, description="このIDより古いメッセージを取得する"),
    after_message_id: int | None = Query(None, ge=1, description="このIDより新しいメッセージを取得する"),
    around_message_id: int | None = Query(None, ge=1, description="このID周辺のメッセージを取得する"),
    per_page: int = Query(CHAT_MESSAGES_OLDER_LOAD, ge=1, le=CHAT_MESSAGES_OLDER_LOAD),
    db: asyncpg.Connection = Depends(get_shared_db),
):
    """チャットスレッドのメッセージを追加取得する（過去 / 未来 / 周辺）。"""
    post = await get_post(db, id=thread_id, include_deleted_post=True)
    if not post:
        raise HTTPException(status_code=404, detail="Chat thread not found")

    mode_count = sum(
        1 for value in (before_message_id, after_message_id, around_message_id) if value is not None
    )
    if mode_count != 1:
        raise HTTPException(
            status_code=400,
            detail="Specify exactly one of before_message_id, after_message_id, around_message_id",
        )

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
        if around_message_id is not None:
            if anchor_message.is_deleted:
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


#* /---*---*---*---*---*---*---*---*/
#* 通知タブ
#* /---*---*---*---*---*---*---*---*/
def _normalize_notification_filter(notification_filter: str) -> str:
    if notification_filter not in VALID_NOTIFICATION_FILTERS:
        return "all"
    return notification_filter


@router.get("/notifications", name="notifications")
async def notifications(
    request: Request,
    lang: str,
    filter: str = Query("all", description="通知フィルター"),
    db: asyncpg.Connection = Depends(get_shared_db),
):
    user: User | None = getattr(request.state, "current_user", None)
    if not user:
        return RedirectResponse(
            url=str(request.url_for("team_recruitment_board", lang=lang)),
            status_code=status.HTTP_302_FOUND,
        )

    filter = _normalize_notification_filter(filter)
    try:
        await mark_all_notifications_as_read(db, user.id)
        # 既読処理後はナビ／inbox バッジを即時非表示にする（ミドルウェア取得分を上書き）
        request.state.board_notification_context = empty_board_notification_context()
    except DataBaseError as e:
        logger.error(f"通知既読処理中にDBエラー (User ID: {user.id}): {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error")

    context = {
        "request": request,
        "lang": lang,
        "current_page": "board",
        "filter": filter,
        "brawler_guide_notification_message_limit": BRAWLER_GUIDE_PARTICIPATED_THREAD_NOTIFICATION_LIMIT,
    }
    await _append_board_notification_context(context, db, user)
    return templates.TemplateResponse("recruitment_board/notifications.html", context)


@router.get("/notifications/fragment", name="notifications_fragment")
async def notifications_fragment(
    request: Request,
    lang: str,
    filter: str = Query("all", description="通知フィルター"),
    page: int = BOARD_PAGE_QUERY,
    db: asyncpg.Connection = Depends(get_shared_db),
):
    user: User | None = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login required")

    filter = _normalize_notification_filter(filter)
    try:
        if page <= 1:
            await mark_all_notifications_as_read(db, user.id)
            request.state.board_notification_context = empty_board_notification_context()
        notification_items, has_more = await get_notifications_for_display(
            db,
            user.id,
            notification_filter=filter,
            lang=lang,
            page=page,
            limit=NOTIFICATION_PAGE_SIZE,
        )
    except DataBaseError as e:
        logger.error(f"通知取得中にDBエラー (User ID: {user.id}): {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error")

    context = {
        "request": request,
        "lang": lang,
        "filter": filter,
        "page": page,
        "has_more": has_more,
        "notifications": notification_items,
        "brawler_guide_notification_message_limit": BRAWLER_GUIDE_PARTICIPATED_THREAD_NOTIFICATION_LIMIT,
        "fragment_show_notification_badge": False,
        "fragment_notification_badge_text": "",
    }
    template_name = (
        "recruitment_board/fragments/notifications_append.html"
        if page > 1
        else "recruitment_board/fragments/notifications_fragment.html"
    )
    try:
        return templates.TemplateResponse(template_name, context)
    except Exception as render_err:
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page") from render_err
 

#* /---*---*---*---*---*---*---*---*/
#* 投稿作成関連のエンドポイント
#* /---*---*---*---*---*---*---*---*/
@router.get("/check-permission/{post_type}", name="check_post_permission")
async def check_post_permission(
    request: Request,
    post_type: str,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    """投稿が認められているかどうかと、残りクールタイムを取得します。
    """
    user: User | None = getattr(request.state, "current_user", None)
    if post_type not in ["team", "friend", "club", "general"]:
        raise HTTPException(status_code=400, detail="Invalid post type")

    is_permitted, cooldown = await check_post_permitted(
        db,
        type=post_type,
        ip=get_ip(request),
        user_id=user.id if user else None
    )
    return {"is_permitted": is_permitted, "cooldown": int(cooldown)}

@router.get("/last-post/{post_type}", name="get_last_post_by_user")
async def get_last_post_by_user(
    request: Request,
    post_type: str,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    """ユーザーが最後に行った投稿を取得します。
    """
    user: User | None = getattr(request.state, "current_user", None)
    
    try:
        post = await get_last_post(
            db,
            ip=get_ip(request),
            type=post_type,
            user_id=user.id if user else None
        )
    except DataBaseError as e:
        logger.error(f"最終投稿の取得中にDBエラー: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error")

    if not post:
        raise HTTPException(status_code=404, detail="No recent post found")
    
    return post.to_dict()

@router.post("/parse-link", name="parse_invitation_link")
async def parse_invitation_link(
    request: LinkParseRequest,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    """ユーザーが入力した、ブロスタの招待リンクを含む可能性のあるテキストを解析し、抽出した情報を返します。
    """
    try:
        is_valid, link, region, tag, name = await check_invitation_link(
            db, text=request.text, type=request.type
        )
        return {
            "is_valid": is_valid,
            "link": link,
            "region": region,
            "tag": tag,
            "name": name
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (BrawlStarsAPIError, DataBaseError) as e:
        return {
            "is_valid": False,
            "error_message": "Failed to retrieve player/club data.",
            "detail": str(e)
        }

@router.post("/posts", name="create_new_post")
async def create_new_post(
    request: Request,
    post_data: PostCreateRequest,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    """投稿を作成します。
    """
    user: User | None = getattr(request.state, "current_user", None)
    try:
        post_id = await create_post(
            db=db,
            type=post_data.type,
            ip=get_ip(request),
            user_id=user.id if user else None,
            anonymous_id=request.cookies.get('brawlanonid'),
            region=post_data.region,
            host_player_tag=post_data.host_player_tag,
            host_club_tag=post_data.host_club_tag,
            link=post_data.link,
            comment=post_data.comment,
            conditions_application_type=post_data.conditions_application_type,
            chat_permission_level=post_data.chat_permission_level,
            category=post_data.category,
            mode=post_data.mode,
            hashtags=post_data.hashtags,
            custom_settings=post_data.custom_settings,
            permitted_ids=post_data.permitted_ids,
            prohibited_ids=post_data.prohibited_ids,
            required_highest_trophies=post_data.required_highest_trophies,
            required_current_trophies=post_data.required_current_trophies,
            required_ranked_highest_rank=post_data.required_ranked_highest_rank,
            required_ranked_current_rank=post_data.required_ranked_current_rank,
            required_ranked_highest_score=post_data.required_ranked_highest_score,
            required_ranked_current_score=post_data.required_ranked_current_score,
            required_solo_pl_rank=post_data.required_solo_pl_rank,
            required_max_power_brawlers=post_data.required_max_power_brawlers,
            required_prestige=post_data.required_prestige,
            other_conditions=post_data.other_conditions,
            is_later_recruitment=post_data.is_later_recruitment,
        )
        return {"success": True, "post_id": post_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except DataBaseError as e:
        logger.error(f"投稿作成中にデータベースエラー: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error while creating post.")
    
    
#* /---*---*---*---*---*---*---*---*/
#* 投稿操作関連のエンドポイント
#* /---*---*---*---*---*---*---*---*/
@router.delete("/posts/{post_id}", name="delete_post")
async def delete_post(
    request: Request,
    post_id: int,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    """投稿を削除する。
    """
    user: User | None = getattr(request.state, "current_user", None)
    
    post = await get_post(db, id=post_id, include_deleted_post=True)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
        
    # 権限チェック ---
    is_admin = user.is_admin if user else False
    is_host = (user and post.host_id == user.id) or (post.host_ip == get_ip(request))

    if not is_admin and not is_host:
        raise HTTPException(status_code=403, detail="You do not have permission to delete this post")
        
    try:
        await post.delete(db)
        return {"success": True, "message": "Post deleted successfully"}
    except DataBaseError as e:
        logger.error(f"投稿(ID:{post_id})の削除中にDBエラー: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error")


@router.post("/posts/{post_id}/close", name="close_post")
async def close_post(
    request: Request,
    post_id: int,
    db: asyncpg.Connection = Depends(get_shared_db),
):
    """チーム募集投稿をクローズする（ホストのみ。投稿直後は不可）。"""
    user: User | None = getattr(request.state, "current_user", None)

    post = await get_post(db, id=post_id, include_deleted_post=True)
    if not post or post.is_deleted:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.type != "team":
        raise HTTPException(status_code=400, detail="Only team posts can be closed")

    is_host = (user and post.host_id == user.id) or (post.host_ip == get_ip(request))
    if not is_host:
        raise HTTPException(status_code=403, detail="You do not have permission to close this post")

    remaining = post.seconds_until_close_allowed()
    if remaining > 0:
        raise HTTPException(
            status_code=400,
            detail="You can't close a post right after posting. Please wait.",
        )

    if post.is_effectively_closed:
        return {"success": True, "message": "Post already closed"}

    try:
        await post.close(db)
        return {"success": True, "message": "Post closed successfully"}
    except DataBaseError as e:
        logger.error(f"投稿(ID:{post_id})のクローズ中にDBエラー: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error")


@router.post("/posts/{post_id}/reopen", name="reopen_post")
async def reopen_post(
    request: Request,
    post_id: int,
    db: asyncpg.Connection = Depends(get_shared_db),
):
    """チーム募集投稿を再開する（ホストのみ）。"""
    user: User | None = getattr(request.state, "current_user", None)

    post = await get_post(db, id=post_id, include_deleted_post=True)
    if not post or post.is_deleted:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.type != "team":
        raise HTTPException(status_code=400, detail="Only team posts can be reopened")

    is_host = (user and post.host_id == user.id) or (post.host_ip == get_ip(request))
    if not is_host:
        raise HTTPException(status_code=403, detail="You do not have permission to reopen this post")

    if not post.is_effectively_closed:
        return {"success": True, "message": "Post already open"}

    try:
        await post.reopen(db)
        return {"success": True, "message": "Post reopened successfully"}
    except DataBaseError as e:
        logger.error(f"投稿(ID:{post_id})の再開中にDBエラー: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error")

@router.post("/posts/{post_id}/report", name="report_post")
async def report_post(
    request: Request,
    post_id: int,
    report_data: ReportCreateRequest,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    """投稿を通報する。
    """
    user: User | None = getattr(request.state, "current_user", None)

    # 投稿の存在確認
    post = await get_post(db, id=post_id)
    if not post:
        return JSONResponse(status_code=404, content={"detail": "Post to report not found"}) # 例外をJSONResponseで返すように変更
        
    try:
        await create_report(
            db=db,
            user_ip=get_ip(request),
            target_type="post",
            target_id=post_id,
            category=report_data.category,
            user_id=user.id if user else None,
            text=report_data.text
        )
        return {"success": True, "message": "Report submitted successfully"}
    except ValueError as e: # クールダウン中などのエラー
        return JSONResponse(status_code=429, content={"detail": str(e)})
    except DataBaseError as e:
        logger.error(f"投稿(ID:{post_id})の通報中にDBエラー: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"detail": "Database error"})


@router.post("/posts/{post_id}/good", name="toggle_post_good", response_model=GoodToggleResponse)
async def toggle_post_good(
    request: Request,
    post_id: int,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    """なんでも掲示板投稿への👍をトグルする。"""
    user: User | None = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        result = await toggle_general_post_up_vote(db, post_id=post_id, user_id=user.id)
        await handle_post_like_notification(
            db,
            post_id=post_id,
            actor_user_id=user.id,
            is_liked=bool(result["is_up_voted_by_current_user"]),
        )
        return {
            "success": True,
            "up_vote_count": result["up_vote_count"],
            "is_up_voted_by_current_user": result["is_up_voted_by_current_user"]
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except DataBaseError as e:
        logger.error(f"投稿(ID:{post_id})へのグッド操作中にDBエラー: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error")
    

#* /---*---*---*---*---*---*---*---*/
#* メッセージ関連のエンドポイント
#* /---*---*---*---*---*---*---*---*/
@router.websocket("/ws/chat/{thread_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    thread_id: int
):
    """チャットスレッドのWebSocket接続を処理し、双方向通信を行う"""
    # 1. まず接続を許可 (プロキシ等のタイムアウトを防止)
    await websocket.accept()

    user_info = {"id": None, "name": "Guest", "icon_path": None}

    try:
        # 接続初期化処理 ---
        # 2. 短時間だけDB接続を取得してユーザー情報を取得
        try:
            async with get_db_connection_for_bg_task() as db_conn:
                session = websocket.scope.get("session")
                user_id_str = session.get("user_id") if session else None
                user: User | None = None
                if user_id_str:
                    user = await get_user(db_conn, int(user_id_str))

                if user and not user.is_invalid:
                    user_info["id"] = user.id
                    user_info["name"] = user.name
                    if user.main_account:
                        icon_id = await get_player_icon_from_db(user.main_account, db_conn)
                        user_info["icon_path"] = "/" + get_icon_path(icon_id)
        except Exception as e:
            logger.error(f"WebSocket初期化中にエラー (スレッド: {thread_id}): {e}", exc_info=True)
            # エラー時もGuestとして扱うためにreturnはしない

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
                else:
                    logger.warning(f"未定義のイベントタイプを受信: {event_type}")

            except WebSocketDisconnect:
                break # ループを抜けてfinallyへ
            except json.JSONDecodeError:
                logger.warning(f"不正なJSONデータを受信しました。")
                continue
            except Exception as e:
                logger.error(
                    f"WebSocketメッセージ処理中に予期せぬエラー (スレッド: {thread_id}): {e}",
                    exc_info=True
                )
                if isinstance(e, RuntimeError) and "WebSocket is not connected" in str(e):
                    logger.warning(f"回復不能なWebSocketエラーのためループを終了します (スレッド: {thread_id})")
                    break
                continue

    finally:
        # 終了処理 ---
        logger.debug(f"WebSocket接続終了 (スレッド: {thread_id}, ユーザー: {user_info['name']})")
        # タイピング終了を通知
        await manager.broadcast(
            thread_id,
            {"type": "user_stopped_typing", "data": {"id": user_info["id"]}}
        )
        manager.disconnect(websocket, thread_id)

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
        message_id = await create_message(
            db=db,
            thread_id=thread_id,
            ip=get_ip(request),
            message=message_data.message,
            user_id=user.id if user else None,
            reply_to_message_id=message_data.reply_to_message_id,
        )
        
        # トークンとアドバンスミッション
        if user:
            if post.type == "general":
                await user.check_and_claim_advance_mission(db, "chat_general")
            elif post.type == "brawler_guide":
                await user.check_and_claim_advance_mission(db, "chat_brawler")
        
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
            )
        
        return {"success": True, "message_id": message_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except DataBaseError as e:
        logger.error(f"メッセージ作成中(スレッド:{thread_id})にDBエラー: {e}", exc_info=True)
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

    # 権限チェック ---
    if not user.is_admin and message.user_id != user.id:
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

@router.post("/messages/{message_id}/reactions", name="add_message_reaction")
async def add_message_reaction(
    request: Request,
    message_id: int,
    reaction_data: ReactionCreateRequest,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    """メッセージにリアクションを追加する。
    """
    user: User | None = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    # メッセージの存在確認
    message = await get_message(db, id=message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    try:
        reaction_id = await add_reaction(db, message_id, user.id, reaction_data.emoji)

        # 新しいリアクションをブロードキャスト
        # リアクション情報をDBから取得
        row = await db.fetchrow("SELECT * FROM reactions WHERE id = $1", reaction_id)
        if row:
            new_reaction = await Reaction.from_db(row, db)
            await manager.broadcast(
                message.thread_id,
                {"type": "new_reaction", "data": new_reaction.to_dict()}
            )
            await handle_message_reaction_notification(
                db,
                message_id=message_id,
                actor_user_id=user.id,
                reaction_id=reaction_id,
                is_added=True,
            )

        return {"success": True, "reaction_id": reaction_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except asyncpg.UniqueViolationError:
        # 連打・競合による想定内の重複。ERRORログも例外ハンドラも経由させない
        return JSONResponse(
            status_code=409,
            content={"detail": "You have already reacted with this emoji"},
        )
    except DataBaseError as e:
        logger.error(f"リアクション追加中(メッセージ:{message_id})にDBエラー: {e}", exc_info=True)
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
    if not user.is_admin and reaction.user_id != user.id:
        raise HTTPException(status_code=403, detail="You do not have permission to delete this reaction")

    try:
        message_id = reaction.message_id # 削除前にメッセージIDを保持
        await reaction.delete(db)
        
        # リアクションが削除されたことをブロードキャスト
        thread_row = await db.fetchrow("SELECT thread_id FROM messages WHERE id = $1", message_id)
        if thread_row:
            await manager.broadcast(
                thread_row['thread_id'],
                {
                    "type": "delete_reaction", 
                    "data": {
                        "reaction_id": reaction_id,
                        "message_id": message_id
                    }
                }
            )

        return {"success": True}
    except DataBaseError as e:
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
            blocked_anonymous_id=block_data.blocked_anonymous_id
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
