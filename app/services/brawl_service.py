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
from app.exceptions.custom_exceptions import BrawlStarsAPIError, DataBaseError, PlayerNotFoundError
# [この部分は公開用リポジトリでは非公開にされています]
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



# 設定ファイルからBRAWL_TOKENを取得
BRAWL_TOKEN = settings.BRAWL_TOKEN
_brawlstats_client: brawlstats.Client | None = None


def _brawlplex_http_status(response: Any) -> int | None:
    """BrawlPlexの非200応答(NetworkError)からHTTPステータスコードを取り出す。"""
    if isinstance(response, NetworkError):
        try:
            return int(response.message)
        except (TypeError, ValueError):
            return None
    return None


def get_brawlstats_client() -> brawlstats.Client:
    """Brawl Stats APIクライアントを必要時に初期化して返す。"""
    global _brawlstats_client

    if _brawlstats_client is not None:
        return _brawlstats_client

    if not BRAWL_TOKEN:
        raise BrawlStarsAPIError("BRAWL_TOKEN が未設定のため、Brawl Stars API機能は利用できません。")

    try:
        _brawlstats_client = brawlstats.Client(BRAWL_TOKEN)
        logger.info("Brawl Stats APIクライアントを遅延初期化しました。")
    except Exception as e:
        logger.warning(f"Brawl Stats APIクライアントの初期化に失敗しました: {e}")
        raise BrawlStarsAPIError("Brawl Stars APIクライアントの初期化に失敗しました。") from e

    return _brawlstats_client


BRAWLSTATS_API_UNAVAILABLE_CACHE_KEY = "brawlstats_api_unavailable"
BRAWLSTATS_API_UNAVAILABLE_TTL = 60


async def mark_brawlstats_api_unavailable() -> None:
    """公式APIが落ちていることを短時間キャッシュし、連続ヒットを防ぐ。"""
    already = await get_cache(BRAWLSTATS_API_UNAVAILABLE_CACHE_KEY)
    if not already:
        logger.warning("Brawl Stars APIが応答しないため、一時的にAPI呼び出しを抑制します。")
    await set_cache(BRAWLSTATS_API_UNAVAILABLE_CACHE_KEY, True, ttl=BRAWLSTATS_API_UNAVAILABLE_TTL)


async def is_brawlstats_api_unavailable() -> bool:
    """公式APIが直近で応答しなかったかどうか。"""
    return bool(await get_cache(BRAWLSTATS_API_UNAVAILABLE_CACHE_KEY))


# パフォーマンスの問題から、一部項目ではインメモリキャッシュ(10分)を使用。管理画面などでの変更が一時的に別プロセスで反映されないのは許容。
brawler_cache = TTLCache(maxsize=200, ttl=10*60)
skin_cache = TTLCache(maxsize=1, ttl=10*60)      # 全件をdictでキャッシュ
accessory_cache = TTLCache(maxsize=2, ttl=10*60) # 全件をdictでキャッシュ、および既知ペアリストをキャッシュ
pin_cache = TTLCache(maxsize=1, ttl=10*60)
title_cache = TTLCache(maxsize=1, ttl=10*60)
frame_cache = TTLCache(maxsize=1, ttl=10*60)
player_icon_cache = TTLCache(maxsize=1, ttl=10*60)
spray_cache = TTLCache(maxsize=1, ttl=10*60)
buffy_cache = TTLCache(maxsize=1, ttl=10*60)


# バトル履歴の保存上限数 (期間による上限方式に変更したため、この値は現在使われていないが残している)
CURRENT_DEFAULT_BATTLE_LOG_LIMIT = 5000
# BSInfo API経由で所持スキンを記録するようになった開始日時
BSINFO_SKIN_OWNERSHIP_STATS_START_AT = datetime.datetime(
    2026, 4, 26, 20, 10, tzinfo=datetime.timezone(datetime.timedelta(hours=9))
)

RANKED_STATS_CACHE_LOCK_TTL = [この部分は公開用リポジトリでは非公開にされています]
RANKED_STATS_CACHE_WAIT_SECONDS = [この部分は公開用リポジトリでは非公開にされています]
RANKED_STATS_CACHE_POLL_INTERVAL = [この部分は公開用リポジトリでは非公開にされています]
RANKED_STATS_GLOBAL_BUILD_LOCK_KEY = [この部分は公開用リポジトリでは非公開にされています]
RANKED_STATS_CACHE_TTL = [この部分は公開用リポジトリでは非公開にされています]
CURRENT_RANKED_POOL_CACHE_TTL = [この部分は公開用リポジトリでは非公開にされています]
BRAWLER_ANALYSIS_CACHE_TTL = [この部分は公開用リポジトリでは非公開にされています]


def _make_ranked_stats_lock_key(cache_key: str) -> str:
    digest = hashlib.sha1(cache_key.encode("utf-8")).hexdigest()
    return f"lock:ranked_stats:{digest}"


def _make_ranked_stats_cache_key(
    start_date: datetime.date,
    end_date: datetime.date,
    mode_filter: str,
    map_filter: str,
    rank_filter: int,
    include_synergy_stats: bool,
    include_composition_stats: bool
) -> str:
    return (
        f"ranked_stats:{start_date}_{end_date}_{mode_filter}_{map_filter}_{rank_filter}"
        f":s{1 if include_synergy_stats else 0}:c{1 if include_composition_stats else 0}"
    )


async def _restore_previous_ranked_stats_cache(
    start_date: datetime.date,
    end_date: datetime.date,
    mode_filter: str,
    map_filter: str,
    rank_filter: int,
    include_synergy_stats: bool,
    include_composition_stats: bool
) -> "RankedStatsData | None":
    previous_end_date = end_date - datetime.timedelta(days=1)
    previous_start_date = start_date - datetime.timedelta(days=1)
    previous_cache_key = _make_ranked_stats_cache_key(
        previous_start_date,
        previous_end_date,
        mode_filter,
        map_filter,
        rank_filter,
        include_synergy_stats,
        include_composition_stats
    )
    return await _restore_ranked_stats_from_cache(previous_cache_key)


async def _acquire_redis_lock(redis_client: Any, lock_key: str, ttl_seconds: int) -> str | None:
    token = secrets.token_hex(16)
    acquired = await redis_client.set(lock_key, token, nx=True, ex=ttl_seconds)
    return token if acquired else None


async def _refresh_redis_lock(redis_client: Any, lock_key: str, lock_token: str, ttl_seconds: int) -> bool:
    # Keep extending only if this process still owns the lock.
    refreshed = await redis_client.eval(
        "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end",
        1,
        lock_key,
        lock_token,
        str(ttl_seconds)
    )
    return bool(refreshed)


async def _release_redis_lock(redis_client: Any, lock_key: str, lock_token: str) -> None:
    # Delete only when token matches to avoid releasing another worker's lock.
    await redis_client.eval(
        "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
        1,
        lock_key,
        lock_token
    )


async def _restore_ranked_stats_from_cache(cache_key: str) -> "RankedStatsData | None":
    cached_data = await get_cache(cache_key)
    if not cached_data:
        return None

    return {
        "brawler_stats": [BrawlerStat.from_dict(d) for d in cached_data.get("brawler_stats", [])],
        "synergy_stats": [SynergyStat.from_dict(d) for d in cached_data.get("synergy_stats", [])],
        "composition_stats": [CompositionStat.from_dict(d) for d in cached_data.get("composition_stats", [])]
    }


async def _wait_for_ranked_stats_cache(cache_key: str) -> "RankedStatsData | None":
    deadline = asyncio.get_running_loop().time() + RANKED_STATS_CACHE_WAIT_SECONDS

    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(RANKED_STATS_CACHE_POLL_INTERVAL)
        cached_result = await _restore_ranked_stats_from_cache(cache_key)
        if cached_result:
            return cached_result

    return None


async def _get_latest_ranked_stats_date(db: asyncpg.Connection) -> datetime.date:
    latest_date_cache_key = "latest_ranked_stats_date"
    latest_date_str = await get_cache(latest_date_cache_key)
    if latest_date_str:
        return datetime.datetime.fromisoformat(latest_date_str).date()

    latest_date_row = await db.fetchrow("SELECT MAX(date) FROM ranked_stats_brawler")
    if latest_date_row and latest_date_row['max']:
        end_date = latest_date_row['max']
    else:
        end_date = datetime.datetime.now(datetime.timezone.utc).date()

    await set_cache(latest_date_cache_key, end_date.isoformat(), ttl=3600)
    return end_date


#* /---*---*---*---*---*---*---*---*/
#* キャラクター関連
#* /---*---*---*---*---*---*---*---*/
class MultilingualText(TypedDict): 
    """複数言語に対応したテキスト"""
    ja: str
    en: str
    
class SpecialStatus(TypedDict):
    """特殊ステータス"""
    name: MultilingualText
    description: NotRequired[MultilingualText]
    value: int | float
    linked: bool # パワーレベルにリンクしてステータスが上がるかどうか

@dataclass
class SkinData:
    id: int
    brawler_id: int | None
    en: str | None
    ja: str | None
    rarity: int | None
    is_limited: bool | None
    description_en: str | None
    description_ja: str | None
    ownership_rate: float | None = None
    equip_rate: float | None = None
    bling_price: int | None = None
    coins_price: int | None = None
    gems_price: int | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "brawler_id": self.brawler_id,
            "en": self.en,
            "ja": self.ja,
            "rarity": self.rarity,
            "is_limited": self.is_limited,
            "description_en": self.description_en,
            "description_ja": self.description_ja,
            "ownership_rate": self.ownership_rate,
            "equip_rate": self.equip_rate,
            "bling_price": self.bling_price,
            "coins_price": self.coins_price,
            "gems_price": self.gems_price,
        }

@dataclass
class PinData:
    id: int
    brawler_id: int | None
    rarity: int | None
    description_en: str | None
    description_ja: str | None
    equip_rate: float | None = None
    bling_price: int | None = None
    gems_price: int | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "brawler_id": self.brawler_id,
            "rarity": self.rarity,
            "description_en": self.description_en,
            "description_ja": self.description_ja,
            "equip_rate": self.equip_rate,
            "bling_price": self.bling_price,
            "gems_price": self.gems_price,
        }

@dataclass
class TitleData:
    id: int
    brawler_id: int | None
    rarity: int | None
    en: str | None
    ja: str | None
    equip_rate: float | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "brawler_id": self.brawler_id,
            "rarity": self.rarity,
            "en": self.en,
            "ja": self.ja,
            "equip_rate": self.equip_rate
        }

@dataclass
class FrameData:
    id: int
    type: str | None
    en: str | None
    ja: str | None
    equip_rate: float | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "en": self.en,
            "ja": self.ja,
            "equip_rate": self.equip_rate
        }

@dataclass
class PlayerIconData:
    id: int
    bling_price: int | None = None
    gems_price: int | None = None
    equip_rate: float | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "bling_price": self.bling_price,
            "gems_price": self.gems_price,
            "equip_rate": self.equip_rate
        }

@dataclass
class SprayData:
    id: int
    rarity: int | None = None
    bling_price: int | None = None
    gems_price: int | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "rarity": self.rarity,
            "bling_price": self.bling_price,
            "gems_price": self.gems_price
        }

@dataclass
class AccessoryData:
    id: int
    type: str
    brawler_id: int | None
    en: str
    ja: str | None
    description_en: str | None
    description_ja: str | None
    buffie_description_en: str | None
    buffie_description_ja: str | None
    cooldown: int | None
    rarity: int | None
    is_invalid: bool = False

    def to_dict(self) -> dict:
        res = {
            "id": self.id,
            "type": self.type,
            "name": {"ja": self.ja, "en": self.en},
            "description": {"ja": self.description_ja, "en": self.description_en},
            "cooldown": self.cooldown,
            "cooldown_with_buffie": self.cooldown,  # 後方互換
            "duration": 5, # 5秒(共通)
            "duration_with_buffie": 7, # 7秒(共通)
            "rarity": self.rarity,
            "is_invalid": self.is_invalid,
        }
        if self.buffie_description_en and self.buffie_description_ja:
            res["description_with_buffie"] = {"ja": self.buffie_description_ja, "en": self.buffie_description_en}
        else:
            res["description_with_buffie"] = None
        return res

async def get_all_skins(db: asyncpg.Connection) -> dict[int, SkinData]:
    """全スキンデータを取得し、IDをキーとする辞書で返す（キャッシュあり）"""
    if "all" in skin_cache:
        return skin_cache["all"]
    
    rows = await db.fetch("SELECT * FROM skins")
    result = {
        row["id"]: SkinData(
            id=row["id"], brawler_id=row["brawler_id"], en=row["en"], ja=row["ja"],
            rarity=row.get("rarity"), is_limited=row.get("is_limited"),
            description_en=row.get("description_en"), description_ja=row.get("description_ja"),
            ownership_rate=row.get("ownership_rate"), equip_rate=row.get("equip_rate"),
            bling_price=row.get("bling_price"), coins_price=row.get("coins_price"), gems_price=row.get("gems_price"),
        ) for row in rows
    }
    skin_cache["all"] = result
    return result

async def get_skin_catalog_stats(db: asyncpg.Connection) -> dict[str, Any]:
    """全スキンの総数・限定スキン総数・レア度別総数を返す。"""
    all_skins = await get_all_skins(db)

    counts_by_rarity: Counter[int] = Counter()
    limited_count = 0

    for skin in all_skins.values():
        rarity = skin.rarity if skin.rarity is not None else 0
        counts_by_rarity[rarity] += 1
        if skin.is_limited:
            limited_count += 1

    return {
        "total_count": len(all_skins),
        "limited_count": limited_count,
        "counts_by_rarity": dict(sorted(counts_by_rarity.items())),
    }

# skin_cacheはmaxsize=1のため別枠で保持し、skin_cache["all"]が入れ替わった時だけ再計算する
_skin_count_by_brawler_memo: dict[str, Any] = {"source": None, "counts": {}}

def get_skin_count_by_brawler() -> dict[int, int]:
    """キャラごとの全スキン数を返す。skin_cacheが未ロードの場合は空。"""
    all_skins = skin_cache.get("all")
    if all_skins is None:
        return {}
    if _skin_count_by_brawler_memo["source"] is not all_skins:
        _skin_count_by_brawler_memo["counts"] = dict(Counter(
            skin.brawler_id for skin in all_skins.values() if skin.brawler_id is not None
        ))
        _skin_count_by_brawler_memo["source"] = all_skins
    return _skin_count_by_brawler_memo["counts"]

