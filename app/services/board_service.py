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
from app.services import reaction_rules
from app.services.mention_rules import has_mention_mark, name_in_text_sql, pick_mentions
from app.core.board_trending import GENERAL_BOARD_TRENDING
from app.core.text_search import GeneralBoardSearch, LIKE_ESCAPE_SQL, escape_like_literal, search_normalize_sql
from app.services.all_maps_service import resolve_board_map
from app.services.map_mode_catalog import DEFAULT_BOARD_COLORS, ensure_catalog, get_mode_board_colors
# [この部分は公開用リポジトリでは非公開にされています]


#* /---*---*---*---*---*---*---*---*/
#* 投稿関連
#* /---*---*---*---*---*---*---*---*/
# なんでも掲示板で条件設定を許可しないカテゴリ。切替中の入力はフロントで保持し、
# 投稿時・読み込み時にサーバー側で破棄／無視する。
_GENERAL_NO_CONDITION_CATEGORIES = frozenset({"chat", "question", "tournament", "offtopic"})
# なんでも掲示板の「大会」カテゴリー。リンクの代わりに、主催・参加している大会を custom_settings に持つ
GENERAL_TOURNAMENT_CATEGORY = "tournament"
# なんでも掲示板の一覧に掲載された日時（リポストした投稿はリポストした日時）。式インデックス idx_posts_type_activity_at_desc と同じ式にする
GENERAL_POST_ACTIVITY_AT_SQL = "COALESCE(reposted_at, created_at)"
# リポストしたときに変える、なんでも掲示板の一覧キャッシュの版。直後の読み直しで並び順・表示が古いままにならないようにする。
# 一覧キャッシュ（最長30秒）より長く持てば、期限切れ後に古いキャッシュを拾うことはない
GENERAL_POSTS_CACHE_VERSION_KEY = "general_posts_cache_version"
GENERAL_POSTS_CACHE_VERSION_TTL_SECONDS = 10 * 60

# チーム募集のクローズ関連定数（変更しやすいようモジュール先頭に定義）
TEAM_POST_CLOSE_COOLDOWN_SECONDS = 120
TEAM_POST_AUTO_CLOSE_SECONDS = 6 * 60 * 60
TEAM_POST_LATER_AUTO_CLOSE_SECONDS = 7 * 24 * 60 * 60
THEME_POST_TYPE = "theme"
THEME_CATEGORY_BRAWLER = "brawler"
THEME_CATEGORY_MAP = "map"
LEGACY_THEME_POST_TYPE = "brawler_guide"
# キャラ掲示板のカード・チャットヘッダーのグラデーション色 (color1, color2)。レアリティ別
THEME_BRAWLER_RARITY_COLORS: dict[int, tuple[str, str]] = {
    1: ("#d4f5f7", "#c4f0f3"),
    2: ("#d4f5d4", "#c4f0c4"),
    3: ("#d4e0f8", "#c4d6f5"),
    4: ("#f0d4fb", "#eac4f8"),
    5: ("#fad4d4", "#f5c4c4"),
    6: ("#f9f5d4", "#f5f0c4"),
    7: ("#faefd4", "#f5e8c4"),
}
THEME_DEFAULT_BOARD_COLORS: tuple[str, str] = DEFAULT_BOARD_COLORS


def is_theme_post_type(post_type: str | None) -> bool:
    """テーマ掲示板（旧キャラクター図鑑スレッド含む）かどうかを返す。"""
    return post_type in (THEME_POST_TYPE, LEGACY_THEME_POST_TYPE)


def is_team_post_effectively_closed(
    *,
    post_type: str | None,
    is_closed: bool | None,
    auto_close_exempt: bool | None,
    created_at: datetime.datetime | None,
    is_later_recruitment: bool | None = False,
    now: datetime.datetime | None = None,
) -> bool:
    """チーム募集投稿が実効的にクローズされているかを判定する。"""
    if is_closed:
        return True
    if post_type != "team" or auto_close_exempt or created_at is None:
        return False
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=datetime.timezone.utc)
    age_seconds = (now - created_at.astimezone(datetime.timezone.utc)).total_seconds()
    auto_close_seconds = (
        TEAM_POST_LATER_AUTO_CLOSE_SECONDS if is_later_recruitment else TEAM_POST_AUTO_CLOSE_SECONDS
    )
    return age_seconds >= auto_close_seconds


class HostPlayer:
    """掲示板の投稿のホストプレイヤー情報を入れるクラス。
    """
    def __init__(self) -> None:
        self.tag: str = None
        self.name: str = None
        self.icon_id: int = None
        self.icon_path: str | None = None
        self.current_trophies: int = None
        self.highest_trophies: int = None
        self.max_power_brawlers: int = None
        self.team_victories: int = None
        self.solo_victories: int = None
        self.duo_victories: int = None
        self.unlocked_brawlers: int = None
        self.max_tier_brawlers: int = None
        self.ranked_current_rank: int | None = None
        self.ranked_current_score: int | None = None
        self.ranked_highest_rank: int | None = None
        self.ranked_highest_score: int | None = None
        self.solo_pl_rank: int | None = None
        self.team_pl_rank: int | None = None
        self.region: str | None = None
        self.region_ja: str | None = None
        self.region_en: str | None = None
        self.legacy_rank_35s: int | None = None
        self.prestige: int | None = None
        self.total_prestige_level: int | None = None
    
    def to_dict(self) -> dict:
        return {
            "tag": self.tag,
            "name": self.name,
            "icon_id": self.icon_id,
            "icon_path": self.icon_path,
            "current_trophies": self.current_trophies,
            "highest_trophies": self.highest_trophies,
            "max_power_brawlers": self.max_power_brawlers,
            "team_victories": self.team_victories,
            "solo_victories": self.solo_victories,
            "duo_victories": self.duo_victories,
            "unlocked_brawlers": self.unlocked_brawlers,
            "max_tier_brawlers": self.max_tier_brawlers,
            "ranked_current_rank": self.ranked_current_rank,
            "ranked_current_score": self.ranked_current_score,
            "ranked_highest_rank": self.ranked_highest_rank,
            "ranked_highest_score": self.ranked_highest_score,
            "solo_pl_rank": self.solo_pl_rank,
            "team_pl_rank": self.team_pl_rank,
            "region": self.region,
            "region_ja": self.region_ja,
            "region_en": self.region_en,
            "legacy_rank_35s": self.legacy_rank_35s,
            "prestige": self.prestige,
            "total_prestige_level": self.total_prestige_level
        }
        
    @classmethod
    async def from_db(cls, dbrow: asyncpg.Record, db: asyncpg.Connection):
        instance = cls()
        instance.tag = dbrow["tag"]
        instance.name = dbrow["name"]
        instance.icon_id = dbrow["icon_id"]
        instance.icon_path = "/" + get_icon_path(instance.icon_id)
        instance.current_trophies = dbrow["current_trophies"]
        instance.highest_trophies = dbrow["highest_trophies"]
        instance.max_power_brawlers = dbrow["max_power_brawlers"]
        instance.team_victories = dbrow["team_victories"]
        instance.solo_victories = dbrow["solo_victories"]
        instance.duo_victories = dbrow["duo_victories"]
        instance.unlocked_brawlers = dbrow["unlocked_brawlers"]
        instance.max_tier_brawlers = dbrow["max_tier_brawlers"]
        instance.ranked_current_rank = dbrow["acc_ranked_current_rank"]
        instance.ranked_current_score = dbrow["ranked_current_score"]
        instance.ranked_highest_rank = dbrow["acc_ranked_highest_rank"]
        instance.ranked_highest_score = dbrow["ranked_highest_score"]
        instance.solo_pl_rank = dbrow["solo_pl_rank"]
        instance.team_pl_rank = dbrow["team_pl_rank"]
        instance.region = dbrow["region"]
        instance.region_ja = await get_region_name(db, "ja", instance.region)
        instance.region_en = await get_region_name(db, "en", instance.region)
        instance.legacy_rank_35s = dbrow["legacy_rank_35s"]
        instance.prestige = dbrow["prestige"]
        instance.total_prestige_level = dbrow["total_prestige_level"]
        return instance
    
    @classmethod
    async def from_dict(cls, data: dict):
        instance = cls()
        instance.tag = data.get("tag")
        instance.name = data.get("name")
        instance.icon_id = data.get("icon_id")
        instance.icon_path = data.get("icon_path")
        instance.current_trophies = data.get("current_trophies")
        instance.highest_trophies = data.get("highest_trophies")
        instance.max_power_brawlers = data.get("max_power_brawlers")
        instance.team_victories = data.get("team_victories")
        instance.solo_victories = data.get("solo_victories")
        instance.duo_victories = data.get("duo_victories")
        instance.unlocked_brawlers = data.get("unlocked_brawlers")
        instance.max_tier_brawlers = data.get("max_tier_brawlers")
        instance.ranked_current_rank = data.get("ranked_current_rank")
        instance.ranked_current_score = data.get("ranked_current_score")
        instance.ranked_highest_rank = data.get("ranked_highest_rank")
        instance.ranked_highest_score = data.get("ranked_highest_score")
        instance.solo_pl_rank = data.get("solo_pl_rank")
        instance.team_pl_rank = data.get("team_pl_rank")
        instance.region = data.get("region")
        instance.region_ja = data.get("region_ja")
        instance.region_en = data.get("region_en")
        instance.legacy_rank_35s = data.get("legacy_rank_35s")
        instance.prestige = data.get("prestige")
        instance.total_prestige_level = data.get("total_prestige_level")
        return instance

async def get_hostplayer(db: asyncpg.Connection, tag: str) -> HostPlayer:
    """ホストプレイヤーの情報をデータベースから取得する。APIへのアクセスは行わない。3分間のキャッシュを使用する。

    Args:
        db (asyncpg.Connection): データベース接続
        tag (str): プレイヤータグ

    Raises:
        DataBaseError: データベースエラー

    Returns:
        HostPlayer: ホストプレイヤーの情報
    """
    cache_key = f"hostplayer:{tag}"
    cached_data = await get_cache(cache_key)
    if cached_data:
        return await HostPlayer.from_dict(cached_data)
    
    try:
        query = "SELECT * FROM players WHERE tag = $1 AND is_invalid = FALSE"
        result = await db.fetchrow(query, tag)
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e
    
    if result:
        hostplayer: HostPlayer = await HostPlayer.from_db(result, db)
        await set_cache(key=cache_key, value=hostplayer.to_dict(), ttl=180)
        return hostplayer
    else:
        return None

