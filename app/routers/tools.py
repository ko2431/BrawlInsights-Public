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
from app.core.admin_permissions import is_admin_request
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
# [この部分は公開用リポジトリでは非公開にされています]

# [この部分は公開用リポジトリでは非公開にされています]

    # テンプレートに渡すコンテキスト
    context = {
        "request": request,
        "lang": lang,
        "user": user,
        "main_account": main_account,
        "analysis": analysis,
        "grouped_matchups": grouped_matchups,
        "pool_data": pool_data,
        "brawler": brawler_data,
        "accessory_stats": accessory_stats,
        "bsinfo_accessory_levels": bsinfo_accessory_levels,
        "accessory_db_fallback": accessory_db_fallback,
        "start_date": format_utc_date(start_date) if start_date else None,
        "end_date": format_utc_date(end_date) if end_date else None,
        "use_cache": use_cache,
        "brawler_thread_id": brawler_thread_id,
        "brawler_preview_messages": brawler_preview_messages,
    }
    nav_ctx = resolve_nav_context(
        lang=lang,
        page_kind="brawler_guide",
        tab=tab,
        from_source=from_source,
        is_stats_tab=is_stats_tab,
        is_tools_tab=is_tools_tab,
        player=player,
        battles_tab=battles_tab,
        map_id=map_id,
    )
    context.update(nav_template_vars(nav_ctx))
    await try_claim_tutorial_mission(user, db, "view_brawler_guide")

    try:
        return templates.TemplateResponse("tools/brawler_guide.html", context)
    except Exception as render_err: # テンプレートレンダリングエラーも捕捉
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")


#* /---*---*---*---*---*---*---*---*/
#* キャラクタールーレット
#* /---*---*---*---*---*---*---*---*/
@router.get("/random_brawler", name="random_brawler")
async def random_brawler(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    try:
        # キャラクターの一覧を取得
        brawlers = await get_available_brawlers(db)
    except Exception as e:
        logger.error(f"Error in random_brawler: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    
    #^ 登録アカウント(メイン・サブ)ごとの所持キャラ。画面上で即座に切り替えられるよう、まとめて渡す
    user: User | None = getattr(request.state, "current_user", None)
    player_account_options: list[dict] = []
    owned_ids_by_account: dict[str, list[int]] = {}
    power11_ids_by_account: dict[str, list[int]] = {}

    if user:
        try:
            player_account_options = await build_player_account_options(user, db)
            account_data = await get_player_accounts_brawler_data([o["tag"] for o in player_account_options], db)
            for tag, data in account_data.items():
                owned_ids_by_account[tag] = [b["id"] for b in data["brawlers"]]
                power11_ids_by_account[tag] = [b["id"] for b in data["brawlers"] if b["power"] >= 11]
            # プレイヤーデータがないアカウントは選択肢に出さない
            player_account_options = [o for o in player_account_options if o["tag"] in owned_ids_by_account]
        except Exception as e:
            logger.debug(f"{user.name}の登録アカウントのプレイヤーデータ取得中にエラーが発生しました: {e}", exc_info=True)

    # テンプレートに渡すコンテキスト
    context = {
        "request": request,
        "lang": lang,
        "brawlers": brawlers,
        "user": user,
        "player_account_options": player_account_options,
        "owned_ids_by_account": owned_ids_by_account,
        "power11_ids_by_account": power11_ids_by_account,
        "current_page": "tools",
    }

    try:
        return templates.TemplateResponse("tools/random_brawler.html", context)
    except Exception as render_err:
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")


#* /---*---*---*---*---*---*---*---*/
#* 育成計算機
#* /---*---*---*---*---*---*---*---*/
# パワーレベルごとの、最大パワーまでに必要な (パワーポイント, コイン)
_REMAINING_POWER_COSTS: dict[int, tuple[int, int]] = {
    1: (3740, 7765),
    2: (3720, 7745),
    3: (3690, 7710),
    4: (3640, 7635),
    5: (3560, 7495),
    6: (3430, 7205),
    7: (3220, 6725),
    8: (2880, 5925),
    9: (2330, 4675),
    10: (1440, 2800),
}


def _calc_required_power_costs(powers: list[int]) -> tuple[int, int]:
    """所持キャラのパワーレベルから、全キャラを最大パワーにするのに必要なパワーポイントとコインを返す。"""
    required_pps = 0
    required_coins = 0
    for power in powers:
        pps, coins = _REMAINING_POWER_COSTS.get(power, (0, 0))
        required_pps += pps
        required_coins += coins
    return required_pps, required_coins


@router.get("/cost_calc", name="cost_calc")
async def cost_calc(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db) # DB接続が必要な場合
):
    user: User | None = getattr(request.state, "current_user", None)
    num_of_available_brawlers: int | None = None
    cost_accounts: list[dict] = []

    # ログイン済みの場合は、登録アカウント(メイン・サブ)ごとのデータをまとめて取得する
    if user:
        try:
            player_account_options = await build_player_account_options(user, db)
            tags = [o["tag"] for o in player_account_options]
            account_data = await get_player_accounts_brawler_data(tags, db)
            if user.main_account not in account_data:
                # メインアカウントがDBにない場合はAPIから取得する (取得時にDBへ保存される)
                try:
                    if await get_player(user.main_account, db):
                        account_data.update(await get_player_accounts_brawler_data([user.main_account], db))
                except BrawlStarsAPIError as e:
                    logger.debug(f"{user.name}のメインアカウントのプレイヤーデータ取得中にAPIエラーが発生しました: {e}。スキップします。", exc_info=True)
            for option in player_account_options:
                data = account_data.get(option["tag"])
                if not data:
                    continue
                required_pps, required_coins = _calc_required_power_costs([b["power"] for b in data["brawlers"]])
                cost_accounts.append({
                    "tag": option["tag"],
                    "name": data["name"],
                    "required_pps": required_pps,
                    "required_coins": required_coins,
                    "gadgets": data["gadgets"],
                    "star_powers": data["star_powers"],
                    "gears": data["gears"],
                    "hyper_charges": data["hyper_charges"],
                    "buffies": data["buffies"],
                    "brawlers": [
                        {"gadget": b["gadget"], "starPower": b["star_power"], "gear": b["gear"]}
                        for b in data["brawlers"]
                    ],
                })
        except Exception as e:
            logger.debug(f"{user.name}の登録アカウントのプレイヤーデータ取得中にエラーが発生しました: {e}", exc_info=True)

    try:
        num_of_available_brawlers = await calc_num_of_available_brawlers(db)
    except Exception as e:
        logger.error(f"Error getting num_of_available_brawlers: {e}", exc_info=True)
        num_of_available_brawlers = 85 # fallback
    
    # 現在のアクセサリの最大数を取得
    max_accessory_counts = await get_max_accessory_counts(db)
    
    # テンプレートに渡すコンテキスト
    context = {
        "request": request,
        "lang": lang,
        "cost_accounts": cost_accounts,
        "num_of_available_brawlers": num_of_available_brawlers,
        "max_accessory_counts": max_accessory_counts,
        "current_page": "tools"
    }

    try:
        return templates.TemplateResponse("tools/cost_calc.html", context)
    except Exception as render_err: # テンプレートレンダリングエラーも捕捉
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")


#* /---*---*---*---*---*---*---*---*/
#* 報酬量計算機
#* /---*---*---*---*---*---*---*---*/
@router.get("/reward_calc", name="reward_calc")
async def reward_calc(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db) # DB接続が必要な場合
):
    # テンプレートに渡すコンテキスト
    context = {
        "request": request,
        "lang": lang,
        "current_page": "tools"
    }

    try:
        return templates.TemplateResponse("tools/reward_calc.html", context)
    except Exception as render_err: # テンプレートレンダリングエラーも捕捉
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")