async def get_all_pins(db: asyncpg.Connection) -> dict[int, PinData]:
    """全ピンズデータを取得し、IDをキーとする辞書で返す（キャッシュあり）"""
    if "all" in pin_cache:
        return pin_cache["all"]
    
    rows = await db.fetch("SELECT * FROM pins")
    result = {
        row["id"]: PinData(
            id=row["id"], brawler_id=row["brawler_id"], rarity=row.get("rarity"),
            description_en=row.get("description_en"), description_ja=row.get("description_ja"),
            equip_rate=row.get("equip_rate"),
            bling_price=row.get("bling_price"), gems_price=row.get("gems_price"),
        ) for row in rows
    }
    pin_cache["all"] = result
    return result

async def get_all_titles(db: asyncpg.Connection) -> dict[int, TitleData]:
    """全タイトルデータを取得し、IDをキーとする辞書で返す（キャッシュあり）"""
    if "all" in title_cache:
        return title_cache["all"]
    
    rows = await db.fetch("SELECT * FROM titles")
    result = {
        row["id"]: TitleData(
            id=row["id"], brawler_id=row["brawler_id"], rarity=row.get("rarity"),
            en=row.get("en"), ja=row.get("ja"), equip_rate=row.get("equip_rate")
        ) for row in rows
    }
    title_cache["all"] = result
    return result

async def get_all_frames(db: asyncpg.Connection) -> dict[int, FrameData]:
    """全フレームデータを取得し、IDをキーとする辞書で返す（キャッシュあり）"""
    if "all" in frame_cache:
        return frame_cache["all"]
    
    rows = await db.fetch("SELECT * FROM frames")
    result = {
        row["id"]: FrameData(
            id=row["id"], type=row.get("type"), en=row.get("en"), ja=row.get("ja"),
            equip_rate=row.get("equip_rate")
        ) for row in rows
    }
    frame_cache["all"] = result
    return result

async def get_all_player_icons(db: asyncpg.Connection) -> dict[int, PlayerIconData]:
    """全プレイヤーアイコンデータを取得し、IDをキーとする辞書で返す（キャッシュあり）"""
    if "all" in player_icon_cache:
        return player_icon_cache["all"]

    rows = await db.fetch("SELECT * FROM player_icons")
    result = {
        row["id"]: PlayerIconData(
            id=row["id"],
            bling_price=row.get("bling_price"),
            gems_price=row.get("gems_price"),
            equip_rate=row.get("equip_rate")
        ) for row in rows
    }
    player_icon_cache["all"] = result
    return result

async def get_all_sprays(db: asyncpg.Connection) -> dict[int, SprayData]:
    """全スプレーデータを取得し、IDをキーとする辞書で返す（キャッシュあり）"""
    if "all" in spray_cache:
        return spray_cache["all"]

    rows = await db.fetch("SELECT * FROM sprays")
    result = {
        row["id"]: SprayData(
            id=row["id"],
            rarity=row.get("rarity"),
            bling_price=row.get("bling_price"),
            gems_price=row.get("gems_price"),
        ) for row in rows
    }
    spray_cache["all"] = result
    return result


@dataclass
class BuffyData:
    id: int
    brawler_id: int
    type: str  # "default" / "gadget" / "starPower" / "hypercharge" / "bling" / "ghostly"
    en: str | None
    ja: str | None
    is_invalid: bool = False


async def get_all_buffies(db: asyncpg.Connection) -> dict[int, BuffyData]:
    """全バフィーデータを取得し、IDをキーとする辞書で返す（キャッシュあり）"""
    if "all" in buffy_cache:
        return buffy_cache["all"]

    rows = await db.fetch("SELECT * FROM buffies")
    result = {
        row["id"]: BuffyData(
            id=row["id"],
            brawler_id=row["brawler_id"],
            type=row["type"],
            en=row["en"],
            ja=row["ja"],
            is_invalid=bool(row["is_invalid"]),
        ) for row in rows
    }
    buffy_cache["all"] = result
    return result

async def get_all_accessories(db: asyncpg.Connection) -> dict[tuple[int, int], AccessoryData]:
    """全アクセサリーデータを取得し、(ID, キャラクターID)の複合キーとする辞書で返す（キャッシュあり）"""
    if "all" in accessory_cache:
        return accessory_cache["all"]
    
    rows = await db.fetch("SELECT * FROM accessories")
    result = {}
    known_pairs = set()
    for row in rows:
        acc = AccessoryData(
            id=row["id"], type=row["type"], brawler_id=row["brawler_id"],
            en=row["en"], ja=row["ja"],
            description_en=row["description_en"], description_ja=row["description_ja"],
            buffie_description_en=row["buffie_description_en"], buffie_description_ja=row["buffie_description_ja"],
            cooldown=row["cooldown"], rarity=row["rarity"],
            is_invalid=row["is_invalid"]
        )
        result[(row["id"], row["brawler_id"])] = acc
        known_pairs.add((row["id"], row["brawler_id"]))
        
    accessory_cache["all"] = result
    accessory_cache["known_pairs"] = known_pairs
    return result

# [この部分は公開用リポジトリでは非公開にされています]


class Brawler:
    """キャラクターの基本データの格納用。
    """
    def __init__(self, id: int, db: asyncpg.Connection) -> None:
        self.id: int = id
        self.name_en: str = ""
        self.name_ja: str | None = None
        self.names_ja: list[str] | None = None
        self.is_temporary: bool = False
        self.rarity: int | None = None
        # 以下の3つは、トップランカーボーダーで値を保管しておくために使う
        self.border: int | None = None
        self.comparison_target_border: int | None = None
        self.fluctuation: int | None = None
        self.db: asyncpg.Connection = db
        #^ BSInfo API由来のキャラクターステータス情報
        self.has_bsinfo_data: bool = False
        self.class_ja: str | None = None
        self.class_en: str | None = None
        self.class_id: int | None = None
        self.title_mastery_ja: str | None = None
        self.title_mastery_en: str | None = None
        self.title_prestige_ja: str | None = None
        self.title_prestige_en: str | None = None
        self.hp: int | None = None
        self.speed: int | None = None
        self.damage: int | None = None
        self.range: float | None = None
        self.reload_speed: float | None = None
        self.max_ammo: int | None = None
        self.spread: int | float | None = None

    def apply_bsinfo_status(self, bsinfo_brawler: bsinfoapi.BSInfoBrawler) -> None:
        """BSInfo APIから取得したステータスをBrawlerへ反映する。"""
        self.class_ja = bsinfo_brawler.class_ja
        self.class_en = bsinfo_brawler.class_en
        self.class_id = bsinfo_brawler.class_id
        self.title_mastery_ja = bsinfo_brawler.title_mastery_ja
        self.title_mastery_en = bsinfo_brawler.title_mastery_en
        self.title_prestige_ja = bsinfo_brawler.title_prestige_ja
        self.title_prestige_en = bsinfo_brawler.title_prestige_en
        self.hp = bsinfo_brawler.hp
        self.speed = bsinfo_brawler.speed
        self.damage = bsinfo_brawler.damage
        self.range = bsinfo_brawler.range
        self.reload_speed = bsinfo_brawler.reload_speed
        self.max_ammo = bsinfo_brawler.max_ammo
        self.spread = bsinfo_brawler.spread
        self.has_bsinfo_data = bsinfo_brawler.has_any_data()

    async def get_brawler_info(self, retry: bool = True) -> None:
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
        # idがdataに存在するか確認
        brawler_id = data.get("id")
        if brawler_id is None:
            raise ValueError("Brawler.from_dict: 'id' not found in data")
        instance = cls(id=brawler_id, db=db) # id をコンストラクタに渡す
        instance.name_en = data.get("name_en", "") # デフォルト値を設定
        instance.name_ja = data.get("name_ja")
        instance.names_ja = data.get("names_ja")
        instance.is_temporary = data.get("is_temporary", False) # デフォルト値を設定
        instance.rarity = data.get("rarity")
        instance.has_bsinfo_data = data.get("has_bsinfo_data", False)
        instance.class_ja = data.get("class_ja")
        instance.class_en = data.get("class_en")
        instance.class_id = data.get("class_id")
        instance.title_mastery_ja = data.get("title_mastery_ja")
        instance.title_mastery_en = data.get("title_mastery_en")
        instance.title_prestige_ja = data.get("title_prestige_ja")
        instance.title_prestige_en = data.get("title_prestige_en")
        instance.hp = data.get("hp")
        instance.speed = data.get("speed")
        instance.damage = data.get("damage")
        instance.range = data.get("range")
        instance.reload_speed = data.get("reload_speed")
        instance.max_ammo = data.get("max_ammo")
        instance.spread = data.get("spread")
        return instance

    @property
    def gadgets(self) -> list[dict]:
        cache = accessory_cache.get("all", {})
        return [acc.to_dict() for acc in cache.values() if acc.brawler_id == self.id and acc.type == 'gadget']

    @property
    def star_powers(self) -> list[dict]:
        cache = accessory_cache.get("all", {})
        return [acc.to_dict() for acc in cache.values() if acc.brawler_id == self.id and acc.type == 'starPower']

    @property
    def gears(self) -> list[dict]:
        cache = accessory_cache.get("all", {})
        return [acc.to_dict() for acc in cache.values() if acc.brawler_id == self.id and acc.type == 'gear']

    @property
    def hypercharges(self) -> list[dict]:
        cache = accessory_cache.get("all", {})
        return [acc.to_dict() for acc in cache.values() if acc.brawler_id == self.id and acc.type == 'hyperCharge']

    @property
    def hypercharge(self) -> dict | None:
        cache = accessory_cache.get("all", {})
        hcs = [acc.to_dict() for acc in cache.values() if acc.brawler_id == self.id and acc.type == 'hyperCharge']
        return hcs[0] if hcs else None
    
    @property
    def all_skins(self) -> list["SkinData"]:
        cache = skin_cache.get("all", {})
        return [skin.to_dict() for skin in cache.values() if skin.brawler_id == self.id]

async def get_brawler(id: int, db: asyncpg.Connection) -> Brawler:
    """Brawlerデータを取得する。60分間のキャッシュを使用する。

    Args:
        id (int): Brawler ID
        db (asyncpg.Connection): データベース接続

    Returns:
        Brawler: キャラクター情報
    """
    # 依存するアクセサリーとスキンのキャッシュを評価時に利用できるよう事前にロード
    if "all" not in accessory_cache:
        await get_all_accessories(db)
    if "all" not in skin_cache:
        await get_all_skins(db)

    cached_data = brawler_cache.get(id)
    if cached_data:
        return await Brawler.from_dict(data=cached_data, db=db)
    
    brawler = Brawler(id, db)
    await brawler.get_brawler_info()
    
    brawler_cache[id] = brawler.to_dict()
    return brawler

async def search_brawler(input_name: str, db: asyncpg.Connection) -> Brawler | None:
    """ユーザーから入力されたキャラ名からキャラを取得する。マッチ対象はデータベースの英語名(大文字小文字は問わない)、または日本語名(複数ある)。キャラが見つからなかった場合はNoneを返す。

    Args:
        input_name (str): ユーザーから入力されたキャラ名。
        db (asyncpg.Connection): データベース接続。

    Returns:
        Brawler: キャラクターデータ。見つからなかった場合はNone。
    """
    # 英語名で検索 (大文字小文字を区別しない) - ILIKE を使用
    brawler_id = await db.fetchval("SELECT id FROM brawlers WHERE en ILIKE $1", input_name)
    if brawler_id:
        brawler = Brawler(id=brawler_id, db=db)
        await brawler.get_brawler_info()
        return brawler

    # ja カラムに `["ジェシー", "じぇしー"]` のようなJSON配列が格納されている想定
    brawler_id = await db.fetchval("SELECT id FROM brawlers WHERE ja @> $1::jsonb", [input_name])
    if brawler_id:
        brawler = Brawler(id=brawler_id, db=db)
        await brawler.get_brawler_info()
        return brawler

    return None # 見つからなかった場合

async def get_available_brawlers(db: asyncpg.Connection) -> list[Brawler]:
    """利用可能なキャラクターデータ一覧を取得する。APIが利用できない場合でも、DB内にあるキャラ一覧をもとに返す。10分間のキャッシュ。

    Args:
        db (asyncpg.Connection): DBの接続。

    Returns:
        list[Brawler]: Brawlerデータのリスト。
    """
    cache_key = "available_brawlers"
    cached_data = await get_cache(cache_key)
    if cached_data:
        result = []
        for data in cached_data:
            result.append(await Brawler.from_dict(data=data, db=db))
        return result
    
    available_brawler_ids: list[int] = []
    try:
        brawlstats_brawlers: list = get_brawlstats_client().get_brawlers()
        available_brawler_ids = [brawler.id for brawler in brawlstats_brawlers]
    except Exception: # より広い例外を捕捉
        # メンテナンス中などで、APIからキャラ一覧が取得できなかった場合
        # データベースに存在するBrawler IDのリストを取得
        results = await db.fetch("SELECT id FROM brawlers")
        available_brawler_ids = [row['id'] for row in results]
    
    brawlers = []
    for brawler_id in available_brawler_ids:
        brawler = await get_brawler(brawler_id, db)
        brawlers.append(brawler)
    await set_cache(key=cache_key, value=[brawler.to_dict() for brawler in brawlers], ttl=600)
    return brawlers

async def calc_num_of_available_brawlers(db: asyncpg.Connection) -> int:
    """DBのプレイヤーデータのアンロック済みキャラクター数の最大値を計算する。デフォルト時間のキャッシュ。

    Args:
        db (asyncpg.Connection): DBの接続。

    Returns:
        int: キャラクター数
    """
    cache_key = "num_of_available_brawlers"
    cached_data = await get_cache(cache_key)
    if cached_data:
        return cached_data
    
    # fetchval を使用
    max_unlocked_brawlers = await db.fetchval('SELECT MAX(unlocked_brawlers) FROM players')
    if max_unlocked_brawlers is None:
        max_unlocked_brawlers = 0
    
    await set_cache(key=cache_key, value=max_unlocked_brawlers)
    return max_unlocked_brawlers