class HostClub:
    """掲示板の投稿のホストクラブ情報を入れるクラス。
    """
    def __init__(self) -> None:
        self.tag: str = None
        self.name: str = None
        self.description: str | None = None
        self.type: str = None
        self.trophies: int = None
        self.required_trophies: int = None
        self.badge_id: int = None
        self.region: str | None = None
        self.region_ja: str | None = None
        self.region_en: str | None = None
        self.members_online: int | None = None
        self.is_family_friendly: bool | None = None
        self.member_count: int = None
        self.total_megapig_wins: int = None
        self.total_remaining_tickets: int = None
        self.used_all_tickets_members: int = None
        self.played_megapig_members: int = None
    
    def to_dict(self) -> dict:
        return {
            "tag": self.tag,
            "name": self.name,
            "description": self.description,
            "type": self.type,
            "trophies": self.trophies,
            "required_trophies": self.required_trophies,
            "badge_id": self.badge_id,
            "region": self.region,
            "region_ja": self.region_ja,
            "region_en": self.region_en,
            "members_online": self.members_online,
            "is_family_friendly": self.is_family_friendly,
            "member_count": self.member_count,
            "total_megapig_wins": self.total_megapig_wins,
            "total_remaining_tickets": self.total_remaining_tickets,
            "used_all_tickets_members": self.used_all_tickets_members,
            "played_megapig_members": self.played_megapig_members
        }
        
    @classmethod
    async def from_db(cls, dbrow: asyncpg.Record, club_data: Club | None, db: asyncpg.Connection):
        instance = cls()
        instance.tag = dbrow["tag"]
        instance.name = dbrow["name"]
        instance.description = club_data.description if club_data else None
        instance.type = dbrow["type"]
        instance.trophies = dbrow["trophies"]
        instance.required_trophies = club_data.required_trophies if club_data else None
        instance.badge_id = dbrow["badge_id"]
        instance.region = dbrow["region"]
        instance.region_ja = await get_region_name(db, "ja", instance.region)
        instance.region_en = await get_region_name(db, "en", instance.region)
        instance.members_online = club_data.members_online if club_data else None
        instance.is_family_friendly = club_data.is_family_friendly if club_data else None
        instance.member_count = len(club_data.members) if club_data else None
        instance.total_megapig_wins = club_data.total_megapig_wins if club_data else None
        instance.total_remaining_tickets = club_data.total_remaining_tickets if club_data else None
        instance.used_all_tickets_members = club_data.used_all_tickets_members if club_data else None
        instance.played_megapig_members = club_data.played_megapig_members if club_data else None
        return instance
    
    @classmethod
    async def from_dict(cls, data: dict):
        instance = cls()
        instance.tag = data.get("tag")
        instance.name = data.get("name")
        instance.description = data.get("description")
        instance.type = data.get("type")
        instance.trophies = data.get("trophies")
        instance.required_trophies = data.get("required_trophies")
        instance.badge_id = data.get("badge_id")
        instance.region = data.get("region")
        instance.region_ja = data.get("region_ja")
        instance.region_en = data.get("region_en")
        instance.members_online = data.get("members_online")
        instance.is_family_friendly = data.get("is_family_friendly")
        instance.member_count = data.get("member_count")
        instance.total_megapig_wins = data.get("total_megapig_wins")
        instance.total_remaining_tickets = data.get("total_remaining_tickets")
        instance.used_all_tickets_members = data.get("used_all_tickets_members")
        instance.played_megapig_members = data.get("played_megapig_members")
        return instance

async def get_hostclub(db: asyncpg.Connection, tag: str) -> HostClub:
    """ホストクラブの情報をデータベースから取得する。追加情報取得のためにAPIへのアクセスを行う。20-28時間(ランダム)のキャッシュを使用する。
    APIが落ちている場合はDBの情報のみで組み立て、掲示板の表示を維持する。

    Args:
        db (asyncpg.Connection): データベース接続
        tag (str): プレイヤータグ

    Raises:
        DataBaseError: データベースエラー

    Returns:
        HostClub: ホストクラブの情報
    """
    cache_key = f"hostclub:{tag}"
    cached_data = await get_cache(cache_key)
    if cached_data:
        return await HostClub.from_dict(cached_data)
    
    try:
        query = "SELECT * FROM clubs WHERE tag = $1 AND is_invalid = FALSE"
        result = await db.fetchrow(query, tag)
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e

    if not result:
        return None

    club_data = None
    try:
        if not await is_brawlstats_api_unavailable():
            await asyncio.sleep(0.1) # API使用前に少し待機(連続使用対策)
        club_data = await get_club(tag, db, use_long_cache=True)
    except BrawlStarsAPIError:
        logger.debug(f"ホストクラブ {tag} のAPI取得に失敗したため、DBの情報のみで表示します。")
    
    hostclub: HostClub = await HostClub.from_db(result, club_data, db)
    # API成功時は長期キャッシュ。失敗時は短くしてメンテ明け後にすぐ取り直す。
    ttl = random.randint(20 * 60 * 60, 28 * 60 * 60) if club_data else 60
    await set_cache(key=cache_key, value=hostclub.to_dict(), ttl=ttl)
    return hostclub

