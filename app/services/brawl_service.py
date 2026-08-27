import datetime
import asyncio
import json
import random
import time
from decimal import Decimal
from typing import Any, TypedDict, NotRequired
from collections import Counter, defaultdict
from dataclasses import dataclass
import asyncpg
import brawlstats
import BrawlPlex
from BrawlPlex.errors import NetworkError
from cachetools import TTLCache
import itertools
import re
import math
import hashlib
import secrets

from app.core.config import settings
from app.models.missing import MISSING
from app.core.logger import logger
from app.core.cache import get_cache, set_cache, delete_cache, get_redis
from app.services import meowapi, bsinfoapi
from app.services.user_service import get_region_name
from app.utils.utils import calc_tier, calc_old_tier, parse_utc_datetime, parse_api_utc_datetime, format_utc_date, format_utc_datetime, is_expired, update_logdict, estimate_play_time, calc_auto_activate_hours, parse_utc_date, calc_mastery_rank, calc_ranked_season, confirm_tag
from app.exceptions.custom_exceptions import BrawlStarsAPIError, DataBaseError
from app.services.admin_notification_service import emit_admin_notification
from app.services.map_mode_catalog import (
    ensure_catalog,
    format_mode_slug_to_display,
    get_japanese_map_name,
    get_japanese_mode_name,
    get_map_by_en,
    get_map_by_id,
    get_mode_by_id,
    get_mode_by_slug,
    mode_icon_candidates,
    mode_sort_key,
    normalize_official_mode_id,
    prepare_battle_mode_id,
    resolve_map_filter_label,
    resolve_mode_filter_label,
)



# [この部分は公開用リポジトリでは非公開にされています]
            
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name_en": self.name_en,
            "name_ja": self.name_ja,
            "names_ja": self.names_ja,
            "is_temporary": self.is_temporary,
            "rarity": self.rarity,
            "has_bsinfo_data": self.has_bsinfo_data,
            "class_ja": self.class_ja,
            "class_en": self.class_en,
            "class_id": self.class_id,
            "title_mastery_ja": self.title_mastery_ja,
            "title_mastery_en": self.title_mastery_en,
            "title_prestige_ja": self.title_prestige_ja,
            "title_prestige_en": self.title_prestige_en,
            "hp": self.hp,
            "speed": self.speed,
            "damage": self.damage,
            "range": self.range,
            "reload_speed": self.reload_speed,
            "max_ammo": self.max_ammo,
            "spread": self.spread,
            "gadgets": self.gadgets,
            "star_powers": self.star_powers,
            "gears": self.gears,
            "hypercharges": self.hypercharges,
            "hypercharge": self.hypercharge,
            "all_skins": self.all_skins
        }
        
    @classmethod
    async def from_dict(cls, data: dict, db: asyncpg.Connection):
        # [この部分は公開用リポジトリでは非公開にされています]

async def update_brawler(id: int, db: asyncpg.Connection, ja: list[str] | None = None, 
                   append_ja: bool = True, is_temporary: bool | None = None, rarity: int | None = MISSING) -> None:
    """キャラクターの日本語訳、および一時的なキャラクターかどうかの設定を手動で更新する。APIに存在しないBrawler IDのキャラクターは追加できない。

    Args:
        id (int): 更新対象のBrawler ID
        db (asyncpg.Connection): データベース接続
        ja (list[str] | None): 更新する日本語名リスト。Noneの場合は更新しない。
        append_ja (bool): Trueの場合は既存の日本語名に追加、Falseの場合は上書き。デフォルトはTrue。
        is_temporary (bool | None): 一時的なキャラクターかどうか。Noneの場合は更新しない。
        rarity (int | None): レアリティ。数字またはNone(レア度なし)。指定されなかった場合は更新しない。

    Raises:
        ValueError: 指定されたIDのキャラクターが存在しない場合
        DataBaseError: データベースエラー

    Returns:
        None
    """
    # [この部分は公開用リポジトリでは非公開にされています]

class PlayerBrawler:
    """プレイヤーのキャラクターデータの格納用。
    highest_season_trophies, mastery, mastery_rankはMeowAPIが利用できない時はNoneとなる。またその場合はseason_trophiesの計算に現在トロフィーを用いる。
    """
    # [この部分は公開用リポジトリでは非公開にされています]



# [この部分は公開用リポジトリでは非公開にされています]