async def add_new_brawlers(brawlstats_brawlers: list, db: asyncpg.Connection) -> None:
    """brawlstatsで取得したキャラクターのリストから、DBのbrawlersテーブルに入っていないものがあれば英語名とともに追加する。

    Args:
        brawlstats_brawlers (list): brawlstatsで取得したキャラクターのリスト。
        db (asyncpg.Connection): DBの接続。
    """
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



#* /---*---*---*---*---*---*---*---*/
#* マップ/モード名関連は map_mode_catalog を参照
#* /---*---*---*---*---*---*---*---*/


#* /---*---*---*---*---*---*---*---*/
#* トップランカー関連
#* /---*---*---*---*---*---*---*---*/
async def record_prestige_borders(available_brawlers: list[Brawler], db: asyncpg.Connection) -> None:
    """トップランカーボーダーを記録する。

    Args:
        available_brawlers (list[Brawler]): 利用可能なキャラクターのリスト
        db (asyncpg.Connection): データベース接続
    """
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

#* /---*---*---*---*---*---*---*---*/
#* ランキング関連
#* /---*---*---*---*---*---*---*---*/
async def insert_new_players_from_rankings(db: asyncpg.Connection, regions: list[str] = [None, "jp"]) -> int:
    """総合トロフィーランキングとキャラクターランキングから新しいプレイヤーをDBに追加する。

    Args:
        db (asyncpg.Connection): データベース接続
        regions (list[str], optional): どの国のランキングを対象とするか. デフォルトは[None, "jp"]。

    Returns:
        int: 新しく追加したプレイヤーの数。
    """
    # [この部分は公開用リポジトリでは非公開にされています]

class RankingPlayer:
    """データベースから集計するタイプのランキングプレイヤー格納用。
    """
    def __init__(self, dbrow: asyncpg.Record = MISSING, i: int = MISSING, score_name: str = MISSING):
        if dbrow is not MISSING and i is not MISSING and score_name is not MISSING:
            self.tag: str = dbrow["tag"]
            self.name: str = dbrow["name"]
            score_value = dbrow.get(score_name)
            # Decimal型が返ってきた場合は、JSONシリアライズ可能なfloat型に変換する
            if isinstance(score_value, Decimal):
                self.score: int | float = float(score_value)
            else:
                self.score: int | float = score_value
            self.rank: int = i
            self.icon_id: int = dbrow["icon_id"]
            self.winstreak_brawler_id: int | None = dbrow.get("winstreak_brawler")
            self.record_level: int | None = dbrow.get("record_level")
        else:
            self.tag: str = None
            self.name: str = None
            self.score: int | float = None
            self.rank: int = None
            self.icon_id: int = None
            self.winstreak_brawler_id: int | None = None
            self.record_level: int | None = None
        self.icon_path: str | None = None
    
    def to_dict(self) -> dict:
        return {
            "tag": self.tag,
            "name": self.name,
            "score": self.score,
            "rank": self.rank,
            "icon_id": self.icon_id,
            "icon_path": self.icon_path,
            "winstreak_brawler_id": self.winstreak_brawler_id,
            "record_level": self.record_level
        }
        
    @classmethod
    def from_dict(cls, data: dict):
        instance = cls()
        instance.tag = data.get("tag")
        instance.name = data.get("name")
        instance.score = data.get("score")
        instance.rank = data.get("rank")
        instance.icon_id = data.get("icon_id")
        instance.icon_path = data.get("icon_path")
        instance.winstreak_brawler_id = data.get("winstreak_brawler_id")
        instance.record_level = data.get("record_level")
        return instance
    
    @property
    def fame_rank(self) -> int:
        """名声ランクを計算する。(ランキングタイプが名声でない時に実行しても、意味のない数値が返るので注意)

        Returns:
            int: 名声ランク(0: ランクなし, 1: アース1, ..., 24: スターフォース3)
        """
        if not self.score: return 0
        elif 0 < self.score < 2000: return 1 # Global 1
        elif 2000 <= self.score < 4000: return 2 # Global 2
        elif 4000 <= self.score < 6000: return 3 # Global 3
        elif 6000 <= self.score < 9200: return 4 # Lunar 1
        elif 9200 <= self.score < 12400: return 5 # Lunar 2
        elif 12400 <= self.score < 15600: return 6 # Lunar 3
        elif 15600 <= self.score < 20100: return 7 # Martian 1
        elif 20100 <= self.score < 24600: return 8 # Martian 2
        elif 24600 <= self.score < 29100: return 9 # Martian 3
        elif 29100 <= self.score < 37100: return 10 # Saturnian 1
        elif 37100 <= self.score < 45100: return 11 # Saturnian 2
        elif 45100 <= self.score < 53100: return 12 # Saturnian 3
        elif 53100 <= self.score < 65100: return 13 # Solar 1
        elif 65100 <= self.score < 77100: return 14 # Solar 2
        elif 77100 <= self.score < 89100: return 15 # Solar 3
        elif 89100 <= self.score < 109100: return 16 # Meteoric 1
        elif 109100 <= self.score < 129100: return 17 # Meteoric 2
        elif 129100 <= self.score < 149100: return 18 # Meteoric 3
        elif 149100 <= self.score < 199100: return 19 # Alien 1
        elif 199100 <= self.score < 249100: return 20 # Alien 2
        elif 249100 <= self.score < 299100: return 21 # Alien 3
        elif 299100 <= self.score < 374100: return 22 # Starr Force 1
        elif 374100 <= self.score < 449100: return 23 # Starr Force 2
        elif self.score >= 449100: return 24 # Starr Force 3

class RankingTrophyPlayer:
    """総合トロフィー・キャラクターランキングのランキングプレイヤー格納用。
    """
    def __init__(self, brawlstats_rankingplayer = MISSING):
        if brawlstats_rankingplayer is not MISSING:
            self.tag: str = brawlstats_rankingplayer.tag
            self.name: str = brawlstats_rankingplayer.name
            self.trophies: int = brawlstats_rankingplayer.trophies
            self.rank: int = brawlstats_rankingplayer.rank
            self.icon_id: int = brawlstats_rankingplayer.icon.id
            self.icon_path: str | None = None
        else:
            self.tag: str = None
            self.name: str = None
            self.trophies: int = None
            self.rank: int = None
            self.icon_id: int = None
            self.icon_path: str | None = None
    
    def to_dict(self) -> dict:
        return {
            "tag": self.tag,
            "name": self.name,
            "trophies": self.trophies,
            "rank": self.rank,
            "icon_id": self.icon_id,
            "icon_path": self.icon_path
        }
        
    @classmethod
    def from_dict(cls, data: dict):
        instance = cls()
        instance.tag = data.get("tag")
        instance.name = data.get("name")
        instance.trophies = data.get("trophies")
        instance.rank = data.get("rank")
        instance.icon_id = data.get("icon_id")
        instance.icon_path = data.get("icon_path")
        return instance

class RankingRankedPlayer:
    """ガチバトル・旧パワーリーグランキングのランキングプレイヤー格納用。
    """
    def __init__(self):
        self.tag: str = None
        self.name: str = None
        self.ranked_rank: int = None
        self.ranked_score: int = None
        self.rank: int = None
        self.icon_id: int = None
        self.icon_path: str | None = None
    
    def to_dict(self) -> dict:
        return {
            "tag": self.tag,
            "name": self.name,
            "ranked_rank": self.ranked_rank,
            "ranked_score": self.ranked_score,
            "rank": self.rank,
            "icon_id": self.icon_id,
            "icon_path": self.icon_path
        }
        
    @classmethod
    def from_dict(cls, data: dict):
        instance = cls()
        instance.tag = data.get("tag")
        instance.name = data.get("name")
        instance.ranked_rank = data.get("ranked_rank")
        instance.ranked_score = data.get("ranked_score")
        instance.rank = data.get("rank")
        instance.icon_id = data.get("icon_id")
        instance.icon_path = data.get("icon_path")
        return instance

class RankingClub:
    """クラブランキングのランキングクラブ格納用。
    """
    def __init__(self, brawlstats_rankingclub = MISSING):
        if brawlstats_rankingclub is not MISSING:
            self.tag: str = brawlstats_rankingclub.tag
            self.name: str = brawlstats_rankingclub.name
            self.trophies: int = brawlstats_rankingclub.trophies
            self.rank: int = brawlstats_rankingclub.rank
            self.member_count: int = brawlstats_rankingclub.member_count
            self.badge_id: int = brawlstats_rankingclub.badge_id
        else:
            self.tag: str = None
            self.name: str = None
            self.trophies: int = None
            self.rank: int = None
            self.member_count: int = None
            self.badge_id: int = None
        
    def to_dict(self) -> dict:
        return {
            "tag": self.tag,
            "name": self.name,
            "trophies": self.trophies,
            "rank": self.rank,
            "member_count": self.member_count,
            "badge_id": self.badge_id
        }
        
    @classmethod
    def from_dict(cls, data: dict):
        instance = cls()
        instance.tag = data.get("tag")
        instance.name = data.get("name")
        instance.trophies = data.get("trophies")
        instance.rank = data.get("rank")
        instance.member_count = data.get("member_count")
        instance.badge_id = data.get("badge_id")
        return instance

VALID_RANKING_NUMERIC_COLUMNS = ["current_trophies", "highest_trophies", "season_trophies", "average_highest_trophies", "team_victories", "solo_victories", "duo_victories", "max_winstreak", "fame_points", "season_high_trophies", "prestige", "total_mastery", "record_points"]
async def get_custom_ranking(db: asyncpg.Connection, ranking_type: str, region: str | None = None, limit: int = 200) -> list[RankingPlayer]:
    """DBから読み出すタイプのランキングを取得する。5分間のキャッシュを使用する。

    Args:
        ranking_type (str): ランキングのタイプ。DBのカラム名を指定すること。
        region (str | None): 地域コード。デフォルトはNoneで、Noneの場合は指定なし。
        limit (int): ランキングの取得数。デフォルトは200。
        
    Raises:
        ValueError: ランキングタイプ、地域、リミットの値が不適切だった場合
        DataBaseError: データベースエラー

    Returns:
        list[RankingPlayer]: ランキングのリスト
    """
    cache_key = f"ranking:{ranking_type}_{region if region else "global"}_{limit}"
    cached_data = await get_cache(cache_key)
    if cached_data:
        return [RankingPlayer.from_dict(data) for data in cached_data]
    
    # SQLインジェクションを防ぐため、許可されたカラム名か検証
    if ranking_type not in VALID_RANKING_NUMERIC_COLUMNS:
        raise ValueError(f"無効なランキングタイプが指定されました: {ranking_type}")
    if limit < 1:
        raise ValueError("limitは1以上で指定してください。")

    # クエリとパラメータを動的に構築
    params = []
    where_clauses = ["is_invalid = FALSE"] # 無効なプレイヤーは除外
    
    if region:
        where_clauses.append(f"region = ${len(params) + 1}")
        params.append(region)

    where_str = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    
    # ranking_type は検証済みなので、f-stringで安全に埋め込める
    query = f"""
        SELECT tag, name, icon_id, {ranking_type if ranking_type not in ["max_winstreak", "record_points"] else "max_winstreak, winstreak_brawler" if ranking_type == "max_winstreak" else "record_points, record_level"}
        FROM players
        {where_str}
        ORDER BY {ranking_type} DESC NULLS LAST
        LIMIT ${len(params) + 1}
    """
    params.append(limit)
    
    try:
        results = await db.fetch(query, *params)
    except asyncpg.PostgresError as e:
        logger.error(f"カスタムランキング取得中にDBエラー: {e}", exc_info=True)
        raise DataBaseError("ランキングデータの取得中にエラーが発生しました。") from e

    ranking_list = [RankingPlayer(dbrow=row, i=i, score_name=ranking_type) for i, row in enumerate(results, 1)]

    await set_cache(key=cache_key, value=[player.to_dict() for player in ranking_list], ttl=300)
    
    return ranking_list

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


#* /---*---*---*---*---*---*---*---*/
#* クラブ関連
#* /---*---*---*---*---*---*---*---*/
class Member:
    """クラブメンバーのデータ格納用。
    """
    def __init__(self, brawlstats_member = MISSING):
        if brawlstats_member is not MISSING:
            self.tag: str = brawlstats_member.tag
            self.name: str = brawlstats_member.name
            self.role: str = brawlstats_member.role
            self.trophies: int = brawlstats_member.trophies
            self.icon_id: int = brawlstats_member.icon.id
            self.icon_path: str | None = None
        else:
            self.tag: str = None
            self.name: str = None
            self.role: str = None
            self.trophies: int = None
            self.icon_id: int = None
            self.icon_path: str | None = None
        self.megapig_wins: int | None = None
        self.megapig_tickets: int | None = None
            
    def to_dict(self) -> dict:
        return {
            "tag": self.tag,
            "name": self.name,
            "role": self.role,
            "trophies": self.trophies,
            "icon_id": self.icon_id,
            "icon_path": self.icon_path,
            "megapig_wins": self.megapig_wins,
            "megapig_tickets": self.megapig_tickets
        }

    @classmethod
    def from_dict(cls, data: dict):
        instance = cls()
        instance.tag = data.get("tag")
        instance.name = data.get("name")
        instance.role = data.get("role")
        instance.trophies = data.get("trophies")
        instance.icon_id = data.get("icon_id")
        instance.icon_path = data.get("icon_path", None)
        instance.megapig_wins = data.get("megapig_wins")
        instance.megapig_tickets = data.get("megapig_tickets")
        return instance

class Club:
    """クラブのデータ格納用。初期化時に、自動でクラブデータ取得を行い、情報が各変数に入る。
    取得時、データベースに未追加のクラブは自動で追加される。
    APIに接続できないか、無効なクラブタグの場合はBrawlStarsAPIErrorが発生する。データベースエラーの場合はDataBaseError。
    """
    def __init__(self, tag: str, db: asyncpg.Connection) -> None:
        self.tag: str = tag
        self.name: str = None
        self.description: str = None
        self.type: str = None
        self.trophies: int = None
        self.required_trophies: int = None
        self.badge_id: int = None
        self.region: str | None = None
        self.region_ja: str | None = None
        self.region_en: str | None = None
        self.members_online: int | None = None
        self.is_family_friendly: bool | None = None
        self.members: list[Member] = []
        self.db: asyncpg.Connection = db
        
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

    if await is_brawlstats_api_unavailable():
        if cached_data:
            return await Club.from_dict(cached_data, db)
        raise BrawlStarsAPIError()
    
    freshness_flag = await get_cache(freshness_key)
    if not freshness_flag:
        await set_cache(key=freshness_key, value=True, ttl=3)
    if cached_data and freshness_flag:
        return await Club.from_dict(cached_data, db)
    
    try:
        club = Club(tag, db)
        await club.get_club_data()
    except (BrawlStarsAPIError, DataBaseError):
        if cached_data:
            return await Club.from_dict(cached_data, db)
        raise
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


