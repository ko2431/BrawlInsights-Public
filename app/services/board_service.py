import asyncpg
import asyncio
import datetime
import math
import re
import random
from typing import Any
from urllib.parse import urlparse, urlunparse

from app.exceptions.custom_exceptions import BrawlStarsAPIError, DataBaseError
from app.services.brawl_service import Player, get_player, get_player_from_db, get_player_name, Club, get_club, get_club_name, get_player_icon_from_db, is_brawlstats_api_unavailable
from app.services.user_service import User, get_region_name, get_user
from app.utils.utils import format_utc_datetime, parse_utc_datetime, get_normalized_ip, get_icon_path, format_tag
from app.utils.url_detect import text_contains_detected_url
from app.core.logger import logger
from app.core.cache import get_cache, set_cache, delete_cache, get_redis
from app.core.board_trending import GENERAL_BOARD_TRENDING
from app.services.admin_notification_service import (
    POST_TYPE_LABELS_JA,
    clip_admin_notification_text,
    emit_admin_notification,
    format_admin_user_label,
)


# [この部分は公開用リポジトリでは非公開にされています]


async def check_post_permitted(db: asyncpg.Connection, type: str, ip: str, user_id: int | None = None) -> tuple[bool, int]:
    """投稿するのが認められているかどうか確認する。認められていない場合は残りクールダウン時間(秒)を返す。ただし投稿自体が禁止されている場合はクールダウン時間は0となる。

    Args:
        db (asyncpg.Connection): データベース接続
        type (str): 掲示板のタイプ("team"/"friend"/"club"/"general")
        ip (str): ユーザーのIPアドレス
        user_id (int | None): ユーザーID。ログインしていないユーザーについて確認する場合はNoneでよい。

    Returns:
        tuple[bool, int]: 投稿するのが認められているかどうか。そして、残りクールダウン時間(投稿自体が禁止されている場合は0)。
    """
    if user_id:
        user = await get_user(db, user_id)
        if user.is_prohibit_posting:
            return False, 0
    
    cache_key = f"last_post:{type}_{user_id if user_id else get_normalized_ip(ip)}"
    cached_data = await get_cache(cache_key)
    if cached_data:
        td = (datetime.datetime.now(datetime.timezone.utc) - parse_utc_datetime(cached_data)).total_seconds()
        match type:
            case "team" | "general":
                cooldown = 60
            case _:
                cooldown = 180 # [この部分は公開用リポジトリでは非公開にされています]

async def get_last_post(db: asyncpg.Connection, ip: str, type: str | None = None, user_id: int | None = None) -> Post | None:
    """ユーザーが最後に行った投稿の情報を取得する。5秒間のキャッシュを使用する。

    Args:
        db (asyncpg.Connection): データベース接続
        ip (str): IPアドレス
        type (str | None): 投稿のタイプ("team"/"friend"/"club")。指定しない場合は、タイプに関係なくユーザーが最後に行った投稿を取得する。
        user_id (int | None): ユーザーID。ログインしていないユーザーについて取得する場合はNoneでよい。

    Raises:
        DataBaseError: データベースエラー

    Returns:
        Post | None: 投稿。見つからなかった場合はNone。
    """
    # [この部分は公開用リポジトリでは非公開にされています]