async def get_prestige_borders(db: asyncpg.Connection, date: datetime.date) -> dict[int, int]:
    """指定された日の全キャラのトップランカーボーダーを取得する。最大で30日間のキャッシュを使用する。

    Args:
        db (asyncpg.Connection): データベース接続
        date (datetime.date): 取得するUTC日付

    Raises:
        DataBaseError: データベースエラー
        ValueError: 無効な(まだ記録されていない)日付が指定された場合

    Returns:
        dict[int, int]: 結果。キャラID、ボーダーの順で、キャラIDが小さい順の辞書。
    """
    # [この部分は公開用リポジトリでは非公開にされています]

async def get_prestige_border_trend(db: asyncpg.Connection, brawler_id: int, dates: list[datetime.date]) -> dict[datetime.date, int]:
    """指定されたキャラクターの、指定した日付リストのボーダーの値を取得する。

    Args:
        db (asyncpg.Connection): データベース接続
        brawler_id (int): キャラクターID (テーブル 'prestige_borders' のカラム名として使用される数値)
        dates (list[datetime.date]): 取得したい日付のリスト。並び替えはされていなくてもよい。

    Raises:
        DataBaseError: データベースエラー

    Returns:
        dict[datetime.date, int]: 日付をキー、ボーダーの値を値とする辞書。古いものが先に並んでいる。
    """
    # [この部分は公開用リポジトリでは非公開にされています]

# [この部分は公開用リポジトリでは非公開にされています]

class RankingPlayer:
    """データベースから集計するタイプのランキングプレイヤー格納用。
    """
    def __init__(self, dbrow: asyncpg.Record = MISSING, i: int = MISSING, score_name: str = MISSING):
        if dbrow is not MISSING and i is not MISSING and score_name is not MISSING:
            self.tag: str = dbrow["tag"]
            self.name: str = dbrow["name"]
            score_value = dbrow.get(score_name)
            # [この部分は公開用リポジトリでは非公開にされています]

async def get_play_time_ranking(db: asyncpg.Connection, region: str | None = None, limit: int = 200) -> list[RankingPlayer]:
    """推定プレイ時間に基づいたランキングを取得する。15秒間のキャッシュを使用する。

    Args:
        db (asyncpg.Connection): データベース接続オブジェクト
        region (str | None): 地域コード。デフォルトはNoneで、Noneの場合はグローバル。
        limit (int): ランキングの取得数。デフォルトは200。

    Raises:
        ValueError: limitの値が不適切だった場合
        DataBaseError: データベースエラー

    Returns:
        list[RankingPlayer]: ランキングのリスト
    """
    # [この部分は公開用リポジトリでは非公開にされています]

async def get_player_ranking(region: str | None = None) -> list[RankingTrophyPlayer]:
    """総合トロフィーランキングを取得する。(表示用) 永続するキャッシュを使用するが、3分ごとに新規取得を試みる。

    Args:
        region (str | None): 地域。デフォルトはNoneで、Noneの場合はグローバル。

    Raises:
        BrawlStarsAPIError: APIエラー(無効な地域が指定された場合も発生)

    Returns:
        list[RankingTrophyPlayer]: ランキング
    """
    try:
        result = await meowapi.get_player_ranking(region=region)
    except Exception as e:
        raise BrawlStarsAPIError(str(e))
    
    return [RankingTrophyPlayer.from_dict(data) for data in result]

async def get_player_alltime_ranking(region: str | None = None) -> list[RankingTrophyPlayer]:
    """総合トロフィー歴代ランキングを取得する。(表示用) 永続するキャッシュを使用するが、3分ごとに新規取得を試みる。

    Args:
        region (str | None): 地域。デフォルトはNoneで、Noneの場合はグローバル。

    Raises:
        BrawlStarsAPIError: APIエラー(無効な地域が指定された場合も発生)

    Returns:
        list[RankingTrophyPlayer]: ランキング
    """
    try:
        result = await meowapi.get_player_alltime_ranking(region=region)
    except Exception as e:
        raise BrawlStarsAPIError(str(e))
    
    return [RankingTrophyPlayer.from_dict(data) for data in result]

