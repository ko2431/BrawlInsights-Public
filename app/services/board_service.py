import asyncpg
import asyncio
import datetime
import json
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
from app.services.all_maps_service import resolve_board_map
from app.services.map_mode_catalog import DEFAULT_BOARD_COLORS, ensure_catalog, get_mode_board_colors
from app.services.admin_notification_service import (
    POST_TYPE_LABELS_JA,
    clip_admin_notification_text,
    emit_admin_notification,
    format_admin_user_label,
)


# [この部分は公開用リポジトリでは非公開にされています]


async def get_or_create_theme_map_post(db: asyncpg.Connection, map_id: int) -> "Post | None":
    """テーマ掲示板（マップ）用のpostレコードをDBから取得する。存在しなければ作成する。
    重複登録されたマップIDは、マップ一覧に載る正規のIDの掲示板に寄せる。
    一覧に載らないマップ（開発用・名前不明など）では作成せず None を返す。
    結果は6時間Redisにキャッシュされる。

    Args:
        db (asyncpg.Connection): データベース接続
        map_id (int): マップID（例: 15000026）

    Raises:
        DataBaseError: データベースエラー

    Returns:
        Post | None: テーマ掲示板用のpost。対象マップが一覧に無い場合はNone
    """
    # [この部分は公開用リポジトリでは非公開にされています]


async def get_theme_brawler_index_json(db: asyncpg.Connection) -> str:
    """テーマ掲示板の検索索引（全キャラ）のJSON文字列。Redisに1時間キャッシュする。
    形: [[id, en, [ja名...], color1, color2], ...]"""
    cache_key = "theme_board_brawler_index"
    cached = await get_cache(cache_key)
    if isinstance(cached, str):
        return cached
    try:
        rows = await db.fetch("SELECT id, en, ja, rarity FROM brawlers ORDER BY id")
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e
    items: list[list[Any]] = []
    for row in rows:
        names_ja = row["ja"] or []
        if isinstance(names_ja, str):
            try:
                names_ja = json.loads(names_ja)
            except (json.JSONDecodeError, TypeError):
                names_ja = []
        if not isinstance(names_ja, list):
            names_ja = []
        color1, color2 = get_theme_brawler_board_colors(row["rarity"])
        items.append([row["id"], row["en"] or "", names_ja, color1, color2])
    index_json = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    await set_cache(cache_key, index_json, ttl=60 * 60)
    return index_json


def get_theme_brawler_board_colors(rarity: int | None) -> tuple[str, str]:
    """キャラ掲示板のグラデーション色 (color1, color2)。レアリティ別。"""
    return THEME_BRAWLER_RARITY_COLORS.get(rarity or 0, THEME_DEFAULT_BOARD_COLORS)


def resolve_theme_map_display(map_id: int | None) -> dict[str, Any] | None:
    """マップ掲示板の表示用情報（名前・モード・アイコン・色）。ensure_catalog 済みであること。"""
    board_map = resolve_board_map(map_id)
    if not board_map:
        return None
    color1, color2 = get_mode_board_colors(board_map["mode_id"])
    return {
        "map_id": board_map["id"],
        "map_name_ja": board_map["ja"] or board_map["en"] or "",
        "map_name_en": board_map["en"] or board_map["ja"] or "",
        "mode_id": board_map["mode_id"],
        "mode_name_ja": board_map["mode_ja"] or board_map["mode_en"] or "",
        "mode_name_en": board_map["mode_en"] or board_map["mode_ja"] or "",
        "mode_icons": board_map["mode_icons"],
        "board_c1": color1,
        "board_c2": color2,
    }


# [この部分は公開用リポジトリでは非公開にされています]


async def _query_theme_board_posts(
    db: asyncpg.Connection,
    *,
    condition: str,
    extra_filters: str,
    query_params: list[Any],
    limit: int,
) -> list[dict[str, Any]]:
    """テーマ掲示板の投稿を取得し、表示用の辞書にする。query_params の $1 はブロック中のユーザーID。
    ensure_catalog 済みであること。"""
    # [この部分は公開用リポジトリでは非公開にされています]


async def _get_rotation_board_map_ids(db: asyncpg.Connection) -> list[int]:
    """マップ周期に含まれるマップの掲示板用ID。現在出現中のマップ → 次に出るマップ…の順に、各枠から交互に並べる。"""
    from app.services.map_rotation_service import build_map_rotation_payload

    try:
        payload = await build_map_rotation_payload(db)
    except Exception as e:
        logger.warning(f"マップ掲示板の補充用にマップ周期を取得できませんでした: {e}")
        return []

    queues: list[list[int]] = []
    for slot in payload.get("slots") or []:
        slot_map_ids = [item.get("map_id") for item in slot.get("maps") or []]
        if not slot_map_ids:
            continue
        start = int(slot.get("latestIndex") or 0) % len(slot_map_ids)
        queues.append(slot_map_ids[start:] + slot_map_ids[:start])

    ordered: list[int] = []
    seen: set[int] = set()
    for position in range(max((len(queue) for queue in queues), default=0)):
        for queue in queues:
            if position >= len(queue) or not queue[position]:
                continue
            board_map = resolve_board_map(int(queue[position]))
            if board_map and board_map["id"] not in seen:
                seen.add(board_map["id"])
                ordered.append(board_map["id"])
    return ordered


async def _get_rotation_fill_map_posts(
    db: asyncpg.Connection,
    *,
    exclude_map_ids: set[int],
    count: int,
    blocked_ids: list[int],
) -> list[dict[str, Any]]:
    """マップタブの件数が少ないときに下へ加える、マップ周期に含まれるまだ書き込みのないマップ掲示板。
    掲示板が無ければまとめて作成する。ensure_catalog 済みであること。"""
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
                       before_message_id: int | None = None, from_oldest: bool = False,
                       exclude_warning: bool = False) -> tuple[list[Message], int]:
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
        exclude_warning (bool): True の場合、警告メッセージを除外する。
        
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