@router.get("/trophy_reward_calc", name="trophy_reward_calc")
async def trophy_reward_calc(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db),
):
    trophy_rewards_data = normalize_trophy_rewards_data(request, load_trophy_rewards_data())

    context = {
        "request": request,
        "lang": lang,
        "trophy_rewards_data": trophy_rewards_data,
        "current_page": "tools",
    }

    try:
        return templates.TemplateResponse("tools/trophy_reward_calc.html", context)
    except Exception as render_err:
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")

@router.get("/starrdrop_calc", name="starrdrop_calc")
async def starrdrop_calc(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db), # DB接続が必要な場合
    box: str | None = Query(None, description="初期表示するドロップ/ボックス")
):
    drop_boxes_data = normalize_drop_boxes_data(request, load_drop_boxes_data())

    # テンプレートに渡すコンテキスト
    context = {
        "request": request,
        "lang": lang,
        "drop_boxes_data": drop_boxes_data,
        "initial_box": box,
        "current_page": "tools"
    }

    try:
        return templates.TemplateResponse("tools/starrdrop_calc.html", context)
    except Exception as render_err: # テンプレートレンダリングエラーも捕捉
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")


# ボックスシミュレーターの所持状況(排出済みのアイテム)。サーバーでは保持せず、端末に保存したものを開封のたびに受け取る。
# 種類ごとに「基準ID からのずれ」をビット位置にしたビットセットを base64 にして送り合う(全アイテムを出し切っても約1.5KB)
BOX_INVENTORY_VERSION = 1
BOX_INVENTORY_ID_BASES: dict[str, int] = {
    "brawler": 16000000,
    "skin": 29000000,
    "gadget": 23000000,
    "starPower": 23000000,
    "hypercharge": 23000000,
    "buffy": 29000000,
    "pin": 52000000,
    "playerIcon": 28000000,
    "spray": 68000000,
    "nanoPower": 16000000,
    "fusion": 16000000,
}
# 種類ごとのビット数の上限(2KB)。不正に大きなデータを受け取らないようにする
BOX_INVENTORY_MAX_BITS = 1 << 14
BOX_INVENTORY_MAX_ENCODED_LENGTH = (BOX_INVENTORY_MAX_BITS // 8 + 2) // 3 * 4


def _encode_box_inventory(picked_target_keys: set[tuple[str, Any]]) -> dict[str, Any]:
    bitsets: dict[str, bytearray] = {}
    for kind, target_id in picked_target_keys:
        base = BOX_INVENTORY_ID_BASES.get(kind)
        if base is None or not isinstance(target_id, int):
            continue
        offset = target_id - base
        if not 0 <= offset < BOX_INVENTORY_MAX_BITS:
            continue
        bitset = bitsets.setdefault(kind, bytearray())
        byte_index = offset // 8
        if len(bitset) <= byte_index:
            bitset.extend(b"\x00" * (byte_index + 1 - len(bitset)))
        bitset[byte_index] |= 1 << (offset % 8)

    items = {
        kind: base64.urlsafe_b64encode(bytes(bitset)).decode("ascii").rstrip("=")
        for kind, bitset in bitsets.items()
    }
    upgrades = {
        str(brawler_id): level
        for brawler_id, level in _get_starrnova_upgrade_levels(picked_target_keys).items()
        if level > BOX_STARRNOVA_UPGRADE_START_LEVEL
    }
    return {"v": BOX_INVENTORY_VERSION, "items": items, "upgrades": upgrades}


def _decode_box_inventory(raw: Any) -> set[tuple[str, Any]]:
    """端末から受け取った所持状況を排出済みのキーの集合に戻す。形式が不正な部分は無視する"""
    picked_target_keys: set[tuple[str, Any]] = set()
    if not isinstance(raw, dict) or raw.get("v") != BOX_INVENTORY_VERSION:
        return picked_target_keys

    items = raw.get("items")
    if isinstance(items, dict):
        for kind, encoded in items.items():
            base = BOX_INVENTORY_ID_BASES.get(kind)
            if base is None or not isinstance(encoded, str) or len(encoded) > BOX_INVENTORY_MAX_ENCODED_LENGTH:
                continue
            try:
                bitset = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            except (ValueError, TypeError):
                continue
            for byte_index, byte in enumerate(bitset):
                if not byte:
                    continue
                for bit in range(8):
                    if byte & (1 << bit):
                        picked_target_keys.add((kind, base + byte_index * 8 + bit))

    upgrades = raw.get("upgrades")
    if isinstance(upgrades, dict):
        for brawler_id_text, level in upgrades.items():
            if not str(brawler_id_text).isdigit() or not isinstance(level, int):
                continue
            brawler_id = int(brawler_id_text)
            if brawler_id not in BOX_STARRNOVA_UPGRADE_SKIN_IDS:
                continue
            level = min(level, BOX_STARRNOVA_UPGRADE_MAX_LEVEL)
            if level > BOX_STARRNOVA_UPGRADE_START_LEVEL:
                picked_target_keys.add((BOX_STARRNOVA_UPGRADE_KIND, f"{brawler_id}:{level}"))
    return picked_target_keys


# 所持状況パネルの項目の並び順
BOX_INVENTORY_STAT_KINDS: list[str] = [
    "brawler", "skin", "gadget", "starPower", "hypercharge", "buffy",
    "pin", "playerIcon", "spray", "nanoPower", "fusion", BOX_STARRNOVA_UPGRADE_KIND,
]
# 抽選対象の総数は全ボックスの全報酬から集めるため重く、一定時間使い回す
BOX_INVENTORY_UNIVERSE_TTL_SECONDS = 600
_box_inventory_universe_cache: dict[str, Any] = {"expires_at": 0.0, "value": None}


def _get_reward_target_universe(reward: dict[str, Any], dynamic_targets: dict[str, Any]) -> tuple[str, set[Any]] | None:
    """報酬1つ分の抽選候補 (kind, IDの集合)。シミュレーターの抽選と同じ条件で候補を集める"""
    reward_type = str(reward.get("type") or "")
    if reward_type.endswith("Skins"):
        return "skin", {skin.id for skin in _get_skin_target_candidates(reward, reward_type, dynamic_targets)}
    if "Pins" in reward_type:
        explicit_candidates, general_candidates = _get_pin_target_candidates(reward, reward_type, dynamic_targets)
        return "pin", {pin.id for pin, _target in explicit_candidates} | {pin.id for pin in general_candidates}
    if reward_type == "profileIcons":
        icons_by_id = dynamic_targets.get("player_icons_by_id", {})
        explicit_ids = {
            int(target["id"]) for target in reward.get("icon_targets", [])
            if isinstance(target, dict) and str(target.get("id", "")).isdigit() and int(target["id"]) in icons_by_id
        }
        if explicit_ids:
            return "playerIcon", explicit_ids
        return "playerIcon", {
            icon.id for icon in dynamic_targets.get("player_icons", [])
            if _is_general_player_icon(icon) and _reward_price_matches(icon, reward)
        }
    if reward_type == "sprays":
        sprays_by_id = dynamic_targets.get("sprays_by_id", {})
        explicit_ids = {
            int(target["id"]) for target in reward.get("spray_targets", [])
            if isinstance(target, dict) and str(target.get("id", "")).isdigit() and int(target["id"]) in sprays_by_id
        }
        if explicit_ids:
            return "spray", explicit_ids
        return "spray", {spray.id for spray in dynamic_targets.get("sprays", []) if _is_general_spray(spray)}
    if reward_type.endswith("Brawlers"):
        target_ids = {
            int(target["id"]) for target in reward.get("brawler_targets", [])
            if isinstance(target, dict) and str(target.get("id", "")).isdigit()
        }
        return "brawler", {
            brawler.id for brawler in dynamic_targets.get("brawlers", [])
            if (
                brawler.id in target_ids and getattr(brawler, "is_temporary", False) is not True
                if target_ids
                else _is_box_brawler_candidate(brawler, reward_type)
            )
        }
    if reward_type in {"gadgets", "starPowers", "hypercharges"}:
        accessory_type, kind = {
            "gadgets": ("gadget", "gadget"),
            "starPowers": ("starPower", "starPower"),
            "hypercharges": ("hyperCharge", "hypercharge"),
        }[reward_type]
        return kind, {
            accessory.id for accessory in dynamic_targets.get("accessories", [])
            if getattr(accessory, "type", None) == accessory_type and getattr(accessory, "is_invalid", False) is not True
        }
    if reward_type == "buffies":
        return "buffy", {buffy.id for buffy in _get_buffy_target_candidates(dynamic_targets)}
    if reward_type in BOX_BRAWLER_BOOST_CONFIGS:
        return BOX_BRAWLER_BOOST_CONFIGS[reward_type]["kind"], {
            brawler.id for brawler in _get_brawler_boost_target_candidates(reward_type, dynamic_targets)
        }
    return None


def _iter_box_rewards(box: dict[str, Any]):
    yield from box.get("rewards", [])
    yield from box.get("premium_rewards", [])
    for rarity in (box.get("rarities") or {}).values():
        if isinstance(rarity, dict):
            yield from rarity.get("rewards", [])
            yield from rarity.get("premium_rewards", [])


def _get_box_inventory_universe(raw_data: dict[str, Any], dynamic_targets: dict[str, Any]) -> dict[str, set[Any]]:
    """シミュレーターで開封できる全ボックスの全報酬について、抽選候補を種類ごとに合わせた集合(=所持状況の総数)"""
    now = time.monotonic()
    cached = _box_inventory_universe_cache.get("value")
    if cached is not None and now < _box_inventory_universe_cache.get("expires_at", 0.0):
        return cached

    universe: dict[str, set[Any]] = {}
    seen_signatures: set[str] = set()
    has_starrnova_upgrade = False
    for box in _get_simulatable_boxes(raw_data).values():
        for reward in _iter_box_rewards(box):
            if not isinstance(reward, dict):
                continue
            if reward.get("type") == "starrnovaSpike":
                has_starrnova_upgrade = True
                continue
            # 確率や数量だけが違う同じ条件の報酬は、候補も同じなので1回だけ集める
            signature = json.dumps(
                {key: value for key, value in reward.items() if key not in {"probability", "amount"}},
                sort_keys=True,
                default=str,
            )
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)
            reward_universe = _get_reward_target_universe(reward, dynamic_targets)
            if reward_universe:
                kind, target_ids = reward_universe
                universe.setdefault(kind, set()).update(target_ids)
    if has_starrnova_upgrade:
        universe[BOX_STARRNOVA_UPGRADE_KIND] = set()

    _box_inventory_universe_cache["value"] = universe
    _box_inventory_universe_cache["expires_at"] = now + BOX_INVENTORY_UNIVERSE_TTL_SECONDS
    return universe