async def get_club_ranking(region: str | None = None) -> list[RankingClub]:
    """クラブランキングを取得する。(表示用)  永続するキャッシュを使用するが、3分ごとに新規取得を試みる。

    Args:
        region (str | None): 地域。デフォルトはNoneで、Noneの場合はグローバル。

    Raises:
        BrawlStarsAPIError: APIエラー(無効な地域が指定された場合も発生)

    Returns:
        list[RankingClub]: ランキング
    """
    cache_key_update_lock = f"ranking_update_lock:club_{region if region else "global"}"
    cached_data_update_lock = await get_cache(cache_key_update_lock)
    if not cached_data_update_lock:
        await set_cache(key=cache_key_update_lock, value=True, ttl=180)

    cache_key = f"ranking:club_{region if region else "global"}"
    cached_data = await get_cache(cache_key)
    if cached_data_update_lock and cached_data:
        return [RankingClub.from_dict(data) for data in cached_data]
    
    try:
        ranking_clubs = get_brawlstats_client().get_rankings(ranking="clubs", region=region)
    except Exception as e:
        raise BrawlStarsAPIError(str(e))
    
    ranking = [RankingClub(c) for c in ranking_clubs]
    await set_cache(key=cache_key, value=[rankingclub.to_dict() for rankingclub in ranking], ttl=None)
    await set_cache(key=cache_key_update_lock, value=True, ttl=180)
    return ranking

async def get_brawler_ranking(brawler_id: int, region: str | None = None) -> list[RankingTrophyPlayer]:
    """キャラクターランキングを取得する。(表示用) 3分間のキャッシュを使用する。 永続するキャッシュを使用するが、3分ごとに新規取得を試みる。

    Args:
        brawler_id (int): キャラクターID
        region (str | None): 地域。デフォルトはNoneで、Noneの場合はグローバル。

    Raises:
        BrawlStarsAPIError: APIエラー(無効なキャラクターID, 地域が指定された場合も発生)

    Returns:
        list[RankingTrophyPlayer]: ランキング
    """
    cache_key_update_lock = f"ranking_update_lock:{brawler_id}_{region if region else "global"}"
    cached_data_update_lock = await get_cache(cache_key_update_lock)
    if not cached_data_update_lock:
        await set_cache(key=cache_key_update_lock, value=True, ttl=180)

    cache_key = f"ranking:{brawler_id}_{region if region else "global"}"
    cached_data = await get_cache(cache_key)
    if cached_data_update_lock and cached_data:
        return [RankingTrophyPlayer.from_dict(data) for data in cached_data]
    
    try:
        ranking_players = get_brawlstats_client().get_rankings(ranking="brawlers", region=region, brawler=brawler_id)
    except Exception as e:
        raise BrawlStarsAPIError(str(e))
    
    ranking = [RankingTrophyPlayer(p) for p in ranking_players]
    await set_cache(key=cache_key, value=[rankingplayer.to_dict() for rankingplayer in ranking], ttl=None)
    await set_cache(key=cache_key_update_lock, value=True, ttl=180)
    return ranking

async def get_brawler_alltime_ranking(brawler_id: int, region: str | None = None) -> list[RankingTrophyPlayer]:
    """キャラトロフィー歴代ランキングを取得する。(表示用) 永続するキャッシュを使用するが、3分ごとに新規取得を試みる。

    Args:
        brawler_id (int): キャラクターID
        region (str | None): 地域。デフォルトはNoneで、Noneの場合はグローバル。

    Raises:
        BrawlStarsAPIError: APIエラー(無効な地域が指定された場合も発生)

    Returns:
        list[RankingTrophyPlayer]: ランキング
    """
    try:
        result = await meowapi.get_brawler_alltime_ranking(id=brawler_id, region=region)
    except Exception as e:
        raise BrawlStarsAPIError(str(e))
    
    return [RankingTrophyPlayer.from_dict(data) for data in result]

async def get_ranked_ranking(db: asyncpg.Connection, season: int | None = None, region: str | None = None) -> list[RankingRankedPlayer]:
    """ガチバトルランキングを取得する。(表示用) 永続するキャッシュを使用するが、3分(前シーズンは1日、それより前のシーズンは30日)ごとに新規取得を試みる。

    Args:
        db: データベース接続
        season (int | None): シーズン番号。未指定の場合は現在のシーズン。-1の場合は全期間、-2の場合は2024旧ガチバトルの全期間、-3の場合は2025新ガチバトルの全期間。
        region (str | None): 地域。デフォルトはNoneで、Noneの場合はグローバル。

    Raises:
        BrawlStarsAPIError: APIエラー(無効な地域が指定された場合も発生)

    Returns:
        list[RankingRankedPlayer]: ランキング
    """
    if not season:
        season = calc_ranked_season()

    try:
        result = await meowapi.get_ranked_ranking(db, season, region)
    except Exception as e:
        raise BrawlStarsAPIError(str(e))
    
    return [RankingRankedPlayer.from_dict(data) for data in result]


# [この部分は公開用リポジトリでは非公開にされています]