async def get_posts(db: asyncpg.Connection, page: int = 1, per_page: int = 100, type: str | None = None,
                    region: str | None = None, target_user: User | None = None, target_player: Player | None = None,
                    category: str | None = None, mode: str | None = None, hashtag: str | None = None, include_deleted_post: bool = False,
                    eliminate_duplicates: bool = False, author_user_id: int | None = None, author_ip: str | None = None,
                    filter: str | None = None, exclude_category: str | None = None,
                    only_joinable: bool = False, viewer_ip: str | None = None) -> tuple[list[Post], int]:
    """投稿を、条件に合わせて新しい順に取得する。3秒間のキャッシュを使用する。

    Args:
        db (asyncpg.Connection): データベース接続
        page (int): 何ページ目か。デフォルトは1。
        per_page (int): 1ページあたりの表示数。デフォルトは100。
        type (str | None): 取得する投稿のタイプ。"team", "friend", "club"のどれか。デフォルトはNoneで、Noneの場合はすべて取得する。
        region (str | None): 取得する募集の地域。デフォルトはNoneで、Noneの場合はすべて取得する。
        target_user (User | None): 条件判定用のユーザー。指定すると、そのユーザーが参加可能な募集のみを取得する。デフォルトはNoneで、Noneの場合は条件判定を行わない。
        target_player (Player | None): 条件判定用のユーザーのメインアカウントのプレイヤー情報。こちらも指定すれば条件判定に用いられる。
        category (str | None): 指定した場合、該当カテゴリーの募集のみ取得される。デフォルトはNone。
        mode (str | None): 指定した場合、該当モードの募集のみ取得される。デフォルトはNone。
        hashtag (str | None): 指定した場合、該当ハッシュタグが含まれている募集のみ取得される。デフォルトはNone。
        include_deleted_post (bool): 削除されている投稿も取得対象とするかどうか。デフォルトはFalse。
        eliminate_duplicates (bool): Trueの場合、募集のプレイヤーまたはクラブが重複する投稿について、最新の1件のみを取得し、残りは排除する。デフォルトはFalse。
        author_user_id (int | None): 指定した場合、そのユーザーIDがホストの投稿のみを取得する。
        author_ip (str | None): 指定した場合、そのIPアドレスがホストの投稿のみを取得する。author_user_idと同時に指定するとOR条件になる。
        filter (str | None): 絞り込み種別。'only_instant_recruitment' / 'only_later_recruitment' / 'only_participated_threads' / 'only_liked_posts' など。
        exclude_category (str | None): 指定した場合、該当カテゴリーの投稿を除外する。デフォルトはNone。
        only_joinable (bool): Trueの場合、参加可能な投稿のみに絞り込む。filter と同時指定可能。
        viewer_ip (str | None): 閲覧者IP。参加可否フィルタで自身の投稿（host_ip一致）を残すために用いる。
        
    Raises:
        BrawlStarsAPIError: APIエラー
        DataBaseError: データベースエラー

    Returns:
        tuple[list[Post], int]: 取得した投稿のリストと、検索結果総数。
    """
    # [この部分は公開用リポジトリでは非公開にされています]

async def get_trending_general_posts(
    db: asyncpg.Connection,
    per_page: int = 60,
    page: int = 1,
    region: str | None = None,
    category: str | None = None,
    exclude_category: str | None = None,
) -> tuple[list[Post], int]:
    """なんでも掲示板の投稿を話題順で取得する。候補は直近 candidate_max_age_days 日以内。

    スコア = (weight_likes * ln(1+いいね) + weight_comments * ln(1+コメント))
             / (経過時間[h] + age_offset_hours) ^ gravity
    集約SQLと共有キャッシュを使い、リクエストごとのN+1を避える。
    """
    # [この部分は公開用リポジトリでは非公開にされています]

async def get_today_post_count_by_user(db: asyncpg.Connection, user_id: int | None) -> int:
    """ユーザーが今日(UTC)投稿した数を取得する。

    Args:
        db (asyncpg.Connection): データベース接続
        user_id (int | None): ユーザーID

    Raises:
        DataBaseError: データベースエラー

    Returns:
        int: ユーザーが今日投稿した数。ユーザーが存在しない、または投稿がない場合は0。
    """
    # [この部分は公開用リポジトリでは非公開にされています]

async def get_general_post_vote_summary(
    db: asyncpg.Connection,
    post_ids: list[int],
    user_id: int | None = None,
) -> dict[int, dict[str, int | bool | None]]:
    """なんでも掲示板投稿の投票集計を取得する。将来的な👎実装も見据え、up/downの両方を返す。"""
    # [この部分は公開用リポジトリでは非公開にされています]

async def toggle_general_post_up_vote(db: asyncpg.Connection, post_id: int, user_id: int) -> dict[str, int | bool]:
    """なんでも掲示板投稿の👍をトグルする。"""
    # [この部分は公開用リポジトリでは非公開にされています]

# [この部分は公開用リポジトリでは非公開にされています]


async def check_invitation_link(db: asyncpg.Connection, text: str, type: str) -> tuple[bool, str, str, str | None, str | None]:
    """ユーザーが入力した、招待リンクの含まれるテキストから招待リンクを抽出し、さらにそこに含まれるプレイヤータグまたはクラブタグから名前を取得する。10秒間のキャッシュを使用する。

    Args:
        db (asyncpg.Connection): データベース接続
        text (str): リンクの含まれるテキスト
        type (str): リンクのタイプ("team"/"friend"/"club")

    Raises:
        ValueError: リンクのタイプが無効な場合

    Returns:
        tuple[bool, str, str, str | None, str | None]: 正しいリンクの含まれているテキストかどうか / 招待リンク / 地域("JP"/"EN") / プレイヤータグまたはクラブタグ / プレイヤー名またはクラブ名
    """
    # [この部分は公開用リポジトリでは非公開にされています]