def _build_box_inventory_stats(
    picked_target_keys: set[tuple[str, Any]],
    dynamic_targets: dict[str, Any],
    raw_data: dict[str, Any],
) -> list[dict[str, Any]]:
    """所持状況パネル用の集計。total はいずれかのボックスで抽選対象になり得るアイテムの数"""
    universe = _get_box_inventory_universe(raw_data, dynamic_targets)
    owned_ids: dict[str, set[Any]] = {}
    for kind, target_id in picked_target_keys:
        owned_ids.setdefault(kind, set()).add(target_id)

    stats: list[dict[str, Any]] = []
    for kind in BOX_INVENTORY_STAT_KINDS:
        if kind not in universe:
            continue
        if kind == BOX_STARRNOVA_UPGRADE_KIND:
            upgrade_levels = _get_starrnova_upgrade_levels(picked_target_keys)
            stats.append({
                "key": kind,
                "owned": sum(level - BOX_STARRNOVA_UPGRADE_START_LEVEL for level in upgrade_levels.values()),
                "total": (BOX_STARRNOVA_UPGRADE_MAX_LEVEL - BOX_STARRNOVA_UPGRADE_START_LEVEL) * len(upgrade_levels),
            })
            continue
        stats.append({
            "key": kind,
            "owned": len(owned_ids.get(kind, set()) & universe[kind]),
            "total": len(universe[kind]),
        })
    return stats