async def get_club(tag: str, db: asyncpg.Connection, use_long_cache: bool = False) -> Club:
    """Clubデータを取得する。30分間(ただし通常は3分間ごとにデータ更新)のキャッシュを使用する。

    Args:
        tag (str): クラブタグ
        db (asyncpg.Connection): データベース接続
        use_long_cahce (bool): Trueの場合は長期キャッシュ(30分)を利用し、利用できる場合はAPIにアクセスしない。デフォルトはFalse。これがオンの場合でも、エラー時は長期キャッシュがあればそれを返す。

    Raises:
        BrawlStarsAPIError: APIに接続できない場合
        DataBaseError: データベースの更新に失敗した場合

    Returns:
        Club: クラブ情報
    """
    cache_key = f"club:{tag}"
    freshness_key = f"club_fresh:{tag}"
    
    cached_data = await get_cache(cache_key)
    if cached_data and use_long_cache:
        return await Club.from_dict(cached_data, db)
    
    freshness_flag = await get_cache(freshness_key)
    if not freshness_flag:
        await set_cache(key=freshness_key, value=True, ttl=3)
    if cached_data and freshness_flag:
        return await Club.from_dict(cached_data, db)
    
    try:
        club = Club(tag, db)
        await club.get_club_data()
    except (BrawlStarsAPIError, DataBaseError) as e:
        if cached_data:
            return await Club.from_dict(cached_data, db)
        raise e
    else:
        await set_cache(key=freshness_key, value=True, ttl=180)
        await set_cache(key=cache_key, value=club.to_dict(), ttl=1800)
        return club

async def get_club_name(tag: str, db: asyncpg.Connection) -> Club:
    """クラブタグからクラブ名を取得する。DBのclubsに存在する場合は、APIへのアクセスは行わない。DBのclubsに存在しない場合は、APIから取得した上で新規追加を行う。
    デフォルト時間のキャッシュを使用する。

    Args:
        tag (str): クラブタグ
        db (asyncpg.Connection): データベース接続

    Raises:
        ValueError: クラブタグの形式が不正な場合
        BrawlStarsAPIError: APIに接続できない場合
        DataBaseError: データベースの更新に失敗した場合

    Returns:
        str: クラブ名。ただしNoneがtagとして渡された時はNone。
    """
    if not tag: return None
    
    if not confirm_tag(tag):
        raise ValueError("クラブタグの形式が不正")
    
    cache_key = f"club_name:{tag}"
    cached_data = await get_cache(cache_key)
    if cached_data:
        return cached_data

    result = await db.fetchval("SELECT name FROM clubs WHERE tag=$1", tag)
    if result:
        await set_cache(key=cache_key, value=result)
        return result

    else:
        try:
            club = await get_club(tag, db)
        except BrawlStarsAPIError as e:
            raise BrawlStarsAPIError(e)
        except DataBaseError as e:
            raise DataBaseError(e)
        await set_cache(key=cache_key, value=club.name)
        return club.name

async def search_clubs(query: str, db: asyncpg.Connection, page: int = 1, per_page: int = 120,
                exact_match: bool = False, include_invalid: bool = False) -> tuple[list[dict[str, str | int]], int, bool]:
    """クラブ名からクラブを検索する

    Returns:
        tuple[list[dict], int, bool]: 検索結果リスト、総件数（上限内）、is_capped（上限超えか）
    """
    # [この部分は公開用リポジトリでは非公開にされています]


# [この部分は公開用リポジトリでは非公開にされています]


def _wrap_ranked_mode_display(ja_name: str | None, en_name: str | None, slug: str | None, lang: str) -> str:
    if lang == "ja" and ja_name:
        if ja_name == "エメラルドハント":
            return "エメラルド\nハント"
        if ja_name == "ノックアウト":
            return "ノック\nアウト"
        if ja_name == "ブロストライカー":
            return "ブロスト\nライカー"
        if len(ja_name) >= 7:
            split_point = len(ja_name) // [この部分は公開用リポジトリでは非公開にされています]

class APIBattle:
    """バトルのAPIデータを整理する用。
    """
    def __init__(self, tag: str, apibattle: dict) -> None:
        self.datetime: datetime.datetime = parse_api_utc_datetime(apibattle["battleTime"])
        self.event_id: int | None = apibattle['event'].get("id") # [この部分は公開用リポジトリでは非公開にされています]


# [この部分は公開用リポジトリでは非公開にされています]


# [この部分は公開用リポジトリでは非公開にされています]