#* /---*---*---*---*---*---*---*---*/
#* バトル関連
#* /---*---*---*---*---*---*---*---*/
class Battle:
    """バトル。初期化時にDBのRowを渡す。
    """
    def __init__(self, dbrow: asyncpg.Record) -> None:
        self.tag: str = dbrow["tag"]
        self.datetime: datetime.datetime = dbrow["datetime"]
        self.datetime_jst: datetime.datetime = self.datetime.astimezone(datetime.timezone(datetime.timedelta(hours=9)))
        self.event_id: int | None = dbrow["event_id"] # おそらく必ず存在する。コンペの時は0
        self.event_mode: str | None = dbrow["event_mode"] # Ver11.0より保存を開始。
        self.event_mode_ja: str | None = None
        self.event_map: str | None = dbrow["event_map"] # 英語のマップ名。コンペの時はNone
        self.event_map_ja: str | None = None # 日本語のマップ名。
        self.event_map_en: str | None = None
        self.battle_mode: str | None = dbrow["battle_mode"] # バトル自体のモードであり、おそらく必ず存在する。55では33と同じモード名となるので注意。
        self.battle_mode_ja: str | None = None
        try:
            self.mode_id: int | None = dbrow["mode_id"]
        except (KeyError, IndexError):
            self.mode_id = None
        self.mode_slug: str | None = None
        self.mode_en: str | None = None
        self.mode_ja: str | None = None
        self.battle_type: str | None = dbrow["battle_type"] # 通常='ranked', チャレンジ='challenge','championshipChallenge'ほか, フレバト='friendly', ガチバトル='soloRanked', ビッグゲーム系=None
        self.result: str = dbrow["result"] # 勝利"w", 引き分け"d", 敗北"l"。バトルセット時、セットのリザルト。
        self.rank: int | None = dbrow["rank"] # 順位のあるモードにおける順位。
        self.is_starplayer: bool | None = dbrow["is_starplayer"]
        self.brawler: int | None = dbrow["brawler"]
        self.power: int | None = dbrow["power"]
        self.trophies: int | None = dbrow["trophies"]
        self.trophy_change: int | None = dbrow["trophy_change"]
        self.brawlers: list[tuple[int | None]] | None = dbrow["brawlers"]
        self.team_size: int = dbrow["team_size"]
        self.num_of_teams: int = dbrow["num_of_teams"]
        self.teammate_tags: list[str] = dbrow["teammate_tags"]
        self.opponent_tags: list[str] = dbrow["opponent_tags"]
        self.teammate_names: list[str] = dbrow["teammate_names"]
        self.opponent_names: list[str] = dbrow["opponent_names"]
        self.teammate_brawlers: list[int] = dbrow["teammate_brawlers"]
        self.opponent_brawlers: list[int] = dbrow["opponent_brawlers"]
        self.ranked_score_after: int | None = dbrow["ranked_score_after"] # 試合終了後のガチバトルスコア。このデータは一部のバトルのみにある。
        self.set_w: int | None = None # バトルセット時、セットの勝利数。
        self.set_d: int | None = None # バトルセット時、セットの引き分け数。
        self.set_l: int | None = None # バトルセット時、セットの敗北数。
    
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
            split_point = len(ja_name) // 2
            return ja_name[:split_point] + "\n" + ja_name[split_point:]
        return ja_name
    if en_name:
        return en_name
    return format_mode_slug_to_display(slug) if slug else (slug or "")


def _wrap_ranked_map_display(ja_name: str | None, en_name: str | None, lang: str) -> str:
    if lang == "ja" and ja_name:
        if len(ja_name) >= 10:
            split_point = len(ja_name) // 2
            return ja_name[:split_point] + "\n" + ja_name[split_point:]
        return ja_name
    return en_name or ja_name or ""


class Battles:
    def __init__(self, dbrows: list[asyncpg.Record], player_tag_for_battles: str, db: asyncpg.Connection):
        self.battles: list[Battle] = [Battle(dbrow=dbrow) for dbrow in dbrows]
        self.player_tag: str = player_tag_for_battles # 統計計算時にどのプレイヤー視点かを知るため
        self.db: asyncpg.Connection = db # Brawler名取得などに使う可能性

        # 基本統計 (初期化時に計算)
        self.wins: int = 0
        self.losses: int = 0
        self.draws: int = 0
        self._calculate_basic_stats() # wins, losses, draws, win_rateなどを計算
        self.max_win_streak: int = self._calculate_max_streak("w")
        self.max_loss_streak: int = self._calculate_max_streak("l")

        # 遅延計算される統計データのキャッシュ用 (初期値はNone)
        self._win_rate: float | None = None
        self._total_trophy_change: int | None = None
        self._average_trophy_change: float | None = None
        self._brawler_usage_rate: dict[int | str, float] | None = None
        self._teammate_usage_rate: dict[str, float] | None = None
        self._opponent_usage_rate: dict[str, float] | None = None
        self._mode_usage_rate: dict[str, float] | None = None
        self._overall_trophy_trend_data: dict[str, Any] | None = None
        self._ranked_rank_trend_data: dict[str, Any] | None = None
        self._character_trophy_trend_data: dict[int, dict[str, Any] | None] = {} # brawler_id をキーとする
        self._current_ranked_season_modes_and_maps: dict[str, Any] | None = None
        self._ranked_mode_win_rates: dict[str, float] | None = None
        self._ranked_map_win_rates: dict[str, dict[str, float] | None] = {} # mode_name をキーとする
        self._ranked_brawler_pick_rates: dict[str, dict[str | None, dict[int | str, float] | None]] = {} # mode_name, map_name をキー

    @property # 常に最新のバトル数を返すプロパティとして定義
    def total_battles(self) -> int:
        return len(self.battles)

    def _calculate_basic_stats(self):
        self.wins: int = 0
        self.losses: int = 0
        self.draws: int = 0

        if self.total_battles == 0:
            self._win_rate = 0.0
            return

        for battle in self.battles:
            if battle.result == "w":
                self.wins += 1
            elif battle.result == "l":
                self.losses += 1
            elif battle.result == "d":
                self.draws += 1
        
        # 実際の勝敗がついたバトル数 (引き分けを除く)
        decided_battles = self.wins + self.losses
        self._win_rate = (self.wins / decided_battles * 100) if decided_battles > 0 else 0.0

    def _calculate_max_streak(self, result_type: str) -> int:
        current_streak = 0
        max_streak = 0
        for battle in self.battles: # 新しい順に並んでいる想定
            if battle.result == result_type:
                current_streak += 1
            else:
                max_streak = max(max_streak, current_streak)
                current_streak = 0
        max_streak = max(max_streak, current_streak) # 最後の連続も考慮
        return max_streak

    #> 各統計メソッド (遅延計算とキャッシュ)
    # [この部分は公開用リポジトリでは非公開にされています]

class APIBattle:
    """バトルのAPIデータを整理する用。
    """
    def __init__(self, tag: str, apibattle: dict) -> None:
        self.datetime: datetime.datetime = parse_api_utc_datetime(apibattle["battleTime"])
        self.event_id: int | None = apibattle['event'].get("id") # おそらく必ず存在する。コンペの時は0
        self.event_mode: str | None = apibattle['event'].get("mode") # Ver11.0より保存を開始。
        self.event_map: str | None = apibattle['event'].get("map") # 英語のマップ名。コンペの時はNone
        self.battle_mode: str | None = apibattle['battle'].get("mode") # バトル自体のモードであり、おそらく必ず存在する。55では33と同じモード名となるので注意。
        raw_mode_id = apibattle['event'].get("modeId")
        try:
            raw_mode_id_int = int(raw_mode_id) if raw_mode_id is not None else None
        except (TypeError, ValueError):
            raw_mode_id_int = None
        self.mode_id: int | None = normalize_official_mode_id(raw_mode_id_int)
        self.battle_type: str | None = apibattle["battle"].get("type") # 通常='ranked', チャレンジ='challenge','championshipChallenge'ほか, フレバト='friendly', ガチバトル='soloRanked', ビッグゲーム系=None
        self.result: str = apibattle["battle"].get("result") # 勝利"w", 引き分け"d", 敗北"l" バトロイなどではここではNoneとなってしまうため、後から順位を元に追加する
        self.rank: int | None = apibattle['battle'].get("rank") # 順位のあるモードにおける順位。
        starplayer_tag: str | None = apibattle["battle"].get("starPlayer").get("tag") if apibattle["battle"].get("starPlayer") else None # MVPのプレイヤータグ。
        if starplayer_tag:
            self.is_starplayer: bool | None = True if starplayer_tag == tag else False
        else:
            self.is_starplayer: bool | None = None
        self.brawler: int | None = None
        self.power: int | None = None
        self.trophies: int | None = None
        self.trophy_change: int | None = apibattle["battle"].get("trophyChange", 0) # トロフィー変動は、ない場合は0を入れておく。(デュエルも一時的に0が入る。)
        self.brawlers: list[tuple[int | None]] | None = []
        self.team_size: int = None
        self.num_of_teams: int = None
        self.teammate_tags: list[str] = []
        self.opponent_tags: list[str] = []
        self.teammate_names: list[str] = []
        self.opponent_names: list[str] = []
        self.teammate_brawlers: list[int] = []
        self.opponent_brawlers: list[int] = []
        self.ranked_score_after: int | None = None
        
        players: list[dict] | None = apibattle["battle"].get("players")
        teams: list[list[dict]] | None = apibattle["battle"].get("teams") # 上記2つどちらかが存在する。
        # [この部分は公開用リポジトリでは非公開にされています]


#* /---*---*---*---*---*---*---*---*/
#* バトル検索
#* /---*---*---*---*---*---*---*---*/
async def search_battles(
    db: asyncpg.Connection,
    *,
    tag: str | None = None,
    tags: list[str] | None = None,
    reverse: bool = False,
    limit: int = 100000,
    until: datetime.datetime | None = None,
    after: datetime.datetime | None = None,
    event_ids: list[int | None] | None = None,
    exclude_event_ids: list[int | None] | None = None,
    event_modes: list[str | None] | None = None,
    exclude_event_modes: list[str | None] | None = None,
    event_maps: list[str | None] | None = None,
    exclude_event_maps: list[str | None] | None = None,
    battle_modes: list[str | None] | None = None,
    exclude_battle_modes: list[str | None] | None = None,
    battle_types: list[str | None] | None = None,
    exclude_battle_types: list[str | None] | None = None,
    include_win: bool = True,
    include_draw: bool = True,
    include_loss: bool = True,
    ranks: list[int | None] | None = None,
    exclude_ranks: list[int | None] | None = None,
    is_starplayer: bool | None = None,
    brawler_ids: list[int] | None = None,
    exclude_brawler_ids: list[int] | None = None,
    min_power: int | None = None,
    max_power: int | None = None,
    min_trophies: int | None = None,
    max_trophies: int | None = None,
    min_trophy_change: int | None = None,
    max_trophy_change: int | None = None,
    team_sizes: list[int] | None = None,
    num_of_teams_list: list[int] | None = None,
    player_tags: list[str] | None = None,
    player_names: list[str] | None = None,
    player_filter_mode: bool | None = None,
    related_brawler_ids: list[int] | None  = None,
    related_brawler_filter_mode: bool | None = None
) -> Battles | None:
    """
    指定された条件でバトル履歴を検索する関数。

    Args:
        db (asyncpg.Connection): データベース接続。必須。
        tag (Optional[Union[int, str]]): 検索対象とする単一プレイヤータグ。tags指定時は無視される。
        tags (Optional[list[Union[int, str]]]): 検索対象とする複数プレイヤータグのリスト。
        reverse (bool): Trueの場合、古い順にソートする (デフォルト: False、新しい順)。
        limit (int): 最大検索件数 (デフォルト: 100000)。
        until (Optional[datetime.datetime]): この日時以前のバトルのみを対象とする。
        after (Optional[datetime.datetime]): この日時以降のバトルのみを対象とする。
        event_ids (Optional[list[Union[int, None]]]): 検索対象とするイベントIDのリスト。Noneを含むことができる。
        exclude_event_ids (Optional[list[Union[int, None]]]): 除外するイベントIDのリスト。event_ids指定時は無視される。
        event_modes (Optional[list[Union[str, None]]]): 検索対象とするイベントモードのリスト。
        exclude_event_modes (Optional[list[Union[str, None]]]): 除外するイベントモードのリスト。event_modes指定時は無視される。
        event_maps (Optional[list[Union[str, None]]]): 検索対象とするイベントマップのリスト。
        exclude_event_maps (Optional[list[Union[str, None]]]): 除外するイベントマップのリスト。event_maps指定時は無視される。
        battle_modes (Optional[list[Union[str, None]]]): 検索対象とするバトルモードのリスト。
        exclude_battle_modes (Optional[list[Union[str, None]]]): 除外するバトルモードのリスト。battle_modes指定時は無視される。
        battle_types (Optional[list[Union[str, None]]]): 検索対象とするバトルタイプのリスト。
        exclude_battle_types (Optional[list[Union[str, None]]]): 除外するバトルタイプのリスト。battle_types指定時は無視される。
        include_win (bool): 結果が'w'のバトルを含めるか (デフォルト: True)。
        include_draw (bool): 結果が'd'のバトルを含めるか (デフォルト: True)。
        include_loss (bool): 結果が'l'のバトルを含めるか (デフォルト: True)。
        ranks (Optional[list[Union[int, None]]]): 検索対象とするランクのリスト。
        exclude_ranks (Optional[list[Union[int, None]]]): 除外するランクのリスト。ranks指定時は無視される。
        is_starplayer (bool | None): Trueならスタープレイヤー(1)のみ、Falseなら非スタープレイヤー(0またはNone)のみ、Noneなら絞り込まない。
        brawler_ids (Optional[list[int]]): 検索対象とする自身の使用キャラクターIDのリスト。brawlerカラムまたはbrawlersカラムのいずれか。
        exclude_brawler_ids (Optional[list[int]]): 除外する自身の使用キャラクターIDのリスト。brawler_ids指定時は無視される。
        min_power (int | None): 最小パワーレベル (powerカラム)。指定するとpower=Noneは除外。
        max_power (int | None): 最大パワーレベル (powerカラム)。指定するとpower=Noneは除外。
        min_trophies (int | None): 最小トロフィー数 (trophiesカラム)。指定するとtrophies=Noneは除外。
        max_trophies (int | None): 最大トロフィー数 (trophiesカラム)。指定するとtrophies=Noneは除外。
        min_trophy_change (int | None): 最小トロフィー変動量 (trophy_changeカラム)。指定するとtrophy_change=Noneは除外。
        max_trophy_change (int | None): 最大トロフィー変動量 (trophy_changeカラム)。指定するとtrophy_change=Noneは除外。
        team_sizes (Optional[list[int]]): 検索対象とするチームサイズのリスト。
        num_of_teams_list (Optional[list[int]]): 検索対象とするチーム数のリスト。
        player_tags (Optional[list[str]]): 検索対象とするチームメイトまたは対戦相手のプレイヤータグのリスト。JSON文字列内の値と比較。これを指定した場合、player_namesは指定しても無視される。
        player_names (Optional[list[str]]): 検索対象とするチームメイトまたは対戦相手の名前リスト。JSON文字列内の値と比較。
        player_filter_mode (bool | None): player_tags, namesの絞り込み方法。True: チームメイトのみ, False: 対戦相手のみ, None: 両方。
        related_brawler_ids (Optional[list[int]]): 検索対象とするチームメイトまたは対戦相手のキャラクターIDリスト。JSON配列内の数値と比較。
        related_brawler_filter_mode (bool | None): related_brawler_idsの絞り込み方法。True: チームメイトのみ, False: 対戦相手のみ, None: 両方。


    Returns:
        Union[Battles, None]: 検索結果のバトル履歴のリスト。該当なしの場合はNone。

    Raises:
        DataBaseError: データベースエラー
    """
    # [この部分は公開用リポジトリでは非公開にされています]