def _annotate_box_inventory_deltas(
    result: dict[str, Any],
    owned_before_open: set[tuple[str, Any]],
    universe: dict[str, set[Any]],
) -> None:
    """新しく排出されたアイテムの報酬に、排出状況へ追加する内容(inventory_delta)を付ける。
    counted は所持状況パネルの集計(いずれかのボックスで排出対象のもの)に数えるかどうか"""
    for event in result.get("events", []):
        target_item = event.get("target_item")
        if event.get("type") != "reward" or event.get("substitute_for") or not isinstance(target_item, dict):
            continue
        kind = target_item.get("kind")
        target_id = target_item.get("id")
        if not kind or target_id is None or (kind, target_id) in owned_before_open:
            continue
        event["inventory_delta"] = {
            "kind": kind,
            "id": target_id,
            "counted": kind == BOX_STARRNOVA_UPGRADE_KIND or target_id in universe.get(kind, set()),
        }


async def _load_box_simulator_dynamic_targets(db: asyncpg.Connection) -> dict[str, Any]:
    """具体的なアイテムの抽選に使うマスターデータ(いずれもキャッシュされている)"""
    all_skins = await get_all_skins(db)
    all_pins = await get_all_pins(db)
    all_player_icons = await get_all_player_icons(db)
    all_sprays = await get_all_sprays(db)
    all_accessories = await get_all_accessories(db)
    all_buffies = await get_all_buffies(db)
    all_brawlers = await get_available_brawlers(db)
    return {
        "skins_by_id": all_skins,
        "skins": list(all_skins.values()),
        "pins_by_id": all_pins,
        "pins": list(all_pins.values()),
        "player_icons_by_id": all_player_icons,
        "player_icons": list(all_player_icons.values()),
        "sprays_by_id": all_sprays,
        "sprays": list(all_sprays.values()),
        "accessories": list(all_accessories.values()),
        "buffies": list(all_buffies.values()),
        "brawlers": all_brawlers,
        "default_skin_ids": _get_default_skin_ids(all_skins.values()),
    }


def _build_player_owned_target_keys(player: Player, dynamic_targets: dict[str, Any]) -> set[tuple[str, Any]]:
    """メインアカウントの所持状況を排出済みのキーにする(ピンズ等の所持状況が取得できないものは含まない)"""
    buffy_ids_by_brawler_type = {
        (buffy.brawler_id, buffy.type): buffy.id
        for buffy in dynamic_targets.get("buffies", [])
        if getattr(buffy, "type", None) in BOX_BUFFY_TYPES
    }
    picked_target_keys: set[tuple[str, Any]] = set()
    for brawler in player.brawlers or []:
        brawler_id = getattr(brawler, "id", None)
        if not isinstance(brawler_id, int):
            continue
        picked_target_keys.add(("brawler", brawler_id))
        for skin_id in getattr(brawler, "owned_skin_ids", None) or []:
            picked_target_keys.add(("skin", skin_id))
        if getattr(brawler, "skin_id", None):
            picked_target_keys.add(("skin", brawler.skin_id))
        for kind, attribute in (("gadget", "gadget_ids"), ("starPower", "star_power_ids"), ("hypercharge", "hyper_charge_ids")):
            for accessory_id in getattr(brawler, attribute, None) or []:
                picked_target_keys.add((kind, accessory_id))
        for buffy_type, attribute in (("gadget", "buffie_gadget"), ("starPower", "buffie_star_power"), ("hypercharge", "buffie_hyper_charge")):
            buffy_id = buffy_ids_by_brawler_type.get((brawler_id, buffy_type))
            if getattr(brawler, attribute, False) and buffy_id is not None:
                picked_target_keys.add(("buffy", buffy_id))
    return picked_target_keys


@router.get("/box_simulator", name="box_simulator")
async def box_simulator(
    request: Request,
    lang: str,
    box: str | None = Query(None, description="初期表示するドロップ/ボックス"),
):
    raw_data = load_drop_boxes_data()
    simulatable_boxes = _get_simulatable_boxes(raw_data)
    drop_boxes_data = normalize_drop_boxes_data(
        request,
        {
            **raw_data,
            "boxes": simulatable_boxes,
        },
    )

    # 所持状況の読み込み元として選べる登録アカウント。サブアカウントがある場合のみDBから名前を取得する
    user: User | None = getattr(request.state, "current_user", None)
    player_account_options: list[dict] = []
    if user and user.sub_accounts:
        try:
            async with get_db_connection_for_bg_task() as db:
                player_account_options = await build_player_account_options(user, db)
        except Exception as e:
            logger.warning(f"ボックスシミュレーターの登録アカウント一覧の取得に失敗しました: user={user.id} error={e}")

    context = {
        "request": request,
        "lang": lang,
        "player_account_options": player_account_options,
        "drop_boxes_data": drop_boxes_data,
        "initial_box": box,
        "max_open_count": BOX_SIM_MAX_OPEN_COUNT,
        # 排出状況のビットセットを端末側で更新するための基準ID
        "inventory_id_bases": BOX_INVENTORY_ID_BASES,
        "inventory_max_bits": BOX_INVENTORY_MAX_BITS,
        "current_page": "tools",
    }

    try:
        return templates.TemplateResponse("tools/box_simulator.html", context)
    except Exception as render_err:
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")


@router.post("/api/box_simulator/open", name="box_simulator_open_api")
async def box_simulator_open_api(
    request: Request,
    lang: str,
    box: str = Query(..., description="開封するドロップ/ボックス"),
    count: int = Query(1, description="まとめて開封する個数(ドロップのみ)"),
    payload: dict[str, Any] | None = Body(None),
    db: asyncpg.Connection = Depends(get_shared_db),
):
    try:
        raw_data = load_drop_boxes_data()
        simulatable_boxes = _get_simulatable_boxes(raw_data)
        if box not in simulatable_boxes:
            return JSONResponse({"success": False, "message": "Box not found"}, status_code=404)
        open_count = max(1, min(BOX_SIM_MAX_OPEN_COUNT, count))
        dynamic_targets = await _load_box_simulator_dynamic_targets(db)
        # 所持状況を引き継ぐ場合は、これまでの排出済みのアイテムを入手済みとして抽選する
        persist_inventory = isinstance(payload, dict) and payload.get("persist") is True
        picked_target_keys = _decode_box_inventory(payload.get("inventory")) if persist_inventory else set()
        # 抽選で排出済みの集合に追記されるため、開封前の状態を控えておく
        owned_before_open = set(picked_target_keys)
        simulation_data = {
            **raw_data,
            "boxes": simulatable_boxes,
        }
        if open_count > 1:
            result = simulate_drop_box_multi_open(
                request,
                simulation_data,
                box,
                open_count,
                dynamic_targets=dynamic_targets,
                picked_target_keys=picked_target_keys,
            )
        else:
            result = simulate_drop_box_open(
                request,
                simulation_data,
                box,
                dynamic_targets=dynamic_targets,
                picked_target_keys=picked_target_keys,
            )
        # 運の良さ判定に失敗しても開封結果は返す
        try:
            result["luck"] = await evaluate_box_luck(box, simulatable_boxes[box], result, dynamic_targets, open_count)
        except Exception as luck_err:
            logger.warning(f"Failed to evaluate box luck: box={box} open_count={open_count} error={luck_err}", exc_info=True)
        response_data: dict[str, Any] = {"success": True, "result": result}
        if persist_inventory:
            # 排出状況は、端末側で報酬が表示された時点で1つずつ反映する(演出を最後まで見なかった分は引かなかった扱い)。
            # そのため開封前の集計と、報酬ごとに増える分を返す
            _annotate_box_inventory_deltas(result, owned_before_open, _get_box_inventory_universe(raw_data, dynamic_targets))
            response_data["inventory_stats"] = _build_box_inventory_stats(owned_before_open, dynamic_targets, raw_data)
        return JSONResponse(response_data)
    except Exception as e:
        logger.error(f"Error in box_simulator_open_api: {e}", exc_info=True)
        return JSONResponse({"success": False, "message": "Failed to simulate box"}, status_code=500)