async def check_verify(tag: str) -> tuple[bool, str | None]:
    """認証を実際に確認する。

    Args:
        tag (str): プレイヤータグ

    Returns:
        tuple[bool, str | None]: (認証結果, 失敗理由キーまたはNone)
                                 失敗理由キー: "icon_mismatch", "no_cached_id", "api_error", "unknown_error"
    """
    cache_key = f"player_verify:{tag}"
    cached_id: int | None = await get_cache(cache_key)
    if not cached_id:
        return False, "no_cached_id" # [この部分は公開用リポジトリでは非公開にされています]

async def get_player(tag: str, db: asyncpg.Connection, use_long_cache: bool = False, is_bg_task: bool = False) -> Player:
    """Playerデータを取得する。30分間(ただし通常は3分間ごとにデータ更新)のキャッシュを使用する。

    Args:
        tag (str): プレイヤータグ
        db (asyncpg.Connection): データベース接続
        use_long_cahce (bool): 非推奨。Trueの場合はget_player_from_dbと同等。

    Raises:
        BrawlStarsAPIError: APIに接続できない場合
        DataBaseError: データベースの更新に失敗した場合

    Returns:
        Player: プレイヤー情報
    """
    # [この部分は公開用リポジトリでは非公開にされています]

async def get_player_from_db(tag: str, db: asyncpg.Connection) -> Player | None:
    """データベースからのみプレイヤーデータを取得する。Redisを用いた30分のキャッシュ機能(player:tag)を持つ。

    Args:
        tag (str): プレイヤータグ
        db (asyncpg.Connection): データベース接続

    Returns:
        Player | None: プレイヤーデータが存在する場合はPlayerオブジェクト、存在しない場合はNone
    """
    # [この部分は公開用リポジトリでは非公開にされています]

async def get_player_name(tag: str, db: asyncpg.Connection) -> str:
    """プレイヤータグからプレイヤー名を取得する。DBのplayersに存在する場合は、APIへのアクセスは行わない。DBのplayersに存在しない場合は、APIから取得した上で新規追加を行う。
    デフォルト時間のキャッシュを使用する。

    Args:
        tag (str): プレイヤータグ
        db (asyncpg.Connection): データベース接続

    Raises:
        ValueError: プレイヤータグの形式が不正
        BrawlStarsAPIError: APIに接続できない場合
        DataBaseError: データベースの更新に失敗した場合

    Returns:
        str: プレイヤー名
    """
    # [この部分は公開用リポジトリでは非公開にされています]

async def get_player_icon_from_db(tag: str, db: asyncpg.Connection) -> int | None:
    """データベースから、プレイヤーのプレイヤーアイコンを取得する。10分間のキャッシュを使用する。

    Args:
        tag (str): プレイヤータグ
        db (asyncpg.Connection): データベース接続
        
    Raises:
        DataBaseError: データベースエラー

    Returns:
        int | None: アイコンのID。プレイヤータグが見つからなかった場合はNone。
    """
    # [この部分は公開用リポジトリでは非公開にされています]

async def get_player_icon(tag: str) -> int:
    """APIにアクセスし、最新のプレイヤーのプレイヤーアイコンを取得する。プレイヤー認証システムに使う用。15秒間のキャッシュを使用する。

    Args:
        tag (str): プレイヤータグ
        
    Raises:
        BrawlStarsAPIError: APIに接続できない場合

    Returns:
        int: アイコンのID
    """
    # [この部分は公開用リポジトリでは非公開にされています]

async def get_club_badge_id_from_db(tag: str, db: asyncpg.Connection) -> int | None:
    """データベースからクラブのバッジIDを取得する。1日間のキャッシュを使用する。

    Args:
        tag (str): クラブタグ
        db (asyncpg.Connection): データベース接続

    Returns:
        int | None: バッジID。見つからない場合はNone。
    """
    # [この部分は公開用リポジトリでは非公開にされています]

async def get_player_for_tracking_extension(tag: str, db: asyncpg.Connection) -> Player | None:
    """トークンによる自動追跡延長に必要な最小限のプレイヤー情報をDBから取得する。

    API呼び出しは行わない。無効プレイヤー・未登録プレイヤーの場合はNoneを返す。

    Args:
        tag (str): プレイヤータグ
        db (asyncpg.Connection): データベース接続

    Returns:
        Player | None: 延長処理に使用するPlayerオブジェクト
    """
    # [この部分は公開用リポジトリでは非公開にされています]