#* /---*---*---*---*---*---*---*---*/
#* プレイヤー関連
#* /---*---*---*---*---*---*---*---*/
class Player:
    """プレイヤーのデータ格納用。
    """
    def __init__(self, tag: str, db: asyncpg.Connection) -> None:
        self.tag: str = tag
        self.name: str = None
        self.previous_names: dict[str, str] = {}
        self.club_tag: str | None = None
        self.club_name: str | None = None
        self.previous_clubs: dict[str, str | None] = {}
        self.icon_id: int = None
        self.current_trophies: int = None
        self.highest_trophies: int = None
        self.season_trophies: int = None
        self.average_highest_trophies: float = None
        self.average_power: float = None
        self.max_power_brawlers: int = None
        self.gadgets: int = None
        self.star_powers: int = None
        self.gears: int = 0
        self.hyper_charges: int = 0
        self.buffies: int = 0
        self.team_victories: int = 0
        self.solo_victories: int = None
        self.duo_victories: int = None
        self.unlocked_temporary_brawlers: int = 0
        self.unlocked_brawlers: int = None
        self.max_tier_brawlers: int = None
        self.ranked_current_rank: int | None = 0
        self.ranked_current_score: int | None = 0
        self.ranked_highest_rank: int | None = 0
        self.ranked_highest_score: int | None = 0
        self.acc_ranked_season_highest_rank: int | None = 0 # 公式APIから入れる
        self.ranked_season_highest_score: int | None = 0 # 公式APIから入れる
        self.solo_pl_rank: int | None = 0
        self.team_pl_rank: int | None = 0
        self.estimated_play_time: float = None
        self.is_invalid: bool = False
        self.level: int = 20  # 0=未閲覧, 10=非アクティブ, 20=アクティブ, 30=自動追跡有効
        self.last_viewed_at: datetime.datetime | None = None
        self.last_updated_at: datetime.datetime | None = None
        self.auto_track_expiration: datetime.datetime | None = None
        self.auto_activate_time: datetime.timedelta = None
        self.remaining_time: datetime.timedelta | None = None
        # --- MeowAPI関連の追加項目。MeowAPIが利用できない場合はDBから入れるが、それもない場合はNoneとなる。 
        self.meowapi_success: bool = False
        self.winstreak_brawler: int | None = None
        self.max_winstreak: int | None = None
        self.acc_ranked_current_rank: int | None = None
        self.acc_ranked_highest_rank: int | None = None
        self.highest_club_league: int | None = None
        self.account_creation_year: int | None = None
        self.fame_points: int | None = None
        self.legacy_rank_35s: int | None = None
        self.season_high_trophies: int | None = None
        self.prestige: int | None = None
        self.total_mastery: int | None = None
        self.average_mastery: float | None = None
        self.average_mastery_level: int | None = None
        self.titles: int | None = None
        self.brawlpass: int | None = None
        self.region: str | None = None
        self.region_ja: str | None = None
        self.region_en: str | None = None
        self.record_points: int | None = None
        self.record_level: int | None = None
        self.favorite_skin: int | None = None
        self.owned_skin_count: int | None = 0 # Noneは所持スキン不明(BSInfoから未取得)
        self.owned_limited_skin_count: int | None = 0
        self.owned_skins_synced_at: datetime.datetime | None = None
        self.owned_skin_ids_available: bool | None = None # キャラ別の所持スキン一覧が正確か (Noneは未判定)
        self.owned_skin_counts_by_rarity: dict[int, int] = {}
        self.first_profile_avatar: int | None = None
        self.second_profile_avatar: int | None = None
        self.battle_card_emote: int | None = None
        self.battle_card_title: int | None = None
        self.battle_card_frame: int | None = None
        self.battle_card_stars: dict[str, int] = {}
        self.milestone: int | None = None
        # --- BrawlPlex新データ
        self.exp_level: int | None = None
        self.exp_points: int | None = None
        self.total_prestige_level: int | None = None
        self.last_battle_at: datetime.datetime | None = None
        self.prestige_1_brawlers: int | None = None  # prestigeLevel>=1 のキャラ数
        self.prestige_2_brawlers: int | None = None  # prestigeLevel>=2 のキャラ数
        self.prestige_3_brawlers: int | None = None  # prestigeLevel>=3 のキャラ数
        # ----
        self.hide_name_history: bool = False
        self.hide_club_history: bool = False
        self.battle_log_limit: int = CURRENT_DEFAULT_BATTLE_LOG_LIMIT
        self.battle_log_retention_months: int | None = None  # NULL=システムデフォルト(4ヶ月)
        self.brawlers: list[PlayerBrawler] = []
        self.db: asyncpg.Connection = db

    def to_dict(self) -> dict:
        return {
            "tag": self.tag,
            "name": self.name,
            "previous_names": self.previous_names,
            "club_tag": self.club_tag,
            "club_name": self.club_name,
            "previous_clubs": self.previous_clubs,
            "icon_id": self.icon_id,
            "current_trophies": self.current_trophies,
            "highest_trophies": self.highest_trophies,
            "season_trophies": self.season_trophies,
            "average_highest_trophies": self.average_highest_trophies,
            "average_power": self.average_power,
            "max_power_brawlers": self.max_power_brawlers,
            "gadgets": self.gadgets,
            "star_powers": self.star_powers,
            "gears": self.gears,
            "hyper_charges": self.hyper_charges,
            "buffies": self.buffies,
            "team_victories": self.team_victories,
            "solo_victories": self.solo_victories,
            "duo_victories": self.duo_victories,
            "unlocked_temporary_brawlers": self.unlocked_temporary_brawlers,
            "unlocked_brawlers": self.unlocked_brawlers,
            "max_tier_brawlers": self.max_tier_brawlers,
            "ranked_current_rank": self.ranked_current_rank,
            "ranked_current_score": self.ranked_current_score,
            "ranked_highest_rank": self.ranked_highest_rank,
            "ranked_highest_score": self.ranked_highest_score,
            "acc_ranked_season_highest_rank": self.acc_ranked_season_highest_rank,
            "ranked_season_highest_score": self.ranked_season_highest_score,
            "solo_pl_rank": self.solo_pl_rank,
            "team_pl_rank": self.team_pl_rank,
            "estimated_play_time": self.estimated_play_time,
            "is_invalid": self.is_invalid,
            "level": self.level,
            "last_viewed_at": format_utc_datetime(self.last_viewed_at) if self.last_viewed_at else None,
            "last_updated_at": format_utc_datetime(self.last_updated_at) if self.last_updated_at else None,
            "auto_track_expiration": format_utc_datetime(self.auto_track_expiration) if self.auto_track_expiration else None,
            "auto_activate_time": self.auto_activate_time.total_seconds() if self.auto_activate_time is not None else 0,
            "remaining_time": self.remaining_time.total_seconds() if self.remaining_time else None,
            "meowapi_success": self.meowapi_success,
            "winstreak_brawler": self.winstreak_brawler,
            "max_winstreak": self.max_winstreak,
            "acc_ranked_current_rank": self.acc_ranked_current_rank,
            "acc_ranked_highest_rank": self.acc_ranked_highest_rank,
            "highest_club_league": self.highest_club_league,
            "account_creation_year": self.account_creation_year,
            "fame_points": self.fame_points,
            "legacy_rank_35s": self.legacy_rank_35s,
            "season_high_trophies": self.season_high_trophies,
            "prestige": self.prestige,
            "total_mastery": self.total_mastery,
            "average_mastery": self.average_mastery,
            "average_mastery_level": self.average_mastery_level,
            "titles": self.titles,
            "brawlpass": self.brawlpass,
            "region": self.region,
            "region_ja": self.region_ja,
            "region_en": self.region_en,
            "record_points": self.record_points,
            "record_level": self.record_level,
            "favorite_skin": self.favorite_skin,
            "owned_skin_count": self.owned_skin_count,
            "owned_limited_skin_count": self.owned_limited_skin_count,
            "owned_skin_counts_by_rarity": self.owned_skin_counts_by_rarity,
            "owned_skins_synced_at": format_utc_datetime(self.owned_skins_synced_at) if self.owned_skins_synced_at else None,
            "owned_skin_ids_available": self.owned_skin_ids_available,
            "first_profile_avatar": self.first_profile_avatar,
            "second_profile_avatar": self.second_profile_avatar,
            "battle_card_emote": self.battle_card_emote,
            "battle_card_title": self.battle_card_title,
            "battle_card_frame": self.battle_card_frame,
            "battle_card_stars": self.battle_card_stars,
            "milestone": self.milestone,
            "exp_level": self.exp_level,
            "exp_points": self.exp_points,
            "total_prestige_level": self.total_prestige_level,
            "prestige_1_brawlers": self.prestige_1_brawlers,
            "prestige_2_brawlers": self.prestige_2_brawlers,
            "prestige_3_brawlers": self.prestige_3_brawlers,
            "last_battle_at": format_utc_datetime(self.last_battle_at) if self.last_battle_at else None,
            "hide_name_history": self.hide_name_history,
            "hide_club_history": self.hide_club_history,
            "battle_log_limit": self.battle_log_limit,
            "battle_log_retention_months": self.battle_log_retention_months,
            "brawlers": [b.to_dict() for b in self.brawlers]
        }
        
    @classmethod
    async def from_dict(cls, data: dict, db: asyncpg.Connection):
        # tagがdataに存在するか確認
        tag = data.get("tag")
        if tag is None:
            raise ValueError("Player.from_dict: 'tag' not found in data")
        instance = cls(tag=tag, db=db)
        instance.name = data.get("name")
        instance.previous_names = data.get("previous_names")
        instance.club_tag = data.get("club_tag")
        instance.club_name = data.get("club_name")
        instance.previous_clubs = data.get("previous_clubs")
        instance.icon_id = data.get("icon_id")
        instance.current_trophies = data.get("current_trophies")
        instance.highest_trophies = data.get("highest_trophies")
        instance.season_trophies = data.get("season_trophies")
        instance.average_highest_trophies = data.get("average_highest_trophies")
        instance.average_power = data.get("average_power")
        instance.max_power_brawlers = data.get("max_power_brawlers")
        instance.gadgets = data.get("gadgets")
        instance.star_powers = data.get("star_powers")
        instance.gears = data.get("gears")
        instance.hyper_charges = data.get("hyper_charges", 0)
        instance.buffies = data.get("buffies", 0)
        instance.team_victories = data.get("team_victories")
        instance.solo_victories = data.get("solo_victories")
        instance.duo_victories = data.get("duo_victories")
        instance.unlocked_temporary_brawlers = data.get("unlocked_temporary_brawlers")
        instance.unlocked_brawlers = data.get("unlocked_brawlers")
        instance.max_tier_brawlers = data.get("max_tier_brawlers")
        instance.ranked_current_rank = data.get("ranked_current_rank")
        instance.ranked_current_score = data.get("ranked_current_score")
        instance.ranked_highest_rank = data.get("ranked_highest_rank")
        instance.ranked_highest_score = data.get("ranked_highest_score")
        instance.acc_ranked_season_highest_rank = data.get("acc_ranked_season_highest_rank")
        instance.ranked_season_highest_score = data.get("ranked_season_highest_score")
        instance.solo_pl_rank = data.get("solo_pl_rank")
        instance.team_pl_rank = data.get("team_pl_rank")
        instance.estimated_play_time = data.get("estimated_play_time")
        instance.is_invalid = data.get("is_invalid")
        instance.level = data.get("level", 20)
        instance.last_viewed_at = parse_utc_datetime(data.get("last_viewed_at")) if data.get("last_viewed_at") else None
        instance.last_updated_at = parse_utc_datetime(data.get("last_updated_at")) if data.get("last_updated_at") else None
        instance.auto_track_expiration = parse_utc_datetime(data.get("auto_track_expiration")) if data.get("auto_track_expiration") else None
        instance.auto_activate_time = datetime.timedelta(seconds=data.get("auto_activate_time"))
        instance.remaining_time = datetime.timedelta(seconds=data.get("remaining_time")) if data.get("remaining_time") else None
        instance.meowapi_success = data.get("meowapi_success", False)
        instance.winstreak_brawler = data.get("winstreak_brawler")
        instance.max_winstreak = data.get("max_winstreak")
        instance.acc_ranked_current_rank = data.get("acc_ranked_current_rank")
        instance.acc_ranked_highest_rank = data.get("acc_ranked_highest_rank")
        instance.highest_club_league = data.get("highest_club_league")
        instance.account_creation_year = data.get("account_creation_year")
        instance.fame_points = data.get("fame_points")
        instance.legacy_rank_35s = data.get("legacy_rank_35s")
        instance.season_high_trophies = data.get("season_high_trophies")
        instance.prestige = data.get("prestige")
        instance.total_mastery = data.get("total_mastery")
        instance.average_mastery = data.get("average_mastery")
        instance.average_mastery_level = data.get("average_mastery_level")
        instance.titles = data.get("titles")
        instance.brawlpass = data.get("brawlpass")
        instance.region = data.get("region")
        instance.region_ja = data.get("region_ja")
        instance.region_en = data.get("region_en")
        instance.record_points = data.get("record_points")
        instance.record_level = data.get("record_level")
        instance.favorite_skin = data.get("favorite_skin")
        instance.owned_skin_count = data.get("owned_skin_count", 0)
        instance.owned_skins_synced_at = parse_utc_datetime(data.get("owned_skins_synced_at")) if data.get("owned_skins_synced_at") else None
        instance.owned_skin_ids_available = data.get("owned_skin_ids_available")
        instance.owned_limited_skin_count = data.get("owned_limited_skin_count", 0)
        raw_owned_skin_counts_by_rarity = data.get("owned_skin_counts_by_rarity", {})
        if isinstance(raw_owned_skin_counts_by_rarity, dict):
            instance.owned_skin_counts_by_rarity = {
                int(k): int(v)
                for k, v in raw_owned_skin_counts_by_rarity.items()
            }
        else:
            instance.owned_skin_counts_by_rarity = {}
        instance.first_profile_avatar = data.get("first_profile_avatar")
        instance.second_profile_avatar = data.get("second_profile_avatar")
        instance.battle_card_emote = data.get("battle_card_emote")
        instance.battle_card_title = data.get("battle_card_title")
        instance.battle_card_frame = data.get("battle_card_frame")
        instance.battle_card_stars = data.get("battle_card_stars", {})
        instance.milestone = data.get("milestone")
        instance.exp_level = data.get("exp_level")
        instance.exp_points = data.get("exp_points")
        instance.total_prestige_level = data.get("total_prestige_level")
        instance.prestige_1_brawlers = data.get("prestige_1_brawlers")
        instance.prestige_2_brawlers = data.get("prestige_2_brawlers")
        instance.prestige_3_brawlers = data.get("prestige_3_brawlers")
        instance.last_battle_at = parse_utc_datetime(data.get("last_battle_at")) if data.get("last_battle_at") else None
        instance.hide_name_history = data.get("hide_name_history")
        instance.hide_club_history = data.get("hide_club_history")
        instance.battle_log_limit = data.get("battle_log_limit")
        instance.battle_log_retention_months = data.get("battle_log_retention_months")
        instance.brawlers = [PlayerBrawler.from_dict(d) for d in data.get("brawlers")]
        return instance

    @property
    def has_owned_skin_ids(self) -> bool:
        """キャラ別の所持スキン一覧が正確に取得できているか"""
        if self.owned_skin_ids_available is not None:
            return self.owned_skin_ids_available
        # 未判定(DBから復元した場合など)は、一覧をDBに保存しているレベル20以上で取得済みかで判定する
        return self.owned_skins_synced_at is not None and (self.level or 0) >= 20

    # [この部分は公開用リポジトリでは非公開にされています]