@router.post("/api/box_simulator/inventory/stats", name="box_simulator_inventory_stats_api")
async def box_simulator_inventory_stats_api(
    request: Request,
    lang: str,
    payload: dict[str, Any] | None = Body(None),
    db: asyncpg.Connection = Depends(get_shared_db),
):
    """所持状況パネルを開いたとき・リセットしたときの集計"""
    try:
        dynamic_targets = await _load_box_simulator_dynamic_targets(db)
        picked_target_keys = _decode_box_inventory(payload.get("inventory") if isinstance(payload, dict) else None)
        return JSONResponse({
            "success": True,
            "inventory_stats": _build_box_inventory_stats(picked_target_keys, dynamic_targets, load_drop_boxes_data()),
        })
    except Exception as e:
        logger.error(f"Error in box_simulator_inventory_stats_api: {e}", exc_info=True)
        return JSONResponse({"success": False, "message": "Failed to load inventory stats"}, status_code=500)


class BoxInventoryImportRequest(BaseModel):
    player_tag: str | None = Field(default=None, max_length=20) # 読み込む登録アカウント。省略時はメインアカウント


@router.post("/api/box_simulator/inventory/import", name="box_simulator_inventory_import_api")
async def box_simulator_inventory_import_api(
    request: Request,
    lang: str,
    payload: BoxInventoryImportRequest | None = None,
    db: asyncpg.Connection = Depends(get_shared_db),
):
    """登録アカウント(メイン・サブ)の所持キャラ・スキン・ガジェット等を、所持状況として読み込む。
    最新の所持状況を公式API等から取得し、取得できなければDBの保存済みデータを使う"""
    is_ja = lang == "ja"
    user: User | None = getattr(request.state, "current_user", None)
    if not user:
        return JSONResponse({
            "success": False,
            "message": "ログインが必要です。" if is_ja else "Please log in.",
        }, status_code=401)
    if not user.main_account:
        return JSONResponse({
            "success": False,
            "message": "メインアカウントが設定されていません。" if is_ja else "No main account is set.",
        }, status_code=400)
    # 本人の登録アカウント以外のタグが指定された場合はメインアカウントを使う
    target_tag = resolve_user_player_tag(user, format_tag(payload.player_tag) if payload and payload.player_tag else None)
    try:
        # 最新の所持状況を反映するため公式API等から取得する(数秒かかる)。取得できなければDBの保存済みデータを使う
        player: Player | None = None
        try:
            player = await get_player(target_tag, db)
        except BrawlStarsAPIError as api_err:
            logger.warning(f"排出状況のインポートで最新のプレイヤーデータを取得できませんでした。保存済みデータを使います: tag={target_tag} error={api_err}")
        if not player or not player.brawlers:
            player = await get_player_from_db(target_tag, db)
        if not player or not player.brawlers:
            return JSONResponse({
                "success": False,
                "message": (
                    "このアカウントのデータがまだありません。プレイヤーページで一度表示してから、もう一度お試しください。"
                    if is_ja else
                    "No data for this account yet. Open its player page once, then try again."
                ),
            }, status_code=404)
        dynamic_targets = await _load_box_simulator_dynamic_targets(db)
        picked_target_keys = _build_player_owned_target_keys(player, dynamic_targets)
        return JSONResponse({
            "success": True,
            "inventory": _encode_box_inventory(picked_target_keys),
            "inventory_stats": _build_box_inventory_stats(picked_target_keys, dynamic_targets, load_drop_boxes_data()),
            "account": {
                "tag": player.tag,
                "name": player.name,
                "last_updated_at": format_utc_datetime(player.last_updated_at) if player.last_updated_at else None,
            },
        })
    except Exception as e:
        logger.error(f"Error in box_simulator_inventory_import_api: user={user.name} tag={target_tag} error={e}", exc_info=True)
        return JSONResponse({
            "success": False,
            "message": "読み込みに失敗しました。" if is_ja else "Failed to load your account.",
        }, status_code=500)