class Post:
    """掲示板の投稿クラス。
    """
    def __init__(self) -> None:
        self.id: int = None
        self.type: str = None # "team"/"friend"/"club"
        self.host_id: int | None = None
        self.host_user_name: str | None = None
        self.host_user_icon_id: int | None = None
        self.host_user_icon_path: str | None = None
        self.host_ip: str = None
        self.host_anonymous_id: str | None = None
        self.created_at: datetime.datetime = None
        self.reposted_at: datetime.datetime | None = None # 最後にリポストした日時（なんでも掲示板のみ）
        self.region: str | None = None
        self.hostplayer: HostPlayer | None = None
        self.hostclub: HostClub | None = None
        self.link: str | None = None
        self.comment: str | None = None
        self.conditions_application_type: str = None # "and"/"or"
        self.chat_permission_level: int = None # 0(チャット不可)、10(ホストのみ)、20(ホストと、条件を満たしたログイン済みユーザーのみ)、30(ホストと、ログイン済みユーザーのみ)、40(全ユーザー)
        self.is_deleted: bool = None
        self.is_closed: bool = False
        self.auto_close_exempt: bool = False
        self.is_later_recruitment: bool = False
        self.category: str | None = None
        self.mode: str | None = None
        self.hashtags: list[str] = None
        self.custom_settings: dict = None
        self.permitted_ids: list[int] = None
        self.prohibited_ids: list[int] = None
        self.required_highest_trophies: int | None = None
        self.required_current_trophies: int | None = None
        self.required_ranked_highest_rank: int | None = None
        self.required_ranked_current_rank: int | None = None
        self.required_ranked_highest_score: int | None = None
        self.required_ranked_current_score: int | None = None
        self.required_solo_pl_rank: int | None = None
        self.required_max_power_brawlers: int | None = None
        self.required_prestige: int | None = None
        self.other_conditions: dict = None
        self.message_count: int = None
        self.up_vote_count: int = 0
        self.down_vote_count: int = 0
        self.is_up_voted_by_current_user: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "host_id": self.host_id,
            "host_user_name": self.host_user_name,
            "host_user_icon_id": self.host_user_icon_id,
            "host_user_icon_path": self.host_user_icon_path,
            "host_ip": self.host_ip,
            "host_anonymous_id": self.host_anonymous_id,
            "created_at": format_utc_datetime(self.created_at),
            "reposted_at": format_utc_datetime(self.reposted_at) if self.reposted_at else None,
            "activity_at": format_utc_datetime(self.activity_at) if self.activity_at else None,
            "region": self.region,
            "hostplayer": self.hostplayer.to_dict() if self.hostplayer else None,
            "hostclub": self.hostclub.to_dict() if self.hostclub else None,
            "link": self.link,
            "comment": self.comment,
            "conditions_application_type": self.conditions_application_type,
            "chat_permission_level": self.chat_permission_level,
            "is_deleted": self.is_deleted,
            "is_closed": self.is_closed,
            "auto_close_exempt": self.auto_close_exempt,
            "is_later_recruitment": self.is_later_recruitment,
            "is_effectively_closed": self.is_effectively_closed,
            "category": self.category,
            "mode": self.mode,
            "hashtags": self.hashtags,
            "custom_settings": self.custom_settings,
            "permitted_ids": self.permitted_ids,
            "prohibited_ids": self.prohibited_ids,
            "required_highest_trophies": self.required_highest_trophies,
            "required_current_trophies": self.required_current_trophies,
            "required_ranked_highest_rank": self.required_ranked_highest_rank,
            "required_ranked_current_rank": self.required_ranked_current_rank,
            "required_ranked_highest_score": self.required_ranked_highest_score,
            "required_ranked_current_score": self.required_ranked_current_score,
            "required_solo_pl_rank": self.required_solo_pl_rank,
            "required_max_power_brawlers": self.required_max_power_brawlers,
            "required_prestige": self.required_prestige,
            "other_conditions": self.other_conditions,
            "message_count": self.message_count,
            "up_vote_count": self.up_vote_count,
            "down_vote_count": self.down_vote_count,
            "is_up_voted_by_current_user": self.is_up_voted_by_current_user,
            "ago_ja": self.ago_ja,
            "ago_en": self.ago_en
        }

    def _discard_conditions_if_category_disallows(self) -> None:
        """雑談・質問・大会・オフトピックでは保存済み条件があっても効かないようにする。"""
        if self.type != "general" or self.category not in _GENERAL_NO_CONDITION_CATEGORIES:
            return
        self.conditions_application_type = "and"
        self.required_highest_trophies = None
        self.required_current_trophies = None
        self.required_ranked_highest_rank = None
        self.required_ranked_current_rank = None
        self.required_ranked_highest_score = None
        self.required_ranked_current_score = None
        self.required_solo_pl_rank = None
        self.required_max_power_brawlers = None
        self.required_prestige = None
        self.other_conditions = None
    
    @classmethod
    async def from_db(cls, dbrow: asyncpg.Record, db: asyncpg.Connection):
        instance = cls()
        instance.id = dbrow["id"]
        instance.type = dbrow["type"]
        instance.host_id = dbrow["host_id"]
        if instance.host_id:
            host_user = await get_user(db, instance.host_id)
            instance.host_user_name = host_user.name if host_user else None
            if instance.type == "general" and host_user:
                instance.host_user_icon_id = await get_player_icon_from_db(host_user.main_account, db)
                instance.host_user_icon_path = "/" + get_icon_path(instance.host_user_icon_id)
        instance.host_ip = str(dbrow["host_ip"])
        instance.host_anonymous_id = dbrow["host_anonymous_id"]
        instance.created_at = dbrow["created_at"]
        instance.reposted_at = dbrow["reposted_at"] if "reposted_at" in dbrow.keys() else None
        instance.region = dbrow["region"]
        host_player_tag = dbrow["host_player_tag"]
        if host_player_tag:
            instance.hostplayer = await get_hostplayer(db, host_player_tag)
        host_club_tag = dbrow["host_club_tag"]
        if host_club_tag:
            instance.hostclub = await get_hostclub(db, host_club_tag)
        instance.link = dbrow["link"]
        instance.comment = dbrow["comment"]
        instance.conditions_application_type = dbrow["conditions_application_type"]
        instance.chat_permission_level = dbrow["chat_permission_level"]
        instance.is_deleted = dbrow["is_deleted"]
        instance.is_closed = bool(dbrow["is_closed"]) if "is_closed" in dbrow.keys() else False
        instance.auto_close_exempt = bool(dbrow["auto_close_exempt"]) if "auto_close_exempt" in dbrow.keys() else False
        instance.is_later_recruitment = (
            bool(dbrow["is_later_recruitment"]) if "is_later_recruitment" in dbrow.keys() else False
        )
        instance.category = dbrow["category"]
        instance.mode = dbrow["mode"]
        instance.hashtags = dbrow["hashtags"]
        instance.custom_settings = dbrow["custom_settings"]
        instance.permitted_ids = dbrow["permitted_ids"]
        instance.prohibited_ids = dbrow["prohibited_ids"]
        instance.required_highest_trophies = dbrow["required_highest_trophies"]
        instance.required_current_trophies = dbrow["required_current_trophies"]
        instance.required_ranked_highest_rank = dbrow["required_ranked_highest_rank"]
        instance.required_ranked_current_rank = dbrow["required_ranked_current_rank"]
        instance.required_ranked_highest_score = dbrow["required_ranked_highest_score"]
        instance.required_ranked_current_score = dbrow["required_ranked_current_score"]
        instance.required_solo_pl_rank = dbrow["required_solo_pl_rank"]
        instance.required_max_power_brawlers = dbrow["required_max_power_brawlers"]
        instance.required_prestige = dbrow["required_prestige"]
        instance.other_conditions = dbrow["other_conditions"]
        instance.message_count = await get_message_count(db, instance.id)
        instance._discard_conditions_if_category_disallows()
        return instance
    
    @classmethod
    async def from_dict(cls, data: dict):
        instance = cls()
        instance.id = data.get("id")
        instance.type = data.get("type")
        instance.host_id = data.get("host_id")
        instance.host_user_name = data.get("host_user_name")
        instance.host_user_icon_id = data.get("host_user_icon_id")
        instance.host_user_icon_path = data.get("host_user_icon_path")
        instance.host_ip = data.get("host_ip")
        instance.host_anonymous_id = data.get("host_anonymous_id")
        instance.created_at = parse_utc_datetime(data.get("created_at"))
        instance.reposted_at = parse_utc_datetime(data["reposted_at"]) if data.get("reposted_at") else None
        instance.region = data.get("region")
        instance.hostplayer = await HostPlayer.from_dict(data.get("hostplayer")) if data.get("hostplayer") else None
        instance.hostclub = await HostClub.from_dict(data.get("hostclub")) if data.get("hostclub") else None
        instance.link = data.get("link")
        instance.comment = data.get("comment")
        instance.conditions_application_type = data.get("conditions_application_type")
        instance.chat_permission_level = data.get("chat_permission_level")
        instance.is_deleted = data.get("is_deleted")
        instance.is_closed = bool(data.get("is_closed", False))
        instance.auto_close_exempt = bool(data.get("auto_close_exempt", False))
        instance.is_later_recruitment = bool(data.get("is_later_recruitment", False))
        instance.category = data.get("category")
        instance.mode = data.get("mode")
        instance.hashtags = data.get("hashtags")
        instance.custom_settings = data.get("custom_settings")
        instance.permitted_ids = data.get("permitted_ids")
        instance.prohibited_ids = data.get("prohibited_ids")
        instance.required_highest_trophies = data.get("required_highest_trophies")
        instance.required_current_trophies = data.get("required_current_trophies")
        instance.required_ranked_highest_rank = data.get("required_ranked_highest_rank")
        instance.required_ranked_current_rank = data.get("required_ranked_current_rank")
        instance.required_ranked_highest_score = data.get("required_ranked_highest_score")
        instance.required_ranked_current_score = data.get("required_ranked_current_score")
        instance.required_solo_pl_rank = data.get("required_solo_pl_rank")
        instance.required_max_power_brawlers = data.get("required_max_power_brawlers")
        instance.required_prestige = data.get("required_prestige")
        instance.other_conditions = data.get("other_conditions")
        instance.message_count = data.get("message_count")
        instance.up_vote_count = data.get("up_vote_count", 0)
        instance.down_vote_count = data.get("down_vote_count", 0)
        instance.is_up_voted_by_current_user = data.get("is_up_voted_by_current_user", False)
        instance._discard_conditions_if_category_disallows()
        return instance

    async def delete(self, db: asyncpg.Connection) -> None:
        """投稿を削除(論理削除)する。削除後、キャッシュがクリアされる。

        Args:
            db (asyncpg.Connection): データベース接続
            
        Raises:
            DataBaseError: データベースエラー
        """
        cache_key = f"post:{self.id}"
        query = "UPDATE posts SET is_deleted = $1 WHERE id = $2"
        params = (True, self.id)
        try:
            await db.execute(query, *params)
        except asyncpg.PostgresError as e:
            raise DataBaseError(e) from e
        await delete_cache(cache_key)
        logger.info(f"ID: {self.id} ({self.type})の投稿を削除しました")

    async def close(self, db: asyncpg.Connection) -> None:
        """チーム募集をクローズする。キャッシュをクリアする。"""
        cache_key = f"post:{self.id}"
        query = "UPDATE posts SET is_closed = TRUE WHERE id = $1"
        try:
            await db.execute(query, self.id)
        except asyncpg.PostgresError as e:
            raise DataBaseError(e) from e
        self.is_closed = True
        await delete_cache(cache_key)
        logger.info(f"ID: {self.id} ({self.type})の投稿をクローズしました")

    async def reopen(self, db: asyncpg.Connection) -> None:
        """チーム募集のクローズを解除する。以降の自動クローズ対象外にする。"""
        cache_key = f"post:{self.id}"
        query = "UPDATE posts SET is_closed = FALSE, auto_close_exempt = TRUE WHERE id = $1"
        try:
            await db.execute(query, self.id)
        except asyncpg.PostgresError as e:
            raise DataBaseError(e) from e
        self.is_closed = False
        self.auto_close_exempt = True
        await delete_cache(cache_key)
        logger.info(f"ID: {self.id} ({self.type})の投稿を再開しました")
    
    async def add_permitted_id(self, db: asyncpg.Connection, permitted_id: int) -> bool:
        """新しい許可IDを追加する。編集後、キャッシュがクリアされる。

        Args:
            db (asyncpg.Connection): データベース接続
            permitted_id (int): 追加したいユーザーのID
            
        Raises:
            DataBaseError: データベースエラー
            
        Returns:
            bool: 追加を行ったかどうか。すでに存在しておりスキップした場合はFalse。
        """
        cache_key = f"post:{self.id}"
        query = """
            UPDATE posts
            SET permitted_ids = permitted_ids || to_jsonb($1::int)
            WHERE id = $2 AND NOT (permitted_ids @> to_jsonb(ARRAY[$1]::int[]));
            """
        params = (permitted_id, self.id)
        try:
            result = await db.execute(query, *params)
        except asyncpg.PostgresError as e:
            raise DataBaseError(e) from e
        if result.endswith("UPDATE 1"):
            await delete_cache(cache_key)
            logger.info(f"ID: {self.id} ({self.type})の投稿に、新しい許可ID({permitted_id})を追加しました")
            return True
        else:
            return False
        
    async def remove_permitted_id(self, db: asyncpg.Connection, permitted_id: int) -> bool:
        """指定した許可IDを削除する。編集後、キャッシュがクリアされる。

        Args:
            db (asyncpg.Connection): データベース接続
            permitted_id (int): 削除したいユーザーのID
            
        Raises:
            DataBaseError: データベースエラー
            
        Returns:
            bool: 削除を行ったかどうか。もともと存在せずスキップした場合はFalse。
        """
        cache_key = f"post:{self.id}"
        query = """
            UPDATE posts
            SET permitted_ids = (
                SELECT COALESCE(jsonb_agg(elem), '[]'::jsonb)
                FROM jsonb_array_elements(permitted_ids) AS elem
                WHERE elem <> to_jsonb($1::int)
            )
            WHERE id = $2 AND permitted_ids @> to_jsonb(ARRAY[$1]::int[]);
            """
        params = (permitted_id, self.id)
        try:
            result = await db.execute(query, *params)
        except asyncpg.PostgresError as e:
            raise DataBaseError(e) from e
        if result.endswith("UPDATE 1"):
            await delete_cache(cache_key)
            logger.info(f"ID: {self.id} ({self.type})の投稿から、許可ID({permitted_id})を削除しました")
            return True
        else:
            return False
        
    async def add_prohibited_id(self, db: asyncpg.Connection, prohibited_id: int) -> bool:
        """新しい禁止IDを追加する。編集後、キャッシュがクリアされる。

        Args:
            db (asyncpg.Connection): データベース接続
            prohibited_id (int): 追加したいユーザーのID
            
        Raises:
            DataBaseError: データベースエラー
            
        Returns:
            bool: 追加を行ったかどうか。すでに存在しておりスキップした場合はFalse。
        """
        cache_key = f"post:{self.id}"
        query = """
            UPDATE posts
            SET prohibited_ids = prohibited_ids || to_jsonb($1::int)
            WHERE id = $2 AND NOT (prohibited_ids @> to_jsonb(ARRAY[$1]::int[]));
            """
        params = (prohibited_id, self.id)
        try:
            result = await db.execute(query, *params)
        except asyncpg.PostgresError as e:
            raise DataBaseError(e) from e
        if result.endswith("UPDATE 1"):
            await delete_cache(cache_key)
            logger.info(f"ID: {self.id} ({self.type})の投稿に、新しい禁止ID({prohibited_id})を追加しました")
            return True
        else:
            return False
        
    async def remove_prohibited_id(self, db: asyncpg.Connection, prohibited_id: int) -> bool:
        """指定した禁止IDを削除する。編集後、キャッシュがクリアされる。

        Args:
            db (asyncpg.Connection): データベース接続
            prohibited_id (int): 削除したいユーザーのID
            
        Raises:
            DataBaseError: データベースエラー
            
        Returns:
            bool: 削除を行ったかどうか。もともと存在せずスキップした場合はFalse。
        """
        cache_key = f"post:{self.id}"
        query = """
            UPDATE posts
            SET prohibited_ids = (
                SELECT COALESCE(jsonb_agg(elem), '[]'::jsonb)
                FROM jsonb_array_elements(prohibited_ids) AS elem
                WHERE elem <> to_jsonb($1::int)
            )
            WHERE id = $2 AND prohibited_ids @> to_jsonb(ARRAY[$1]::int[]);
            """
        params = (prohibited_id, self.id)
        try:
            result = await db.execute(query, *params)
        except asyncpg.PostgresError as e:
            raise DataBaseError(e) from e
        if result.endswith("UPDATE 1"):
            await delete_cache(cache_key)
            logger.info(f"ID: {self.id} ({self.type})の投稿から、禁止ID({prohibited_id})を削除しました")
            return True
        else:
            return False
    
    async def is_permitted_to_chat(self, db: asyncpg.Connection, user_id: int | None = None) -> bool:
        """この募集のチャットスレッドへの参加が許可されているかどうかを確認する。

        Args:
            db (asyncpg.Connection): データベース接続
            user_id (int | None): ユーザーID。ログインしていないユーザーについて確認する場合はNoneでよい。

        Returns:
            bool: チャットスレッドへの参加が許可されているかどうか
        """
        # [この部分は公開用リポジトリでは非公開にされています]
        user: User | None = None
        if user_id:
            user = await get_user(db, user_id)
            if user.id in self.prohibited_ids or user.is_prohibit_posting: # この投稿の禁止IDに含まれているか、ユーザー自体が投稿禁止状態の場合
                return False
            if user.is_admin or user.has_perm("chat.delete_messages"):
                return True # 管理者・メッセージ削除権限のある副管理者はどんなチャットにも参加できる
        if self.is_deleted: # 投稿が削除されている場合
            return False
        elif self.chat_permission_level <= 0: # チャットスレッド閉鎖
            return False
        elif 0 < self.chat_permission_level <= 10: # ホストのみ送信可
            return True if self.host_id and self.host_id == user_id else False
        elif 10 < self.chat_permission_level <= 20: # ホストと、条件を満たしたログイン済みユーザーのみ
            if self.host_id and self.host_id == user_id:
                return True
            if not user_id:
                return False
            try:
                if not user or not user.main_account:
                    return False
                main_account = await get_player_from_db(user.main_account, db)
            except Exception:
                return False # APIが使用できない場合は不可判定とする
            if self.conditions_application_type == "and": # すべての条件を満たしていないといけない
                if (self.required_highest_trophies and self.required_highest_trophies > main_account.highest_trophies) or \
                    (self.required_current_trophies and self.required_current_trophies > main_account.current_trophies) or \
                    (self.required_ranked_highest_rank and self.required_ranked_highest_rank > (main_account.acc_ranked_highest_rank or 0)) or \
                    (self.required_ranked_current_rank and self.required_ranked_current_rank > (main_account.acc_ranked_current_rank or 0)) or \
                    (self.required_ranked_highest_score and self.required_ranked_highest_score > (main_account.ranked_highest_score or 0)) or \
                    (self.required_ranked_current_score and self.required_ranked_current_score > (main_account.ranked_current_score or 0)) or \
                    (self.required_solo_pl_rank and self.required_solo_pl_rank > (main_account.solo_pl_rank or 0)) or \
                    (self.required_max_power_brawlers and self.required_max_power_brawlers > main_account.max_power_brawlers) or \
                    (self.required_prestige and self.required_prestige > (main_account.prestige or 0)):
                        return False
                else:
                    return True
            else: # いずれかの条件を満たしていれば良い
                if (self.required_highest_trophies and self.required_highest_trophies <= main_account.highest_trophies) or \
                    (self.required_current_trophies and self.required_current_trophies <= main_account.current_trophies) or \
                    (self.required_ranked_highest_rank and self.required_ranked_highest_rank <= (main_account.acc_ranked_highest_rank or 0)) or \
                    (self.required_ranked_current_rank and self.required_ranked_current_rank <= (main_account.acc_ranked_current_rank or 0)) or \
                    (self.required_ranked_highest_score and self.required_ranked_highest_score <= (main_account.ranked_highest_score or 0)) or \
                    (self.required_ranked_current_score and self.required_ranked_current_score <= (main_account.ranked_current_score or 0)) or \
                    (self.required_solo_pl_rank and self.required_solo_pl_rank <= (main_account.solo_pl_rank or 0)) or \
                    (self.required_max_power_brawlers and self.required_max_power_brawlers <= main_account.max_power_brawlers) or \
                    (self.required_prestige and self.required_prestige <= (main_account.prestige or 0)) or \
                    (not (self.required_highest_trophies or self.required_current_trophies or self.required_ranked_highest_rank or self.required_ranked_current_rank or \
                        self.required_ranked_highest_score or self.required_ranked_current_score or self.required_solo_pl_rank or self.required_max_power_brawlers or self.required_prestige)):
                        return True
                else:
                    return False
        elif 20 < self.chat_permission_level <= 30: # ホストと、すべてのログイン済みユーザーのみ
            return True if user else False
        elif self.chat_permission_level > 30: # 全ユーザーが送信可
            return True
        
    async def is_permitted_to_click_link(self, db: asyncpg.Connection, user_id: int | None = None) -> bool:
        """この募集のリンクのクリックが許可されているかどうかを確認する。リンクがそもそも設定されていない募集の場合は常にFalseが返る。

        Args:
            db (asyncpg.Connection): データベース接続
            user_id (int | None): ユーザーID。ログインしていないユーザーについて確認する場合はNoneでよい。

        Returns:
            bool: リンクのクリックが許可されているかどうか
        """
        if user_id:
            user = await get_user(db, user_id)
            if user.id in self.prohibited_ids: # この投稿の個別禁止IDに含まれている場合
                return False
            elif user.id in self.permitted_ids: # この投稿の個別許可IDに含まれている場合
                return True
            try:
                main_account = await get_player_from_db(user.main_account, db)
            except Exception:
                return False # APIが使用できない場合はクリック不可とする
            if self.conditions_application_type == "and": # すべての条件を満たしていないといけない
                if (self.required_highest_trophies and self.required_highest_trophies > main_account.highest_trophies) or \
                    (self.required_current_trophies and self.required_current_trophies > main_account.current_trophies) or \
                    (self.required_ranked_highest_rank and self.required_ranked_highest_rank > (main_account.acc_ranked_highest_rank or 0)) or \
                    (self.required_ranked_current_rank and self.required_ranked_current_rank > (main_account.acc_ranked_current_rank or 0)) or \
                    (self.required_ranked_highest_score and self.required_ranked_highest_score > (main_account.ranked_highest_score or 0)) or \
                    (self.required_ranked_current_score and self.required_ranked_current_score > (main_account.ranked_current_score or 0)) or \
                    (self.required_solo_pl_rank and self.required_solo_pl_rank > (main_account.solo_pl_rank or 0)) or \
                    (self.required_max_power_brawlers and self.required_max_power_brawlers > main_account.max_power_brawlers) or \
                    (self.required_prestige and self.required_prestige > (main_account.prestige or 0)):
                        return False
                else:
                    return True
            else: # いずれかの条件を満たしていれば良い
                if (self.required_highest_trophies and self.required_highest_trophies <= main_account.highest_trophies) or \
                    (self.required_current_trophies and self.required_current_trophies <= main_account.current_trophies) or \
                    (self.required_ranked_highest_rank and self.required_ranked_highest_rank <= (main_account.acc_ranked_highest_rank or 0)) or \
                    (self.required_ranked_current_rank and self.required_ranked_current_rank <= (main_account.acc_ranked_current_rank or 0)) or \
                    (self.required_ranked_highest_score and self.required_ranked_highest_score <= (main_account.ranked_highest_score or 0)) or \
                    (self.required_ranked_current_score and self.required_ranked_current_score <= (main_account.ranked_current_score or 0)) or \
                    (self.required_solo_pl_rank and self.required_solo_pl_rank <= (main_account.solo_pl_rank or 0)) or \
                    (self.required_max_power_brawlers and self.required_max_power_brawlers <= main_account.max_power_brawlers) or \
                    (self.required_prestige and self.required_prestige <= (main_account.prestige or 0)) or \
                    (not (self.required_highest_trophies or self.required_current_trophies or self.required_ranked_highest_rank or self.required_ranked_current_rank or \
                        self.required_ranked_highest_score or self.required_ranked_current_score or self.required_solo_pl_rank or self.required_max_power_brawlers or self.required_prestige)):
                        return True
                else:
                    return False
        else: # ログインしていないユーザーは、条件が何も設定されていない募集に限りリンククリックできる
            if (not (self.required_highest_trophies or self.required_current_trophies or self.required_ranked_highest_rank or self.required_ranked_current_rank or \
                    self.required_ranked_highest_score or self.required_ranked_current_score or self.required_solo_pl_rank or self.required_max_power_brawlers or self.required_prestige)):
                return True
            else:
                return False

    @property
    def is_effectively_closed(self) -> bool:
        """明示クローズまたは自動クローズにより実効的にクローズされているか。"""
        return is_team_post_effectively_closed(
            post_type=self.type,
            is_closed=self.is_closed,
            auto_close_exempt=self.auto_close_exempt,
            created_at=self.created_at,
            is_later_recruitment=self.is_later_recruitment,
        )

    def seconds_until_close_allowed(self, now: datetime.datetime | None = None) -> int:
        """掲示板からのクローズ操作が可能になるまでの残り秒数。0なら即時可能。"""
        if self.created_at is None:
            return 0
        if now is None:
            now = datetime.datetime.now(datetime.timezone.utc)
        created_at = self.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=datetime.timezone.utc)
        elapsed = (now - created_at.astimezone(datetime.timezone.utc)).total_seconds()
        remaining = TEAM_POST_CLOSE_COOLDOWN_SECONDS - elapsed
        return max(0, int(math.ceil(remaining))) if remaining > 0 else 0

    @property
    def activity_at(self) -> datetime.datetime | None:
        """一覧に掲載された日時（リポストした投稿はリポストした日時）。"""
        return self.reposted_at or self.created_at

    @property
    def ago_ja(self) -> str:
        """投稿（リポストした投稿はリポスト）がどれくらい前かのテキスト(日本語表示用)を生成する。

        Returns:
            str: 投稿がどれくらい前かのテキスト
        """
        return format_board_ago_text(self.activity_at, "ja")
        
    @property
    def ago_en(self) -> str:
        """投稿（リポストした投稿はリポスト）がどれくらい前かのテキスト(英語表示用)を生成する。

        Returns:
            str: 投稿がどれくらい前かのテキスト
        """
        return format_board_ago_text(self.activity_at, "en")


