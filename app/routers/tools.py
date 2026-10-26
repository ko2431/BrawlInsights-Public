import asyncio
import base64
import asyncpg
import bisect
import json
import copy
import operator
import random
import time
from pathlib import Path
from types import SimpleNamespace
from fastapi import APIRouter, Request, HTTPException, Query, Depends, Body
from fastapi.responses import JSONResponse, RedirectResponse
import datetime
from itertools import groupby
from typing import Any, Callable, Iterable
from pydantic import BaseModel, Field

from app.core.logger import logger
# [この部分は公開用リポジトリでは非公開にされています]
from app.core.templating import templates
from app.db.db import get_shared_db, get_db_connection_for_bg_task
from app.core.cache import get_cache, set_cache
from app.services.player_account_service import build_player_account_options, get_player_accounts_brawler_data, resolve_user_player_tag
from app.services.brawl_service import (Player, get_player, get_player_from_db, calc_num_of_available_brawlers, get_available_brawlers,
                                        get_brawler_analysis, get_current_ranked_pool, resolve_ranked_filter, get_brawler,
                                        get_ban_suggestions, get_pick_suggestions, predict_win_rate,
                                        get_accessory_stats, get_max_accessory_counts, get_all_skins, get_all_pins, get_all_player_icons, get_all_accessories, get_all_sprays, get_all_buffies,
                                        get_player_name, get_player_icon_from_db)
from app.services import bsinfoapi
from app.services.image_generation_service import (
    IMAGE_REGENERATE_AFTER,
    INITIAL_PROFILE_IMAGE_TYPES,
    ImageGenerationJobData,
    build_image_generation_cache_key,
    create_image_generation_job,
    get_image_generation_job,
    get_image_generation_jobs_ahead_count,
    get_image_job_min_wait_until,
    get_image_job_priority,
    get_latest_cached_image_generation_job,
)
from app.services.user_service import User, try_claim_tutorial_mission, sanitize_faq_item_for_ios
from app.services.board_service import get_or_create_theme_brawler_post, get_or_create_theme_map_post, get_messages
from app.exceptions.custom_exceptions import BrawlStarsAPIError, DataBaseError
from app.utils.utils import confirm_tag, format_tag, format_utc_date, format_utc_datetime
from app.utils.utils import build_brawler_tier_groups, brawler_name_sort_key, get_brawler_display_name
from app.utils.nav_context import nav_template_vars, resolve_nav_context
from app.services.map_mode_catalog import ensure_catalog, get_map_by_id, get_map_names_by_id, get_mode_slug_to_id, get_mode_by_id, get_mode_theme, get_mode_board_colors, mode_icon_candidates, MODE_THEME_BY_ID
from app.services.trophy_stats_service import get_trophy_stats

router = APIRouter(
    prefix="/{lang}/tools",
    tags=["Tools"]
)

DROP_BOXES_PATH = Path(__file__).resolve().parent.parent / "data" / "drop_boxes.json"
TROPHY_REWARDS_PATH = Path(__file__).resolve().parent.parent / "data" / "trophy_rewards.json"
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
CHOICE_REVEAL_BOX_KEYS: set[str] = {"nanodrop", "smoothiedrop"}
VALUE_EFFECT_BOX_KEYS: set[str] = {"angelicdrop", "demonicdrop"}
# ボックスシミュレーターでまとめて開封できる最大個数
BOX_SIM_MAX_OPEN_COUNT = 12
# ボックスから出るバフィーの種類(デフォルト・ジュエル・ゴーストバフィーは出ない)
BOX_BUFFY_TYPES: tuple[str, ...] = ("gadget", "starPower", "hypercharge")
# ナノパワー・フュージョンは特定のキャラを強化するドロップ限定のアイテム。実装されたキャラ(このID以下の全キャラ)が抽選対象
# ノヴァドロップのアップグレードは、スパイク・ビー・アンバー・ジュジュのいずれかのパワーレベルを1上げる(イベント限定の強化)。
# 4キャラともパワー11から始まり、上限の30に達したキャラは対象外。全員30になると代替報酬になる
BOX_STARRNOVA_UPGRADE_KIND = "starrnovaUpgrade"
BOX_STARRNOVA_UPGRADE_START_LEVEL = 11
BOX_STARRNOVA_UPGRADE_MAX_LEVEL = 30
# キャラID: 表示に使うスキンID
BOX_STARRNOVA_UPGRADE_SKIN_IDS: dict[int, int] = {
    16000005: 29001606,  # スパイク
    16000029: 29001604,  # ビー
    16000040: 29001609,  # アンバー
    16000087: 29001607,  # ジュジュ
}
BOX_BRAWLER_BOOST_CONFIGS: dict[str, dict[str, Any]] = {
    "nanoPowers": {"kind": "nanoPower", "max_brawler_id": 16000106, "image": "/images/ui/nanodrop.png"},
    "fusions": {"kind": "fusion", "max_brawler_id": 16000107, "image": "/images/ui/smoothiedrop.png"},
}
# 代替報酬の量が範囲指定されているスキンは、実際に抽選されたスキンのレア度(10刻みの帯)に対応する報酬種別の代替報酬を使う
SUBSTITUTE_SKIN_TYPE_BY_RARITY_BAND: dict[int, str] = {
    1: "rareSkins",
    2: "superRareSkins",
    3: "epicSkins",
    4: "mythicSkins",
    5: "legendarySkins",
    6: "hyperchargeSkins",
}
VALUE_EFFECT_RESOURCE_TYPES: set[str] = {"coins", "powerPoints", "bling"}
VALUE_EFFECT_SHORT_TYPES: set[str] = {"xpDoublers"}
VALUE_EFFECT_MIDDLE_TYPES: set[str] = {"eventPins", "normalPins", "rarePins", "epicPins", "normal-rare-epicPins", "profileIcons", "sprays"}
LANDSCAPE_ONLY_PROFILE_IMAGE_TYPES: set[str] = {"equipment_skins"}
DEFAULT_BRAWLER_GUIDE_ID = 16000000
_PG_INT4_MAX = 2_147_483_647


def load_drop_boxes_data() -> dict:
    with DROP_BOXES_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_trophy_rewards_data() -> dict:
    with TROPHY_REWARDS_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _box_has_simulation_data(box: dict[str, Any]) -> bool:
    if not isinstance(box, dict):
        return False
    if box.get("has_rarity_selector"):
        return any(
            isinstance(rarity, dict)
            and (
                isinstance(rarity.get("rewards"), list)
                or isinstance(rarity.get("premium_rewards"), list)
            )
            for rarity in (box.get("rarities") or {}).values()
        )
    return isinstance(box.get("rewards"), list) or isinstance(box.get("premium_rewards"), list)


def _get_simulatable_boxes(raw_data: dict[str, Any]) -> dict[str, Any]:
    boxes = raw_data.get("boxes", {})
    return {
        box_key: box_data
        for box_key, box_data in boxes.items()
        if _box_has_simulation_data(box_data)
    }


def _weighted_choice(items: list[dict[str, Any]], *, weight_key: str) -> dict[str, Any] | None:
    candidates: list[tuple[dict[str, Any], float]] = []
    total = 0.0
    for item in items:
        weight = float(item.get(weight_key, 0) or 0)
        if weight <= 0:
            continue
        candidates.append((item, weight))
        total += weight

    if not candidates or total <= 0:
        return None

    roll = random.random() * total
    cumulative = 0.0
    for item, weight in candidates:
        cumulative += weight
        if roll <= cumulative:
            return item
    return candidates[-1][0]


def _parse_reward_amount(amount: Any) -> int:
    if isinstance(amount, bool):
        return int(amount)
    if isinstance(amount, (int, float)):
        return max(0, int(amount))
    if isinstance(amount, str):
        normalized = amount.strip().replace(" ", "")
        if not normalized:
            return 0
        if "-" in normalized:
            parts = normalized.split("-", 1)
            try:
                start = int(parts[0])
                end = int(parts[1])
            except ValueError:
                return 0
            low, high = sorted((start, end))
            return random.randint(low, high)
        try:
            return max(0, int(normalized))
        except ValueError:
            return 0
    return 0


def _reward_amount_rank_value(amount: Any) -> int:
    if isinstance(amount, bool):
        return int(amount)
    if isinstance(amount, (int, float)):
        return int(amount)
    if isinstance(amount, str):
        normalized = amount.strip().replace(" ", "")
        if "-" in normalized:
            parts = normalized.split("-", 1)
            try:
                return max(int(parts[0]), int(parts[1]))
            except ValueError:
                return 0
        try:
            return int(normalized)
        except ValueError:
            return 0
    return 0


def _classify_value_drop_effect_tier(box: dict[str, Any], reward: dict[str, Any], amount: int) -> str:
    reward_type = str(reward.get("type") or "")
    if reward_type in VALUE_EFFECT_SHORT_TYPES:
        return "short"

    if reward_type in VALUE_EFFECT_RESOURCE_TYPES:
        amount_values = sorted({
            _reward_amount_rank_value(candidate.get("amount"))
            for candidate in box.get("rewards", [])
            if isinstance(candidate, dict) and candidate.get("type") == reward_type
        })
        amount_values = [value for value in amount_values if value > 0]
        if not amount_values:
            return "short"

        rank_value = _reward_amount_rank_value(reward.get("amount")) or amount
        if rank_value <= amount_values[0]:
            return "short"
        if rank_value >= amount_values[-1]:
            return "long"
        return "middle"

    if reward_type in VALUE_EFFECT_MIDDLE_TYPES:
        return "middle"

    return "long"


def _simulate_reward_from_pool(
    reward_pool: list[dict[str, Any]],
    reward_types: dict[str, Any],
    *,
    event_index: int,
    box_key: str,
    reveal_index: int,
    rarity_key: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, int], dict[str, Any] | None]:
    reward = _weighted_choice(reward_pool, weight_key="probability")
    if not reward or not reward.get("type"):
        return None, {}, None

    reward_type = str(reward["type"])
    amount = _parse_reward_amount(reward.get("amount"))
    reward_type_def = reward_types.get(reward_type, {})
    event = {
        "event_index": event_index,
        "type": "reward",
        "box_key": box_key,
        "reveal_index": reveal_index,
        "reward_type": reward_type,
        "amount": amount,
        "display_name_ja": reward.get("display_name_ja") or reward_type_def.get("name_ja") or reward_type,
        "display_name_en": reward.get("display_name_en") or reward_type_def.get("name_en") or reward_type,
        "rarity_key": rarity_key,
    }
    summary = {reward_type: amount} if amount > 0 else {}
    return event, summary, reward


# 結果まとめの並び順。キャラクター > 強化系 > スキン > ピンズ > アイコン > スプレー > 特別アイテム > 資源
SUMMARY_CATEGORY_ORDER: list[str] = [
    "brawlers",
    "buffies",
    "hypercharges",
    "starPowers",
    "gadgets",
    "proPassSkinUpgrade",
    "skins",
    "pins",
    "profileIcons",
    "sprays",
    "special",
    "resources",
]
SUMMARY_SPECIAL_ORDER: list[str] = ["nanoPowers", "fusions", "starrnovaSpike"]
SUMMARY_RESOURCE_ORDER: list[str] = ["gems", "coins", "powerPoints", "credits", "bling", "xpDoublers", "proPassXp"]
SUMMARY_BRAWLER_RARITY_BY_TYPE: dict[str, int] = {
    "rareBrawlers": 2,
    "superRareBrawlers": 3,
    "epicBrawlers": 4,
    "mythicBrawlers": 5,
    "legendaryBrawlers": 6,
    "ultralegendaryBrawlers": 7,
}
# 抽選されたスキンが無い場合は、報酬種別の中で最も高いレア度(例: rare-mythicSkins はウルトラレア)で並べる
SUMMARY_SKIN_BAND_BY_NAME: dict[str, int] = {
    "rare": 1,
    "superRare": 2,
    "epic": 3,
    "mythic": 4,
    "legendary": 5,
    "hypercharge": 6,
}
SUMMARY_PIN_RARITY_BY_TYPE: dict[str, int] = {"normalPins": 10, "rarePins": 20, "epicPins": 30}


def _summary_sort_group(reward_type: str, target_item: dict[str, Any] | None) -> tuple[int, int]:
    """結果まとめでの (種別の順番, 種別内の順番) を返す。種別内の順番はレア度が高いほど小さい"""
    rarity = target_item.get("rarity") if isinstance(target_item, dict) else None
    rarity = rarity if isinstance(rarity, int) else None

    if reward_type.endswith("Brawlers"):
        brawler_rarity = rarity if rarity is not None else SUMMARY_BRAWLER_RARITY_BY_TYPE.get(reward_type, 0)
        return SUMMARY_CATEGORY_ORDER.index("brawlers"), -brawler_rarity
    if reward_type.endswith("Skins"):
        if rarity is not None and rarity > 0:
            band = (rarity - 1) // 10 + 1
        else:
            band = SUMMARY_SKIN_BAND_BY_NAME.get(reward_type[:-len("Skins")].split("-")[-1], 0)
        return SUMMARY_CATEGORY_ORDER.index("skins"), -band
    if "Pins" in reward_type:
        pin_rarity = rarity if rarity is not None else SUMMARY_PIN_RARITY_BY_TYPE.get(reward_type, 0)
        return SUMMARY_CATEGORY_ORDER.index("pins"), -pin_rarity
    if reward_type in SUMMARY_CATEGORY_ORDER:
        return SUMMARY_CATEGORY_ORDER.index(reward_type), 0
    if reward_type in SUMMARY_RESOURCE_ORDER:
        return SUMMARY_CATEGORY_ORDER.index("resources"), SUMMARY_RESOURCE_ORDER.index(reward_type)
    # 未知の種別は特別アイテムの最後に置く
    special_index = SUMMARY_SPECIAL_ORDER.index(reward_type) if reward_type in SUMMARY_SPECIAL_ORDER else len(SUMMARY_SPECIAL_ORDER)
    return SUMMARY_CATEGORY_ORDER.index("special"), special_index