# [この部分は公開用リポジトリでは非公開にされています]

async def get_message(db: asyncpg.Connection, id: int, include_deleted_message: bool = False) -> Message:
    """メッセージIDをもとに単一のメッセージを取得する。デフォルト時間のキャッシュを使用する。

    Args:
        db (asyncpg.Connection): データベース接続
        id (int): メッセージID
        include_deleted_post (bool): 削除されているメッセージも取得対象とするかどうか。デフォルトはFalse。

    Raises:
        DataBaseError: データベースエラー
        
    Returns:
        Message: メッセージ
    """
    # [この部分は公開用リポジトリでは非公開にされています]

async def get_messages(db: asyncpg.Connection, page: int = 1, per_page: int = 100, thread_id: int | None = None,
                       include_deleted_message: bool = False, after_message_id: int | None = None,
                       before_message_id: int | None = None, from_oldest: bool = False) -> tuple[list[Message], int]:
    """メッセージを、条件に合わせて新しい順に取得する。1秒間のキャッシュを使用する。

    Args:
        db (asyncpg.Connection): データベース接続
        page (int): 何ページ目か。デフォルトは1。
        per_page (int): 1ページあたりの表示数。デフォルトは100。
        thread_id (int | None): スレッドのID。指定されなかった場合は、スレッドで絞り込みせずすべてのメッセージを取得する。
        include_deleted_message (bool): 削除されているメッセージも取得対象とするかどうか。デフォルトはFalse。
        after_message_id (int | None): 指定した場合、このメッセージIDより後のメッセージのみ取得する。
        before_message_id (int | None): 指定した場合、このメッセージIDより前のメッセージのみ取得する。
        from_oldest (bool): True の場合、最新ではなく最古側から取得する（戻り値は新しい順のまま）。
        
    Raises:
        DataBaseError: データベースエラー

    Returns:
        tuple[list[Message], int]: 取得したメッセージのリストと、検索結果総数。
    """
    # [この部分は公開用リポジトリでは非公開にされています]
    
async def get_message_count(db: asyncpg.Connection, thread_id: int) -> int:
    """該当スレッドのメッセージ数を取得する。15秒間のキャッシュを使用する。

    Args:
        db (asyncpg.Connection): データベース接続
        thread_id (int): スレッドID

    Raises:
        DataBaseError: データベースエラー

    Returns:
        int: メッセージ数
    """
    # [この部分は公開用リポジトリでは非公開にされています]

# [この部分は公開用リポジトリでは非公開にされています]

async def get_reactions(db: asyncpg.Connection, page: int = 1, per_page: int = 100, message_id: int | None = None,
                       grouping: bool = True, to_dict: bool = True) -> tuple[list[Reaction], int]:
    """リアクションを、条件に合わせて新しい順に取得する。3秒間のキャッシュを使用する。

    Args:
        db (asyncpg.Connection): データベース接続
        page (int): 何ページ目か。デフォルトは1。
        per_page (int): 1ページあたりの表示数。デフォルトは100。
        message_id (int | None): メッセージのID。指定されなかった場合は、メッセージで絞り込みせずすべてのリアクションを取得する。
        grouping (bool): Trueの場合は、取得したあと、同じ絵文字ごとに順番を整列する。
        to_dict (bool): Trueの場合は、Reaction型ではなく、辞書に変換して返す。
        
    Raises:
        DataBaseError: データベースエラー

    Returns:
        tuple[list[Reaction], int]: 取得したリアクションのリストと、検索結果総数。
    """
    # [この部分は公開用リポジトリでは非公開にされています]

# [この部分は公開用リポジトリでは非公開にされています]

async def create_report(db: asyncpg.Connection, user_ip: str, target_type: str, target_id: int, category: str, user_id: int | None = None,
                        text: str | None = None) -> None:
    """新しい通報を追加する。

    Args:
        db (asyncpg.Connection): データベース接続
        user_ip (str): IPアドレス
        target_type (str): 通報対象のタイプ("post"/"message")
        target_id (int): 通報対象のID
        category (str): カテゴリー
        user_id (int | None): ユーザーID。ログインしていないユーザーの場合はNoneでよい。
        text (str | None): 本文

    Raises:
        ValueError: 存在しないユーザーID, 投稿が禁止されているユーザーID, 通報後のクールダウン(1分)がまだ終了していないユーザーIDまたはIPアドレスが指定された場合。
        DataBaseError: データベースエラー
    """
    # [この部分は公開用リポジトリでは非公開にされています]