def format_board_ago_text(created_at: datetime.datetime | None, lang: str) -> str:
    """掲示板カード用の相対時刻テキストを返す。"""
    if created_at is None:
        return ""
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=datetime.timezone.utc)
    td: datetime.timedelta = datetime.datetime.now(datetime.timezone.utc) - created_at.astimezone(datetime.timezone.utc)
    days = td.days
    seconds = int(td.total_seconds())
    hours = seconds // 3600
    minutes = seconds // 60

    if lang == "ja":
        if days >= 10:
            return f"{days}日前"
        if days:
            return f"{days}日 {hours - days * 24}時間前"
        if hours >= 10:
            return f"{hours}時間前"
        if hours:
            return f"{hours}時間 {minutes - hours * 60}分前"
        if minutes:
            return f"{minutes}分前"
        return "たった今"

    if days >= 10:
        return f"{days}d ago"
    if days:
        return f"{days}d {hours - days * 24}h ago"
    if hours >= 10:
        return f"{hours}h ago"
    if hours:
        return f"{hours}h {minutes - hours * 60}m ago"
    if minutes:
        return f"{minutes}m ago"
    return "Just Now"


async def get_or_create_theme_brawler_post(db: asyncpg.Connection, brawler_id: int) -> "Post | None":
    """テーマ掲示板（キャラクター）用のpostレコードをDBから取得する。存在しなければ作成する。
    brawlersテーブルに存在しないIDでは作成せず None を返す。
    結果は6時間Redisにキャッシュされる。

    Args:
        db (asyncpg.Connection): データベース接続
        brawler_id (int): ブロスタのキャラクターID（例: 16000001）

    Raises:
        DataBaseError: データベースエラー

    Returns:
        Post | None: テーマ掲示板用のpost。対象キャラがDBに無い場合はNone
    """
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