def _build_summary_item(
    event: dict[str, Any],
    reward_types_order: list[str],
) -> dict[str, Any]:
    target_item = event.get("target_item") if isinstance(event, dict) else None
    reward_type = str(event.get("reward_type") or "")
    amount = max(0, int(event.get("amount") or 0))
    reveal_index = int(event.get("reveal_index") or 0)
    category_order, group_order = _summary_sort_group(reward_type, target_item)

    # 種別もレア度も同じものは出た順。資源等の合算される報酬は最初に出た位置を使う(_merge_summary_items は先の値を保つ)
    item: dict[str, Any] = {
        "summary_key": reward_type,
        "reward_type": reward_type,
        "amount": amount,
        "sort_amount": amount,
        "sort_order": (
            category_order,
            group_order,
            reveal_index,
            reward_types_order.index(reward_type) if reward_type in reward_types_order else 10_000,
        ),
    }
    if not isinstance(target_item, dict):
        return item

    target_item_id = target_item.get("id")
    target_kind = target_item.get("kind")
    if target_item_id is None or not target_kind:
        return item

    item["summary_key"] = f"{reward_type}:{target_kind}:{target_item_id}:{reveal_index}"
    # ノヴァドロップのアップグレードは、同じキャラの分を1つにまとめる(例: パワー11 → 13)
    if target_kind == BOX_STARRNOVA_UPGRADE_KIND:
        item["summary_key"] = f"{reward_type}:{target_kind}:{target_item.get('brawler_id')}"
    item["target_item"] = dict(target_item)
    item["display_name_ja"] = event.get("display_name_ja") or ""
    item["display_name_en"] = event.get("display_name_en") or ""
    item["sort_amount"] = 1
    return item


def _merge_summary_items(
    current: dict[str, Any] | None,
    incoming: dict[str, Any],
) -> dict[str, Any]:
    if current is None:
        return dict(incoming)

    merged = dict(current)
    current_target = current.get("target_item")
    incoming_target = incoming.get("target_item")
    if (
        isinstance(current_target, dict)
        and isinstance(incoming_target, dict)
        and current_target.get("kind") == BOX_STARRNOVA_UPGRADE_KIND
    ):
        # アップグレードは個数ではなく、上がる前と後のパワーレベルをまとめて表示する(個数は1のまま)
        from_level = min(int(current_target.get("from_level") or 0), int(incoming_target.get("from_level") or 0))
        to_level = max(int(current_target.get("to_level") or 0), int(incoming_target.get("to_level") or 0))
        merged["target_item"] = {
            **current_target,
            "from_level": from_level,
            "to_level": to_level,
            "name_ja": f"パワー{from_level} → {to_level}",
            "name_en": f"Power {from_level} → {to_level}",
        }
        return merged
    merged["amount"] = int(current.get("amount") or 0) + int(incoming.get("amount") or 0)
    return merged