async def check_verify(tag: str, scope: str) -> tuple[bool, str | None]:
    """認証を実際に確認する。

    Args:
        tag (str): プレイヤータグ
        scope (str): 認証を行うセッションのID (player_verification_service.get_verification_scope)

    Returns:
        tuple[bool, str | None]: (認証結果, 失敗理由キーまたはNone)
                                 失敗理由キー: "icon_mismatch", "no_cached_id", "api_error", "unknown_error"
    """
    from app.services.player_verification_service import instruction_cache_key
    cache_key = instruction_cache_key(scope, tag)
    cached_id: int | None = await get_cache(cache_key)
    if not cached_id:
        return False, "no_cached_id" # 認証指示がない（タイムアウトなど）

    try:
        icon_id = await get_player_icon(tag)
    except BrawlStarsAPIError:
        return False, "api_error" # APIエラー
    except Exception:
        return False, "unknown_error" # その他の予期せぬエラー

    logger.debug(f"アイコン認証: {icon_id}, {cached_id}")
    if icon_id == cached_id:
        return True, None # 認証成功
    else:
        return False, "icon_mismatch" # アイコン不一致

async def insert_not_viewed_player(tag: str, name: str, db: asyncpg.Connection, *, club_tag: str | None = MISSING, icon_id: int = MISSING, current_trophies: int = MISSING,
                                 highest_trophies: int = MISSING) -> bool:
    """閲覧されていないプレイヤーをデータベースに追加します。

    Args:
        tag (str): プレイヤータグ
        name (str): 名前。必須。
        db (asyncpg.Connection): データベース接続
        club_tag (str | None, optional): クラブタグ. Defaults to MISSING.
        icon_id (int, optional): アイコンID. Defaults to MISSING.
        current_trophies (int, optional): 現在トロフィー. Defaults to MISSING.
        highest_trophies (int, optional): 総合トロフィー. Defaults to MISSING.

    Raises:
        DataBaseError: データベースエラー

    Returns:
        bool: 新しく追加したかどうか。新しく追加した場合はTrue, 既存のプレイヤーであり追加をスキップした場合はFalse。
    """
    # [この部分は公開用リポジトリでは非公開にされています]

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

async def get_player_summaries_from_db(tags: list[str], db: asyncpg.Connection) -> dict[str, dict]:
    """データベースから、複数プレイヤーの名前とアイコンIDを1回のクエリでまとめて取得する。APIへのアクセスは行わない。

    Args:
        tags (list[str]): プレイヤータグのリスト
        db (asyncpg.Connection): データベース接続

    Returns:
        dict[str, dict]: タグ → {"name": 名前, "icon_id": アイコンID}。DBに存在しないタグは含まれない。
    """
    if not tags:
        return {}
    try:
        rows = await db.fetch("SELECT tag, name, icon_id FROM players WHERE tag = ANY($1::text[])", list(tags))
    except asyncpg.PostgresError as e:
        logger.warning(f"プレイヤー名・アイコンの一括取得中にエラーが発生しました: {e}")
        return {}
    return {row["tag"]: {"name": row["name"], "icon_id": row["icon_id"]} for row in rows}

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

def _hours_to_timedelta(hours: float) -> datetime.timedelta:
    """時間（小数可）を秒丸めの timedelta に変換する。

    0 以外は少なくとも 1 秒の符号付き delta にする。
    正の微小値を 0 秒に丸めると、activate 側の再計測で期限が無効化されるため。
    """
    seconds = round(float(hours) * 3600)
    if hours > 0:
        seconds = max(1, seconds)
    elif hours < 0:
        seconds = min(-1, seconds)
    return datetime.timedelta(seconds=seconds)


async def add_auto_tracking_time(player: Player, hours: float) -> None:
    """
    プレイヤーの自動追跡機能の有効期限を指定時間分、延長する。
    有効期限が設定されていない、または期限切れの場合は、現在時刻から延長する。
    負の時間を指定した場合は期限を短縮し、過去になった場合は無効化する。
    期限切れ／未設定の状態で 0 以下を指定した場合は何もしない。

    Args:
        player (Player):対象のプレイヤーオブジェクト
        hours (float): 延長する時間（小数・負数可）
    """
    # [この部分は公開用リポジトリでは非公開にされています]