async def add_auto_tracking_time(player: Player, hours: int) -> None:
    """
    プレイヤーの自動追跡機能の有効期限を指定時間分、延長する。
    有効期限が設定されていない、または期限切れの場合は、現在時刻から延長する。

    Args:
        player (Player):対象のプレイヤーオブジェクト
        hours (int): 延長する時間
    """
    # [この部分は公開用リポジトリでは非公開にされています]

async def get_hide_history_settings(db: asyncpg.Connection, tag: str) -> tuple[bool, bool] | None:
    """指定したプレイヤータグの履歴非公開設定状況を取得する。デフォルト時間のキャッシュを使用する。

    Args:
        db (asyncpg.Connection): データベース接続
        tag (str): プレイヤータグ

    Returns:
        tuple[bool, bool] | None: 改名履歴の非公開設定状況、そしてクラブ履歴の非公開設定状況。プレイヤーが見つからなかった場合はNoneが返る。
    """
    # [この部分は公開用リポジトリでは非公開にされています]

async def get_battle_log_limit(db: asyncpg.Connection, tag: str) -> int | None:
    """指定したプレイヤータグのバトル履歴保存上限を取得する。データベースに保存されている値がNULLである場合は、現在のシステムデフォルト上限値を返す。デフォルト時間のキャッシュを使用する。

    Args:
        db (asyncpg.Connection): データベース接続
        tag (str): プレイヤータグ

    Returns:
        int: 現在のバトル履歴保存上限値。プレイヤーが見つからなかった場合はNone。
    """
    # [この部分は公開用リポジトリでは非公開にされています]

async def extend_battle_log_limit(db: asyncpg.Connection, tag: str, new_limit: int | None):
    """指定したプレイヤーのバトル履歴保存上限を書き換える。

    Args:
        db (asyncpg.Connection): データベース接続
        tag (str): プレイヤータグ
        new_limit (int): 新しいバトル履歴保存上限。None(=システムのデフォルト値を参照させるように変更)も可能。
    """
    # [この部分は公開用リポジトリでは非公開にされています]

async def search_players_fast(
    query: str,
    db: asyncpg.Connection,
    *,
    page: int = 1,
    per_page: int = 120,
    include_previous_names: bool = False,
    exact_match: bool = False,
    include_invalid: bool = False,
    is_acquire_automatically: bool | None = None,
    is_viewed: bool | None = None,
) -> tuple[list[dict[str, str]], int, bool]:
    """
    DB側で完結する単一クエリ方式のプレイヤー検索関数。

    include_previous_names=True の場合でも、まず現在名のみで件数を確認し、
    既に MAX_SEARCH_RESULTS を超えている場合は旧名検索をスキップして
    現在名のみの結果を返す（上限内の場合のみ旧名 OR 条件を追加）。

    Returns:
        (results, total_count, is_capped):
            results: 取得したプレイヤーリスト
            total_count: 実際の/上限で打ち切った件数
            is_capped: MAX_SEARCH_RESULTS を超えて結果を打ち切った場合 True
    """
    # [この部分は公開用リポジトリでは非公開にされています]

# [この部分は公開用リポジトリでは非公開にされています]


# [この部分は公開用リポジトリでは非公開にされています]

def _invert_result(result: str) -> str:
    """勝敗を反転させるヘルパー関数"""
    if result == 'w': return 'l'
    if result == 'l': return 'w'
    return 'd' # [この部分は公開用リポジトリでは非公開にされています]

async def _calculate_and_store_synergy_stats(db: asyncpg.Connection, ranked_battles: list[RankedBattle], target_date: datetime.date) -> None:
    # [この部分は公開用リポジトリでは非公開にされています]

async def _calculate_and_store_composition_stats(db: asyncpg.Connection, ranked_battles: list[RankedBattle], target_date: datetime.date) -> None:
    # [この部分は公開用リポジトリでは非公開にされています]

async def _calculate_and_store_multi_brawler_stats(db: asyncpg.Connection, trophy_battles: list[TrophyBattle], target_date: datetime.date) -> None:
    """
    トロフィーバトル（マルチプレイ）のキャラクター統計を集計し、ranked_stats_brawlerテーブルに格納する。
    データは rank_tier = -2 として保存される。
    """
    # [この部分は公開用リポジトリでは非公開にされています]


# [この部分は公開用リポジトリでは非公開にされています]
# [この部分は公開用リポジトリでは非公開にされています]
# [この部分は公開用リポジトリでは非公開にされています]

# [この部分は公開用リポジトリでは非公開にされています]

# [この部分は公開用リポジトリでは非公開にされています]

# [この部分は公開用リポジトリでは非公開にされています]