def _sort_summary_items(summary: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    summary_items = [item for item in summary.values() if int(item.get("amount") or 0) > 0]
    summary_items.sort(
        key=lambda item: (
            item.get("sort_order", (len(SUMMARY_CATEGORY_ORDER), 0, 0, 10_000)),
            -(int(item.get("sort_amount") or 0)),
            item["reward_type"],
        )
    )
    return summary_items


def _skin_reward_type_matches_rarity(reward_type: str, rarity: int | None) -> bool:
    if rarity is None:
        return False
    if reward_type == "rareSkins":
        return 1 <= rarity <= 10
    if reward_type == "rare-superRareSkins":
        return 1 <= rarity <= 20
    if reward_type == "superRareSkins":
        return 11 <= rarity <= 20
    if reward_type == "superRare-epicSkins":
        return 11 <= rarity <= 30
    if reward_type == "epicSkins":
        return 21 <= rarity <= 30
    if reward_type == "mythicSkins":
        return 31 <= rarity <= 40
    if reward_type == "rare-epicSkins":
        return 1 <= rarity <= 30
    if reward_type == "rare-mythicSkins":
        return 1 <= rarity <= 40
    if reward_type == "mythic-legendarySkins":
        return 31 <= rarity <= 50
    if reward_type == "legendarySkins":
        return 41 <= rarity <= 50
    if reward_type == "hyperchargeSkins":
        return 51 <= rarity <= 60
    if reward_type == "rare-hyperchargeSkins":
        return 1 <= rarity <= 60
    return False


def _pin_reward_type_matches_rarity(reward_type: str, rarity: int | None) -> bool:
    if rarity is None:
        return reward_type == "eventPins"
    if reward_type == "normalPins":
        return rarity == 10
    if reward_type == "rarePins":
        return rarity == 20
    if reward_type == "epicPins":
        return rarity == 30
    if reward_type == "normal-rare-epicPins":
        return rarity in {10, 20, 30}
    if reward_type == "eventPins":
        return True
    return False


def _brawler_reward_type_matches_rarity(reward_type: str, rarity: int | None) -> bool:
    rarity_by_reward_type = {
        "rareBrawlers": 2,
        "superRareBrawlers": 3,
        "epicBrawlers": 4,
        "mythicBrawlers": 5,
        "legendaryBrawlers": 6,
        "ultralegendaryBrawlers": 7,
    }
    return rarity_by_reward_type.get(reward_type) == rarity


def _build_skin_target_item(request: Request, skin: Any, reward_type: str) -> dict[str, Any] | None:
    if skin is None:
        return None
    rarity = getattr(skin, "rarity", None)
    name_ja = getattr(skin, "ja", None) or getattr(skin, "en", None) or f"スキン {getattr(skin, 'id', 0)}"
    name_en = getattr(skin, "en", None) or getattr(skin, "ja", None) or f"Skin {getattr(skin, 'id', 0)}"
    return {
        "id": skin.id,
        "kind": "skin",
        "reward_type": reward_type,
        "name_ja": name_ja,
        "name_en": name_en,
        "rarity": rarity,
        "rarity_icon": build_static_url(request, get_skin_rarity_icon_path(rarity)),
        "image": build_static_url(request, f"/images/skins/{skin.id}.webp"),
        "fallback_image": build_static_url(request, "/images/ui/mystery.png"),
    }


# 親スキンの購入が必要な色違いスキン(光と闇のメカ モーティス等)は、ウルトラレア・レジェンドレアでも49エメラルドで販売される
BOX_VARIANT_SKIN_RARITIES = {40, 50}
BOX_VARIANT_SKIN_GEMS_PRICE = 49


def _is_variant_skin(skin: Any) -> bool:
    """親スキンが必要な色違いスキンか(ボックスからは出ない)"""
    return (
        getattr(skin, "rarity", None) in BOX_VARIANT_SKIN_RARITIES
        and getattr(skin, "gems_price", None) == BOX_VARIANT_SKIN_GEMS_PRICE
    )


def _is_available_box_skin(skin: Any) -> bool:
    if skin is None:
        return False
    return getattr(skin, "is_limited", False) is not True and not _is_variant_skin(skin)


def _build_pin_target_item(request: Request, pin: Any, reward_type: str) -> dict[str, Any] | None:
    if pin is None:
        return None
    description_ja = getattr(pin, "description_ja", None)
    description_en = getattr(pin, "description_en", None)
    name_ja = description_ja or description_en or "ピンズ"
    name_en = description_en or description_ja or "Pin"
    return {
        "id": pin.id,
        "kind": "pin",
        "reward_type": reward_type,
        "name_ja": name_ja,
        "name_en": name_en,
        "rarity": getattr(pin, "rarity", None),
        "image": build_static_url(request, f"/images/pins/{pin.id}.webp"),
        "fallback_image": build_static_url(request, "/images/ui/mystery.png"),
    }


def _build_explicit_pin_target_item(
    request: Request,
    pin: Any,
    reward_type: str,
    target: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if pin is None:
        return None
    target = target or {}
    pin_id = getattr(pin, "id", 0)
    item = _build_pin_target_item(request, pin, reward_type)
    if item is None:
        return None
    item["name_ja"] = target.get("name_ja") or target.get("name_en") or item["name_ja"] or "ピンズ"
    item["name_en"] = target.get("name_en") or target.get("name_ja") or item["name_en"] or "Pin"
    return item


def _build_player_icon_target_item(request: Request, icon: Any, reward_type: str) -> dict[str, Any] | None:
    if icon is None:
        return None
    return {
        "id": icon.id,
        "kind": "playerIcon",
        "reward_type": reward_type,
        "name_ja": "プロフィールアイコン",
        "name_en": "Profile Icon",
        "image": build_static_url(request, f"/images/player_icon/{icon.id}.webp"),
        "fallback_image": build_static_url(request, "/images/player_icon/0.png"),
    }


def _build_explicit_player_icon_target_item(
    request: Request,
    icon: Any,
    reward_type: str,
    target: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if icon is None:
        return None
    target = target or {}
    icon_id = getattr(icon, "id", 0)
    item = _build_player_icon_target_item(request, icon, reward_type)
    if item is None:
        return None
    item["name_ja"] = target.get("name_ja") or target.get("name_en") or item["name_ja"] or "プロフィールアイコン"
    item["name_en"] = target.get("name_en") or target.get("name_ja") or item["name_en"] or "Profile Icon"
    return item


def _is_general_player_icon(icon: Any) -> bool:
    if icon is None:
        return False
    return (
        getattr(icon, "bling_price", None) is not None
        or getattr(icon, "gems_price", None) is not None
    )


def _reward_price_matches(item: Any, reward: dict[str, Any]) -> bool:
    target_gems_price = reward.get("target_gems_price")
    if target_gems_price is not None and getattr(item, "gems_price", None) != target_gems_price:
        return False

    target_bling_price = reward.get("target_bling_price")
    if target_bling_price is not None and getattr(item, "bling_price", None) != target_bling_price:
        return False

    return True


def _is_general_spray(spray: Any) -> bool:
    if spray is None:
        return False
    return (
        getattr(spray, "bling_price", None) is not None
        or getattr(spray, "gems_price", None) is not None
    )


def _build_spray_target_item(
    request: Request,
    spray: Any,
    reward_type: str,
    target: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if spray is None:
        return None
    spray_id = getattr(spray, "id", 0)
    target = target or {}
    name_ja = target.get("name_ja") or target.get("name_en") or "スプレー"
    name_en = target.get("name_en") or target.get("name_ja") or "Spray"
    return {
        "id": spray_id,
        "kind": "spray",
        "reward_type": reward_type,
        "name_ja": name_ja,
        "name_en": name_en,
        "rarity": getattr(spray, "rarity", None),
        "image": build_static_url(request, f"/images/spray/{spray_id}.webp"),
        "fallback_image": build_static_url(request, "/images/ui/sprays.webp"),
    }


def _get_default_skin_ids(skins: Iterable[Any]) -> dict[int, int]:
    """キャラごとのデフォルトスキンID(そのキャラのスキンのうち最もIDが若いもの)を返す"""
    default_skin_ids: dict[int, int] = {}
    for skin in skins:
        brawler_id = getattr(skin, "brawler_id", None)
        skin_id = getattr(skin, "id", None)
        if brawler_id is None or skin_id is None:
            continue
        if brawler_id not in default_skin_ids or skin_id < default_skin_ids[brawler_id]:
            default_skin_ids[brawler_id] = skin_id
    return default_skin_ids


def _build_brawler_target_item(
    request: Request,
    brawler: Any,
    reward_type: str,
    default_skin_id: int | None = None,
) -> dict[str, Any] | None:
    if brawler is None:
        return None

    brawler_id = getattr(brawler, "id", 0)
    name_ja = getattr(brawler, "name_ja", None) or getattr(brawler, "name_en", None) or f"キャラクター {brawler_id}"
    name_en = getattr(brawler, "name_en", None) or getattr(brawler, "name_ja", None) or f"Brawler {brawler_id}"
    portrait_image = build_static_url(request, f"/images/brawler_portraits/{brawler_id}.png")
    # 画像はスキンと同じ見た目になるよう、デフォルトスキンの画像を使う
    return {
        "id": brawler_id,
        "kind": "brawler",
        "reward_type": reward_type,
        "name_ja": name_ja,
        "name_en": name_en,
        "rarity": getattr(brawler, "rarity", None),
        "image": build_static_url(request, f"/images/skins/{default_skin_id}.webp") if default_skin_id else portrait_image,
        "fallback_image": portrait_image,
    }


def _is_box_brawler_candidate(brawler: Any, reward_type: str) -> bool:
    return (
        _brawler_reward_type_matches_rarity(reward_type, getattr(brawler, "rarity", None))
        and getattr(brawler, "is_temporary", False) is not True
    )


def build_box_brawler_targets_by_type(
    request: Request,
    reward_types: dict[str, Any],
    brawlers: list[Any],
    skins: Iterable[Any],
) -> dict[str, list[dict[str, Any]]]:
    """確率表用に、キャラ報酬の種別ごとの排出対象キャラ一覧を返す(出現キャラが指定されていない報酬で使う)"""
    default_skin_ids = _get_default_skin_ids(skins)
    targets_by_type: dict[str, list[dict[str, Any]]] = {}
    for reward_type, reward_type_data in reward_types.items():
        if not reward_type.endswith("Brawlers"):
            continue
        rarity_icon = build_static_url(request, reward_type_data.get("icon"))
        targets = []
        for brawler in sorted(brawlers, key=lambda brawler: getattr(brawler, "id", 0)):
            if not _is_box_brawler_candidate(brawler, reward_type):
                continue
            item = _build_brawler_target_item(request, brawler, reward_type, default_skin_ids.get(brawler.id))
            targets.append({
                "id": item["id"],
                "name_ja": item["name_ja"],
                "name_en": item["name_en"],
                "image": item["image"],
                "fallback_image": item["fallback_image"],
                "rarity_icon": rarity_icon,
            })
        targets_by_type[reward_type] = targets
    return targets_by_type


def _build_accessory_target_item(request: Request, accessory: Any, reward_type: str) -> dict[str, Any] | None:
    if accessory is None:
        return None

    reward_type_config = {
        "gadgets": {
            "kind": "gadget",
            "image": f"/images/gadgets/{getattr(accessory, 'id', 0)}.png",
            "fallback": "/images/accessories/gadget.png",
        },
        "starPowers": {
            "kind": "starPower",
            "image": f"/images/starpowers/{getattr(accessory, 'id', 0)}.png",
            "fallback": "/images/accessories/starpower.png",
        },
        "hypercharges": {
            "kind": "hypercharge",
            "image": f"/images/hypercharge/{getattr(accessory, 'id', 0)}.png",
            "fallback": "/images/ui/hypercharge.webp",
        },
    }
    config = reward_type_config.get(reward_type)
    if not config:
        return None

    accessory_id = getattr(accessory, "id", 0)
    name_ja = getattr(accessory, "ja", None) or getattr(accessory, "en", None) or f"アクセサリー {accessory_id}"
    name_en = getattr(accessory, "en", None) or getattr(accessory, "ja", None) or f"Accessory {accessory_id}"
    return {
        "id": accessory_id,
        "kind": config["kind"],
        "reward_type": reward_type,
        "name_ja": name_ja,
        "name_en": name_en,
        "brawler_id": getattr(accessory, "brawler_id", None),
        "rarity": getattr(accessory, "rarity", None),
        "image": build_static_url(request, config["image"]),
        "fallback_image": build_static_url(request, config["fallback"]),
    }


def _build_buffy_target_item(request: Request, buffy: Any, reward_type: str) -> dict[str, Any] | None:
    if buffy is None:
        return None
    buffy_id = getattr(buffy, "id", 0)
    name_ja = getattr(buffy, "ja", None) or getattr(buffy, "en", None) or "バフィー"
    name_en = getattr(buffy, "en", None) or getattr(buffy, "ja", None) or "Buffie"
    return {
        "id": buffy_id,
        "kind": "buffy",
        "reward_type": reward_type,
        "name_ja": name_ja,
        "name_en": name_en,
        "brawler_id": getattr(buffy, "brawler_id", None),
        "buffy_type": getattr(buffy, "type", None),
        "image": build_static_url(request, f"/images/buffies/{buffy_id}.png"),
        "fallback_image": build_static_url(request, "/images/ui/buddy.png"),
    }


def _build_brawler_boost_target_item(request: Request, brawler: Any, reward_type: str) -> dict[str, Any] | None:
    """ナノパワー・フュージョンの抽選結果。画像は共通のアイコンで、対象キャラのピンを右下に重ねて表示する"""
    config = BOX_BRAWLER_BOOST_CONFIGS.get(reward_type)
    if brawler is None or config is None:
        return None
    brawler_id = getattr(brawler, "id", 0)
    name_ja = getattr(brawler, "name_ja", None) or getattr(brawler, "name_en", None) or f"キャラクター {brawler_id}"
    name_en = getattr(brawler, "name_en", None) or getattr(brawler, "name_ja", None) or f"Brawler {brawler_id}"
    image = build_static_url(request, config["image"])
    return {
        "id": brawler_id,
        "kind": config["kind"],
        "reward_type": reward_type,
        "name_ja": name_ja,
        "name_en": name_en,
        "brawler_id": brawler_id,
        "image": image,
        "fallback_image": image,
        "badge_image": build_static_url(request, f"/images/brawler_pins/{brawler_id}.png"),
    }


def _get_brawler_boost_target_candidates(reward_type: str, dynamic_targets: dict[str, Any]) -> list[Any]:
    config = BOX_BRAWLER_BOOST_CONFIGS.get(reward_type)
    if config is None:
        return []
    return [
        brawler for brawler in dynamic_targets.get("brawlers", [])
        if isinstance(getattr(brawler, "id", None), int)
        and brawler.id <= config["max_brawler_id"]
        and getattr(brawler, "is_temporary", False) is not True
    ]


def _get_starrnova_upgrade_levels(picked_target_keys: set[tuple[str, Any]] | None) -> dict[int, int]:
    """今回のシミュレーションでの、ノヴァドロップのアップグレード対象キャラごとの現在のパワーレベル。
    アップグレードは (kind, "キャラID:上がった後のレベル") として排出済みのキーに記録されている"""
    levels = {brawler_id: BOX_STARRNOVA_UPGRADE_START_LEVEL for brawler_id in BOX_STARRNOVA_UPGRADE_SKIN_IDS}
    for kind, key in picked_target_keys or ():
        if kind != BOX_STARRNOVA_UPGRADE_KIND or not isinstance(key, str):
            continue
        brawler_id_text, _, level_text = key.partition(":")
        if not brawler_id_text.isdigit() or not level_text.isdigit():
            continue
        brawler_id = int(brawler_id_text)
        if brawler_id in levels:
            levels[brawler_id] = max(levels[brawler_id], int(level_text))
    return levels


def _build_starrnova_upgrade_target_item(
    request: Request,
    brawler_id: int,
    from_level: int,
    reward_type: str,
    dynamic_targets: dict[str, Any],
) -> dict[str, Any]:
    """ノヴァドロップのアップグレードの抽選結果。対象キャラのスキンを大きく表示し、アップグレードのアイコンを右下に重ねる"""
    to_level = from_level + 1
    brawler = next((b for b in dynamic_targets.get("brawlers", []) if getattr(b, "id", None) == brawler_id), None)
    brawler_name_ja = getattr(brawler, "name_ja", None) or getattr(brawler, "name_en", None) or ""
    brawler_name_en = getattr(brawler, "name_en", None) or getattr(brawler, "name_ja", None) or ""
    return {
        "id": f"{brawler_id}:{to_level}",
        "kind": BOX_STARRNOVA_UPGRADE_KIND,
        "reward_type": reward_type,
        "name_ja": f"パワー{from_level} → {to_level}",
        "name_en": f"Power {from_level} → {to_level}",
        "brawler_id": brawler_id,
        "brawler_name_ja": brawler_name_ja,
        "brawler_name_en": brawler_name_en,
        "from_level": from_level,
        "to_level": to_level,
        "image": build_static_url(request, f"/images/skins/{BOX_STARRNOVA_UPGRADE_SKIN_IDS[brawler_id]}.webp"),
        "fallback_image": build_static_url(request, f"/images/brawler_portraits/{brawler_id}.png"),
        "badge_image": build_static_url(request, "/images/ui/starrnova_spike.png"),
    }


def _get_buffy_target_candidates(dynamic_targets: dict[str, Any]) -> list[Any]:
    return [
        buffy for buffy in dynamic_targets.get("buffies", [])
        if getattr(buffy, "type", None) in BOX_BUFFY_TYPES
        and getattr(buffy, "is_invalid", False) is not True
    ]


def _reward_type_supports_target_item(reward_type: str) -> bool:
    return (
        reward_type.endswith("Skins")
        or "Pins" in reward_type
        or reward_type == "profileIcons"
        or reward_type == "sprays"
        or reward_type in {"gadgets", "starPowers", "hypercharges", "buffies"}
        or reward_type in BOX_BRAWLER_BOOST_CONFIGS
        or reward_type == "starrnovaSpike"
        or reward_type.endswith("Brawlers")
    )


def _get_skin_target_candidates(
    reward: dict[str, Any],
    reward_type: str,
    dynamic_targets: dict[str, Any],
) -> list[Any]:
    skins_by_id = dynamic_targets.get("skins_by_id", {})
    targets = [
        target for target in reward.get("skin_targets", [])
        if isinstance(target, dict) and str(target.get("id", "")).isdigit()
    ]
    if targets:
        candidates = []
        for target in targets:
            skin_id = int(target["id"])
            if skin_id in skins_by_id:
                candidates.append(skins_by_id[skin_id])
            elif isinstance(target.get("rarity"), int):
                # DB未登録の新スキンは、drop_boxes.json に書いた名前・レア度で先行して抽選対象にする(DB登録後はDBの値が優先される)
                candidates.append(SimpleNamespace(
                    id=skin_id,
                    ja=target.get("name_ja"),
                    en=target.get("name_en"),
                    rarity=target["rarity"],
                    is_limited=None,
                ))
        return candidates
    return [
        skin for skin in dynamic_targets.get("skins", [])
        if _is_available_box_skin(skin)
        and _skin_reward_type_matches_rarity(reward_type, getattr(skin, "rarity", None))
    ]


def _get_pin_target_candidates(
    reward: dict[str, Any],
    reward_type: str,
    dynamic_targets: dict[str, Any],
) -> tuple[list[tuple[Any, dict[str, Any]]], list[Any]]:
    """(明示指定のピン候補, 一般ピン候補) を返す。明示指定があればそちらが優先される"""
    pins_by_id = dynamic_targets.get("pins_by_id", {})
    target_candidates: list[tuple[Any, dict[str, Any]]] = []
    for target in reward.get("pin_targets", []):
        if not isinstance(target, dict) or not str(target.get("id", "")).isdigit():
            continue
        pin_id = int(target["id"])
        if pin_id in pins_by_id:
            target_candidates.append((pins_by_id[pin_id], target))
        elif target.get("name_en") or target.get("name_ja"):
            # DB未登録の新ピンズは、drop_boxes.json に書いた名前で先行して抽選対象にする
            target_candidates.append((
                SimpleNamespace(id=pin_id, rarity=None, description_ja=None, description_en=None),
                target,
            ))

    if target_candidates:
        return target_candidates, []

    pin_candidates = [
        pin for pin in dynamic_targets.get("pins", [])
        if _pin_reward_type_matches_rarity(reward_type, getattr(pin, "rarity", None))
        and _reward_price_matches(pin, reward)
    ]
    return [], pin_candidates


class _RewardTargetsExhausted(Exception):
    """1回の開封内で、報酬の抽選候補がすべて排出済みになった"""


def _exclude_picked_targets(
    candidates: list[Any],
    kind: str,
    picked_target_keys: set[tuple[str, Any]] | None,
    *,
    get_target: Callable[[Any], Any] = lambda candidate: candidate,
) -> list[Any]:
    """同じ開封内で排出済みのアイテムを候補から除外する。候補がすべて排出済みなら例外を送出する"""
    if not candidates or not picked_target_keys:
        return candidates
    remaining = [
        candidate for candidate in candidates
        if (kind, getattr(get_target(candidate), "id", None)) not in picked_target_keys
    ]
    if not remaining:
        raise _RewardTargetsExhausted(kind)
    return remaining


def _pick_reward_target_item(
    request: Request,
    reward: dict[str, Any],
    reward_type: str,
    dynamic_targets: dict[str, Any] | None,
    picked_target_keys: set[tuple[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if not dynamic_targets:
        return None

    if reward_type.endswith("Skins"):
        skin_candidates = _get_skin_target_candidates(reward, reward_type, dynamic_targets)
        if not skin_candidates:
            return None
        skin_candidates = _exclude_picked_targets(skin_candidates, "skin", picked_target_keys)
        return _build_skin_target_item(request, random.choice(skin_candidates), reward_type)

    if "Pins" in reward_type:
        target_candidates, pin_candidates = _get_pin_target_candidates(reward, reward_type, dynamic_targets)
        if target_candidates:
            target_candidates = _exclude_picked_targets(
                target_candidates, "pin", picked_target_keys, get_target=lambda candidate: candidate[0]
            )
            pin, target = random.choice(target_candidates)
            return _build_explicit_pin_target_item(request, pin, reward_type, target)
        if not pin_candidates:
            return None
        pin_candidates = _exclude_picked_targets(pin_candidates, "pin", picked_target_keys)
        return _build_pin_target_item(request, random.choice(pin_candidates), reward_type)

    if reward_type == "profileIcons":
        icons_by_id = dynamic_targets.get("player_icons_by_id", {})
        target_candidates: list[tuple[Any, dict[str, Any]]] = []
        for target in reward.get("icon_targets", []):
            if not isinstance(target, dict) or not str(target.get("id", "")).isdigit():
                continue
            icon_id = int(target["id"])
            if icon_id in icons_by_id:
                target_candidates.append((icons_by_id[icon_id], target))

        if target_candidates:
            target_candidates = _exclude_picked_targets(
                target_candidates, "playerIcon", picked_target_keys, get_target=lambda candidate: candidate[0]
            )
            icon, target = random.choice(target_candidates)
            return _build_explicit_player_icon_target_item(request, icon, reward_type, target)

        icons = [
            icon for icon in dynamic_targets.get("player_icons", [])
            if _is_general_player_icon(icon)
            and _reward_price_matches(icon, reward)
        ]
        if not icons:
            return None
        icons = _exclude_picked_targets(icons, "playerIcon", picked_target_keys)
        return _build_player_icon_target_item(request, random.choice(icons), reward_type)

    if reward_type == "sprays":
        sprays_by_id = dynamic_targets.get("sprays_by_id", {})
        target_candidates: list[tuple[Any, dict[str, Any]]] = []
        for target in reward.get("spray_targets", []):
            if not isinstance(target, dict) or not str(target.get("id", "")).isdigit():
                continue
            spray_id = int(target["id"])
            if spray_id in sprays_by_id:
                target_candidates.append((sprays_by_id[spray_id], target))

        if target_candidates:
            target_candidates = _exclude_picked_targets(
                target_candidates, "spray", picked_target_keys, get_target=lambda candidate: candidate[0]
            )
            spray, target = random.choice(target_candidates)
            return _build_spray_target_item(request, spray, reward_type, target)

        spray_candidates = [
            spray for spray in dynamic_targets.get("sprays", [])
            if _is_general_spray(spray)
        ]
        if not spray_candidates:
            return None
        spray_candidates = _exclude_picked_targets(spray_candidates, "spray", picked_target_keys)
        return _build_spray_target_item(request, random.choice(spray_candidates), reward_type)

    if reward_type.endswith("Brawlers"):
        # 新キャラのボックス等、出現するキャラが決まっている報酬は brawler_targets で指定する
        target_ids = {
            int(target["id"])
            for target in reward.get("brawler_targets", [])
            if isinstance(target, dict) and str(target.get("id", "")).isdigit()
        }
        brawler_candidates = [
            brawler for brawler in dynamic_targets.get("brawlers", [])
            if (
                getattr(brawler, "id", None) in target_ids
                and getattr(brawler, "is_temporary", False) is not True
                if target_ids
                else _is_box_brawler_candidate(brawler, reward_type)
            )
        ]
        if not brawler_candidates:
            return None
        brawler_candidates = _exclude_picked_targets(brawler_candidates, "brawler", picked_target_keys)
        brawler = random.choice(brawler_candidates)
        default_skin_id = dynamic_targets.get("default_skin_ids", {}).get(getattr(brawler, "id", None))
        return _build_brawler_target_item(request, brawler, reward_type, default_skin_id)

    if reward_type == "buffies":
        buffy_candidates = _get_buffy_target_candidates(dynamic_targets)
        if not buffy_candidates:
            return None
        buffy_candidates = _exclude_picked_targets(buffy_candidates, "buffy", picked_target_keys)
        return _build_buffy_target_item(request, random.choice(buffy_candidates), reward_type)

    if reward_type == "starrnovaSpike":
        # 排出済みの記録が無い呼び出し(代替報酬の元になったアイテムの表示用)では、レベルが分からないので割り当てない
        if picked_target_keys is None:
            return None
        levels = _get_starrnova_upgrade_levels(picked_target_keys)
        upgradable_ids = [
            brawler_id for brawler_id, level in levels.items()
            if level < BOX_STARRNOVA_UPGRADE_MAX_LEVEL
        ]
        if not upgradable_ids:
            raise _RewardTargetsExhausted(BOX_STARRNOVA_UPGRADE_KIND)
        brawler_id = random.choice(upgradable_ids)
        return _build_starrnova_upgrade_target_item(request, brawler_id, levels[brawler_id], reward_type, dynamic_targets)

    if reward_type in BOX_BRAWLER_BOOST_CONFIGS:
        boost_candidates = _get_brawler_boost_target_candidates(reward_type, dynamic_targets)
        if not boost_candidates:
            return None
        boost_candidates = _exclude_picked_targets(
            boost_candidates, BOX_BRAWLER_BOOST_CONFIGS[reward_type]["kind"], picked_target_keys
        )
        return _build_brawler_boost_target_item(request, random.choice(boost_candidates), reward_type)

    if reward_type in {"gadgets", "starPowers", "hypercharges"}:
        accessory_type_by_reward_type = {
            "gadgets": ("gadget", "gadget"),
            "starPowers": ("starPower", "starPower"),
            "hypercharges": ("hyperCharge", "hypercharge"),
        }
        accessory_type, accessory_kind = accessory_type_by_reward_type[reward_type]
        accessory_candidates = [
            accessory for accessory in dynamic_targets.get("accessories", [])
            if getattr(accessory, "type", None) == accessory_type
            and getattr(accessory, "is_invalid", False) is not True
        ]
        if not accessory_candidates:
            return None
        accessory_candidates = _exclude_picked_targets(accessory_candidates, accessory_kind, picked_target_keys)
        return _build_accessory_target_item(request, random.choice(accessory_candidates), reward_type)

    return None


def _resolve_substitute_amount(
    reward_type: str,
    substitute: dict[str, Any],
    reward_types: dict[str, Any],
    duplicate_item: dict[str, Any] | None,
) -> int:
    """代替報酬1個分の量。範囲指定(例: レア〜ウルトラレアスキンの 100-1000)は、抽選された入手済みスキンのレア度で決める"""
    amount = substitute.get("amount")
    if isinstance(amount, str) and "-" in amount and reward_type.endswith("Skins"):
        rarity = duplicate_item.get("rarity") if isinstance(duplicate_item, dict) else None
        if isinstance(rarity, int) and rarity > 0:
            band_type = SUBSTITUTE_SKIN_TYPE_BY_RARITY_BAND.get((rarity - 1) // 10 + 1)
            band_substitute = (reward_types.get(band_type) or {}).get("substitute") if band_type else None
            if isinstance(band_substitute, dict) and band_substitute.get("type") == substitute.get("type"):
                return _parse_reward_amount(band_substitute.get("amount"))
    return _parse_reward_amount(amount)


def _get_reward_substitute(reward: dict[str, Any] | None, reward_type: str, reward_types: dict[str, Any] | None) -> Any:
    """代替報酬。ボックス(排出プール)ごとに異なる場合は報酬の行の substitute で上書きし、無ければ報酬種別ごとの共通の値を使う"""
    if isinstance(reward, dict) and isinstance(reward.get("substitute"), dict):
        return reward["substitute"]
    return ((reward_types or {}).get(reward_type) or {}).get("substitute")


def _build_substitute_event(
    event: dict[str, Any],
    reward_types: dict[str, Any] | None,
    units: int,
    duplicate_item: dict[str, Any] | None,
    chosen_reward: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """入手済みのアイテムが抽選された分を、代替報酬(ジュエルチップ等)に置き換えたイベント"""
    reward_type = str(event.get("reward_type") or "")
    substitute = _get_reward_substitute(chosen_reward, reward_type, reward_types)
    if not isinstance(substitute, dict) or not substitute.get("type") or units <= 0:
        return None
    amount = _resolve_substitute_amount(reward_type, substitute, reward_types or {}, duplicate_item) * units
    if amount <= 0:
        return None
    substitute_type = str(substitute["type"])
    substitute_type_def = (reward_types or {}).get(substitute_type, {})
    substitute_event = dict(event)
    substitute_event.update({
        "reward_type": substitute_type,
        "amount": amount,
        "display_name_ja": substitute_type_def.get("name_ja") or substitute_type,
        "display_name_en": substitute_type_def.get("name_en") or substitute_type,
        "target_item": None,
        "substitute_for": {
            "reward_type": reward_type,
            "amount": units,
            "display_name_ja": event.get("display_name_ja") or reward_type,
            "display_name_en": event.get("display_name_en") or reward_type,
            "target_item": duplicate_item,
        },
    })
    return substitute_event


def _materialize_reward_events(
    request: Request,
    event: dict[str, Any],
    chosen_reward: dict[str, Any] | None,
    dynamic_targets: dict[str, Any] | None,
    picked_target_keys: set[tuple[str, Any]] | None = None,
    reward_types: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """報酬イベントに具体的なアイテムを割り当てる。
    picked_target_keys を渡すと同じ開封内での重複を避け、選んだアイテムを追記する。
    候補をすべて入手済みの場合は、実際のゲーム同様にそのまま抽選した上で、報酬種別ごとの代替報酬に置き換える"""
    if not event:
        return []

    reward_type = str(event.get("reward_type") or "")
    amount = max(0, int(event.get("amount") or 0))
    if amount <= 0:
        return []

    if not _reward_type_supports_target_item(reward_type):
        materialized_event = dict(event)
        materialized_event["target_item"] = _pick_reward_target_item(
            request,
            chosen_reward or {},
            reward_type,
            dynamic_targets,
        )
        return [materialized_event]

    valid_target_items: list[dict[str, Any]] = []
    substitute_units = 0
    duplicate_item: dict[str, Any] | None = None
    for _ in range(amount):
        try:
            target_item = _pick_reward_target_item(
                request,
                chosen_reward or {},
                reward_type,
                dynamic_targets,
                picked_target_keys,
            )
        except _RewardTargetsExhausted:
            # 入手済みのアイテムの中から抽選され、代替報酬になる
            substitute_units += 1
            if duplicate_item is None:
                duplicate_item = _pick_reward_target_item(request, chosen_reward or {}, reward_type, dynamic_targets)
            continue
        if not target_item:
            continue
        valid_target_items.append(target_item)
        if picked_target_keys is not None and target_item.get("kind") and target_item.get("id") is not None:
            picked_target_keys.add((str(target_item["kind"]), target_item["id"]))
    materialized_events: list[dict[str, Any]] = []
    for target_item in valid_target_items:
        materialized_event = dict(event)
        materialized_event["amount"] = 1
        materialized_event["target_item"] = target_item
        materialized_events.append(materialized_event)
    if substitute_units:
        substitute_event = _build_substitute_event(event, reward_types, substitute_units, duplicate_item, chosen_reward)
        if substitute_event:
            materialized_events.append(substitute_event)
    if not materialized_events:
        materialized_event = dict(event)
        materialized_event["target_item"] = duplicate_item
        return [materialized_event]
    return materialized_events


def _draw_unique_reward_events(
    request: Request,
    reward_pool: list[dict[str, Any]],
    reward_types: dict[str, Any],
    *,
    box: dict[str, Any],
    box_key: str,
    event_index: int,
    reveal_index: int,
    rarity_key: str | None = None,
    dynamic_targets: dict[str, Any] | None,
    picked_target_keys: set[tuple[str, Any]],
) -> list[dict[str, Any]]:
    """報酬プールから1枠分を抽選し、具体的なアイテムを割り当てたイベントを返す。
    実際のゲーム同様に1回の開封内で同じアイテムは重複させず、候補をすべて入手済みの報酬は代替報酬に置き換える
    (確率を他の報酬に配り直さないよう、引き直しはしない)"""
    event, _delta, chosen_reward = _simulate_reward_from_pool(
        reward_pool,
        reward_types,
        event_index=event_index,
        box_key=box_key,
        reveal_index=reveal_index,
        rarity_key=rarity_key,
    )
    if not event:
        return []
    if box_key in VALUE_EFFECT_BOX_KEYS:
        event["opening_effect_tier"] = _classify_value_drop_effect_tier(box, chosen_reward or {}, int(event.get("amount") or 0))
    return _materialize_reward_events(
        request,
        event,
        chosen_reward,
        dynamic_targets,
        picked_target_keys,
        reward_types,
    )


def _get_drop_item_counts(container: dict[str, Any], fallback_items_per_open: int) -> tuple[int, int]:
    total_items = int(container.get("items_per_open") or fallback_items_per_open or 1)
    total_items = max(total_items, 1)
    premium_drops = int(container.get("premium_drops") or 0)
    premium_count = min(max(premium_drops, 0), total_items)
    normal_count = max(total_items - premium_count, 0)
    return normal_count, premium_count


def _should_insert_rarity_reveal(box_key: str) -> bool:
    return box_key in {"starrdrop", "chaosdrop"}


def _build_sushi_split_cycles(final_count: int) -> list[dict[str, Any]]:
    normalized_final_count = max(1, min(8, int(final_count or 1)))
    stages = [1]
    while stages[-1] < normalized_final_count:
        stages.append(stages[-1] * 2)

    cycles: list[dict[str, Any]] = []
    for index, count in enumerate(stages):
        can_split = count < normalized_final_count and count < 8
        success_at = random.randint(1, 4) if can_split else None
        cycles.append({
            "count": count,
            "success_at": success_at,
            "to_count": count * 2 if can_split else count,
        })
    return cycles


def _build_chaos_reveal_steps(final_count: int, final_rarity_key: str | None) -> list[dict[str, Any]]:
    rarity_order = ["rare", "superRare", "epic", "mythic", "legendary", "ultra"]
    target_rarity = final_rarity_key if final_rarity_key in rarity_order else "superRare"
    target_tier = rarity_order.index(target_rarity)
    normalized_final_count = max(1, min(8, int(final_count or 1)))

    split_actions: list[dict[str, Any]] = []
    visible_count = 1
    while visible_count < normalized_final_count:
        next_count = min(visible_count * 2, normalized_final_count)
        split_actions.append({
            "action": "split",
            "from_count": visible_count,
            "to_count": next_count,
            "count": next_count,
        })
        visible_count = next_count

    upgrade_actions: list[dict[str, Any]] = []
    for tier in range(target_tier):
        upgrade_actions.append({
            "action": "upgrade",
            "from_rarity_key": rarity_order[tier],
            "to_rarity_key": rarity_order[tier + 1],
            "rarity_key": rarity_order[tier + 1],
            "jump": 1,
        })

    steps: list[dict[str, Any]] = []
    if upgrade_actions and upgrade_actions[0].get("from_rarity_key") == "rare":
        steps.append(upgrade_actions.pop(0))

    queues = {
        "split": split_actions,
        "upgrade": upgrade_actions,
    }
    available_action_types = [
        action_type
        for action_type, actions in queues.items()
        if actions
    ]
    displayed_count = 1
    displayed_rarity = "rare"

    while available_action_types:
        action_type = random.choice(available_action_types)
        action = queues[action_type].pop(0)
        if action_type == "split":
            displayed_count = int(action.get("to_count") or displayed_count)
            action["rarity_key"] = displayed_rarity
        else:
            displayed_rarity = str(action.get("to_rarity_key") or displayed_rarity)
            action["count"] = displayed_count
        action["display_count"] = displayed_count
        action["display_rarity_key"] = displayed_rarity
        steps.append(action)
        available_action_types = [
            next_type
            for next_type, actions in queues.items()
            if actions
        ]

    success_count = len(steps)
    hold_count = random.randint(2, 4) if success_count == 0 else random.randint(0, 3)
    for _ in range(hold_count):
        insert_at = random.randint(1, len(steps)) if steps else 0
        steps.insert(insert_at, {"action": "hold"})

    displayed_count = 1
    displayed_rarity = "rare"
    resolved_steps: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        action = dict(step)
        if action.get("action") == "split":
            displayed_count = int(action.get("to_count") or displayed_count)
            action["rarity_key"] = displayed_rarity
        elif action.get("action") == "upgrade":
            displayed_rarity = str(action.get("to_rarity_key") or displayed_rarity)
            action["count"] = displayed_count
        else:
            action["action"] = "hold"
            action["count"] = displayed_count
            action["rarity_key"] = displayed_rarity
        action["display_count"] = displayed_count
        action["display_rarity_key"] = displayed_rarity
        action["check_index"] = index + 1
        resolved_steps.append(action)

    return resolved_steps


def _build_choice_pick_item_counts(total_items: int, choice_count: int = 3, max_items_per_pick: int = 3) -> list[int]:
    normalized_choice_count = max(1, int(choice_count or 3))
    normalized_max = max(1, int(max_items_per_pick or 3))
    normalized_total = max(1, min(int(total_items or 1), normalized_choice_count * normalized_max))
    counts = [0 for _ in range(normalized_choice_count)]

    for _ in range(normalized_total):
        available_indexes = [
            index for index, count in enumerate(counts)
            if count < normalized_max
        ]
        if not available_indexes:
            break
        counts[random.choice(available_indexes)] += 1
    return counts


def simulate_drop_box_open(
    request: Request,
    raw_data: dict[str, Any],
    box_key: str,
    dynamic_targets: dict[str, Any] | None = None,
    picked_target_keys: set[tuple[str, Any]] | None = None,
) -> dict[str, Any]:
    boxes = _get_simulatable_boxes(raw_data)
    box = boxes.get(box_key)
    if not box:
        raise KeyError(box_key)

    reward_types = raw_data.get("reward_types", {})
    split = box.get("split") or {}
    split_distribution = split.get("distribution") if split.get("enabled") else None
    split_count = 1
    if isinstance(split_distribution, list) and split_distribution:
        selected_split = _weighted_choice(split_distribution, weight_key="probability")
        if selected_split:
            split_count = max(1, int(selected_split.get("count") or 1))

    events: list[dict[str, Any]] = []
    summary: dict[str, dict[str, Any]] = {}
    reward_types_order = list(reward_types.keys())
    # 1回の開封内で排出済みのアイテム (kind, id)。同じスキン等が重複して出ないようにする
    # まとめて開封する場合は、入手済み扱いとして全開封で共有する
    if picked_target_keys is None:
        picked_target_keys = set()
    event_index = 0
    reveal_index = 0

    if box_key == "chaosdrop" and box.get("has_rarity_selector"):
        rarity_items = []
        for rarity_key, rarity_data in (box.get("rarities") or {}).items():
            if isinstance(rarity_data, dict):
                rarity_items.append({"key": rarity_key, **rarity_data})
        selected_rarity = _weighted_choice(rarity_items, weight_key="drop_probability")
        if selected_rarity:
            rarity_key = selected_rarity.get("key")
            events.append({
                "event_index": event_index,
                "type": "chaos_reveal",
                "box_key": box_key,
                "final_count": split_count,
                "final_rarity_key": rarity_key,
                "steps": _build_chaos_reveal_steps(split_count, rarity_key),
            })
            event_index += 1

            normal_count, premium_count = _get_drop_item_counts(
                selected_rarity,
                int(box.get("items_per_open") or 1),
            )

            for _drop_index in range(split_count):
                slot_pool_flags = ([False] * normal_count) + ([True] * premium_count)
                random.shuffle(slot_pool_flags)

                for is_premium in slot_pool_flags:
                    reward_pool = selected_rarity.get("premium_rewards") if is_premium else selected_rarity.get("rewards")
                    if not isinstance(reward_pool, list):
                        continue
                    materialized_events = _draw_unique_reward_events(
                        request,
                        reward_pool,
                        reward_types,
                        box=box,
                        box_key=box_key,
                        event_index=event_index,
                        reveal_index=reveal_index + 1,
                        rarity_key=rarity_key,
                        dynamic_targets=dynamic_targets,
                        picked_target_keys=picked_target_keys,
                    )
                    for materialized_event in materialized_events:
                        reveal_index += 1
                        materialized_event["event_index"] = event_index
                        materialized_event["reveal_index"] = reveal_index
                        materialized_event["is_premium"] = is_premium
                        events.append(materialized_event)
                        summary_item = _build_summary_item(materialized_event, reward_types_order)
                        summary[summary_item["summary_key"]] = _merge_summary_items(
                            summary.get(summary_item["summary_key"]),
                            summary_item,
                        )
                        event_index += 1

        summary_items = _sort_summary_items(summary)

        return {
            "box_key": box_key,
            "split_count": split_count,
            "events": events,
            "summary": summary_items,
            "total_reveals": reveal_index,
        }

    if split_count > 1:
        if box_key in CHOICE_REVEAL_BOX_KEYS:
            choice_count = 3
            max_items_per_capsule = 3
            events.append({
                "event_index": event_index,
                "type": "choice_reveal",
                "box_key": box_key,
                "capsules": [
                    {
                        "slot_index": index,
                    }
                    for index in range(5)
                ],
                "choice_count": choice_count,
                "max_items_per_capsule": max_items_per_capsule,
                "total_items": split_count,
                "pick_item_counts": _build_choice_pick_item_counts(
                    split_count,
                    choice_count,
                    max_items_per_capsule,
                ),
            })
            event_index += 1
        else:
            events.append({
                "event_index": event_index,
                "type": "split",
                "box_key": box_key,
                "from_count": 1,
                "to_count": split_count,
            })
            event_index += 1

    if box_key in CHOICE_REVEAL_BOX_KEYS and not any(event.get("type") == "choice_reveal" for event in events):
        choice_count = 3
        max_items_per_capsule = 3
        events.append({
            "event_index": event_index,
            "type": "choice_reveal",
            "box_key": box_key,
            "capsules": [
                {
                    "slot_index": index,
                }
                for index in range(5)
            ],
            "choice_count": choice_count,
            "max_items_per_capsule": max_items_per_capsule,
            "total_items": split_count,
            "pick_item_counts": _build_choice_pick_item_counts(
                split_count,
                choice_count,
                max_items_per_capsule,
            ),
        })
        event_index += 1

    for _drop_index in range(split_count):
        if box.get("has_rarity_selector"):
            rarity_items = []
            for rarity_key, rarity_data in (box.get("rarities") or {}).items():
                if isinstance(rarity_data, dict):
                    rarity_items.append({"key": rarity_key, **rarity_data})
            selected_rarity = _weighted_choice(rarity_items, weight_key="drop_probability")
            if not selected_rarity:
                continue

            rarity_key = selected_rarity.get("key")
            if box_key == "sushi":
                split_count = max(1, min(8, int(selected_rarity.get("items_per_open") or 1)))
                events.append({
                    "event_index": event_index,
                    "type": "sushi_split",
                    "box_key": box_key,
                    "final_count": split_count,
                    "cycles": _build_sushi_split_cycles(split_count),
                })
                event_index += 1
            if rarity_key and _should_insert_rarity_reveal(box_key):
                events.append({
                    "event_index": event_index,
                    "type": "rarity",
                    "box_key": box_key,
                    "rarity_key": rarity_key,
                })
                event_index += 1
            normal_count, premium_count = _get_drop_item_counts(
                selected_rarity,
                int(box.get("items_per_open") or 1),
            )

            slot_pool_flags = ([False] * normal_count) + ([True] * premium_count)
            random.shuffle(slot_pool_flags)

            for is_premium in slot_pool_flags:
                reward_pool = selected_rarity.get("premium_rewards") if is_premium else selected_rarity.get("rewards")
                if not isinstance(reward_pool, list):
                    continue
                materialized_events = _draw_unique_reward_events(
                    request,
                    reward_pool,
                    reward_types,
                    box=box,
                    box_key=box_key,
                    event_index=event_index,
                    reveal_index=reveal_index + 1,
                    rarity_key=rarity_key,
                    dynamic_targets=dynamic_targets,
                    picked_target_keys=picked_target_keys,
                )
                for materialized_event in materialized_events:
                    reveal_index += 1
                    materialized_event["event_index"] = event_index
                    materialized_event["reveal_index"] = reveal_index
                    materialized_event["is_premium"] = is_premium
                    events.append(materialized_event)
                    summary_item = _build_summary_item(materialized_event, reward_types_order)
                    summary[summary_item["summary_key"]] = _merge_summary_items(
                        summary.get(summary_item["summary_key"]),
                        summary_item,
                    )
                    event_index += 1
            continue

        normal_count, premium_count = _get_drop_item_counts(box, int(box.get("items_per_open") or 1))
        slot_pool_flags = ([False] * normal_count) + ([True] * premium_count)
        random.shuffle(slot_pool_flags)

        for is_premium in slot_pool_flags:
            reward_pool = box.get("premium_rewards") if is_premium else box.get("rewards")
            if not isinstance(reward_pool, list):
                continue
            materialized_events = _draw_unique_reward_events(
                request,
                reward_pool,
                reward_types,
                box=box,
                box_key=box_key,
                event_index=event_index,
                reveal_index=reveal_index + 1,
                dynamic_targets=dynamic_targets,
                picked_target_keys=picked_target_keys,
            )
            for materialized_event in materialized_events:
                reveal_index += 1
                materialized_event["event_index"] = event_index
                materialized_event["reveal_index"] = reveal_index
                materialized_event["is_premium"] = is_premium
                events.append(materialized_event)
                summary_item = _build_summary_item(materialized_event, reward_types_order)
                summary[summary_item["summary_key"]] = _merge_summary_items(
                    summary.get(summary_item["summary_key"]),
                    summary_item,
                )
                event_index += 1

    summary_items = _sort_summary_items(summary)

    return {
        "box_key": box_key,
        "split_count": split_count,
        "events": events,
        "summary": summary_items,
        "total_reveals": reveal_index,
    }


def simulate_drop_box_multi_open(
    request: Request,
    raw_data: dict[str, Any],
    box_key: str,
    open_count: int,
    dynamic_targets: dict[str, Any] | None = None,
    picked_target_keys: set[tuple[str, Any]] | None = None,
) -> dict[str, Any]:
    """同じドロップをまとめて複数個開封する。
    opens には開封ごとの演出イベント(報酬以外)を、events には全開封の報酬を通し番号で並べて返す"""
    reward_types_order = list(raw_data.get("reward_types", {}).keys())
    if picked_target_keys is None:
        picked_target_keys = set()
    opens: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    summary: dict[str, dict[str, Any]] = {}

    for open_index in range(open_count):
        single_result = simulate_drop_box_open(
            request,
            raw_data,
            box_key,
            dynamic_targets=dynamic_targets,
            picked_target_keys=picked_target_keys,
        )
        stage_events: list[dict[str, Any]] = []
        opening_effect_tier = None
        for event in single_result["events"]:
            event["open_index"] = open_index
            if event.get("type") != "reward":
                stage_events.append(event)
                continue
            if opening_effect_tier is None:
                opening_effect_tier = event.get("opening_effect_tier")
            event["event_index"] = len(events)
            event["reveal_index"] = len(events) + 1
            events.append(event)
            summary_item = _build_summary_item(event, reward_types_order)
            summary[summary_item["summary_key"]] = _merge_summary_items(
                summary.get(summary_item["summary_key"]),
                summary_item,
            )
        opens.append({
            "open_index": open_index,
            "split_count": single_result["split_count"],
            "events": stage_events,
            "opening_effect_tier": opening_effect_tier,
        })

    return {
        "box_key": box_key,
        "open_count": open_count,
        "opens": opens,
        "events": events,
        "summary": _sort_summary_items(summary),
        "total_reveals": len(events),
    }


#* /---*---*---*---*---*---*---*---*/
#* ボックスシミュレーター: 運の良さ判定
#* 報酬をエメラルド換算した総価値の確率分布をボックス定義から厳密に求め、今回の結果が上位何%かを判定する
#* /---*---*---*---*---*---*---*---*/
# [この部分は公開用リポジトリでは非公開にされています]

# (box_key, 開封数) -> (有効期限(monotonic), drop_boxes.jsonのmtime, 判定テーブル)。ワーカープロセスごとに保持する
_BOX_LUCK_TABLE_CACHE: dict[tuple[str, int], tuple[float, float, dict[str, Any]]] = {}
_BOX_LUCK_TABLE_LOCKS: dict[tuple[str, int], asyncio.Lock] = {}


def _box_luck_quantize(value: float) -> int:
    return int(round(value / BOX_LUCK_VALUE_STEP))


def _box_luck_skin_value(rarity: Any, reward_type: str) -> float:
    if isinstance(rarity, int) and rarity > 0:
        band_value = BOX_LUCK_SKIN_VALUES_BY_BAND.get((rarity - 1) // 10 + 1)
        if band_value is not None:
            return band_value
    return BOX_LUCK_SKIN_TYPE_VALUES.get(reward_type, 0.0)


def _box_luck_pin_value(rarity: Any, reward_type: str) -> float:
    if isinstance(rarity, int) and rarity in BOX_LUCK_PIN_VALUES:
        return BOX_LUCK_PIN_VALUES[rarity]
    if rarity is None and reward_type == "eventPins":
        return BOX_LUCK_PIN_DEFAULT_VALUE
    return BOX_LUCK_PIN_TYPE_VALUES.get(reward_type, BOX_LUCK_PIN_DEFAULT_VALUE)


def _box_luck_buffy_value(buffy_type: Any) -> float:
    return BOX_LUCK_BUFFY_VALUES.get(str(buffy_type or ""), BOX_LUCK_ITEM_VALUES["buffies"])


def _box_luck_item_value(reward_type: str, target_item: dict[str, Any] | None) -> float:
    rarity = target_item.get("rarity") if isinstance(target_item, dict) else None
    if reward_type.endswith("Skins"):
        if isinstance(target_item, dict):
            return _box_luck_skin_value(rarity, reward_type)
        return BOX_LUCK_SKIN_TYPE_VALUES.get(reward_type, 0.0)
    if "Pins" in reward_type:
        if isinstance(target_item, dict):
            return _box_luck_pin_value(rarity, reward_type)
        return BOX_LUCK_PIN_TYPE_VALUES.get(reward_type, BOX_LUCK_PIN_DEFAULT_VALUE)
    if reward_type == "buffies" and isinstance(target_item, dict):
        return _box_luck_buffy_value(target_item.get("buffy_type"))
    return BOX_LUCK_ITEM_VALUES.get(reward_type, 0.0)


def _reward_event_luck_value(event: dict[str, Any]) -> int:
    """実際に出た報酬イベント1件の価値(量子化済み)。
    代替報酬は、入手状況に左右されない抽選の運を測るため、抽選された元のアイテムの価値で評価する(判定の分布も入手状況を考慮しない)"""
    substitute_for = event.get("substitute_for")
    if isinstance(substitute_for, dict) and substitute_for.get("reward_type"):
        event = {
            "reward_type": substitute_for["reward_type"],
            "amount": substitute_for.get("amount"),
            "target_item": substitute_for.get("target_item"),
        }
    reward_type = str(event.get("reward_type") or "")
    amount = max(0, int(event.get("amount") or 0))
    if amount <= 0:
        return 0
    if reward_type in BOX_LUCK_UNIT_VALUES:
        return _box_luck_quantize(amount * BOX_LUCK_UNIT_VALUES[reward_type])
    item_value = _box_luck_item_value(reward_type, event.get("target_item"))
    return _box_luck_quantize(item_value) * amount


def _reward_amount_outcomes(amount: Any) -> list[tuple[int, float]]:
    """_parse_reward_amount と同じ解釈で、取りうる数量とその確率を返す"""
    if isinstance(amount, str):
        normalized = amount.strip().replace(" ", "")
        if "-" in normalized:
            parts = normalized.split("-", 1)
            try:
                low, high = sorted((int(parts[0]), int(parts[1])))
            except ValueError:
                return [(0, 1.0)]
            count = high - low + 1
            return [(max(0, value), 1.0 / count) for value in range(low, high + 1)]
    return [(_parse_reward_amount(amount), 1.0)]


def _reward_entry_luck_distribution(
    reward: dict[str, Any],
    dynamic_targets: dict[str, Any] | None,
) -> dict[int, float]:
    """報酬プール1行を引いたときの価値分布(量子化済み)"""
    reward_type = str(reward.get("type") or "")
    distribution: dict[int, float] = {}

    if reward_type in BOX_LUCK_UNIT_VALUES:
        unit_value = BOX_LUCK_UNIT_VALUES[reward_type]
        for amount, probability in _reward_amount_outcomes(reward.get("amount")):
            key = _box_luck_quantize(amount * unit_value)
            distribution[key] = distribution.get(key, 0.0) + probability
        return distribution

    # 抽選対象のあるアイテムは、実際の抽選候補のレア度構成から価値を決める
    item_values: list[float] = []
    if dynamic_targets and reward_type.endswith("Skins"):
        item_values = [
            _box_luck_skin_value(getattr(skin, "rarity", None), reward_type)
            for skin in _get_skin_target_candidates(reward, reward_type, dynamic_targets)
        ]
    elif dynamic_targets and "Pins" in reward_type:
        explicit_candidates, general_candidates = _get_pin_target_candidates(reward, reward_type, dynamic_targets)
        pins = [pin for pin, _target in explicit_candidates] or general_candidates
        item_values = [_box_luck_pin_value(getattr(pin, "rarity", None), reward_type) for pin in pins]
    elif dynamic_targets and reward_type == "buffies":
        item_values = [_box_luck_buffy_value(getattr(buffy, "type", None)) for buffy in _get_buffy_target_candidates(dynamic_targets)]
    if not item_values:
        item_values = [_box_luck_item_value(reward_type, None)]

    item_probability = 1.0 / len(item_values)
    for amount, amount_probability in _reward_amount_outcomes(reward.get("amount")):
        for item_value in item_values:
            key = _box_luck_quantize(item_value) * amount
            distribution[key] = distribution.get(key, 0.0) + amount_probability * item_probability
    return distribution


def _box_luck_mix(parts: list[tuple[float, dict[int, float]]]) -> dict[int, float]:
    total_weight = sum(weight for weight, _distribution in parts if weight > 0)
    if total_weight <= 0:
        return {0: 1.0}
    mixed: dict[int, float] = {}
    for weight, distribution in parts:
        if weight <= 0:
            continue
        normalized_weight = weight / total_weight
        for key, probability in distribution.items():
            mixed[key] = mixed.get(key, 0.0) + normalized_weight * probability
    return mixed


def _box_luck_convolve(left: dict[int, float], right: dict[int, float]) -> dict[int, float]:
    result: dict[int, float] = {}
    for left_key, left_probability in left.items():
        for right_key, right_probability in right.items():
            key = left_key + right_key
            result[key] = result.get(key, 0.0) + left_probability * right_probability
    return {key: probability for key, probability in result.items() if probability >= BOX_LUCK_MIN_PROBABILITY}


def _box_luck_powers(distribution: dict[int, float], max_count: int) -> list[dict[int, float]]:
    """[分布の0回和, 1回和, ..., max_count回和] を返す。スロット側の分布が小さいので逐次畳み込みの方が速い"""
    powers = [{0: 1.0}]
    for _ in range(max(0, max_count)):
        powers.append(_box_luck_convolve(powers[-1], distribution))
    return powers


def _box_luck_pool_distribution(pool: Any, dynamic_targets: dict[str, Any] | None) -> dict[int, float]:
    if not isinstance(pool, list):
        return {0: 1.0}
    parts = [
        (float(reward.get("probability", 0) or 0), _reward_entry_luck_distribution(reward, dynamic_targets))
        for reward in pool
        if isinstance(reward, dict) and reward.get("type")
    ]
    return _box_luck_mix(parts)


def _box_luck_container_distribution(
    container: dict[str, Any],
    fallback_items_per_open: int,
    dynamic_targets: dict[str, Any] | None,
) -> dict[int, float]:
    """ドロップ1個(通常枠 + 確定枠)の価値分布"""
    normal_count, premium_count = _get_drop_item_counts(container, fallback_items_per_open)
    distribution = _box_luck_powers(_box_luck_pool_distribution(container.get("rewards"), dynamic_targets), normal_count)[-1]
    if premium_count > 0:
        premium_distribution = _box_luck_powers(
            _box_luck_pool_distribution(container.get("premium_rewards"), dynamic_targets),
            premium_count,
        )[-1]
        distribution = _box_luck_convolve(distribution, premium_distribution)
    return distribution


def _box_luck_split_options(box: dict[str, Any]) -> list[tuple[int, float]]:
    split = box.get("split") or {}
    split_distribution = split.get("distribution") if split.get("enabled") else None
    options = [
        (max(1, int(option.get("count") or 1)), float(option.get("probability", 0) or 0))
        for option in (split_distribution or [])
        if isinstance(option, dict) and float(option.get("probability", 0) or 0) > 0
    ]
    return options or [(1, 1.0)]


def _box_luck_split_mixture(
    drop_distribution: dict[int, float],
    split_options: list[tuple[int, float]],
) -> dict[int, float]:
    powers = _box_luck_powers(drop_distribution, max(count for count, _probability in split_options))
    return _box_luck_mix([(probability, powers[count]) for count, probability in split_options])


def _build_box_luck_distribution(
    box_key: str,
    box: dict[str, Any],
    dynamic_targets: dict[str, Any] | None,
) -> dict[int, float]:
    """simulate_drop_box_open と同じ抽選構造で、1回開封したときの総価値の確率分布を求める"""
    fallback_items_per_open = int(box.get("items_per_open") or 1)
    split_options = _box_luck_split_options(box)

    if not box.get("has_rarity_selector"):
        drop_distribution = _box_luck_container_distribution(box, fallback_items_per_open, dynamic_targets)
        return _box_luck_split_mixture(drop_distribution, split_options)

    rarity_items = [
        rarity_data
        for rarity_data in (box.get("rarities") or {}).values()
        if isinstance(rarity_data, dict) and float(rarity_data.get("drop_probability", 0) or 0) > 0
    ]
    # カオスドロップはレア度を1回だけ決め、分裂した全ドロップが同じレア度になる
    if box_key == "chaosdrop":
        return _box_luck_mix([
            (
                float(rarity_data.get("drop_probability", 0) or 0),
                _box_luck_split_mixture(
                    _box_luck_container_distribution(rarity_data, fallback_items_per_open, dynamic_targets),
                    split_options,
                ),
            )
            for rarity_data in rarity_items
        ])

    # それ以外はドロップごとにレア度を抽選する(寿司はレア度ごとに items_per_open / premium_drops が異なる)
    drop_distribution = _box_luck_mix([
        (
            float(rarity_data.get("drop_probability", 0) or 0),
            _box_luck_container_distribution(rarity_data, fallback_items_per_open, dynamic_targets),
        )
        for rarity_data in rarity_items
    ])
    return _box_luck_split_mixture(drop_distribution, split_options)


def _box_luck_multi_bin_width(distribution: dict[int, float]) -> int:
    """1個分の分布をまとめるビン幅(量子化単位)。ばらつきに対して十分細かく、かつビン数が上限を超えない幅にする"""
    total = sum(distribution.values())
    if total <= 0:
        return 1
    mean = sum(value * probability for value, probability in distribution.items()) / total
    variance = sum((value - mean) ** 2 * probability for value, probability in distribution.items()) / total
    width_by_stddev = int(variance ** 0.5 // BOX_LUCK_MULTI_BINS_PER_STDDEV)
    width_by_range = -(-(max(distribution) - min(distribution)) // BOX_LUCK_MULTI_MAX_BINS)
    return max(1, width_by_stddev, width_by_range)


def _box_luck_dense(distribution: dict[int, float], bin_width: int) -> tuple[int, list[float]]:
    """分布をビン番号の連続した配列 (先頭のビン番号, 確率の配列) に変換する"""
    binned: dict[int, float] = {}
    for value, probability in distribution.items():
        bin_index = int(round(value / bin_width))
        binned[bin_index] = binned.get(bin_index, 0.0) + probability
    offset = min(binned)
    probabilities = [0.0] * (max(binned) - offset + 1)
    for bin_index, probability in binned.items():
        probabilities[bin_index - offset] += probability
    return offset, probabilities


def _box_luck_dense_convolve(
    left: tuple[int, list[float]],
    right: tuple[int, list[float]],
) -> tuple[int, list[float]]:
    """内側のループを map で C 実装に任せ、純Pythonでも高速に畳み込む"""
    left_offset, left_probabilities = left
    right_offset, right_probabilities = right
    if len(left_probabilities) > len(right_probabilities):
        left_probabilities, right_probabilities = right_probabilities, left_probabilities
    width = len(right_probabilities)
    result = [0.0] * (len(left_probabilities) + width - 1)
    for index, probability in enumerate(left_probabilities):
        if probability < BOX_LUCK_MULTI_MIN_PROBABILITY:
            continue
        result[index:index + width] = map(
            operator.add,
            result[index:index + width],
            map(probability.__mul__, right_probabilities),
        )
    start = 0
    while start < len(result) - 1 and result[start] < BOX_LUCK_MULTI_MIN_PROBABILITY:
        start += 1
    end = len(result)
    while end > start + 1 and result[end - 1] < BOX_LUCK_MULTI_MIN_PROBABILITY:
        end -= 1
    return left_offset + right_offset + start, result[start:end]


def _box_luck_dense_power(base: tuple[int, list[float]], count: int) -> tuple[int, list[float]]:
    """count 個分の和の分布を二分累乗で求める"""
    result: tuple[int, list[float]] | None = None
    current = base
    remaining = max(1, count)
    while remaining:
        if remaining & 1:
            result = current if result is None else _box_luck_dense_convolve(result, current)
        remaining >>= 1
        if remaining:
            current = _box_luck_dense_convolve(current, current)
    return result or base


def _box_luck_table_from_distribution(distribution: dict[int, float], bin_width: int = 1) -> dict[str, Any]:
    """総価値分布を、上位%を二分探索で引けるよう昇順の値と累積確率に変換する"""
    values = sorted(distribution)
    cumulative: list[float] = []
    running = 0.0
    for value in values:
        running += distribution[value]
        cumulative.append(running)
    return {"values": values, "cumulative": cumulative, "total": running, "bin_width": bin_width}


def _build_box_luck_multi_table(single_table: dict[str, Any], open_count: int) -> dict[str, Any]:
    """1個分の判定テーブルから、同じボックスを open_count 個開けたときの総価値の判定テーブルを作る"""
    distribution: dict[int, float] = {}
    previous = 0.0
    for value, cumulative in zip(single_table["values"], single_table["cumulative"]):
        distribution[value] = cumulative - previous
        previous = cumulative
    bin_width = _box_luck_multi_bin_width(distribution)
    offset, probabilities = _box_luck_dense_power(_box_luck_dense(distribution, bin_width), open_count)
    return _box_luck_table_from_distribution(
        {
            (offset + index) * bin_width: probability
            for index, probability in enumerate(probabilities)
            if probability > 0
        },
        bin_width,
    )


def _build_box_luck_table(
    box_key: str,
    box: dict[str, Any],
    dynamic_targets: dict[str, Any] | None,
    open_count: int = 1,
) -> dict[str, Any]:
    """総価値分布の判定テーブル。複数個開封の場合は、同じボックスを open_count 個開けたときの総価値の分布を使う"""
    single_table = _box_luck_table_from_distribution(_build_box_luck_distribution(box_key, box, dynamic_targets))
    if open_count > 1:
        return _build_box_luck_multi_table(single_table, open_count)
    return single_table


def _box_luck_top_percent(table: dict[str, Any], total_value: int) -> float:
    """上位% = P(総価値 > 今回) + P(総価値 = 今回) / 2。同値が多い単発ドロップでも偏らないよう中央順位で扱う"""
    values = table["values"]
    cumulative = table["cumulative"]
    total = table["total"]
    if not values or total <= 0:
        return 50.0
    # ビンにまとめた分布では、今回の値も同じビンに丸めて比較する
    bin_width = int(table.get("bin_width") or 1)
    if bin_width > 1:
        total_value = int(round(total_value / bin_width)) * bin_width
    le_index = bisect.bisect_right(values, total_value)
    lt_index = bisect.bisect_left(values, total_value)
    probability_le = cumulative[le_index - 1] if le_index > 0 else 0.0
    probability_lt = cumulative[lt_index - 1] if lt_index > 0 else 0.0
    probability_gt = total - probability_le
    probability_eq = probability_le - probability_lt
    top_percent = (probability_gt + probability_eq / 2) / total * 100
    return min(100.0, max(0.01, top_percent))


def _box_luck_grade_from_top_percent(top_percent: float) -> str:
    for grade, max_top_percent in BOX_LUCK_GRADE_THRESHOLDS:
        if top_percent <= max_top_percent:
            return grade
    return BOX_LUCK_GRADE_THRESHOLDS[-1][0]


async def _get_box_luck_table(
    box_key: str,
    box: dict[str, Any],
    dynamic_targets: dict[str, Any] | None,
    open_count: int = 1,
) -> dict[str, Any]:
    cache_key = (box_key, open_count)
    data_mtime = DROP_BOXES_PATH.stat().st_mtime
    cached = _BOX_LUCK_TABLE_CACHE.get(cache_key)
    if cached and cached[0] > time.monotonic() and cached[1] == data_mtime:
        return cached[2]

    lock = _BOX_LUCK_TABLE_LOCKS.setdefault(cache_key, asyncio.Lock())
    async with lock:
        cached = _BOX_LUCK_TABLE_CACHE.get(cache_key)
        if cached and cached[0] > time.monotonic() and cached[1] == data_mtime:
            return cached[2]
        # 複数個開封は、1個分のテーブル(キャッシュ済みなら再利用)から求める
        single_table = await _get_box_luck_table(box_key, box, dynamic_targets) if open_count > 1 else None
        started_at = time.perf_counter()
        # 数十〜数百msかかることがあるため、イベントループを塞がないよう別スレッドで計算する
        if single_table is not None:
            table = await asyncio.to_thread(_build_box_luck_multi_table, single_table, open_count)
        else:
            table = await asyncio.to_thread(_build_box_luck_table, box_key, box, dynamic_targets)
        _BOX_LUCK_TABLE_CACHE[cache_key] = (time.monotonic() + BOX_LUCK_CACHE_TTL_SECONDS, data_mtime, table)
        logger.info(
            f"Built box luck table: box={box_key} open_count={open_count} support={len(table['values'])} "
            f"elapsed_ms={(time.perf_counter() - started_at) * 1000:.1f}"
        )
        return table


async def evaluate_box_luck(
    box_key: str,
    box: dict[str, Any],
    result: dict[str, Any],
    dynamic_targets: dict[str, Any] | None,
    open_count: int = 1,
) -> dict[str, Any]:
    """今回の開封結果(複数個開封の場合は全開封の合計)がそのボックスで上位何%か。価値そのものは返さない"""
    table = await _get_box_luck_table(box_key, box, dynamic_targets, open_count)
    total_value = sum(
        _reward_event_luck_value(event)
        for event in result.get("events", [])
        if isinstance(event, dict) and event.get("type") == "reward"
    )
    top_percent = _box_luck_top_percent(table, total_value)
    return {
        "grade": _box_luck_grade_from_top_percent(top_percent),
        "top_percent": round(top_percent, 4),
    }


def normalize_trophy_rewards_data(request: Request, raw_data: dict) -> dict:
    data = copy.deepcopy(raw_data)
    for reward_type in data.get("reward_types", {}).values():
        reward_type["icon"] = build_static_url(request, reward_type.get("icon"))
    return data


def build_static_url(request: Request, path: str | None) -> str:
    if not path:
        return str(request.url_for("static", path="images/ui/starrdrop.png"))
    if path.startswith("http://") or path.startswith("https://") or path.startswith("/static/"):
        return path
    normalized_path = path.lstrip("/")
    return str(request.url_for("static", path=normalized_path))


def get_skin_rarity_icon_path(rarity: int | None) -> str:
    if rarity is None or rarity == 0:
        return "/images/ui/skin_other.png"
    if rarity <= 10:
        return "/images/ui/skin_rare.png"
    if rarity <= 20:
        return "/images/ui/skin_super_rare.png"
    if rarity <= 30:
        return "/images/ui/skin_epic.png"
    if rarity <= 40:
        return "/images/ui/skin_mythic.png"
    if rarity <= 50:
        return "/images/ui/skin_legendary.png"
    if rarity <= 60:
        return "/images/ui/skin_hypercharge.png"
    if rarity <= 70:
        return "/images/ui/pro_pass.png"
    if rarity <= 80:
        return "/images/ui/skin_true_silver.png"
    return "/images/ui/skin_true_gold.png"


class ProfileImageJobCreateRequest(BaseModel):
    player_tag: str = Field(min_length=2, max_length=32)
    image_type: str = Field(min_length=1, max_length=64)
    orientation: str = Field(default="portrait", min_length=1, max_length=16)
    use_ticket: bool = False
    force_regenerate: bool = False


def _build_profile_image_cache_version(player: Player | None) -> str | None:
    if not player:
        return None
    if getattr(player, "last_updated_at", None):
        return format_utc_datetime(player.last_updated_at)
    return None


def _serialize_image_job_status(job: ImageGenerationJobData, *, ahead_count: int | None = None) -> dict:
    now = datetime.datetime.now(datetime.timezone.utc)
    min_wait_remaining_ms = 0
    is_ready_for_delivery = False
    if job.status == "completed":
        if job.min_wait_until and job.min_wait_until > now:
            min_wait_remaining_ms = max(0, int((job.min_wait_until - now).total_seconds() * 1000))
        else:
            is_ready_for_delivery = True

    payload = job.to_dict()
    payload["queue_ahead_count"] = ahead_count
    payload["queue_message_ja"] = f"あと{ahead_count}人待ち" if ahead_count is not None else None
    payload["queue_message_en"] = f"{ahead_count} ahead in queue" if ahead_count is not None else None
    payload["is_ready_for_delivery"] = is_ready_for_delivery
    payload["min_wait_remaining_ms"] = min_wait_remaining_ms
    payload["can_use_cached_image"] = job.is_cache_available(now)
    payload["can_regenerate"] = job.should_show_regenerate(now)
    payload["regenerate_available_at"] = (
        format_utc_datetime(job.completed_at + IMAGE_REGENERATE_AFTER)
        if job.completed_at
        else None
    )
    return payload


def _get_profile_image_platform(request: Request) -> str:
    platform = getattr(request.state, "platform", "web")
    return platform if platform in {"web", "ios", "android"} else "web"


def _get_profile_image_error_message(lang: str, key: str) -> str:
    messages = {
        "login_required": (
            "チケットを使った高速生成を利用するにはログインが必要です。",
            "You need to log in to use fast generation with tickets.",
        ),
        "invalid_image_type": (
            "未対応の画像タイプです。",
            "Unsupported image type.",
        ),
        "invalid_orientation": (
            "未対応の画像の向きです。",
            "Unsupported image orientation.",
        ),
        "invalid_player_tag": (
            "無効なプレイヤータグ形式です。",
            "Invalid player tag format.",
        ),
        "player_not_found": (
            "指定されたタグのプレイヤーは存在しないか、現在利用できません。",
            "Player with the specified tag does not exist or is currently unavailable.",
        ),
        "not_enough_tickets": (
            "チケットが足りないため、高速生成を開始できません。",
            "You do not have enough tickets to start fast generation.",
        ),
        "job_not_found": (
            "画像生成ジョブが見つかりません。",
            "Image generation job was not found.",
        ),
        "database_error": (
            "データベースエラーが発生しました。",
            "A database error occurred.",
        ),
        "unexpected_error": (
            "予期せぬエラーが発生しました。",
            "An unexpected error occurred.",
        ),
    }
    ja, en = messages[key]
    return ja if lang == "ja" else en


async def _resolve_profile_image_player(
    *,
    player_tag: str,
    db: asyncpg.Connection,
    force_refresh: bool,
) -> Player | None:
    player = await get_player_from_db(player_tag, db)
    if player and not player.is_invalid and not force_refresh and player.brawlers:
        return player

    player = await get_player(player_tag, db)
    if player.is_invalid:
        return None
    return player


async def _build_profile_image_player_entry(
    player_tag: str,
    db: asyncpg.Connection,
    *,
    is_main: bool = False,
    is_sub: bool = False,
) -> dict:
    name = player_tag
    icon_id = 0
    try:
        name_result = await get_player_name(player_tag, db)
        if name_result:
            name = name_result
        icon_result = await get_player_icon_from_db(player_tag, db)
        if icon_result is not None:
            icon_id = icon_result
    except Exception as e:
        logger.warning(f"プロフィール画像生成ツール用のプレイヤー表示情報取得に失敗: tag={player_tag}, error={e}")

    return {
        "tag": player_tag,
        "name": name,
        "icon_id": icon_id,
        "icon_url": f"/static/images/player_icon/{icon_id}.webp" if icon_id else "/static/images/player_icon/0.png",
        "is_main": is_main,
        "is_sub": is_sub,
    }


async def _build_profile_image_player_sources(
    current_user: User | None,
    db: asyncpg.Connection,
) -> tuple[list[dict], list[dict]]:
    if not current_user:
        return [], []

    bookmarked_players: list[dict] = []
    viewed_players: list[dict] = []
    bookmark_display_limit = 25
    viewed_display_limit = 25

    if current_user.main_account:
        bookmarked_players.append(
            await _build_profile_image_player_entry(current_user.main_account, db, is_main=True)
        )
    for player_tag in current_user.sub_accounts or []:
        bookmarked_players.append(await _build_profile_image_player_entry(player_tag, db, is_sub=True))

    bookmark_limit = min(
        bookmark_display_limit - 1,
        current_user.saved_accounts_limit if current_user.saved_accounts_limit else (bookmark_display_limit - 1),
    )
    for player_tag in current_user.saved_accounts[:bookmark_limit]:
        if current_user.is_own_player_tag(player_tag):
            continue
        bookmarked_players.append(await _build_profile_image_player_entry(player_tag, db))

    filtered_viewed_tags = [
        player_tag
        for player_tag in current_user.viewed_accounts
        if not current_user.is_own_player_tag(player_tag) and player_tag not in current_user.saved_accounts
    ]
    viewed_limit = min(
        viewed_display_limit,
        current_user.viewed_accounts_limit if current_user.viewed_accounts_limit else viewed_display_limit,
    )
    for player_tag in filtered_viewed_tags[:viewed_limit]:
        viewed_players.append(await _build_profile_image_player_entry(player_tag, db))

    return bookmarked_players, viewed_players


@router.get("/profile_image", name="profile_image_tool")
async def profile_image_tool(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db),
    tag: str | None = Query(None, description="事前選択するプレイヤータグ"),
    type: str | None = Query(None, description="初期表示する画像種別"),
):
    current_user: User | None = getattr(request.state, "current_user", None)
    bookmarked_players, viewed_players = await _build_profile_image_player_sources(current_user, db)

    selected_player: dict | None = None
    selected_tag = format_tag(tag) if tag else None
    if selected_tag and confirm_tag(selected_tag):
        try:
            player = await _resolve_profile_image_player(
                player_tag=selected_tag,
                db=db,
                force_refresh=False,
            )
            if player:
                selected_player = {
                    "tag": player.tag,
                    "name": player.name,
                    "icon_id": player.icon_id or 0,
                    "icon_url": f"/static/images/player_icon/{player.icon_id}.webp" if player.icon_id else "/static/images/player_icon/0.png",
                }
        except Exception as e:
            logger.warning(f"プロフィール画像生成ツールの初期プレイヤー取得に失敗: tag={selected_tag}, error={e}")

    image_type_options = [
        {
            "id": "standard_profile",
            "label_ja": "通常プロフィール",
            "label_en": "Standard Profile",
            "icon": "/images/ui/total_prestige.png",
            "is_app_only": False,
        },
        {
            "id": "highest_trophies",
            "label_ja": "最多トロフィー",
            "label_en": "Highest Trophies",
            "icon": "/images/ui/leaderboard_bottom.png",
            "is_app_only": False,
        },
        {
            "id": "current_trophies",
            "label_ja": "現在トロフィー",
            "label_en": "Current Trophies",
            "icon": "/images/ui/trophy.png",
            "is_app_only": False,
        },
        {
            "id": "power_progress",
            "label_ja": "育成状況",
            "label_en": "Power Progress",
            "icon": "/images/ui/power_point.png",
            "is_app_only": False,
        },
        {
            "id": "max_winstreak",
            "label_ja": "最大連勝数",
            "label_en": "Max Winstreak",
            "icon": "/images/ui/win_streak.png",
            "is_app_only": False,
        },
        {
            "id": "current_winstreak",
            "label_ja": "現在連勝数",
            "label_en": "Current Winstreak",
            "icon": "/images/ui/win_streak_blue.png",
            "is_app_only": False,
        },
        {
            "id": "legacy_mastery",
            "label_ja": "旧マスタリー",
            "label_en": "Legacy Mastery",
            "icon": "/images/ui/mastery_gold.png",
            "is_app_only": False,
        },
        {
            "id": "equipment_skins",
            "label_ja": "装備スキン一覧",
            "label_en": "Equipment Skins",
            "icon": "/images/ui/skin_super_rare.png",
            "is_app_only": True,
        },
    ]
    default_image_type = type if type in INITIAL_PROFILE_IMAGE_TYPES else "standard_profile"
    profile_image_tool_config = {
        "lang": lang,
        "validateUrl": f"/{lang}/validate/player-tag",
        "createJobUrl": f"/{lang}/tools/api/profile_image/jobs",
        "jobStatusUrlBase": f"/{lang}/tools/api/profile_image/jobs",
        "loginUrl": f"/{lang}/login?next=/{lang}/tools/profile_image",
        "isLoggedIn": bool(current_user),
        "isDeleteAds": bool(current_user.is_delete_ads) if current_user else False,
        "platform": getattr(request.state, "platform", "web"),
        "initialTickets": current_user.ad_skip_tickets if current_user else 0,
        "initialSelectedPlayer": selected_player,
        "bookmarkedPlayers": bookmarked_players,
        "viewedPlayers": viewed_players,
        "imageTypes": image_type_options,
        "landscapeOnlyImageTypes": sorted(LANDSCAPE_ONLY_PROFILE_IMAGE_TYPES),
        "defaultImageType": default_image_type,
    }

    context = {
        "request": request,
        "lang": lang,
        "current_page": "tools",
        "bookmarked_players": bookmarked_players,
        "viewed_players": viewed_players,
        "selected_player": selected_player,
        "default_image_type": default_image_type,
        "image_type_options": image_type_options,
        "profile_image_tool_config": profile_image_tool_config,
    }
    return templates.TemplateResponse("tools/profile_image.html", context)


@router.post("/api/profile_image/jobs", name="create_profile_image_job_api")
async def create_profile_image_job_api(
    request: Request,
    lang: str,
    payload: ProfileImageJobCreateRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
):
    current_user: User | None = getattr(request.state, "current_user", None)
    platform = _get_profile_image_platform(request)
    normalized_image_type = payload.image_type.strip()
    requested_orientation = payload.orientation.strip().lower()

    if normalized_image_type not in INITIAL_PROFILE_IMAGE_TYPES:
        return JSONResponse(
            {"success": False, "message": _get_profile_image_error_message(lang, "invalid_image_type")},
            status_code=400,
        )
    if requested_orientation not in {"portrait", "landscape"}:
        return JSONResponse(
            {"success": False, "message": _get_profile_image_error_message(lang, "invalid_orientation")},
            status_code=400,
        )
    if normalized_image_type in LANDSCAPE_ONLY_PROFILE_IMAGE_TYPES:
        requested_orientation = "landscape"
    if payload.use_ticket and not current_user:
        return JSONResponse(
            {"success": False, "message": _get_profile_image_error_message(lang, "login_required")},
            status_code=401,
        )

    normalized_tag = format_tag(payload.player_tag)
    if not confirm_tag(normalized_tag):
        return JSONResponse(
            {"success": False, "message": _get_profile_image_error_message(lang, "invalid_player_tag")},
            status_code=400,
        )
    consume_ticket = bool(payload.use_ticket and current_user and not current_user.is_delete_ads)
    is_fast_lane = bool((current_user and current_user.is_delete_ads) or payload.use_ticket)
    original_ticket_balance = current_user.ad_skip_tickets if current_user else None

    if consume_ticket and current_user and current_user.ad_skip_tickets < 1:
        return JSONResponse(
            {"success": False, "message": _get_profile_image_error_message(lang, "not_enough_tickets")},
            status_code=400,
        )

    try:
        player = await _resolve_profile_image_player(
            player_tag=normalized_tag,
            db=db,
            force_refresh=payload.force_regenerate,
        )
    except BrawlStarsAPIError as e:
        logger.warning(f"プロフィール画像生成対象プレイヤー取得に失敗: tag={normalized_tag}, error={e}")
        return JSONResponse(
            {"success": False, "message": _get_profile_image_error_message(lang, "player_not_found")},
            status_code=404,
        )
    except DataBaseError as e:
        logger.error(f"プロフィール画像生成対象プレイヤー取得中にデータベースエラー: tag={normalized_tag}, error={e}")
        return JSONResponse(
            {"success": False, "message": _get_profile_image_error_message(lang, "database_error")},
            status_code=500,
        )
    except Exception as e:
        logger.error(f"プロフィール画像生成対象プレイヤー取得中に予期せぬエラー: tag={normalized_tag}, error={e}", exc_info=True)
        return JSONResponse(
            {"success": False, "message": _get_profile_image_error_message(lang, "unexpected_error")},
            status_code=500,
        )

    if not player:
        return JSONResponse(
            {"success": False, "message": _get_profile_image_error_message(lang, "player_not_found")},
            status_code=404,
        )

    try:
        cache_key = build_image_generation_cache_key(
            player_tag=normalized_tag,
            image_type=normalized_image_type,
            orientation=requested_orientation,
            lang=lang,
            cache_version=_build_profile_image_cache_version(player),
        )
    except ValueError as e:
        logger.debug(f"プロフィール画像生成リクエストのバリデーションに失敗: {e}")
        key = "invalid_player_tag" if "プレイヤータグ" in str(e) else "invalid_image_type"
        if "向き" in str(e):
            key = "invalid_orientation"
        return JSONResponse({"success": False, "message": _get_profile_image_error_message(lang, key)}, status_code=400)

    try:
        cached_job = await get_latest_cached_image_generation_job(db, cache_key=cache_key)
        if cached_job and not payload.force_regenerate:
            # アドバンスミッション
            if current_user:
                await try_claim_tutorial_mission(current_user, db, "generate_profile_image")
                mission_mapping = {
                    "highest_trophies": "profile_image_highest_trophies",
                    "power_progress": "profile_image_upgrade_status",
                    "current_trophies": "profile_image_current_trophies",
                    "max_winstreak": "profile_image_max_winstreak"
                }
                if normalized_image_type in mission_mapping:
                    await current_user.check_and_claim_advance_mission(db, mission_mapping[normalized_image_type])
            return JSONResponse(
                {
                    "success": True,
                    "message": "キャッシュされた画像を返しました。" if lang == "ja" else "Returned a cached image.",
                    "used_cache": True,
                    "player_name": player.name,
                    "job": _serialize_image_job_status(cached_job),
                }
            )

        async with db.transaction():
            if consume_ticket and current_user:
                spent = await current_user.spend_tickets(db, 1)
                if not spent:
                    return JSONResponse(
                        {"success": False, "message": _get_profile_image_error_message(lang, "not_enough_tickets")},
                        status_code=400,
                    )

            job = await create_image_generation_job(
                db,
                player_tag=normalized_tag,
                platform=platform,
                lang=lang,
                image_type=normalized_image_type,
                orientation=requested_orientation,
                user_id=current_user.id if current_user else None,
                consume_ticket=consume_ticket,
                is_fast_lane=is_fast_lane,
                cache_key=cache_key,
                min_wait_until=get_image_job_min_wait_until(platform=platform, is_fast_lane=is_fast_lane),
            )
            
            # アドバンスミッション
            if current_user:
                await try_claim_tutorial_mission(current_user, db, "generate_profile_image")
                mission_mapping = {
                    "highest_trophies": "profile_image_highest_trophies",
                    "power_progress": "profile_image_upgrade_status",
                    "current_trophies": "profile_image_current_trophies",
                    "max_winstreak": "profile_image_max_winstreak"
                }
                if normalized_image_type in mission_mapping:
                    await current_user.check_and_claim_advance_mission(db, mission_mapping[normalized_image_type])

        ahead_count = await get_image_generation_jobs_ahead_count(db, job) if job.status == "queued" else None
        return JSONResponse(
            {
                "success": True,
                "message": "画像生成ジョブを受け付けました。" if lang == "ja" else "Image generation job has been queued.",
                "used_cache": False,
                "player_name": player.name,
                "ticket_consumed": consume_ticket,
                "new_ticket_balance": current_user.ad_skip_tickets if current_user else None,
                "job": _serialize_image_job_status(job, ahead_count=ahead_count),
            }
        )
    except DataBaseError as e:
        if consume_ticket and current_user is not None and original_ticket_balance is not None:
            current_user.ad_skip_tickets = original_ticket_balance
        logger.error(f"プロフィール画像生成ジョブ作成中にデータベースエラー: tag={normalized_tag}, error={e}")
        return JSONResponse(
            {"success": False, "message": _get_profile_image_error_message(lang, "database_error")},
            status_code=500,
        )
    except Exception as e:
        if consume_ticket and current_user is not None and original_ticket_balance is not None:
            current_user.ad_skip_tickets = original_ticket_balance
        logger.error(f"プロフィール画像生成ジョブ作成中に予期せぬエラー: tag={normalized_tag}, error={e}", exc_info=True)
        return JSONResponse(
            {"success": False, "message": _get_profile_image_error_message(lang, "unexpected_error")},
            status_code=500,
        )


@router.get("/api/profile_image/jobs/{job_id}", name="get_profile_image_job_api")
async def get_profile_image_job_api(
    request: Request,
    lang: str,
    job_id: int,
    db: asyncpg.Connection = Depends(get_shared_db),
):
    _ = request
    try:
        job = await get_image_generation_job(db, job_id)
        if not job:
            return JSONResponse(
                {"success": False, "message": _get_profile_image_error_message(lang, "job_not_found")},
                status_code=404,
            )

        ahead_count = await get_image_generation_jobs_ahead_count(db, job) if job.status == "queued" else None
        return JSONResponse(
            {
                "success": True,
                "job": _serialize_image_job_status(job, ahead_count=ahead_count),
            }
        )
    except DataBaseError as e:
        logger.error(f"プロフィール画像生成ジョブ状態取得中にデータベースエラー: job_id={job_id}, error={e}")
        return JSONResponse(
            {"success": False, "message": _get_profile_image_error_message(lang, "database_error")},
            status_code=500,
        )
    except Exception as e:
        logger.error(f"プロフィール画像生成ジョブ状態取得中に予期せぬエラー: job_id={job_id}, error={e}", exc_info=True)
        return JSONResponse(
            {"success": False, "message": _get_profile_image_error_message(lang, "unexpected_error")},
            status_code=500,
        )


def normalize_drop_boxes_data(request: Request, raw_data: dict, skins_dict: dict[int, object] | None = None) -> dict:
    data = copy.deepcopy(raw_data)

    for reward_type in data.get("reward_types", {}).values():
        reward_type["icon"] = build_static_url(request, reward_type.get("icon"))
        reward_type["icons"] = [build_static_url(request, path) for path in reward_type.get("icons", [])]
        # 確率表用の省略表示アイコン。"..." は省略記号としてそのまま渡す
        if reward_type.get("table_icons"):
            reward_type["table_icons"] = [
                path if path == "..." else build_static_url(request, path)
                for path in reward_type["table_icons"]
            ]

    # Preserve explicit reward_types ordering for frontend use
    data["reward_types_order"] = list(data.get("reward_types", {}).keys())

    for box in data.get("boxes", {}).values():
        box["icon"] = build_static_url(request, box.get("icon"))
        for reward in box.get("rewards", []):
            normalize_skin_targets(request, reward, skins_dict)
            normalize_icon_targets(request, reward)
            normalize_pin_targets(request, reward)
            normalize_spray_targets(request, reward)
            normalize_brawler_targets(request, reward)
        for reward in box.get("premium_rewards", []):
            normalize_skin_targets(request, reward, skins_dict)
            normalize_icon_targets(request, reward)
            normalize_pin_targets(request, reward)
            normalize_spray_targets(request, reward)
            normalize_brawler_targets(request, reward)
        for rarity in box.get("rarities", {}).values():
            for reward in rarity.get("rewards", []):
                normalize_skin_targets(request, reward, skins_dict)
                normalize_icon_targets(request, reward)
                normalize_pin_targets(request, reward)
                normalize_spray_targets(request, reward)
                normalize_brawler_targets(request, reward)
            for reward in rarity.get("premium_rewards", []):
                normalize_skin_targets(request, reward, skins_dict)
                normalize_icon_targets(request, reward)
                normalize_pin_targets(request, reward)
                normalize_spray_targets(request, reward)
                normalize_brawler_targets(request, reward)

    return data


def normalize_skin_targets(request: Request, reward: dict, skins_dict: dict[int, object] | None = None) -> None:
    for skin in reward.get("skin_targets", []):
        skin_id = skin.get("id")
        rarity = skin.get("rarity")
        if skins_dict and skin_id in skins_dict:
            skin_data = skins_dict[skin_id]
            skin["name_ja"] = skin_data.ja or skin_data.en or skin.get("name_ja") or f"スキン {skin_id}"
            skin["name_en"] = skin_data.en or skin_data.ja or skin.get("name_en") or f"Skin {skin_id}"
            skin["brawler_id"] = skin_data.brawler_id or skin.get("brawler_id", 0)
            rarity = skin_data.rarity
        skin["rarity"] = rarity
        skin["rarity_icon"] = build_static_url(request, get_skin_rarity_icon_path(rarity))
        skin["image"] = build_static_url(
            request,
            skin.get("image") or f"/images/skins/{skin.get('id')}.webp",
        )
        skin["fallback_image"] = build_static_url(request, skin.get("fallback_image") or "/images/ui/mystery.png")


def normalize_brawler_targets(request: Request, reward: dict) -> None:
    """出現するキャラが1人に決まっている報酬(新キャラのボックス等)は、レア度のアイコンの代わりにそのキャラのアイコンを使う。
    アイコン画像が未準備の場合は付けず、レア度のアイコンのままにする"""
    targets = reward.get("brawler_targets") or []
    for brawler in targets:
        icon_path = f"/images/brawler_pins/{brawler.get('id')}.png"
        brawler["image"] = build_static_url(request, icon_path) if (STATIC_DIR / icon_path.lstrip("/")).is_file() else None
    if len(targets) == 1 and targets[0].get("image"):
        reward["icon"] = targets[0]["image"]


def normalize_spray_targets(request: Request, reward: dict) -> None:
    for spray in reward.get("spray_targets", []):
        spray_id = spray.get("id")
        spray["name_ja"] = spray.get("name_ja") or spray.get("name_en") or "スプレー"
        spray["name_en"] = spray.get("name_en") or spray.get("name_ja") or "Spray"
        spray["image"] = build_static_url(
            request,
            spray.get("image") or f"/images/spray/{spray_id}.webp",
        )
        spray["fallback_image"] = build_static_url(request, spray.get("fallback_image") or "/images/ui/sprays.webp")


def normalize_icon_targets(request: Request, reward: dict) -> None:
    for icon in reward.get("icon_targets", []):
        icon_id = icon.get("id")
        icon["name_ja"] = icon.get("name_ja") or icon.get("name_en") or "プロフィールアイコン"
        icon["name_en"] = icon.get("name_en") or icon.get("name_ja") or "Profile Icon"
        icon["image"] = build_static_url(
            request,
            icon.get("image") or f"/images/player_icon/{icon_id}.webp",
        )
        icon["fallback_image"] = build_static_url(request, icon.get("fallback_image") or "/images/player_icon/0.png")


def normalize_pin_targets(request: Request, reward: dict) -> None:
    for pin in reward.get("pin_targets", []):
        pin_id = pin.get("id")
        pin["name_ja"] = pin.get("name_ja") or pin.get("name_en") or "ピンズ"
        pin["name_en"] = pin.get("name_en") or pin.get("name_ja") or "Pin"
        pin["image"] = build_static_url(
            request,
            pin.get("image") or f"/images/pins/{pin_id}.webp",
        )
        pin["fallback_image"] = build_static_url(request, pin.get("fallback_image") or "/images/ui/mystery.png")


#* /---*---*---*---*---*---*---*---*/
#* ツールタブメインページ
#* /---*---*---*---*---*---*---*---*/
@router.get("", name="tools")
async def tools(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db),
):
    
    # テンプレートに渡すコンテキスト
    context = {
        "request": request,
        "lang": lang,
        "current_page": "tools"
    }
    # [この部分は公開用リポジトリでは非公開にされています]

    try:
        return templates.TemplateResponse("tools/tools.html", context)
    except Exception as render_err: # テンプレートレンダリングエラーも捕捉
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")

# [この部分は公開用リポジトリでは非公開にされています]