# テーマ掲示板一覧のタブごとの対象カテゴリ・発言ありのみか・上限件数
_THEME_BRAWLER_CONDITION = "((p.type = 'theme' AND p.category = 'brawler') OR p.type = 'brawler_guide')"
_THEME_MAP_CONDITION = "(p.type = 'theme' AND p.category = 'map')"
_THEME_BOARD_TAB_QUERY: dict[str, tuple[str, bool, int]] = {
    "latest": (f"({_THEME_BRAWLER_CONDITION} OR {_THEME_MAP_CONDITION})", True, 100),
    "brawlers": (_THEME_BRAWLER_CONDITION, False, 500),
    "maps": (_THEME_MAP_CONDITION, True, 200),
    "participated": (f"({_THEME_BRAWLER_CONDITION} OR {_THEME_MAP_CONDITION})", False, 500),
    "liked": (f"({_THEME_BRAWLER_CONDITION} OR {_THEME_MAP_CONDITION})", False, 500),
}
# ユーザーに依存しないタブは、ブロック中のユーザーがいない閲覧者向けに短時間キャッシュする
_THEME_BOARD_SHARED_CACHE_TABS = frozenset({"latest", "brawlers", "maps"})
THEME_BOARD_POSTS_CACHE_TTL_SECONDS = 30
# マップタブで、書き込みのある掲示板がこの件数に満たない場合は、マップ周期に含まれるまだ書き込みのないマップで補う
THEME_MAP_TAB_MIN_CARDS = 30


async def invalidate_theme_board_posts_cache() -> None:
    """テーマ掲示板一覧の共有キャッシュを消す。発言直後に一覧へ戻ったとき、すぐ反映されるようにする。"""
    for tab in _THEME_BOARD_SHARED_CACHE_TABS:
        await delete_cache(f"theme_board_posts:{tab}")