def _logistic_scale(value: float, min_val: float, max_val: float) -> float:
    """
    シグモイド関数（S字カーブ）を用いて、指標の値を0.0～100.0のスコアに変換する。
    min_valで約10点、max_valで約90点、中間点で50点となるように調整されている。
    """
    if min_val >= max_val:
        return 50.0  # [この部分は公開用リポジトリでは非公開にされています]

class SynergyStat:
    """
    集計されたキャラクターペアの相性統計データを格納するためのクラス。
    """
    # [この部分は公開用リポジトリでは非公開にされています]

class CompositionStat:
    """
    集計された3キャラクターのチーム編成の統計データを格納するためのクラス。
    """
    # [この部分は公開用リポジトリでは非公開にされています]

class RankedStatsData(TypedDict):
    """
    集計された各種ガチバトル統計データをまとめるための型定義。
    """
    brawler_stats: list[BrawlerStat]
    synergy_stats: list[SynergyStat]
    composition_stats: list[CompositionStat]

class MatchupStat(TypedDict):
    """特定のキャラクターとの相性情報を格納するための型定義"""
    partner_brawler_id: int
    partner_name_ja: str | None
    partner_name_en: str
    partner_rarity: int
    synergy_win_rate: float
    synergy_games_played: float
    synergy_score: float
    synergy_rank_grade: str
    counter_win_rate: float
    counter_games_played: float

class BrawlerAnalysisData(TypedDict):
    """get_brawler_analysisが返す、キャラクターの総合分析データ"""
    overall_stats: BrawlerStat
    performance_by_mode: list[BrawlerStat]
    performance_by_map: list[BrawlerStat]
    matchups: list[MatchupStat] # [この部分は公開用リポジトリでは非公開にされています]

async def get_ban_suggestions(
        db: asyncpg.Connection,
        mode: str | None = None,
        map_name: str | None = None,
        rank_tier: int | None = None,
        banned_brawlers: list[int] | None = None,
        use_cache: bool = True
    ) -> list[BrawlerStat]:
    """
    指定された条件下で、BANすべきキャラクターを脅威度順に提案する。
    10分間のキャッシュを使用する。

    Args:
        db (asyncpg.Connection): データベース接続
        mode (str | None): ゲームモード。Noneの場合は全モード対象。
        map_name (str | None): マップ名。Noneの場合は全マップ対象。
        rank_tier (int | None): ランク帯。Noneの場合は全ランク帯対象。
        banned_brawlers (list[int] | None): 既にBANされたキャラクターIDのリスト。
        use_cache (bool): キャッシュヒット時、利用するかどうか。デフォルトはTrue。

    Returns:
        list[BrawlerStat]: 脅威度（strength_score）の高い順にソートされたBrawlerStatのリスト。
    """
    # [この部分は公開用リポジトリでは非公開にされています]

async def get_current_ranked_pool(db: asyncpg.Connection, use_cache: bool = True) -> list[dict]:
    """
    現在ガチバトルで出現しているモードとマップの一覧を取得する。
    直近7日間のデータを元に生成し、デフォルト時間のキャッシュを使用する。
    
    Args:
        db (asyncpg.Connection): データベース接続
        use_cache (bool): キャッシュヒット時、利用するかどうか。デフォルトはTrue。

    Returns:
        list[dict]: 辞書はmode, mode_en, mode_ja, mapsの値を持つ。mapsはmap_enとmap_jaの値を持つ。
    """
    # [この部分は公開用リポジトリでは非公開にされています]

async def get_brawler_analysis(
        db: asyncpg.Connection,
        brawler_id: int,
        start_date: datetime.date | None = None,
        end_date: datetime.date | None = None,
        use_cache: bool = True,
        db_timeout: int | float | None = None
    ) -> BrawlerAnalysisData:
    """
    指定されたキャラクターに関する総合的な分析データを取得する。
    
    Args:
        db (asyncpg.Connection): データベース接続
        brawler_id: キャラクターID
        start_date (datetime.date | None): 開始日。デフォルトはNoneで、Noneの場合は30日前。
        end_date (datetime.date | None): 終了日。デフォルトはNoneで、Noneの場合は最新の統計日。
        use_cache (bool): キャッシュヒット時、利用するかどうか。デフォルトはTrue。

    Returns:
        BrawlerAnalysisData: overall_stats: BrawlerStat, performance_by_mode: list[BrawlerStat], performance_by_map: list[BrawlerStat], matchups: list[MatchupStat], best_compositions: list[CompositionStat], performance_trend: dict[str, list]の項目を持つ辞書。
    """
    # [この部分は公開用リポジトリでは非公開にされています]