async def set_auto_tracking_remaining_hours(player: Player, hours: float) -> None:
    """
    プレイヤーの自動追跡の残り時間を、現在時刻からの指定時間に上書きする。
    hours が 0 以下の場合は自動追跡を無効化する。

    Args:
        player (Player): 対象のプレイヤーオブジェクト
        hours (float): 上書き後の残り時間（小数可。0以下なら無効化）
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
    # 1. データベースの値を更新する
    await db.execute(
        "UPDATE players SET battle_log_limit = $1 WHERE tag = $2",
        new_limit,
        tag,
    )
    
    # 2. 対応するキャッシュを削除する
    cache_key = f"player_battle_log_limit:{tag}"
    await delete_cache(cache_key)


async def get_battle_log_retention_months(db: asyncpg.Connection, tag: str) -> int | None:
    """指定したプレイヤータグのバトル履歴保存期間（月数）を取得する。
    NULLの場合はNoneを返す（システムデフォルト適用はフロントエンド側で判断）。

    Args:
        db (asyncpg.Connection): データベース接続
        tag (str): プレイヤータグ

    Returns:
        int | None: プレイヤー固有の保存期間（月数）。NULLの場合はNone。
    """
    cache_key = f"player_battle_log_retention:{tag}"
    cached_data = await get_cache(cache_key)
    if cached_data is not None:
        return cached_data

    row = await db.fetchrow(
        "SELECT battle_log_retention_months FROM players WHERE tag = $1",
        tag,
    )

    if row is None:
        return None

    result = row["battle_log_retention_months"]  # NULLならNone
    if result is not None:
        await set_cache(cache_key, result)

    return result


async def extend_battle_log_retention(db: asyncpg.Connection, tag: str, new_months: int | None):
    """指定したプレイヤーのバトル履歴保存期間を更新する。

    Args:
        db (asyncpg.Connection): データベース接続
        tag (str): プレイヤータグ
        new_months (int | None): 新しい保存期間（月数）。None=システムデフォルトに戻す。
    """
    await db.execute(
        "UPDATE players SET battle_log_retention_months = $1 WHERE tag = $2",
        new_months,
        tag,
    )

    cache_key = f"player_battle_log_retention:{tag}"
    await delete_cache(cache_key)



#* /---*---*---*---*---*---*---*---*/
#* プレイヤー検索
#* /---*---*---*---*---*---*---*---*/
VALID_NUMERIC_COLUMNS = {
    "current_trophies",
    "highest_trophies",
    "season_trophies",
    "average_highest_trophies",
    "average_power",
    "max_power_brawlers",
    "gadgets",
    "star_powers",
    "gears",
    "team_victories",
    "solo_victories",
    "duo_victories",
    "unlocked_brawlers",
    "max_tier_brawlers",
    "ranked_current_rank",
    "ranked_current_score",
    "ranked_highest_rank",
    "ranked_highest_score",
    "solo_pl_rank",
    "team_pl_rank",
}
VALID_SORT_DIRECTIONS = {"ASC", "DESC"}

# 一度の検索で返す結果の最大件数。これを超える場合はDBから全件取得せず、上位件数のみ返す。
MAX_SEARCH_RESULTS = 10_000
# プレイヤー検索の1クエリあたり上限。プール全体の command_timeout(60秒) は変えない。
SEARCH_QUERY_TIMEOUT = 15.0
# 改名前の完全一致。式GIN idx_gin_players_previous_name_values と一致させること。
_PREVIOUS_NAME_VALUES_SQL = "jsonb_path_query_array(previous_names, '$.*'::jsonpath)"
# pg_bigm の LIKE ESCAPE（E'\\' はバックスラッシュ1文字）。
_LIKE_ESCAPE_SQL = "E'\\\\'"
_MAX_LIKE_CASE_VARIANTS = 8


def _escape_like_literal(text: str) -> str:
    """ユーザー入力の % / _ / \\ を LIKE のリテラルにする。"""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _partial_like_pattern(query: str) -> str:
    """半角・全角スペースをワイルドカードにし、それ以外の %/_ はエスケープする。"""
    normalized = query.replace("　", " ")
    parts = [_escape_like_literal(part) for part in normalized.split(" ")]
    return "%" + "%".join(parts) + "%"


def _trgm_useful_char_count(query: str) -> int:
    """pg_trgm が使う英数字・Unicode文字数。記号・絵文字・空白はほぼ無視される。"""
    return sum(1 for ch in query if ch.isalnum())


def _use_bigm_like(query: str) -> bool:
    """ILIKE は gin_bigm_ops を使えない。trgm が効かない短い/記号クエリは LIKE に切り替える。"""
    return _trgm_useful_char_count(query) < 3


def _alnum_only(query: str) -> str:
    return "".join(ch for ch in query if ch.isalnum())


def _has_non_alnum_symbols(query: str) -> bool:
    """空白以外の記号・絵文字。pg_trgm が無視するため、LIKE '%A.K%' だと誤って trgm が選ばれる。"""
    return any(not ch.isalnum() and not ch.isspace() for ch in query)


def _ascii_case_variants(pattern: str, max_variants: int = _MAX_LIKE_CASE_VARIANTS) -> list[str]:
    """LIKE 用に ASCII 英字の大文字小文字違いを列挙する。CJK・絵文字はそのまま。"""
    letter_indexes = [i for i, ch in enumerate(pattern) if ch.isascii() and ch.isalpha()]
    if not letter_indexes:
        return [pattern]
    if 2 ** len(letter_indexes) > max_variants:
        unique_patterns = [pattern, pattern.lower(), pattern.upper()]
        return list(dict.fromkeys(unique_patterns))

    variants: list[str] = []
    seen: set[str] = set()
    for bits in range(2 ** len(letter_indexes)):
        chars = list(pattern)
        for bit_index, pos in enumerate(letter_indexes):
            chars[pos] = chars[pos].upper() if (bits >> bit_index) & 1 else chars[pos].lower()
        candidate = "".join(chars)
        if candidate not in seen:
            seen.add(candidate)
            variants.append(candidate)
    return variants


async def search_players(
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
    level_min: int | None = None,
    level_max: int | None = None,
    # Range filters (add _min or _max suffix)
    current_trophies_min: int | None = None,
    current_trophies_max: int | None = None,
    highest_trophies_min: int | None = None,
    highest_trophies_max: int | None = None,
    season_trophies_min: int | None = None,
    season_trophies_max: int | None = None,
    average_highest_trophies_min: float | None = None,
    average_highest_trophies_max: float | None = None,
    average_power_min: float | None = None,
    average_power_max: float | None = None,
    max_power_brawlers_min: int | None = None,
    max_power_brawlers_max: int | None = None,
    gadgets_min: int | None = None,
    gadgets_max: int | None = None,
    star_powers_min: int | None = None,
    star_powers_max: int | None = None,
    gears_min: int | None = None,
    gears_max: int | None = None,
    team_victories_min: int | None = None,
    team_victories_max: int | None = None,
    solo_victories_min: int | None = None,
    solo_victories_max: int | None = None,
    duo_victories_min: int | None = None,
    duo_victories_max: int | None = None,
    unlocked_brawlers_min: int | None = None,
    unlocked_brawlers_max: int | None = None,
    max_tier_brawlers_min: int | None = None,
    max_tier_brawlers_max: int | None = None,
    ranked_current_rank_min: int | None = None,
    ranked_current_rank_max: int | None = None,
    ranked_current_score_min: int | None = None,
    ranked_current_score_max: int | None = None,
    ranked_highest_rank_min: int | None = None,
    ranked_highest_rank_max: int | None = None,
    ranked_highest_score_min: int | None = None,
    ranked_highest_score_max: int | None = None,
    solo_pl_rank_min: int | None = None,
    solo_pl_rank_max: int | None = None,
    team_pl_rank_min: int | None = None,
    team_pl_rank_max: int | None = None,
    # Sorting options: list of tuples, e.g., [('highest_trophies', 'DESC'), ('name', 'ASC')]
    sort_by: list[tuple[str, str]] | None = None
) -> tuple[list[dict[str, str]], int]:
    """
    プレイヤーを検索します。

    Args:
        query (str): 検索語句
        db (asyncpg.Connection): データベース接続
        page (int): ページ番号。デフォルトは1。
        per_page (int): 1ページあたり件数。デフォルトは120。
        include_previous_names (bool | None): 改名前の名前の検索に含めるかどうか。デフォルトはFalse。旧名は値の完全一致（大文字小文字を区別する）。
        exact_match (bool | None: 完全一致検索をするかどうか。デフォルトはFalse(部分一致LIKE検索)。なお、どちらも大文字小文字は区別しない。
        include_invalid (bool | None): 無効なプレイヤーを検索結果に含めるかどうか。デフォルトはFalse。
        is_acquire_automatically (bool | None): 高頻度自動取得がオンになっているプレイヤーのみ検索するかどうか。Falseの場合はオフのみ検索、Noneの場合は絞り込みなし。デフォルトはNone。
        is_viewed (bool | None): 閲覧されたことのあるプレイヤーのみ検索するかどうか。Falseの場合は閲覧されたことのないプレイヤーのみ検索、Noneの場合は絞り込みなし。デフォルトはNone。
        *_min (Optional[int|float]): 指定された列の最小値。
        *_max (Optional[int|float]): 指定された列の最大値。
        sort_by (Optional[list[tuple[str, str]]]): ソート順を指定するタプルのリスト。各タプルは(列名, 方向('ASC'または'DESC'))とすること。リストの順序がソートの優先度となる。デフォルトはNone(ソートなし)。
    Returns:
        list[dict[str, str]]: 検索結果の'tag','name','icon_id','current_trophies','highest_trophies'を含む辞書のリスト、および検索結果総件数。検索結果がない場合は([], 0)。

    Raises:
        ValueError: sort_byで無効な列名またはソート方向が指定された場合。
        DataBaseError: データベースエラー
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

#* /---*---*---*---*---*---*---*---*/
#* プレイヤーログ関連
#* /---*---*---*---*---*---*---*---*/
class PlayerLogEntry(TypedDict):
    date: str # YYYY-MM-DD
    current_trophies: int | None
    highest_trophies: int | None
    season_trophies: int | None
    average_highest_trophies: float | None
    average_current_trophies: float | None
    average_power: float | None
    max_power_brawlers: int | None
    gadgets: int | None
    star_powers: int | None
    gears: int | None
    team_victories: int | None
    solo_victories: int | None
    duo_victories: int | None
    unlocked_brawlers: int | None
    max_tier_brawlers: int | None
    ranked_current_rank: int | None
    ranked_highest_rank: int | None
    acc_ranked_season_highest_rank: int | None
    season_high_trophies: int | None # <-- 追加
    ranked_current_score: int | None # <-- 追加
    ranked_highest_score: int | None # <-- 追加
    ranked_season_highest_score: int | None # <-- 追加
    max_winstreak: int | None # <-- 追加
    prestige: int | None # <-- 追加
    fame_points: int | None # <-- 追加
    titles: int | None # <-- 追加
    total_mastery: int | None # <-- 追加
    owned_skin_count: int | None
    average_mastery: float | None # <-- 追加 計算
    play_time: float | None # <-- 追加 計算
    average_gadgets: float | None # <-- 追加 計算
    average_star_powers: float | None # <-- 追加 計算
    average_gears: float | None # <-- 追加 計算
    average_hyper_charges: float | None # <-- 追加 計算
    average_buffies: float | None # <-- 追加 計算
    record_points: int | None # <-- 追加
    hyper_charges: int | None
    buffies: int | None
    exp_level: int | None
    exp_points: int | None
    total_prestige_level: int | None
    prestige_1_brawlers: int | None
    prestige_2_brawlers: int | None
    prestige_3_brawlers: int | None

class PlayerLogTrends(TypedDict):
    dates: list[str]
    current_trophies: list[int | None]
    highest_trophies: list[int | None]
    season_trophies: list[int | None]
    average_highest_trophies: list[float | None]
    average_current_trophies: list[float | None]
    average_power: list[float | None]
    max_power_brawlers: list[int | None]
    gadgets: list[int | None]
    star_powers: list[int | None]
    gears: list[int | None]
    team_victories: list[int | None]
    solo_victories: list[int | None]
    duo_victories: list[int | None]
    unlocked_brawlers: list[int | None]
    max_tier_brawlers: list[int | None]
    ranked_current_rank: list[int | None]
    ranked_highest_rank: list[int | None]
    acc_ranked_season_highest_rank: list[int | None]
    season_high_trophies: list[int | None] # <--- 追加
    ranked_current_score: list[int | None] # <--- 追加
    ranked_highest_score: list[int | None] # <--- 追加
    ranked_season_highest_score: list[int | None] # <--- 追加
    max_winstreak: list[int | None] # <--- 追加
    prestige: list[int | None] # <--- 追加
    fame_points: list[int | None] # <--- 追加
    titles: list[int | None] # <--- 追加
    total_mastery: list[int | None] # <--- 追加
    owned_skin_count: list[int | None]
    average_mastery: list[float | None] # <--- 追加
    play_time: list[float | None] # <--- 追加
    average_gadgets: list[float | None] # <--- 追加
    average_star_powers: list[float | None] # <--- 追加
    average_gears: list[float | None] # <--- 追加
    average_hyper_charges: list[float | None] # <--- 追加
    average_buffies: list[float | None] # <--- 追加
    record_points: list[int | None] # <--- 追加
    hyper_charges: list[int | None]
    buffies: list[int | None]
    exp_level: list[int | None]
    exp_points: list[int | None]
    total_prestige_level: list[int | None]
    prestige_1_brawlers: list[int | None]
    prestige_2_brawlers: list[int | None]
    prestige_3_brawlers: list[int | None]

class PlayerStatsPageData(TypedDict):
    log_trends: PlayerLogTrends | None
    oldest_log_date: str | None
    latest_log_date: str | None
    total_log_days: int

async def get_player_log_trends(db: asyncpg.Connection, player_tag: str, period_days: int | None = None) -> PlayerStatsPageData:
    """
    プレイヤーの統計推移データをplayer_logsテーブルから取得します。
    """
    # [この部分は公開用リポジトリでは非公開にされています]


#* /---*---*---*---*---*---*---*---*/
#* ガチバトル統計関連(書き込み部分)
#* /---*---*---*---*---*---*---*---*/
class RankedBattle:
    """ガチバトル集計用の軽量なバトルデータクラス"""
    def __init__(self, dbrow: asyncpg.Record):
        self.event_mode: str = dbrow["event_mode"]
        self.event_map: str = dbrow["event_map"]
        self.result: str = dbrow["result"]
        self.brawler: int = dbrow["brawler"]
        # 初期化の時点でランクをグルーピングする
        self.rank_tier: int = _group_rank_tier(dbrow["trophies"])
        self.is_starplayer: bool | None = dbrow["is_starplayer"]
        self.teammate_brawlers: list[int] = dbrow["teammate_brawlers"]
        self.opponent_brawlers: list[int] = dbrow["opponent_brawlers"]

class TrophyBattle:
    """トロフィーバトル集計用の軽量なバトルデータクラス"""
    def __init__(self, dbrow: asyncpg.Record):
        self.event_mode: str = dbrow["event_mode"]
        self.event_map: str = dbrow["event_map"]
        self.result: str = dbrow["result"]
        self.brawler: int = dbrow["brawler"]
        self.rank_tier: int = -2 # 固定値
        self.is_starplayer: bool | None = dbrow["is_starplayer"]
        self.teammate_brawlers: list[int] = dbrow["teammate_brawlers"]
        self.opponent_brawlers: list[int] = dbrow["opponent_brawlers"]

async def update_ranked_stats(db: asyncpg.Connection, target_date: datetime.date) -> None:
    """ガチバトル統計を集計して更新します。
    """
    # [この部分は公開用リポジトリでは非公開にされています]


def _count_maps_with_data(mode: dict) -> int:
    return sum(1 for map_info in mode["maps"] if map_info.get("has_data", True))


async def warmup_ranked_stats_caches(
    db: asyncpg.Connection,
    target_date: datetime.date,
    ctx: Any = None,
) -> None:
    """当日分のガチバトル統計キャッシュをウォームアップする。

    中断再開時は checkpoint と Redis の既存キーを飛ばす。新規実行では再計算する。
    """
    # [この部分は公開用リポジトリでは非公開にされています]

def _invert_result(result: str) -> str:
    """勝敗を反転させるヘルパー関数"""
    if result == 'w': return 'l'
    if result == 'l': return 'w'
    return 'd' # 引き分けはそのまま

def _group_rank_tier(rank: int) -> int:
    """ガチバトルランクをグルーピングします。1-12は0に、13以上はそのままの値を返します。"""
    return 0 if 1 <= rank <= 12 else rank

async def _calculate_and_store_brawler_stats(db: asyncpg.Connection, ranked_battles: list[RankedBattle], target_date: datetime.date) -> None:
    # [この部分は公開用リポジトリでは非公開にされています]

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


#* /---*---*---*---*---*---*---*---*/
#* ガチバトル統計関連(読み出し部分)
#* /---*---*---*---*---*---*---*---*/
#^ 総合評価スコア計算用の重み係数 (合計は1にすること)
WIN_RATE_WEIGHT = [この部分は公開用リポジトリでは非公開にされています]
USE_RATE_WEIGHT = [この部分は公開用リポジトリでは非公開にされています]
STAR_PLAYER_RATE_WEIGHT = [この部分は公開用リポジトリでは非公開にされています]
#^ マップ指定時に、同モードの別マップのデータをどの程度考慮するかの係数
SIMILAR_MAP_WEIGHT = [この部分は公開用リポジトリでは非公開にされています]
#^ 日付による重み係数 (基準: 今日で1.0)
def _get_date_weight(target_date: datetime.date) -> float:
    """
    日付を受け取り、現在からの経過日数に応じた重み係数を返す。
    新しいほど係数が高い（1.0に近い）。
    """
    # [この部分は公開用リポジトリでは非公開にされています]
#^ ランク帯による重み係数 (基準: 19で1.0)
def _get_rank_weight(rank_tier: int) -> float:
    """
    ランク帯を受け取り、重み係数を返す。
    高ランク帯ほど係数が高い。
    """
    # [この部分は公開用リポジトリでは非公開にされています]
#^ キャラクター評価スコア計算時の最低プレイ数要件
MINIMUM_BRAWLER_GAMES = [この部分は公開用リポジトリでは非公開にされています]

#^ シナジー評価スコア計算用の重み係数 (合計は1にすること)
SYNERGY_PLAYS_WEIGHT = [この部分は公開用リポジトリでは非公開にされています]
SYNERGY_WIN_RATE_WEIGHT = [この部分は公開用リポジトリでは非公開にされています]
#^ シナジー評価スコア計算時のプレイ数の重みが、いくつで0.5となるか
SYNERGY_GAMES_PLAYED_SCALING_FACTOR_K = [この部分は公開用リポジトリでは非公開にされています]

#^ 編成評価スコア計算用の重み係数 (合計は1にすること)
COMPOSITION_WINS_WEIGHT = [この部分は公開用リポジトリでは非公開にされています]
COMPOSITION_GAMES_PLAYED_WEIGHT = [この部分は公開用リポジトリでは非公開にされています]
#^ 編成評価スコア計算時の最低プレイ数要件
MINIMUM_COMPOSITION_GAMES = [この部分は公開用リポジトリでは非公開にされています]
#^ 編成評価スコア計算時のプレイ数の重みが、いくつで0.5となるか
COMPOSITION_GAMES_PLAYED_SCALING_FACTOR_K = [この部分は公開用リポジトリでは非公開にされています]

#^ --- スコアリング改善のための定数 ---
# 各指標の性能を0点～100点にスケーリングするための基準値。
# これらの値を調整することで、スコアの分布をチューニングできる。
# [この部分は公開用リポジトリでは非公開にされています]

#^ --- パフォーマンス改善のための定数 (get_ranked_stats用) ---
# 日次データ取得時に、この試合数未満のレコードは無視する
MIN_DAILY_SYNERGY_GAMES = [この部分は公開用リポジトリでは非公開にされています]
MIN_DAILY_COMPOSITION_GAMES = [この部分は公開用リポジトリでは非公開にされています]
# 最終的に返す編成データの最大件数
COMPOSITION_STATS_LIMIT = [この部分は公開用リポジトリでは非公開にされています]

#^ ピック提案スコア計算用の重み係数 (合計は1にすること)
# [この部分は公開用リポジトリでは非公開にされています]

#^ 勝率予測用の重み・基準値
# [この部分は公開用リポジトリでは非公開にされています]

# [この部分は公開用リポジトリでは非公開にされています]

def _logistic_scale(value: float, min_val: float, max_val: float) -> float:
    """
    シグモイド関数（S字カーブ）を用いて、指標の値を0.0～100.0のスコアに変換する。
    min_valで約10点、max_valで約90点、中間点で50点となるように調整されている。
    """
    if min_val >= max_val:
        return 50.0  # 不正な値の場合は中間点を返す

    midpoint = (min_val + max_val) / 2
    spread = max_val - min_val
    
    # カーブの急峻さを決定する係数。4.4に設定すると、min/maxで約10/90点になる。
    steepness = 4.4 / spread
    
    exponent = -steepness * (value - midpoint)
    
    # 指数関数の結果が大きすぎるとOverflowErrorになるため、上限を設ける
    if exponent > 700: # math.exp(709)あたりが上限
        return 0.0
        
    return 100.0 / (1.0 + math.exp(exponent))

def _scale_win_rate(win_rate: float) -> float:
    """勝率を0～100点の性能評価スコアに変換する"""
    return _logistic_scale(win_rate, WIN_RATE_SCALE_MIN, WIN_RATE_SCALE_MAX)

def _scale_use_rate(use_rate_percent: float) -> float:
    """使用率(%)を0～100点の性能評価スコアに変換する"""
    return _logistic_scale(use_rate_percent, USE_RATE_SCALE_MIN, USE_RATE_SCALE_MAX)

def _scale_star_player_rate(star_player_rate: float) -> float:
    """MVP率を0～100点の性能評価スコアに変換する"""
    return _logistic_scale(star_player_rate, STAR_PLAYER_RATE_SCALE_MIN, STAR_PLAYER_RATE_SCALE_MAX)

def _scale_multi_win_rate(win_rate: float) -> float:
    """勝率を0～100点の性能評価スコアに変換する"""
    return _logistic_scale(win_rate, MULTI_WIN_RATE_SCALE_MIN, MULTI_WIN_RATE_SCALE_MAX)

def _scale_multi_use_rate(use_rate_percent: float) -> float:
    """使用率(%)を0～100点の性能評価スコアに変換する"""
    return _logistic_scale(use_rate_percent, MULTI_USE_RATE_SCALE_MIN, MULTI_USE_RATE_SCALE_MAX)

def _scale_multi_star_player_rate(star_player_rate: float) -> float:
    """MVP率を0～100点の性能評価スコアに変換する"""
    return _logistic_scale(star_player_rate, MULTI_STAR_PLAYER_RATE_SCALE_MIN, MULTI_STAR_PLAYER_RATE_SCALE_MAX)

def _scale_composition_win_rate(win_rate: float) -> float:
    """編成勝率を0～100点の性能評価スコアに変換する"""
    return _logistic_scale(win_rate, COMPOSITION_WIN_RATE_SCALE_MIN, COMPOSITION_WIN_RATE_SCALE_MAX)

def _scale_synergy_win_rate(win_rate: float) -> float:
    """シナジー勝率を0～100点の性能評価スコアに変換する"""
    return _logistic_scale(win_rate, SYNERGY_WIN_RATE_SCALE_MIN, SYNERGY_WIN_RATE_SCALE_MAX)

def _scale_counter_win_rate(win_rate: float) -> float:
    """カウンター勝率を0～100点の性能評価スコアに変換する"""
    return _logistic_scale(win_rate, COUNTER_WIN_RATE_SCALE_MIN, COUNTER_WIN_RATE_SCALE_MAX)

def _scale_advantage_score(advantage_score: float) -> float:
    """有利度スコアを0～100%の勝率に変換する"""
    return _logistic_scale(advantage_score, PREDICTION_ADVANTAGE_SCALE_MIN, PREDICTION_ADVANTAGE_SCALE_MAX)

def _get_rank_grade(score: float) -> str:
    if score >= 95: return "SS"
    if score >= 90: return "S+"
    if score >= 85: return "S+"
    if score >= 80: return "S"
    if score >= 75: return "S"
    if score >= 70: return "S-"
    if score >= 65: return "S-"
    if score >= 60: return "A+"
    if score >= 55: return "A"
    if score >= 50: return "A-"
    if score >= 45: return "B+"
    if score >= 40: return "B"
    if score >= 35: return "B-"
    if score >= 30: return "C+"
    if score >= 25: return "C"
    if score >= 20: return "C-"
    if score >= 15: return "D+"
    if score >= 10: return "D"
    if score >= 5: return "D-"
    if score > 0: return "D-"
    else: return "E"

def _format_camel_case(text: str) -> str:
    """'gemGrab' のようなキャメルケースを 'Gem Grab' のように整形する"""
    return format_mode_slug_to_display(text)

class BrawlerStat:
    """
    集計されたキャラクターの統計データを格納し、総合的な評価を行うためのクラス。
    """
    # [この部分は公開用リポジトリでは非公開にされています]

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
    matchups: list[MatchupStat] # 相性の良い味方（シナジー）と、有利・不利な相手（カウンター）
    best_compositions: list[CompositionStat] # おすすめ編成
    performance_trend: dict[str, list] | None # 例: {'dates': ['2025-08-01', ...], 'scores': [85.2, ...]} -> Noneに変更

async def get_ranked_stats(
        db: asyncpg.Connection,
        start_date: datetime.date | None = None,
        end_date: datetime.date | None = None,
        mode: str | None = None,
        map_name: str | None = None,
        rank_tier: int | None = None,
        use_cache: bool = True,
        include_synergy_stats: bool = True,
        include_composition_stats: bool = True,
        db_timeout: int | float | None = None
    ) -> RankedStatsData:
    """ガチバトルに関する3種類の統計を取得する。2日間のキャッシュを使用する。

    Args:
        db (asyncpg.Connection): データベース接続
        start_date (datetime.date | None): 開始日。デフォルトはNoneで、Noneの場合は30日前。
        end_date (datetime.date | None): 終了日。デフォルトはNoneで、Noneの場合はUTC上の今日。
        mode (str | None): ゲームモード。"gemGrab"のような英語名で指定。デフォルトはNoneで、Noneの場合は全ゲームモードが対象。
        map_name (str | None): マップ。英語名で指定。デフォルトはNoneで、Noneの場合は全マップが対象。指定している場合も、同じゲームモードの他のマップは一定比重で若干考慮される。
        rank_tier (int | None): ランク帯。デフォルトはNoneで、Noneの場合は全ランク帯が対象。
        use_cache (bool): キャッシュヒット時、利用するかどうか。デフォルトはTrue。
        include_synergy_stats (bool): 相性統計を取得するかどうか。デフォルトはTrue。
        include_composition_stats (bool): 編成統計を取得するかどうか。デフォルトはTrue。

    Returns:
        RankedStatsData: 集計された3種類のガチバトル統計データ
    """
    # [この部分は公開用リポジトリでは非公開にされています]

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
        list[dict]: 辞書はmode, mode_en, mode_ja, mapsの値を持つ。mapsはmap_id, map_en, map_ja, has_dataの値を持つ。
            map_en は統計テーブルの map 列と同じ値。has_data は直近7日に統計があるかどうか。
    """
    # [この部分は公開用リポジトリでは非公開にされています]


def resolve_ranked_filter(pool_data: list[dict], mode: str | None, map_name: str | None) -> tuple[str | None, str | None]:
    """直打ちされたモード/マップを、今シーズンのマッププールにあるものだけに絞る。

    プールに無いモードは全体、プールに無いマップはモード単位として扱う。プールが空のときはそのまま返す。
    """
    if not pool_data:
        return mode, map_name
    if not mode:
        return None, None
    mode_data = next((item for item in pool_data if item["mode"] == mode), None)
    if mode_data is None:
        return None, None
    if map_name and not any(map_data["map_en"] == map_name for map_data in mode_data["maps"]):
        map_name = None
    return mode, map_name or None

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
        # 各スコアは0～100点に正規化された値を受け取る
        self.synergy_score: float = synergy_score
        self.counter_score: float = counter_score
        self.defensive_score: float = defensive_score

    @property
    def total_score(self) -> float:
        """キャラクター自身の強さ、シナジー、カウンター性能を総合したスコアを算出する"""
        score = (
            (self.brawler.strength_score * PICK_SCORE_BASE_WEIGHT) +
            (self.synergy_score * PICK_SCORE_SYNERGY_WEIGHT) +
            (self.counter_score * PICK_SCORE_COUNTER_WEIGHT) +
            (self.defensive_score * PICK_SCORE_DEFENSIVE_WEIGHT)
        )
        score_bonus = PICK_SUGGESTION_SCORE_BONUSES.get(self.brawler.brawler_id, 0.0)
        return min(score + score_bonus, 100.0)
    
    @property
    def rank_grade(self) -> str:
        """総合スコアからS+やAなどのランクを判定する"""
        return _get_rank_grade(self.total_score)

    @classmethod
    def from_dict(cls, data: dict):
        """辞書からインスタンスを復元する"""
        brawler = BrawlerStat.from_dict(data["brawler"])
        return cls(
            brawler=brawler,
            synergy_score=data.get("synergy_score", 50.0), # 中間点の50をデフォルトに
            counter_score=data.get("counter_score", 50.0),
            defensive_score=data.get("defensive_score", 50.0)
        )
    
    def to_dict(self) -> dict:
        """インスタンスを辞書に変換する（キャッシュやAPIレスポンス用）"""
        return {
            "brawler": self.brawler.to_dict(),
            "synergy_score": self.synergy_score,
            "counter_score": self.counter_score,
            "defensive_score": self.defensive_score,
            "total_score": self.total_score,
            "rank_grade": self.rank_grade
        }

async def get_pick_suggestions(
    db: asyncpg.Connection,
    mode: str | None = None,
    map_name: str | None = None,
    rank_tier: int | None = None,
    my_team_picks: list[int] | None = None,
    enemy_team_picks: list[int] | None = None,
    banned_brawlers: list[int] | None = None,
    use_cache: bool = True
) -> list[PickSuggestion]:
    """
    現在のBAN/PICK状況に基づき、次にピックすべきキャラクターをおすすめ度順に提案する。
    3分間のキャッシュを使用する。

    Args:
        db (asyncpg.Connection): データベース接続
        mode (str | None): ゲームモード。Noneの場合は全モード対象。
        map_name (str | None): マップ名。Noneの場合は全マップ対象。
        rank_tier (int | None): ランク帯。Noneの場合は全ランク帯対象。
        my_team_picks (list[int] | None): 味方が既にピックしたキャラクターIDのリスト。
        enemy_team_picks (list[int] | None): 敵が既にピックしたキャラクターIDのリスト。
        banned_brawlers (list[int] | None): BANされたキャラクターIDのリスト。
        use_cache (bool): キャッシュヒット時、利用するかどうか。デフォルトはTrue。

    Returns:
        list[PickSuggestion]: おすすめ度（total_score）の高い順にソートされたPickSuggestionのリスト。
    """
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


#* /---*---*---*---*---*---*---*---*/
#* アクセサリー所持率の計算
#* /---*---*---*---*---*---*---*---*/

async def select_high_level_players(db: asyncpg.Connection, n: int = 10000) -> list[str]:
    """プレイヤーテーブルからアクティブな高レベルプレイヤー(level>=20)をランダムにn人選定する。

    「高レベル」の定義: unlocked_brawlers が最大のプレイヤーから優先して確保。
    足りない場合は unlocked_brawlers の要求値を1ずつ下げて、合計n人を確保する。

    Args:
        db: データベース接続
        n: 選定するプレイヤー数（デフォルト10000）

    Returns:
        プレイヤータグのリスト
    """
    # [この部分は公開用リポジトリでは非公開にされています]

async def get_max_accessory_counts(db: asyncpg.Connection) -> dict[str, int]:
    """
    育成計算機等で利用するため、playersテーブルからレベルが20以上のプレイヤーを対象に、各アクセサリーの最大所持数を取得します。
    Redisキャッシュを使用して10分間キャッシュします。
    """
    # [この部分は公開用リポジトリでは非公開にされています]

async def calculate_and_save_skin_stats(db: asyncpg.Connection) -> None:
    """
    レベル20以上の全プレイヤーを対象に装備率を、BSInfo所持スキン対応後に所持スキンを取得できた
    レベル20以上のプレイヤーを対象に所持率を集計して保存します。
    (BSInfoから取得できていないプレイヤーは所持スキンが装備中のみとなり、所持率を下げるため除外する)
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


async def insert_api_battle(db: asyncpg.Connection, tag: str, battle: APIBattle) -> bool:
    """バトルを battles テーブルに追加する（すでにあれば何もしない）。Player._insert_battle と大会のバトルログ取得で使う。

    Returns:
        bool: すでに保存済みだった場合は True
    """
    mode_id = await prepare_battle_mode_id(
        db,
        mode_id=battle.mode_id,
        event_id=battle.event_id,
        event_map=battle.event_map,
        event_mode=battle.event_mode,
        battle_mode=battle.battle_mode,
    )
    query = '''
        INSERT INTO battles (
            tag, datetime, event_id, event_mode, event_map, battle_mode, mode_id, battle_type, result, rank,
            is_starplayer, brawler, power, trophies, trophy_change, brawlers, team_size, num_of_teams,
            teammate_tags, opponent_tags, teammate_names, opponent_names, teammate_brawlers, opponent_brawlers, ranked_score_after
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
            $11, $12, $13, $14, $15, $16, $17, $18,
            $19, $20, $21, $22, $23, $24, $25
        )
        ON CONFLICT DO NOTHING
    '''

    params = (
        tag, battle.datetime, battle.event_id, battle.event_mode, battle.event_map, battle.battle_mode, mode_id, battle.battle_type, battle.result,
        battle.rank, battle.is_starplayer, battle.brawler, battle.power, battle.trophies, battle.trophy_change, battle.brawlers, battle.team_size,
        battle.num_of_teams, battle.teammate_tags, battle.opponent_tags, battle.teammate_names, battle.opponent_names, battle.teammate_brawlers,
        battle.opponent_brawlers, battle.ranked_score_after,
    )

    try:
        result = await db.execute(query, *params)
        # execute()の戻り値は "INSERT 0 1" or "INSERT 0 0"
        return result != "INSERT 0 1"
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e