async def get_theme_board_posts(
    db: asyncpg.Connection,
    *,
    tab: str,
    target_user_id: int | None = None,
    blocked_user_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """テーマ掲示板の投稿一覧を、最新の通常メッセージ順で返す。

    Args:
        db: データベース接続
        tab: latest / brawlers / maps / participated / liked
        target_user_id: 参加・いいね済みタブ用のユーザーID
        blocked_user_ids: 最新メッセージから除外するユーザーID
    """
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
                cooldown = 180 #!
        if td < cooldown:
            return False, cooldown - td
    return True, 0

async def create_post(db: asyncpg.Connection, type: str, ip: str, user_id: int | None = None, anonymous_id: str | None = None, region: str | None = None,
                      host_player_tag: str | None = None, host_club_tag: str | None = None, link: str | None = None,
                      comment: str | None = None, conditions_application_type: str = "and",
                      category: str | None = None, mode: str | None = None, hashtags: list[str] = [], custom_settings: dict = {},
                      permitted_ids: list[int] = [], prohibited_ids: list[int] = [], required_highest_trophies: int | None = None,
                      required_current_trophies: int | None = None, required_ranked_highest_rank: int | None = None,
                      required_ranked_current_rank: int | None = None, required_ranked_highest_score: int | None = None,
                      required_ranked_current_score: int | None = None, required_solo_pl_rank: int | None = None,
                      required_max_power_brawlers: int | None = None, required_prestige: int | None = None, other_conditions: dict = {},
                      is_later_recruitment: bool = False) -> int:
    """新しい投稿を作成する。

    Args:
        db (asyncpg.Connection): データベース接続
        type (str): 投稿のタイプ("team"/"friend"/"club")
        ip (str): IPアドレス
        user_id (int | None): 投稿するユーザーのユーザーID。ログインしていないユーザーの場合はNoneでよい。
        anonymous_id (str | None): 投稿するユーザーの匿名ID。ログインしているユーザーの場合はNoneでよい。
        region (str | None): 投稿の地域。設定しなくてもよい。
        host_player_tag (str | None): ホストのプレイヤータグ。"team"または"friend"の場合は必須、"club"の場合は指定しても無視される。
        host_club_tag (str | None): ホストのクラブタグ。"club"の場合は必須、"team"または"friend"の場合は指定しても無視される。
        link (str | None): 招待リンク。"team"/"friend"/"club"の今すぐ募集では必須。後から募集では不要。
        comment (str | None): コメント。後から募集では必須。
        conditions_application_type (str): 条件の適用方法("and"/"or")。デフォルトは"and"。
        category (str | None): カテゴリー
        mode (str | None): モード
        hashtags (list[str]): ハッシュタグ。リスト形式で複数指定できる。
        custom_settings (dict): カスタム設定
        permitted_ids (list[int]): 条件とは別に、個別に許可するユーザーIDのリスト
        prohibited_ids (list[int]): 条件とは別に、個別に禁止するユーザーIDのリスト
        required_highest_trophies (int | None): 必要最多トロフィー
        required_current_trophies (int | None): 必要現在トロフィー
        required_ranked_highest_rank (int | None): 必要ガチバトル最高ランク
        required_ranked_current_rank (int | None): 必要ガチバトル現在ランク
        required_ranked_highest_score (int | None): 必要ガチバトル最高スコア
        required_ranked_current_score (int | None): 必要ガチバトル現在スコア
        required_solo_pl_rank (int | None): 必要旧ソロパワーリーグ最高ランク
        required_max_power_brawlers (int | None): 必要最大パワーレベルキャラ数
        required_prestige (int | None): 必要トップランカー所持数
        other_conditions (dict): その他の条件
        is_later_recruitment (bool): チーム募集の「後から募集」かどうか

    Raises:
        ValueError: 投稿の項目に不備がある場合(投稿が認められていない場合も含む)
        DataBaseError: データベースエラー
        
    Returns:
        int: 追加した投稿のID
    """
    # [この部分は公開用リポジトリでは非公開にされています]

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

# なんでも掲示板の検索クエリ（count / fetch それぞれ）の上限秒数
GENERAL_BOARD_SEARCH_QUERY_TIMEOUT = 5.0
# 検索結果のキャッシュ秒数（ページ送り・連打の負荷を吸収する）
GENERAL_BOARD_SEARCH_CACHE_TTL_SECONDS = 30
_GENERAL_COMMENT_SEARCH_SQL = search_normalize_sql("comment")
_USER_NAME_SEARCH_SQL = search_normalize_sql("name")


def build_general_board_search_clauses(
    search: GeneralBoardSearch,
    query_params: list[Any],
    table_alias: str = "",
) -> tuple[list[str], list[Any]]:
    """なんでも掲示板の検索条件の WHERE 句を組み立てる。

    部分インデックス idx_posts_general_comment_search_bigm（WHERE type = 'general'）を
    汎用プランでも確実に使わせるため、type 条件はリテラルで入れる。
    """
    prefix = f"{table_alias}." if table_alias else ""
    clauses = [f"{prefix}type = 'general'"]
    if search.mode == "user":
        query_params.append(search.normalized_user_name)
        clauses.append(
            f"{prefix}host_id IN (SELECT id FROM users WHERE {_USER_NAME_SEARCH_SQL} = ${len(query_params)})"
        )
    else:
        comment_sql = search_normalize_sql(f"{prefix}comment") if prefix else _GENERAL_COMMENT_SEARCH_SQL
        for term in search.normalized_terms:
            query_params.append(f"%{escape_like_literal(term)}%")
            clauses.append(f"{comment_sql} LIKE ${len(query_params)} ESCAPE {LIKE_ESCAPE_SQL}")
    return clauses, query_params


async def get_posts(db: asyncpg.Connection, page: int = 1, per_page: int = 100, type: str | None = None,
                    region: str | None = None, target_user: User | None = None, target_player: Player | None = None,
                    category: str | None = None, mode: str | None = None, hashtag: str | None = None, include_deleted_post: bool = False,
                    eliminate_duplicates: bool = False, author_user_id: int | None = None, author_ip: str | None = None,
                    filter: str | None = None, exclude_categories: tuple[str, ...] = (),
                    only_joinable: bool = False, viewer_ip: str | None = None,
                    search: GeneralBoardSearch | None = None) -> tuple[list[Post], int]:
    """投稿を、条件に合わせて新しい順に取得する。3秒間（検索時は30秒間）のキャッシュを使用する。

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
        exclude_categories (tuple[str, ...]): 指定したカテゴリーの投稿を除外する。デフォルトは空（除外しない）。
        only_joinable (bool): Trueの場合、参加可能な投稿のみに絞り込む。filter と同時指定可能。
        viewer_ip (str | None): 閲覧者IP。参加可否フィルタで自身の投稿（host_ip一致）を残すために用いる。
        search (GeneralBoardSearch | None): なんでも掲示板の検索条件（本文 or 投稿者名）。指定時は type='general' に限定される。
        
    Raises:
        BrawlStarsAPIError: APIエラー
        DataBaseError: データベースエラー

    Returns:
        tuple[list[Post], int]: 取得した投稿のリストと、検索結果総数。
    """
    # [この部分は公開用リポジトリでは非公開にされています]

_TRENDING_ARCHIVE_LOCK_TTL_SECONDS = 120


async def _rank_general_posts_by_trending_score(
    db: asyncpg.Connection,
    where_sql: str,
    query_params: list[Any],
    *,
    limit: int | None = None,
    timeout: float | None = None,
) -> list[tuple[int, float]]:
    """候補投稿（posts p）を話題スコア順に並べ、(投稿ID, スコア) のリストを返す。

    スコア = (weight_likes * ln(1+いいね) + weight_comments * ln(1+コメント))
             / (経過時間[h] + age_offset_hours) ^ gravity
    いいね・コメントは候補IDで絞ってから件数を集計し、JOIN による行の膨張を避ける。
    経過時間はリポストした投稿ではリポストした日時から数える。
    同点（主にいいね・コメント0件）は新しい順。
    """
    cfg = GENERAL_BOARD_TRENDING
    score_expr = f"""(
            {cfg.weight_likes} * LN(1 + COALESCE(l.cnt, 0))
            + {cfg.weight_comments} * LN(1 + COALESCE(cm.cnt, 0))
        ) / POWER(
            GREATEST(EXTRACT(EPOCH FROM (NOW() - c.created_at)) / 3600.0, 0) + {cfg.age_offset_hours},
            {cfg.gravity}
        )"""
    limit_sql = f"LIMIT {int(limit)}" if limit else ""
    sql = f"""
        WITH cand AS MATERIALIZED (
            SELECT p.id, COALESCE(p.reposted_at, p.created_at) AS created_at FROM posts p WHERE {where_sql}
        ),
        l AS (
            SELECT pv.post_id, COUNT(*) AS cnt
            FROM post_votes pv
            WHERE pv.vote_type = 1 AND pv.post_id IN (SELECT id FROM cand)
            GROUP BY pv.post_id
        ),
        cm AS (
            SELECT m.thread_id, COUNT(*) AS cnt
            FROM messages m
            WHERE NOT m.is_deleted AND m.thread_id IN (SELECT id FROM cand)
            GROUP BY m.thread_id
        )
        SELECT c.id, {score_expr} AS score
        FROM cand c
        LEFT JOIN l ON l.post_id = c.id
        LEFT JOIN cm ON cm.thread_id = c.id
        ORDER BY score DESC, c.created_at DESC, c.id DESC
        {limit_sql}
    """
    rows = await db.fetch(sql, *query_params, timeout=timeout)
    return [(row["id"], float(row["score"])) for row in rows]


def _general_trending_base_where(
    region: str | None,
    category: str | None,
    exclude_categories: tuple[str, ...],
) -> tuple[list[str], list[Any]]:
    query_params: list[Any] = []
    where_clauses = ["p.type = 'general'", "p.is_deleted = FALSE"]
    if region:
        query_params.append(region)
        where_clauses.append(f"p.region = ${len(query_params)}")
    if category:
        query_params.append(category)
        where_clauses.append(f"p.category = ${len(query_params)}")
    if exclude_categories:
        query_params.append(list(exclude_categories))
        where_clauses.append(f"(p.category IS NULL OR p.category <> ALL(${len(query_params)}::text[]))")
    return where_clauses, query_params


async def _get_trending_general_archive(
    db: asyncpg.Connection,
    base_where: list[str],
    base_params: list[Any],
    filter_key: str,
) -> dict[str, Any]:
    """直近 recent_window_days 日より古い投稿（アーカイブ）の話題順ランキングを取得する。

    古い投稿のスコアはほぼ変動しないため archive_refresh_seconds ごとに再計算する。
    期限切れ時は1プロセスだけが再計算し（Redis ロック）、他は古いデータを使う。
    全件の ID は archive_chunk_size 件ずつ分割して保存し、ページ表示時は必要なチャンクだけ読む。

    Returns:
        {"version", "computed_at", "cutoff", "total", "merge_items": [[id, score], ...]}
    """
    cfg = GENERAL_BOARD_TRENDING
    meta_key = f"trending_general_archive:{filter_key}"
    empty = {"version": "none", "computed_at": 0, "cutoff": None, "total": 0, "merge_items": []}

    r = get_redis()
    if not r:
        # Redis が使えない場合は毎回の全期間集計を避け、直近の投稿のみとする
        return empty

    meta = await get_cache(meta_key)
    now = datetime.datetime.now(datetime.timezone.utc)
    if meta and now.timestamp() - float(meta.get("computed_at", 0)) < cfg.archive_refresh_seconds:
        return meta

    lock_key = f"{meta_key}:lock"
    try:
        acquired = bool(await r.set(lock_key, "1", nx=True, ex=_TRENDING_ARCHIVE_LOCK_TTL_SECONDS))
    except Exception:
        acquired = False
    if not acquired:
        if meta:
            return meta
        # 初回計算中: 少し待って結果を使う。間に合わなければ直近の投稿のみ
        for _ in range(10):
            await asyncio.sleep(0.3)
            meta = await get_cache(meta_key)
            if meta:
                return meta
        return empty

    try:
        cutoff = now - datetime.timedelta(days=cfg.recent_window_days)
        params = [*base_params, cutoff]
        where_sql = " AND ".join([*base_where, f"COALESCE(p.reposted_at, p.created_at) <= ${len(params)}"])
        started = datetime.datetime.now()
        ranked = await _rank_general_posts_by_trending_score(db, where_sql, params)
        elapsed = (datetime.datetime.now() - started).total_seconds()

        version = str(int(now.timestamp() * 1000))
        ttl = cfg.archive_refresh_seconds * 3
        post_ids = [post_id for post_id, _ in ranked]
        chunk_size = cfg.archive_chunk_size
        for i in range(math.ceil(len(post_ids) / chunk_size)):
            await set_cache(f"{meta_key}:{version}:{i}", post_ids[i * chunk_size:(i + 1) * chunk_size], ttl=ttl)

        meta = {
            "version": version,
            "computed_at": now.timestamp(),
            "cutoff": cutoff.isoformat(),
            "total": len(post_ids),
            "merge_items": [[post_id, score] for post_id, score in ranked[:cfg.archive_merge_limit]],
        }
        await set_cache(meta_key, meta, ttl=ttl)
        logger.info(f"なんでも掲示板の話題アーカイブを再計算しました: key={filter_key} 件数={len(post_ids)} 所要={elapsed:.2f}秒")
        return meta
    finally:
        try:
            await r.delete(lock_key)
        except Exception:
            pass


async def _read_trending_general_archive_ids(filter_key: str, archive: dict[str, Any], start: int, count: int) -> list[int]:
    """アーカイブのスコア順 ID リストから [start, start+count) を読む。"""
    total = int(archive.get("total", 0))
    if count <= 0 or start >= total:
        return []
    end = min(start + count, total)
    chunk_size = GENERAL_BOARD_TRENDING.archive_chunk_size
    ids: list[int] = []
    for chunk_index in range(start // chunk_size, (end - 1) // chunk_size + 1):
        chunk = await get_cache(f"trending_general_archive:{filter_key}:{archive['version']}:{chunk_index}")
        if not chunk:
            break
        chunk_start = chunk_index * chunk_size
        ids.extend(chunk[max(start - chunk_start, 0):end - chunk_start])
    return ids


async def get_trending_general_posts(
    db: asyncpg.Connection,
    per_page: int = 60,
    page: int = 1,
    region: str | None = None,
    category: str | None = None,
    exclude_categories: tuple[str, ...] = (),
    search: GeneralBoardSearch | None = None,
) -> tuple[list[Post], int]:
    """なんでも掲示板の投稿を話題順で取得する。全期間の投稿が対象。

    - 検索なし: 直近の投稿（cache_ttl_seconds ごとに再計算）と、アーカイブ上位 archive_merge_limit 件を
      スコア順にマージし、その後ろにアーカイブの残りをスコア順で連結する。
    - 検索あり: ヒットした投稿（全期間）を1クエリでスコア順に並べる（上限 search_max_ranked 件）。
    集約SQLと共有キャッシュを使い、リクエストごとのN+1を避ける。
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

#* /---*---*---*---*---*---*---*---*/
#* 招待リンク関連
#* /---*---*---*---*---*---*---*---*/
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

#* /---*---*---*---*---*---*---*---*/
#* メッセージ関連
#* /---*---*---*---*---*---*---*---*/
# ヘルパー関数
def format_chat_time_ja(utc_dt: datetime.datetime) -> str:
    # JSTに変換
    jst_dt = utc_dt.astimezone(datetime.timezone(datetime.timedelta(hours=9)))
    
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    
    # 今日・昨日・一昨日以前の判定
    today = now.date()
    date_diff = (jst_dt.date() - today).days  # 0:今日, -1:昨日, -2以下:過去
    
    hour = jst_dt.hour
    minute = jst_dt.minute
    
    # 時間フォーマット（1桁か2桁か判定）
    time_str = f"{hour}:{minute:02d}" if hour < 10 else f"{hour:02d}:{minute:02d}"
    
    if date_diff == 0:  # 今日
        return time_str
    elif date_diff == -1:  # 昨日
        return f"昨日 {time_str}"
    else:  # 一昨日以前
        return f"{jst_dt.month:02d}/{jst_dt.day:02d} {time_str}"

def format_chat_time_en(utc_dt: datetime.datetime) -> str:
    # UTCのまま扱う
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    
    # 今日・昨日・一昨日以前の判定
    today = now_utc.date()
    date_diff = (utc_dt.date() - today).days  # 0:今日, -1:昨日, -2以下:過去
    
    hour = utc_dt.hour
    minute = utc_dt.minute
    
    # 時間フォーマット（1桁か2桁か判定）
    time_str = f"{hour}:{minute:02d}" if hour < 10 else f"{hour:02d}:{minute:02d}"
    
    if date_diff == 0:  # 今日
        return time_str
    elif date_diff == -1:  # 昨日
        return f"Yesterday {time_str}"
    else:  # 一昨日以前
        return f"{utc_dt.month:02d}/{utc_dt.day:02d} {time_str}"

class Message:
    def __init__(self) -> None:
        self.id: int = None
        self.thread_id: int = None
        self.user_id: int | None = None
        self.user_name: str | None = None
        self.user_icon_id: int | None = None
        self.user_icon_path: str | None = None
        self.user_main_account_tag: str | None = None
        self.user_main_account_name: str | None = None
        self.user_is_admin: bool = False
        self.user_is_sub_admin: bool = False
        self.user_ip: str = None
        self.created_at: datetime.datetime = None
        self.message_type: str = None
        self.message: str = None
        self.is_deleted: bool = None
        self.reply_to_message_id: int | None = None
        self.reply_to: dict | None = None
        self.mentions: list[dict] | None = None  # 本文でメンションした人（{id, name, user_name, main_account_tag, main_account_name}）
        self.created_at_text_ja: str = None
        self.created_at_text_en: str = None
        self.gift_amount: int | None = None
        self.gift_fee: int | None = None
        self.gift_recipient_user_id: int | None = None
        self.gift_recipient_user_name: str | None = None
        self.gift_recipient_main_account_tag: str | None = None
        self.gift_recipient_main_account_name: str | None = None
        self.is_comment_deleted: bool = False
        
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "user_id": self.user_id,
            "user_name": self.user_name,
            "user_icon_id": self.user_icon_id,
            "user_icon_path": self.user_icon_path,
            "user_main_account_tag": self.user_main_account_tag,
            "user_main_account_name": self.user_main_account_name,
            "user_is_admin": self.user_is_admin,
            "user_is_sub_admin": self.user_is_sub_admin,
            "user_ip": self.user_ip,
            "created_at": format_utc_datetime(self.created_at),
            "message_type": self.message_type,
            "message": self.message,
            "is_deleted": self.is_deleted,
            "reply_to_message_id": self.reply_to_message_id,
            "reply_to": self.reply_to,
            "mentions": self.mentions,
            "created_at_text_ja": self.created_at_text_ja,
            "created_at_text_en": self.created_at_text_en,
            "gift_amount": self.gift_amount,
            "gift_fee": self.gift_fee,
            "gift_recipient_user_id": self.gift_recipient_user_id,
            "gift_recipient_user_name": self.gift_recipient_user_name,
            "gift_recipient_main_account_tag": self.gift_recipient_main_account_tag,
            "gift_recipient_main_account_name": self.gift_recipient_main_account_name,
            "is_comment_deleted": self.is_comment_deleted,
            "gift_tier": (
                {20: "bronze", 30: "silver", 50: "gold", 75: "platinum", 100: "black"}.get(self.gift_amount)
                if self.gift_amount
                else None
            ),
        }
    
    @classmethod
    async def from_db(cls, dbrow: asyncpg.Record, db: asyncpg.Connection):
        instance = cls()
        instance.id = dbrow["id"]
        instance.thread_id = dbrow["thread_id"]
        instance.user_id = dbrow["user_id"]
        if instance.user_id:
            user = await get_user(db, instance.user_id)
            if user:
                instance.user_name = user.name
                instance.user_icon_id = await get_player_icon_from_db(user.main_account, db)
                instance.user_icon_path = "/" + get_icon_path(instance.user_icon_id)
                instance.user_main_account_tag = user.main_account
                instance.user_main_account_name = await get_player_name(instance.user_main_account_tag, db)
                instance.user_is_admin = user.is_admin
                instance.user_is_sub_admin = bool(getattr(user, "is_sub_admin", False))
        instance.user_ip = str(dbrow["user_ip"])
        instance.created_at = dbrow["created_at"]
        instance.message_type = dbrow["message_type"]
        instance.message = dbrow["message"]
        instance.is_deleted = dbrow["is_deleted"]
        instance.reply_to_message_id = dbrow["reply_to_message_id"] if "reply_to_message_id" in dbrow else None
        instance.reply_to = None
        instance.mentions = await _mention_display_info(db, dbrow["mentions"] if "mentions" in dbrow else None)
        instance.created_at_text_ja = format_chat_time_ja(instance.created_at)
        instance.created_at_text_en = format_chat_time_en(instance.created_at) + " (UTC)"
        return instance
        
    @classmethod
    async def from_dict(cls, data: dict):
        instance = cls()
        instance.id = data.get("id")
        instance.thread_id = data.get("thread_id")
        instance.user_id = data.get("user_id")
        instance.user_name = data.get("user_name")
        instance.user_icon_id = data.get("user_icon_id")
        instance.user_icon_path = data.get("user_icon_path")
        instance.user_main_account_tag = data.get("user_main_account_tag")
        instance.user_main_account_name = data.get("user_main_account_name")
        instance.user_is_admin = data.get("user_is_admin", False)
        instance.user_is_sub_admin = data.get("user_is_sub_admin", False)
        instance.user_ip = data.get("user_ip")
        instance.created_at = parse_utc_datetime(data.get("created_at"))
        instance.message_type = data.get("message_type")
        instance.message = data.get("message")
        instance.is_deleted = data.get("is_deleted")
        instance.reply_to_message_id = data.get("reply_to_message_id")
        instance.reply_to = data.get("reply_to")
        instance.mentions = data.get("mentions")
        instance.created_at_text_ja = data.get("created_at_text_ja")
        instance.created_at_text_en = data.get("created_at_text_en")
        instance.gift_amount = data.get("gift_amount")
        instance.gift_fee = data.get("gift_fee")
        instance.gift_recipient_user_id = data.get("gift_recipient_user_id")
        instance.gift_recipient_user_name = data.get("gift_recipient_user_name")
        instance.gift_recipient_main_account_tag = data.get("gift_recipient_main_account_tag")
        instance.gift_recipient_main_account_name = data.get("gift_recipient_main_account_name")
        instance.is_comment_deleted = bool(data.get("is_comment_deleted"))
        return instance
        
    async def delete(self, db: asyncpg.Connection) -> None:
        """メッセージを削除(論理削除)する。削除後、キャッシュがクリアされる。

        Args:
            db (asyncpg.Connection): データベース接続

        Raises:
            DataBaseError: データベースエラー
        """
        cache_key = f"message:{self.id}"
        query = "UPDATE messages SET is_deleted = $1 WHERE id = $2"
        params = (True, self.id)
        try:
            await db.execute(query, *params)
        except asyncpg.PostgresError as e:
            raise DataBaseError(e) from e
        await delete_cache(cache_key)
        logger.info(f"メッセージ {self.message} (ID: {self.id}, スレッド: {self.thread_id})を削除しました")
        
    @property
    def ago_ja(self) -> str:
        """メッセージがどれくらい前かのテキスト(日本語表示用)を生成する。

        Returns:
            str: メッセージがどれくらい前かのテキスト
        """
        td: datetime.timedelta = datetime.datetime.now(datetime.timezone.utc) - self.created_at
        days = td.days
        seconds = int(td.total_seconds())
        hours = seconds // 3600
        minutes = seconds // 60
        
        if days >= 10:
            return f"{days}日前"
        elif days:
            return f"{days}日 {hours - days * 24}時間前"
        elif hours >= 10:
            return f"{hours}時間前"
        elif hours:
            return f"{hours}時間 {minutes - hours * 60}分前"
        elif minutes:
            return f"{minutes}分前"
        elif seconds:
            return f"{seconds}秒前"
        else:
            return "たった今"
        
    @property
    def ago_en(self) -> str:
        """メッセージがどれくらい前かのテキスト(英語表示用)を生成する。

        Returns:
            str: メッセージがどれくらい前かのテキスト
        """
        td: datetime.timedelta = datetime.datetime.now(datetime.timezone.utc) - self.created_at
        days = td.days
        seconds = int(td.total_seconds())
        hours = seconds // 3600
        minutes = seconds // 60
        
        if days >= 10:
            return f"{days}d ago"
        elif days:
            return f"{days}d {hours - days * 24}h ago"
        elif hours >= 10:
            return f"{hours}h ago"
        elif hours:
            return f"{hours}h {minutes - hours * 60}m ago"
        elif minutes:
            return f"{minutes}m ago"
        elif seconds:
            return f"{seconds}s ago"
        else:
            return "Just Now"

#* /---*---*---*---*---*---*---*---*/
#* メンション
#* /---*---*---*---*---*---*---*---*/
# メンションできるのは、スレッド主と、スレッドの最新のメッセージ（この件数）に普通のメッセージを書いたログイン中のユーザー。
# テーマ掲示板のような長く続くスレッドで、ずっと前に一言書いただけの人にまで届かないように
BOARD_MENTION_SCOPE_MESSAGES = 1000
BOARD_MENTION_CANDIDATE_LIMIT = 100  # 入力欄の候補に出す人数（最近書いた人から）
BOARD_MENTION_CANDIDATES_CACHE_TTL = 30


async def get_board_mention_candidates(
    db: asyncpg.Connection,
    post: "Post",
    text: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """スレッドでメンションできる人を、最近書いた人から順に返す（スレッド主は書いていなければ最後）。
    無効になったアカウントと、このスレッドで書き込みを禁止された人は除く。
    text を渡すと、その本文に「@名前」が入っている人だけ（送信時。最終的な判定は pick_mentions）。"""
    # [この部分は公開用リポジトリでは非公開にされています]


async def get_cached_board_mention_candidates(db: asyncpg.Connection, post: "Post") -> list[dict]:
    """入力欄の候補（スレッドごとに少しの間キャッシュする。ブロックした人・自分は画面側で除く）。"""
    cache_key = f"board_mention_candidates:{post.id}"
    cached = await get_cache(cache_key)
    if cached is not None:
        return cached
    items = await get_board_mention_candidates(db, post, limit=BOARD_MENTION_CANDIDATE_LIMIT)
    await set_cache(cache_key, items, ttl=BOARD_MENTION_CANDIDATES_CACHE_TTL)
    return items


async def resolve_board_mentions(db: asyncpg.Connection, post: "Post", user_id: int | None, body: str) -> list[dict]:
    """送る本文からメンションを読み取る（範囲に入っていて、本文に「@名前」がある人）。ゲストのメッセージは数えない。"""
    if not user_id or not has_mention_mark(body):
        return []
    return pick_mentions(body, await get_board_mention_candidates(db, post, text=body), user_id)


async def _mention_display_info(db: asyncpg.Connection, mentions: list[dict] | None) -> list[dict] | None:
    """保存したメンション（{id, name}）に、本文の「@名前」を押したときのメニューで使う今のユーザー情報を足す。
    name は本文に書かれた名前のまま（改名しても本文と合わせるため）。無効になったアカウントは user_name を空にする。"""
    if not mentions:
        return None
    result = []
    for mention in mentions:
        if not mention.get("id"):
            continue  # 大会のチャットの @everyone の印（掲示板のチャットにはない）
        user = await get_user(db, mention["id"])
        tag = user.main_account if user else None
        result.append({
            "id": mention["id"],
            "name": mention["name"],
            "user_name": user.name if user else "",
            "main_account_tag": tag or "",
            "main_account_name": (await get_player_name(tag, db) if tag else None) or "",
        })
    return result


def _deleted_reply_to_preview(reply_to_message_id: int) -> dict:
    return {
        "id": reply_to_message_id,
        "user_id": None,
        "user_name": None,
        "user_icon_path": None,
        "user_is_admin": False,
        "user_is_sub_admin": False,
        "message": None,
        "is_deleted": True,
        "message_type": None,
        "gift_amount": None,
    }


async def attach_reply_to_previews(db: asyncpg.Connection, messages: list[Message]) -> None:
    """メッセージ一覧にリプライ先プレビューを一括で付与する。"""
    reply_ids = {m.reply_to_message_id for m in messages if m.reply_to_message_id}
    if not reply_ids:
        for message in messages:
            message.reply_to = None
        return

    try:
        rows = await db.fetch(
            """
            SELECT
                m.id, m.thread_id, m.user_id, m.message, m.is_deleted, m.message_type,
                g.amount AS gift_amount
            FROM messages m
            LEFT JOIN token_gifts g ON g.message_id = m.id
            WHERE m.id = ANY($1::int[])
            """,
            list(reply_ids),
        )
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e

    previews: dict[int, dict] = {}
    for row in rows:
        if row["is_deleted"]:
            previews[row["id"]] = _deleted_reply_to_preview(row["id"])
            continue

        preview = {
            "id": row["id"],
            "user_id": row["user_id"],
            "user_name": None,
            "user_icon_path": None,
            "user_is_admin": False,
            "user_is_sub_admin": False,
            "message": row["message"],
            "is_deleted": False,
            "message_type": row["message_type"],
            "gift_amount": row["gift_amount"],
        }
        if row["user_id"]:
            user = await get_user(db, row["user_id"])
            if user:
                preview["user_name"] = user.name
                icon_id = await get_player_icon_from_db(user.main_account, db)
                preview["user_icon_path"] = "/" + get_icon_path(icon_id)
                preview["user_is_admin"] = user.is_admin
                preview["user_is_sub_admin"] = bool(getattr(user, "is_sub_admin", False))
        previews[row["id"]] = preview

    for message in messages:
        if not message.reply_to_message_id:
            message.reply_to = None
            continue
        message.reply_to = previews.get(
            message.reply_to_message_id,
            _deleted_reply_to_preview(message.reply_to_message_id),
        )


async def create_message(
    db: asyncpg.Connection,
    thread_id: int,
    ip: str,
    message: str,
    message_type: str = "message",
    user_id: int | None = None,
    reply_to_message_id: int | None = None,
    mentions: list[dict] | None = None,
) -> int:
    """新しいメッセージを作成する。

    Args:
        db (asyncpg.Connection): データベース接続
        thread_id (int): スレッドID
        ip (str): IPアドレス
        message (str): メッセージ
        message_type (str): メッセージのタイプ。デフォルトは"message"。
        user_id (int | None): 送信するユーザーのID。ログインしていないユーザーの場合はNoneでよい。
        reply_to_message_id (int | None): リプライ先メッセージID。同一スレッドなら削除済みでも許可する。
        mentions (list[dict] | None): 本文でメンションした人（{id, name}。resolve_board_mentions で範囲を確かめたもの）。

    Raises:
        ValueError: メッセージの項目に不備がある場合
        DataBaseError: データベースエラー
        
    Returns:
        int: 追加したメッセージのID
    """
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

#* /---*---*---*---*---*---*---*---*/
#* リアクション関連
#* /---*---*---*---*---*---*---*---*/
# メニューに固定で並ぶ絵文字（管理画面の絵文字の絞り込みの並び順にも使う）。付けられる絵文字は reaction_rules で決まる
EMOJIS = ["❤️", "👀", "😄", "😲", "🥲", "😭", "😡"]

class Reaction:
    def __init__(self) -> None:
        self.id: int = None
        self.message_id: int = None
        self.user_id: int = None
        self.user_name: str = None
        self.user_icon_id: int = None
        self.user_icon_path: str = None
        self.created_at: datetime.datetime = None
        self.emoji: str = None
        
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "message_id": self.message_id,
            "user_id": self.user_id,
            "user_name": self.user_name,
            "user_icon_id": self.user_icon_id,
            "user_icon_path": self.user_icon_path,
            "created_at": format_utc_datetime(self.created_at),
            "emoji": self.emoji
        }
        
    @classmethod
    async def from_db(cls, dbrow: asyncpg.Record, db: asyncpg.Connection):
        instance = cls()
        instance.id = dbrow["id"]
        instance.message_id = dbrow["message_id"]
        instance.user_id = dbrow["user_id"]
        if instance.user_id:
            user = await get_user(db, instance.user_id)
            if user:
                instance.user_name = user.name
                instance.user_icon_id = await get_player_icon_from_db(user.main_account, db)
                instance.user_icon_path = "/" + get_icon_path(instance.user_icon_id)
        instance.created_at = dbrow["created_at"]
        instance.emoji = dbrow["emoji"]
        return instance
    
    @classmethod
    async def from_dict(cls, data: dict):
        instance = cls()
        instance.id = data.get("id")
        instance.message_id = data.get("message_id")
        instance.user_id = data.get("user_id")
        instance.user_name = data.get("user_name")
        instance.user_icon_id = data.get("user_icon_id")
        instance.user_icon_path = data.get("user_icon_path")
        instance.created_at = parse_utc_datetime(data.get("created_at"))
        instance.emoji = data.get("emoji")
        return instance
    
    async def delete(self, db: asyncpg.Connection) -> None:
        """リアクションを削除する。

        Args:
            db (asyncpg.Connection): データベース接続

        Raises:
            DataBaseError: データベースエラー
        """
        query = "DELETE FROM reactions WHERE id = $1"
        params = (self.id,)
        try:
            result = await db.execute(query, *params)
        except asyncpg.PostgresError as e:
            raise DataBaseError(e) from e
        if result.startswith("DELETE 1"):
            logger.info(f"{self.user_name} ({self.user_id})がリアクション {self.emoji} (ID: {self.id}, メッセージ: {self.message_id})を削除しました")
            # [この部分は公開用リポジトリでは非公開にされています]

async def add_reaction(db: asyncpg.Connection, message_id: int, user_id: int, emoji: str) -> int:
    """新しいリアクションを追加する。

    Args:
        db (asyncpg.Connection): データベース接続
        message_id (int): メッセージID
        user_id (int): ユーザーID
        emoji (str): 絵文字

    Raises:
        ValueError: 許可されていないリアクションを追加しようとした場合
        DataBaseError: データベースエラー

    Returns:
        int: 追加したリアクションのID
    """
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

#* /---*---*---*---*---*---*---*---*/
#* 通報関連
#* /---*---*---*---*---*---*---*---*/
class Report:
    def __init__(self, dbrow: asyncpg.Record) -> None:
        self.id: int = dbrow["id"]
        self.user_id: int | None = dbrow["user_id"]
        self.user_name: str | None = None
        self.user_ip: str = str(dbrow["user_ip"])
        self.created_at: datetime.datetime = dbrow["created_at"]
        self.created_at_text: str = self.created_at.astimezone(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M') # 表示用
        self.target_type: str = dbrow["target_type"] # "post" or "message"
        self.target_id: int = dbrow["target_id"] # 通報対象のID
        self.target_post: dict | None = None
        self.target_message: dict | None = None
        self.category: str = dbrow["category"]
        self.text: str | None = dbrow["text"]
        self.is_checked: bool = dbrow["is_checked"]
        
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "user_name": self.user_name,
            "user_ip": self.user_ip,
            "created_at_text": self.created_at_text,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "target_message": self.target_message,
            "category": self.category,
            "text": self.text,
            "is_checked": self.is_checked
        }
        
    async def check(self, db: asyncpg.Connection) -> bool:
        """通報をチェックする。

        Args:
            db (asyncpg.Connection): データベース接続

        Raises:
            DataBaseError: データベースエラー

        Returns:
            bool: 書き換えを行った場合はTrue、すでにチェックされていたためスキップした場合はFalse。
        """
        if self.is_checked:
            return False
            
        query = "UPDATE reports SET is_checked = TRUE WHERE id = $1"
        try:
            await db.execute(query, self.id)
        except asyncpg.PostgresError as e:
            raise DataBaseError(e) from e
            
        self.is_checked = True
        logger.info(f"ID: {self.id}の通報をチェック済みにしました")
        return True
    
    async def uncheck(self, db: asyncpg.Connection) -> bool:
        """通報のチェックを外す。

        Args:
            db (asyncpg.Connection): データベース接続

        Raises:
            DataBaseError: データベースエラー

        Returns:
            bool: 書き換えを行った場合はTrue、もともとチェックされていなかったためスキップした場合はFalse。
        """
        if not self.is_checked:
            return False
            
        query = "UPDATE reports SET is_checked = FALSE WHERE id = $1"
        try:
            await db.execute(query, self.id)
        except asyncpg.PostgresError as e:
            raise DataBaseError(e) from e
            
        self.is_checked = False
        logger.info(f"ID: {self.id}の通報のチェックを外しました")
        return True

async def get_report(db: asyncpg.Connection, id: int) -> Report:
    """指定したIDの通報を取得する。

    Args:
        db (asyncpg.Connection): データベース接続
        id (int): 通報ID

    Raises:
        DataBaseError: データベースエラー
        ValueError: 存在しないIDが指定された場合

    Returns:
        Report: 通報
    """
    query = "SELECT * FROM reports WHERE id = $1"
    try:
        result = await db.fetchrow(query, id)
    except asyncpg.PostgresError as e:
        logger.error(f"通報(ID: {id})の取得中にエラー({e})が発生しました", exc_info=True)
        raise DataBaseError(e) from e

    return Report(result) if result else None

async def get_reports(db: asyncpg.Connection, page: int = 1, per_page: int = 100, is_checked: bool | None = None, to_dict: bool = True) -> tuple[list[Report], int]:
    """管理者向け。通報を検索する。

    Args:
        db (asyncpg.Connection): データベース接続
        page (int): ページ番号。デフォルトは1。
        per_page (int): 1ページあたりのフィードバック数。デフォルトは100。
        is_checked (bool | None): 検索対象とするis_checkedの値。指定しない場合はすべて取得。
        to_dict (bool): Trueの場合、Report型ではなく辞書型に変換して返す。

    Raises:
        DataBaseError: データベースエラー

    Returns:
        tuple[list[Report], int]: ページ番号に対応する部分の検索結果のリスト。検索結果がなかった場合は[]。そして検索結果総数。
    """
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