class PickSuggestion:
    """
    1キャラクター分のピック推奨情報を格納し、総合的なおすすめ度を評価するためのクラス。
    """
    def __init__(self, brawler: BrawlerStat, synergy_score: float, counter_score: float, defensive_score: float):
        self.brawler: BrawlerStat = brawler
        # [この部分は公開用リポジトリでは非公開にされています]

async def _calculate_team_power(
    team_picks: list[int],
    brawler_stats_map: dict[int, BrawlerStat],
    synergy_stats_map: dict[tuple[int, int], SynergyStat]
) -> float:
    """チームの基礎戦闘力を計算するヘルパー関数"""
    # [この部分は公開用リポジトリでは非公開にされています]

async def predict_win_rate(
    db: asyncpg.Connection,
    mode: str | None = None,
    map_name: str | None = None,
    rank_tier: int | None = None,
    team_1_picks: list[int] | None = None,
    team_2_picks: list[int] | None = None,
    use_cache: bool = True
) -> float:
    """
    2つのチーム編成に基づき、チーム1の予想勝率を算出する。
    3分間のキャッシュを使用する。

    Args:
        db (asyncpg.Connection): データベース接続
        mode (str | None): ゲームモード。
        map_name (str | None): マップ名。
        rank_tier (int | None): ランク帯。
        team_1_picks (list[int] | None): チーム1のキャラクターIDリスト。
        team_2_picks (list[int] | None): チーム2のキャラクターIDリスト。
        use_cache (bool): キャッシュヒット時、利用するかどうか。デフォルトはTrue。

    Returns:
        float: チーム1の予想勝率（0.0～100.0）。
    """
    # [この部分は公開用リポジトリでは非公開にされています]


# [この部分は公開用リポジトリでは非公開にされています]

async def get_max_accessory_counts(db: asyncpg.Connection) -> dict[str, int]:
    """
    育成計算機等で利用するため、playersテーブルからレベルが20以上のプレイヤーを対象に、各アクセサリーの最大所持数を取得します。
    Redisキャッシュを使用して10分間キャッシュします。
    """
    # [この部分は公開用リポジトリでは非公開にされています]

async def calculate_and_save_skin_stats(db: asyncpg.Connection) -> None:
    """
    レベル20以上の全プレイヤーを対象に装備率を、BSInfo所持スキン対応後に更新された
    レベル20以上のプレイヤーを対象に所持率を集計して保存します。
    """
    # [この部分は公開用リポジトリでは非公開にされています]

async def calculate_and_save_battle_card_stats(db: asyncpg.Connection) -> None:
    """
    レベル20（アクティブ）以上の全プレイヤーを対象に、バトルカード（ピンズ、タイトル、フレーム）の「装備率」を集計して保存します。
    """
    # [この部分は公開用リポジトリでは非公開にされています]

async def calculate_and_save_player_icon_stats(db: asyncpg.Connection) -> None:
    """
    レベル20（アクティブ）以上の全プレイヤーを対象に、プレイヤーアイコンの装備率を集計して保存します。
    """
    # [この部分は公開用リポジトリでは非公開にされています]

async def calculate_and_save_accessory_stats(db: asyncpg.Connection) -> None:
    """高レベルプレイヤーのアクセサリー所持率を計算してDBに保存する。

    1. select_high_level_players() でn人選定
    2. 選定されたプレイヤーのplayer_brawlersデータから直接所持情報を取得
    3. 各キャラクターごとにアクセサリーの所持数を集計
    4. 所持率を計算して brawler_accessory_stats テーブルへ UPSERT

    Args:
        db: データベース接続
    """
    # [この部分は公開用リポジトリでは非公開にされています]

async def get_accessory_stats(db: asyncpg.Connection, brawler_id: int, days: int = 7) -> dict[str, Any]:
    """指定キャラの直近最大N日間のアクセサリー所持率を移動平均で返す。

    Args:
        db: データベース接続
        brawler_id: キャラクターID
        days: 平均を取る最大日数（デフォルト7）

    Returns:
        {
            "gadgets": {gadget_id: avg_rate, ...},
            "star_powers": {sp_id: avg_rate, ...},
            "gears": {gear_id: avg_rate, ...},
            "hyper_charges": {hc_id: avg_rate, ...},
            "buffies": {"gadget": avg_rate, "star_power": avg_rate, "hyper_charge": avg_rate}
        }
        データが存在しない場合は空の dict を返す。
    """
    # [この部分は公開用リポジトリでは非公開にされています]