#* /---*---*---*---*---*---*---*---*/
#* マップ周期
#* /---*---*---*---*---*---*---*---*/
@router.get("/map_rotation", name="map_rotation")
async def map_rotation(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    try:
        map_catalog = await get_map_names_by_id(db)
        mode_slug_to_id = get_mode_slug_to_id()
    except Exception as e:
        logger.error(f"マップ周期のカタログ取得中にエラー: {e}", exc_info=True)
        map_catalog = {}
        mode_slug_to_id = {}
    # グリッド表示のカード色（モードID → テーマ色キー）
    mode_themes = {str(mode_id): theme for mode_id, theme in MODE_THEME_BY_ID.items()}
    try:
        from app.services.map_rotation_service import build_map_rotation_payload
        rotation_payload = await build_map_rotation_payload(db)
    except Exception as e:
        logger.error(f"マップ周期の表示データ取得中にエラー: {e}", exc_info=True)
        rotation_payload = {"slots": [], "visibleCount": 0, "adjusting": False, "startedAt": None, "expectedCompleteAt": None}

    context = {
        "request": request,
        "lang": lang,
        "current_page": "tools",
        "map_catalog": map_catalog,
        "mode_slug_to_id": mode_slug_to_id,
        "mode_themes": mode_themes,
        "rotation_payload": rotation_payload,
    }

    try:
        return templates.TemplateResponse("tools/map_rotation.html", context)
    except Exception as render_err: # テンプレートレンダリングエラーも捕捉
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")


#* /---*---*---*---*---*---*---*---*/
#* ピック提案ツール
#* /---*---*---*---*---*---*---*---*/
@router.get("/pick_tool", name="pick_tool")
async def pick_tool(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    try:
        #^ 1. モードとマップの情報を取得
        pool_data = await get_current_ranked_pool(db)
    except Exception as e:
        logger.error(f"Error in pick_tool: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    
    #^ 登録アカウント(メイン・サブ)ごとのパワー11キャラ。画面上で即座に切り替えられるよう、まとめて渡す
    user: User | None = getattr(request.state, "current_user", None)
    player_account_options: list[dict] = []
    power11_ids_by_account: dict[str, list[int]] = {}
    max_power_brawler_ids: list[int] = []

    if user:
        try:
            player_account_options = await build_player_account_options(user, db)
            account_data = await get_player_accounts_brawler_data([o["tag"] for o in player_account_options], db)
            power11_ids_by_account = {
                tag: [b["id"] for b in data["brawlers"] if b["power"] >= 11]
                for tag, data in account_data.items()
            }
            # プレイヤーデータがないアカウントは選択肢に出さない
            player_account_options = [o for o in player_account_options if o["tag"] in power11_ids_by_account]
        except Exception as e:
            logger.debug(f"{user.name}の登録アカウントのプレイヤーデータ取得中にエラーが発生しました: {e}", exc_info=True)

    #^ 絞り込み対象に加える、今シーズンの最大レベルキャラ
    if power11_ids_by_account:
        try:
            from app.services.ranked_map_pool_service import get_current_max_power_brawler_ids
            max_power_brawler_ids = list(await get_current_max_power_brawler_ids(db))
        except Exception as e:
            logger.warning(f"ピック提案ツールで最大レベルキャラの取得中にエラーが発生しました: {e}", exc_info=True)

    # テンプレートに渡すコンテキスト
    context = {
        "request": request,
        "lang": lang,
        "user": user,
        "player_account_options": player_account_options,
        "power11_ids_by_account": power11_ids_by_account,
        "max_power_brawler_ids": max_power_brawler_ids,
        "pool_data": pool_data,
        "current_page": "tools",
        "hide_navigation_controls": True,
        "tutorial_pick_tool_pending": bool(
            user and not user.has_completed_tutorial_mission("use_pick_tool")
        ),
    }

    try:
        return templates.TemplateResponse("tools/pick_tool.html", context)
    except Exception as render_err: # テンプレートレンダリングエラーも捕捉
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")

@router.post("/api/pick_tool/complete", name="pick_tool_complete")
async def pick_tool_complete(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db),
):
    current_user: User | None = getattr(request.state, "current_user", None)
    if not current_user:
        return JSONResponse({"success": False}, status_code=401)
    await try_claim_tutorial_mission(current_user, db, "use_pick_tool")
    return JSONResponse({"success": True})

@router.get("/api/ban_suggestions", name="get_ban_suggestions_api")
async def get_ban_suggestions_api(
    db: asyncpg.Connection = Depends(get_shared_db),
    mode: str | None = Query(None),
    map_name: str | None = Query(None),
    banned_brawlers: list[int] | None = Query(None)
):
    """BAN候補のキャラクターリストを脅威度順で取得するAPI"""
    try:
        # 今シーズンのプールに無いモード/マップの指定は外す
        mode, map_name = resolve_ranked_filter(await get_current_ranked_pool(db), mode or None, map_name or None)
        # brawl_serviceの関数を呼び出す
        suggestions = await get_ban_suggestions(
            db,
            mode=mode if mode else None,
            map_name=map_name if map_name else None,
            rank_tier=None, # ランク帯は考慮しない
            banned_brawlers=banned_brawlers
        )
        # BrawlerStatオブジェクトのリストを辞書のリストに変換して返す
        return JSONResponse([s.to_dict() for s in suggestions])
    except Exception as e:
        logger.error(f"Error in get_ban_suggestions_api: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get ban suggestions")

@router.get("/api/pick_suggestions", name="get_pick_suggestions_api")
async def get_pick_suggestions_api(
    db: asyncpg.Connection = Depends(get_shared_db),
    mode: str | None = Query(None),
    map_name: str | None = Query(None),
    my_team_picks: list[int] | None = Query(None),
    enemy_team_picks: list[int] | None = Query(None),
    banned_brawlers: list[int] | None = Query(None)
):
    """ピック候補のキャラクターリストをおすすめ度順で取得するAPI"""
    try:
        # 今シーズンのプールに無いモード/マップの指定は外す
        mode, map_name = resolve_ranked_filter(await get_current_ranked_pool(db), mode or None, map_name or None)
        suggestions = await get_pick_suggestions(
            db,
            mode=mode if mode else None,
            map_name=map_name if map_name else None,
            rank_tier=None, # ランク帯は考慮しない
            my_team_picks=my_team_picks,
            enemy_team_picks=enemy_team_picks,
            banned_brawlers=banned_brawlers
        )
        # PickSuggestionオブジェクトのリストを辞書のリストに変換して返す
        return JSONResponse([s.to_dict() for s in suggestions])
    except Exception as e:
        logger.error(f"Error in get_pick_suggestions_api: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get pick suggestions")

@router.get("/api/predict_win_rate", name="predict_win_rate_api")
async def predict_win_rate_api(
    db: asyncpg.Connection = Depends(get_shared_db),
    mode: str | None = Query(None),
    map_name: str | None = Query(None),
    team_1_picks: list[int] = Query(...),
    team_2_picks: list[int] = Query(...)
):
    """チーム1の予想勝率を算出するAPI"""
    try:
        # 今シーズンのプールに無いモード/マップの指定は外す
        mode, map_name = resolve_ranked_filter(await get_current_ranked_pool(db), mode or None, map_name or None)
        win_rate = await predict_win_rate(
            db,
            mode=mode if mode else None,
            map_name=map_name if map_name else None,
            rank_tier=None, # ランク帯は考慮しない
            team_1_picks=team_1_picks,
            team_2_picks=team_2_picks
        )
        # 計算結果をJSON形式で返す
        return JSONResponse({"team_1_win_rate": win_rate})
    except Exception as e:
        logger.error(f"Error in predict_win_rate_api: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to predict win rate")


#* /---*---*---*---*---*---*---*---*/
#* ガチバトルマップ一覧
#* /---*---*---*---*---*---*---*---*/
@router.get("/ranked_maps", name="ranked_maps")
async def ranked_maps(
    request: Request,
    lang: str,
    season: str | None = Query(None),
    period: str | None = Query(None),
    db: asyncpg.Connection = Depends(get_shared_db)
):
    from app.services.ranked_map_pool_service import build_ranked_maps_payload, build_ranked_maps_view

    # 不正なクエリは無視して既定の表示にする
    season_number = int(season) if season and season.isdigit() else None
    period_number = int(period) if period and period.isdigit() else None

    try:
        payload = await build_ranked_maps_payload(db)
        await ensure_catalog(db)
        brawler_names = {
            brawler.id: {"ja": brawler.name_ja or brawler.name_en, "en": brawler.name_en}
            for brawler in await get_available_brawlers(db)
        }
        view = build_ranked_maps_view(payload, lang=lang, season=season_number, period=period_number, brawler_names=brawler_names)
    except Exception as e:
        logger.error(f"ガチバトルマップ一覧の表示データ取得中にエラー: {e}", exc_info=True)
        view = {"seasons": [], "selected": None, "current_pending": False}

    # テンプレートに渡すコンテキスト
    context = {
        "request": request,
        "lang": lang,
        "current_page": "tools",
        "view": view,
    }

    try:
        return templates.TemplateResponse("tools/ranked_map_list.html", context)
    except Exception as render_err: # テンプレートレンダリングエラーも捕捉
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")


#* /---*---*---*---*---*---*---*---*/
#* マップ一覧
#* /---*---*---*---*---*---*---*---*/
@router.get("/all_maps", name="all_maps")
async def all_maps(
    request: Request,
    lang: str,
    mode: str | None = Query(None, description="初期表示するモードID"),
    db: asyncpg.Connection = Depends(get_shared_db)
):
    from app.services.all_maps_service import build_all_maps_data, build_rotation_hint
    from app.services.map_rotation_service import build_map_rotation_payload

    try:
        await ensure_catalog(db)
        maps_data = build_all_maps_data()
    except Exception as e:
        logger.error(f"マップ一覧の表示データ取得中にエラー: {e}", exc_info=True)
        maps_data = {"modes": [], "maps": []}

    # マップ周期が取れない場合は、周期セクション無しで表示する
    try:
        rotation_hint = build_rotation_hint(await build_map_rotation_payload(db))
    except Exception as e:
        logger.error(f"マップ一覧のマップ周期取得中にエラー: {e}", exc_info=True)
        rotation_hint = []

    # 不正なクエリは無視して「すべて」を表示する
    mode_ids = {item["id"] for item in maps_data["modes"]}
    selected_mode = int(mode) if mode and mode.isdigit() and int(mode) in mode_ids else None

    # テンプレートに渡すコンテキスト
    context = {
        "request": request,
        "lang": lang,
        "current_page": "tools",
        "maps_data": maps_data,
        "rotation_hint": rotation_hint,
        "selected_mode": selected_mode,
    }

    try:
        return templates.TemplateResponse("tools/all_maps.html", context)
    except Exception as render_err: # テンプレートレンダリングエラーも捕捉
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")


#* /---*---*---*---*---*---*---*---*/
#* トロフィー増減表
#* /---*---*---*---*---*---*---*---*/
@router.get("/trophy_table", name="trophy_table")
async def trophy_table(
    request: Request,
    lang: str
):
    
    # テンプレートに渡すコンテキスト
    context = {
        "request": request,
        "lang": lang,
        "current_page": "tools"
    }

    try:
        return templates.TemplateResponse("tools/trophy_table.html", context)
    except Exception as render_err: # テンプレートレンダリングエラーも捕捉
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")
    

#* /---*---*---*---*---*---*---*---*/
#* スタードロップ
#* /---*---*---*---*---*---*---*---*/
@router.get("/starrdrop_chances", name="starrdrop_chances")
async def starrdrop_chances(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db),
    box: str | None = Query(None, description="初期表示するドロップ/ボックス")
):
    skins_dict = await get_all_skins(db)
    raw_drop_boxes_data = load_drop_boxes_data()
    drop_boxes_data = normalize_drop_boxes_data(request, raw_drop_boxes_data, skins_dict)
    # 出現キャラが指定されていないキャラ報酬の排出対象。取得に失敗しても確率表自体は表示する
    try:
        brawlers = await get_available_brawlers(db)
        drop_boxes_data["brawler_targets_by_type"] = build_box_brawler_targets_by_type(
            request, raw_drop_boxes_data.get("reward_types", {}), brawlers, skins_dict.values()
        )
    except Exception as e:
        logger.warning(f"Failed to build brawler targets for starrdrop_chances: {e}", exc_info=True)
        drop_boxes_data["brawler_targets_by_type"] = {}

    # テンプレートに渡すコンテキスト
    context = {
        "request": request,
        "lang": lang,
        "drop_boxes_data": drop_boxes_data,
        "initial_box": box,
        "current_page": "tools"
    }

    try:
        return templates.TemplateResponse("tools/starrdrop_chances.html", context)
    except Exception as render_err: # テンプレートレンダリングエラーも捕捉
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")


@router.get("/chaosdrop_chances", name="chaosdrop_chances")
async def chaosdrop_chances(
    request: Request,
    lang: str
):
    return RedirectResponse(
        url=router.url_path_for("starrdrop_chances", lang=lang) + "?box=chaosdrop",
        status_code=307
    )


#* /---*---*---*---*---*---*---*---*/
#* よくある質問
#* /---*---*---*---*---*---*---*---*/
@router.get("/app_faq", name="app_faq")
async def app_faq(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    platform = getattr(request.state, "platform", "unknown")
    try:
        rows = await db.fetch(
            "SELECT * FROM faqs WHERE is_deleted = FALSE ORDER BY priority ASC, id ASC"
        )
    except Exception as e:
        logger.error(f"FAQ取得中にエラー: {e}", exc_info=True)
        rows = []

    # カテゴリごとにグルーピング
    # 各カテゴリの並び順は、カテゴリ内で最もpriorityが小さいFAQのpriority値で決定
    category_key = 'category_ja' if lang == 'ja' else 'category_en'
    categories_map: dict[str, list[dict]] = {}
    category_min_priority: dict[str, int] = {}

    for row in rows:
        cat = row[category_key]
        faq_item = dict(row)
        if platform == "ios":
            faq_item = sanitize_faq_item_for_ios(faq_item)
        if cat not in categories_map:
            categories_map[cat] = []
            category_min_priority[cat] = row['priority']
        categories_map[cat].append(faq_item)
        if row['priority'] < category_min_priority[cat]:
            category_min_priority[cat] = row['priority']

    # カテゴリを最小priority値順にソート
    sorted_categories = sorted(categories_map.keys(), key=lambda c: category_min_priority[c])

    faq_groups = []
    for cat in sorted_categories:
        faq_groups.append({
            "category": cat,
            "faqs": categories_map[cat]
        })

    context = {
        "request": request,
        "lang": lang,
        "faq_groups": faq_groups,
        "current_page": "tools"
    }

    try:
        return templates.TemplateResponse("tools/app_faq.html", context)
    except Exception as render_err:
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")


#* /---*---*---*---*---*---*---*---*/
#* マップ画像表示ページ
#* /---*---*---*---*---*---*---*---*/
@router.get("/map/{id}", name="get_map")
async def get_map(
    request: Request,
    lang: str,
    id: int,
    tab: str | None = Query(None),
    from_source: str | None = Query(None, alias="from"),
    player: str | None = Query(None),
    battles_tab: str | None = Query(None),
    is_tools_tab: bool = Query(False),
    is_stats_tab: bool = Query(False),
    sort: str | None = Query(None),
    use_table: bool = Query(False),
    use_cache: bool = Query(True),
    db: asyncpg.Connection = Depends(get_shared_db),
):
    name = str(id)
    map_info = None
    brawler_stats = []
    grouped_brawler_stats = None
    sort = sort or None
    # キャッシュ無視は再計算を強制できるため、管理者のみ受け付ける
    if not is_admin_request(request):
        use_cache = True
    # TODO: マルチプレイTier表の正式リリース時に、この管理者限定を外す
    current_user = getattr(request.state, "current_user", None) #TODO この行ごと削除
    show_multiplayer_tier = bool(current_user and current_user.is_admin) #TODO この行ごと削除
    try:
        await ensure_catalog(db)
        map_info = get_map_by_id(id)
        if map_info:
            name = map_info.ja if lang == "ja" and map_info.ja else (map_info.en or str(id))
            if map_info.mode_id and show_multiplayer_tier: #TODO and show_multiplayer_tier を削除
                brawler_stats = await get_trophy_stats(
                    db,
                    mode_id=map_info.mode_id,
                    map_id=id,
                    use_cache=use_cache,
                )
                if sort == "use_rate":
                    brawler_stats.sort(key=lambda b: -b.use_rate)
                elif sort == "brawler_id":
                    brawler_stats.sort(key=lambda b: b.brawler_id)
                elif sort == "name":
                    brawler_stats.sort(key=lambda b: brawler_name_sort_key(get_brawler_display_name(b, lang), lang))
                elif sort == "rarity_asc":
                    brawler_stats.sort(key=lambda b: (b.rarity is None, b.rarity, b.brawler_id))
                elif sort == "rarity_desc":
                    brawler_stats.sort(key=lambda b: (b.rarity is None, b.rarity, -b.brawler_id), reverse=True)
                # スコア順・名前順・レア度順の場合は、左にブロックを置くTier表型で表示するためグループ化
                grouped_brawler_stats = build_brawler_tier_groups(brawler_stats, sort, lang)
    except Exception as e:
        logger.error(f"マップページの統計取得中にエラー (id={id}): {e}", exc_info=True)

    # タイトル用: 所属モードの名前・アイコン・テーマ色
    mode_id = map_info.mode_id if map_info else None
    mode_info = get_mode_by_id(mode_id)
    mode_name = (mode_info.display_name(lang) if mode_info else None) or ""
    mode_icons = mode_icon_candidates(mode_id, mode_info.slug if mode_info else None)
    board_c1, board_c2 = get_mode_board_colors(mode_id)

    # マップ掲示板のスレッドを取得（なければ作成）。一覧に載らないマップでは作らない
    map_thread_id: int | None = None
    map_preview_messages: list = []
    try:
        map_board_post = await get_or_create_theme_map_post(db, id)
        if map_board_post:
            map_thread_id = map_board_post.id
            # プレビュー用に最新3件のメッセージを取得（新しい順）
            messages_data, _ = await get_messages(db, per_page=3, thread_id=map_thread_id, exclude_warning=True)
            map_preview_messages = [m.to_dict() for m in messages_data]
    except Exception as e:
        logger.warning(f"マップ掲示板用チャットスレッドの取得/作成に失敗: map_id={id}, error={e}")

    context = {
        "request": request,
        "lang": lang,
        "id": id,
        "name": name,
        "mode_id": mode_id,
        "mode_name": mode_name,
        "mode_icons": mode_icons,
        "mode_theme": get_mode_theme(mode_id),
        "board_c1": board_c1,
        "board_c2": board_c2,
        "map_thread_id": map_thread_id,
        "map_preview_messages": map_preview_messages,
        "brawler_stats": brawler_stats,
        "grouped_brawler_stats": grouped_brawler_stats,
        "current_sort": sort,
        "use_table": use_table,
        "use_cache": use_cache,
        "empty_stats_message": (
            "このマップの統計データはまだありません。" if lang == "ja"
            else "Stats for this map are not available yet."
        ),
    }
    nav_ctx = resolve_nav_context(
        lang=lang,
        page_kind="map",
        tab=tab,
        from_source=from_source,
        is_stats_tab=is_stats_tab,
        is_tools_tab=is_tools_tab,
        player=player,
        battles_tab=battles_tab,
        current_map_id=id,
    )
    context.update(nav_template_vars(nav_ctx))

    try:
        return templates.TemplateResponse("tools/map.html", context)
    except Exception as render_err: # テンプレートレンダリングエラーも捕捉
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")


#* /---*---*---*---*---*---*---*---*/
#* ブロスタ動画
#* /---*---*---*---*---*---*---*---*/
_BRAWL_VIDEOS_CACHE_KEY = "brawl_videos:active_list"
_BRAWL_VIDEOS_CACHE_TTL = 60  # 秒

@router.get("/brawl_videos", name="brawl_videos")
async def brawl_videos(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db),
):
    try:
        cached = await get_cache(_BRAWL_VIDEOS_CACHE_KEY)
        if cached is not None:
            videos = cached
        else:
            rows = await db.fetch(
                """
                SELECT id, title_ja, title_en, platform, video_id,
                       thumbnail_url, is_sponsored, sponsor_name
                FROM brawl_videos
                WHERE is_active = TRUE
                ORDER BY display_order ASC, created_at DESC
                """
            )
            videos = [dict(r) for r in rows]
            await set_cache(_BRAWL_VIDEOS_CACHE_KEY, videos, ttl=_BRAWL_VIDEOS_CACHE_TTL)
    except Exception as e:
        logger.error(f"brawl_videosの取得中にエラー: {e}", exc_info=True)
        videos = []

    context = {
        "request": request,
        "lang": lang,
        "videos": videos,
        "current_page": "tools",
    }

    try:
        return templates.TemplateResponse("tools/brawl_videos.html", context)
    except Exception as render_err:
        logger.error(f"Template rendering error: {render_err}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering page")
# PUBLIC_EXCLUDE_END
