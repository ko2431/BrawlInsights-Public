from fastapi import APIRouter, Request, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
import asyncpg
import math
import datetime
import time
import uuid
from typing import Annotated, Any, Optional
from types import SimpleNamespace

from pydantic import BeforeValidator

from app.exceptions.custom_exceptions import BrawlStarsAPIError
from app.core.logger import logger
from app.core.config import settings
from app.core.logging_config import add_log_info
from app.db.db import get_shared_db
from app.services.brawl_service import get_player, get_player_from_db, get_player_for_tracking_extension, calc_num_of_available_brawlers, get_club_name, search_players_fast, get_player_log_trends, PlayerStatsPageData, Battles, search_battles, add_auto_tracking_time, extend_battle_log_retention, get_battle_log_retention_months, get_max_accessory_counts, get_skin_catalog_stats, get_all_skins, get_all_titles, collect_battle_mode_map_filter_options, get_brawler
from app.services.map_mode_catalog import get_map_by_id, mode_sort_key, resolve_map_filter_label, resolve_mode_filter_label
from app.services.rating_service import build_player_rating_data
from app.core.cache import get_cache, get_redis, set_cache
from app.services.user_service import User, _current_token_claim_date, try_claim_tutorial_mission
from app.utils.utils import format_tag, confirm_tag, get_icon_path, get_ranked_seasons_for_filter, get_remote_ip, get_normalized_ip, group_brawlers_by_rarity
from app.exceptions.custom_exceptions import BrawlStarsAPIError, DataBaseError
from app.core.templating import templates

router = APIRouter(
    prefix="/{lang}/player",
    tags=["Players"]
)

# --- ログイン必須の依存関係 ---
async def get_current_active_user(request: Request) -> User:
    """
    現在のリクエストからアクティブなログインユーザーを取得する。
    未ログインの場合はHTTP 401エラーを発生させる。
    """
    user = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


BATTLE_LOG_TABS = ("all", "trophy", "powerleague", "friendly")


def _blank_query_to_none(value: object) -> object:
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


QueryInt = Annotated[int | None, BeforeValidator(_blank_query_to_none)]


def _normalize_battles_tab(tab: str) -> str:
    if tab == "brawler":
        return "trophy"
    if tab in BATTLE_LOG_TABS:
        return tab
    return "all"


def _positive_id(value: int | None) -> int | None:
    if value is None or value <= 0:
        return None
    return value


def _battles_filter_query(
    *,
    tab: str,
    brawler_id: int | None,
    mode_id: int | None,
    unknown_mode: bool,
    event_id: int | None,
    after: str | None,
    before: str | None,
) -> str:
    parts: list[str] = []
    if tab and tab != "all":
        parts.append(f"tab={tab}")
    if brawler_id is not None:
        parts.append(f"brawler_id={brawler_id}")
    if unknown_mode:
        parts.append("mode_id=0")
    elif mode_id is not None:
        parts.append(f"mode_id={mode_id}")
    if event_id is not None:
        parts.append(f"event_id={event_id}")
    if after:
        parts.append(f"after={after}")
    if before:
        parts.append(f"before={before}")
    return "&".join(parts)


def _brawler_ja_name(ja_value: object, fallback: str) -> str:
    if isinstance(ja_value, list) and ja_value:
        first = ja_value[0]
        if isinstance(first, str) and first.strip():
            return first
    if isinstance(ja_value, str) and ja_value.strip():
        return ja_value
    return fallback


async def _build_log_brawler_filter_options(
    db: asyncpg.Connection,
    player,
    lang: str,
    battles: list[Any],
    selected_brawler_id: int | None,
) -> tuple[list[dict[str, Any]], list[Any]]:
    seen_ids: set[int] = set()
    for battle in battles:
        if battle.brawler:
            seen_ids.add(battle.brawler)
        if battle.brawlers:
            for item in battle.brawlers:
                if item and item[0]:
                    seen_ids.add(item[0])
    if selected_brawler_id is not None:
        seen_ids.add(selected_brawler_id)
    if not seen_ids:
        return [], []

    owned_by_id = {brawler.id: brawler for brawler in (getattr(player, "brawlers", None) or [])}
    resolved: list[Any] = []
    missing_ids: list[int] = []
    for brawler_id in seen_ids:
        owned = owned_by_id.get(brawler_id)
        if owned:
            resolved.append(owned)
        else:
            missing_ids.append(brawler_id)

    if missing_ids:
        rows = await db.fetch(
            "SELECT id, en, ja, rarity FROM brawlers WHERE id = ANY($1)",
            missing_ids,
        )
        catalog_by_id: dict[int, Any] = {}
        for row in rows:
            name_en = row["en"] or str(row["id"])
            catalog_by_id[row["id"]] = SimpleNamespace(
                id=row["id"],
                name_en=name_en,
                name_ja=_brawler_ja_name(row["ja"], name_en),
                rarity=row["rarity"],
            )
        for brawler_id in missing_ids:
            catalog_item = catalog_by_id.get(brawler_id)
            if catalog_item:
                resolved.append(catalog_item)
                continue
            brawler = await get_brawler(brawler_id, db)
            name_en = brawler.name_en or str(brawler_id)
            resolved.append(SimpleNamespace(
                id=brawler.id,
                name_en=name_en,
                name_ja=brawler.name_ja or name_en,
                rarity=brawler.rarity,
            ))

    return group_brawlers_by_rarity(resolved, lang)


def _battle_filter_scope_labels(
    lang: str,
    *,
    has_brawler: bool,
    has_mode: bool,
    has_map: bool,
) -> tuple[str, str]:
    parts_ja: list[str] = []
    parts_en: list[str] = []
    if has_brawler:
        parts_ja.append("キャラクター")
        parts_en.append("brawler")
    if has_mode:
        parts_ja.append("モード")
        parts_en.append("mode")
    if has_map:
        parts_ja.append("マップ")
        parts_en.append("map")
    if not parts_ja:
        return "", ""
    scope_ja = "選択されている" + "、".join(parts_ja) + "の"
    if len(parts_en) == 1:
        scope_en = f" of the selected {parts_en[0]}"
    elif len(parts_en) == 2:
        scope_en = f" of the selected {parts_en[0]} and {parts_en[1]}"
    else:
        scope_en = f" of the selected {', '.join(parts_en[:-1])}, and {parts_en[-1]}"
    return scope_ja, scope_en


def _attach_ranked_score_neighbors(battles_on_page: list) -> None:
    """ページ内の次に古いガチバトルを、ポイント変動の比較対象にする。"""
    ranked_indices = [
        index for index, battle in enumerate(battles_on_page)
        if battle.battle_type == "soloRanked"
    ]
    for position, index in enumerate(ranked_indices):
        battle = battles_on_page[index]
        if position + 1 < len(ranked_indices):
            older = battles_on_page[ranked_indices[position + 1]]
            battle.page_ranked_score_before = older.ranked_score_after
            battle.page_ranked_rank_before = older.ranked_rank_after
        else:
            battle.page_ranked_score_before = None
            battle.page_ranked_rank_before = None


# プレイヤープロフィールページのエンドポイント (シェルHTMLを即時返却)
@router.get("/profile/{tag}", name="get_player_profile")
async def get_player_profile(
    request: Request,
    lang: str,
    tag: str,
    sort: str | None = Query('trophies', alias="sort")
):
    if not tag:
        try:
            home_url = request.url_for('home', lang=lang)
        except Exception:
            home_url = f"/{lang}"
        return RedirectResponse(url=home_url, status_code=status.HTTP_302_FOUND)

    tag = format_tag(tag)
    if not confirm_tag(tag):
        raise HTTPException(status_code=400, detail="Invalid player tag format.")

    # シェルHTMLを即時返却（プレイヤーデータ取得なし）
    context = {
        "request": request,
        "lang": lang,
        "tag": tag,
        "sort": sort or 'trophies',
        "current_page": "home",
    }
    return templates.TemplateResponse("player/profile.html", context)


# プレイヤーバトル履歴ページのエンドポイント (シェルHTMLを即時返却)
@router.get("/battles/{tag}", name="get_player_battles")
async def get_player_battles(
    request: Request,
    lang: str,
    tag: str,
    tab: str = Query("all", description="表示するバトルの区分"),
    brawler_id: QueryInt = Query(None, description="絞り込むBrawlerのID"),
    mode_id: QueryInt = Query(None, description="絞り込むモードID。0はその他"),
    event_id: QueryInt = Query(None, description="絞り込むマップID"),
    map_id: QueryInt = Query(None, description="event_id の別名"),
    before_date_str: str | None = Query(None, alias="before"),
    after_date_str: str | None = Query(None, alias="after"),
    page: int = Query(1, ge=1)
):
    # タグの形式を確認
    if not tag:
        logger.debug("Player profile tag is empty. Redirecting to homepage.")
        try:
            home_url = request.url_for('home', lang=lang)
        except Exception as e:
            logger.warning(f"Could not generate URL for 'home', falling back to '/': {e}")
            home_url = f"/{lang}" # url_forが失敗した場合のフォールバック
        # 302 Found (一時的なリダイレクト) でリダイレクトレスポンスを返す
        return RedirectResponse(url=home_url, status_code=status.HTTP_302_FOUND)
    
    formatted_tag = format_tag(tag)
    if not confirm_tag(formatted_tag):
        raise HTTPException(status_code=400, detail="Invalid player tag format.")

    if tab == "brawler":
        return RedirectResponse(
            url=str(request.url.include_query_params(tab="trophy")),
            status_code=status.HTTP_302_FOUND,
        )

    normalized_tab = _normalize_battles_tab(tab)
    selected_event_id = _positive_id(event_id if event_id is not None else map_id)

    # シェルHTMLを即時返却（プレイヤーデータ取得なし）
    context = {
        "request": request,
        "lang": lang,
        "tag": formatted_tag,
        "current_tab": normalized_tab,
        "before_date_filter": before_date_str,
        "after_date_filter": after_date_str,
        "current_page_num": page,
        "selected_brawler_id": brawler_id,
        "selected_mode_id": mode_id,
        "selected_event_id": selected_event_id,
        "current_page": "home",
    }
    return templates.TemplateResponse("player/battles.html", context)


# プレイヤー統計ページのエンドポイント (シェルHTMLを即時返却)
@router.get("/stats/{tag}", name="get_player_stats")
async def get_player_stats(
    request: Request,
    lang: str,
    tag: str,
    period: str | None = Query("30d", description="表示期間 (例: 7d, 30d, 90d, 180d, 1y, all)"),
):
    if not tag:
        try:
            home_url = request.url_for('home', lang=lang)
        except Exception:
            home_url = f"/{lang}"
        return RedirectResponse(url=home_url, status_code=status.HTTP_302_FOUND)

    tag = format_tag(tag)
    if not confirm_tag(tag):
        raise HTTPException(status_code=400, detail="Invalid player tag format.")

    # シェルHTMLを即時返却（プレイヤーデータ取得なし）
    context = {
        "request": request,
        "lang": lang,
        "tag": tag,
        "current_period": period or "30d",
        "current_page": "home",
    }
    return templates.TemplateResponse("player/stats.html", context)


# ==============================================================
# フラグメントAPIエンドポイント (実際のデータを含むHTMLを返す)
# ==============================================================

api_router = APIRouter(
    prefix="/api/{lang}/player",
    tags=["Player Fragments"]
)

# 所持スキンモーダル用のスキン一覧 (全プレイヤー共通のため、ブラウザにキャッシュさせる)
@api_router.get("/skin_catalog", name="get_player_skin_catalog")
async def get_player_skin_catalog(
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db),
):
    all_skins = await get_all_skins(db)
    # キャラIDごとに [スキンID, 名前, レア度, 限定か] をレア度順で返す
    skins_by_brawler: dict[int, list[list[Any]]] = {}
    for skin in sorted(all_skins.values(), key=lambda s: (s.rarity or 0, s.id)):
        if skin.brawler_id is None:
            continue
        name = (skin.ja or skin.en) if lang == "ja" else (skin.en or skin.ja)
        skins_by_brawler.setdefault(skin.brawler_id, []).append([skin.id, name, skin.rarity or 0, bool(skin.is_limited)])
    return JSONResponse(
        {"skins": skins_by_brawler},
        headers={"Cache-Control": "public, max-age=600"},
    )


# プロフィールフラグメント
@api_router.get("/{tag}/profile", name="get_player_profile_fragment")
async def get_player_profile_fragment(
    request: Request,
    lang: str,
    tag: str,
    db: asyncpg.Connection = Depends(get_shared_db),
    sort: str | None = Query('trophies', alias="sort")
):
    tag = format_tag(tag)
    if not confirm_tag(tag):
        raise HTTPException(status_code=400, detail="Invalid player tag format.")

    try:
        player = await get_player(tag, db)
        num_of_available_brawlers = await calc_num_of_available_brawlers(db)
        previous_clubs: dict[str, tuple[str]] = {}
        for date, club_tag in player.previous_clubs.items():
            club_name = await get_club_name(club_tag, db) if club_tag else None
            previous_clubs[date] = (club_name, club_tag)
    except BrawlStarsAPIError as e:
        logger.debug(f"{tag}のプレイヤーデータ取得中にAPIエラー。DBフォールバック: {e}")
        player = await get_player_from_db(tag, db)
        if not player:
            return templates.TemplateResponse(
                "player/fragments/profile_error.html",
                {"request": request, "lang": lang, "tag": tag, "error_type": "not_found"},
                status_code=404
            )
        num_of_available_brawlers = await calc_num_of_available_brawlers(db)
        previous_clubs: dict[str, tuple[str]] = {}
        for date, club_tag in player.previous_clubs.items():
            club_name = await get_club_name(club_tag, db) if club_tag else None
            previous_clubs[date] = (club_name, club_tag)
    except Exception as e:
        logger.error(f"{tag}のプレイヤーデータ取得中にエラー: {e}", exc_info=True)
        return templates.TemplateResponse(
            "player/fragments/profile_error.html",
            {"request": request, "lang": lang, "tag": tag, "error_type": "server_error"},
            status_code=500
        )

    # 取得後に高レベルなプレイヤーは高頻度自動取得を有効化
    is_auto_activate = await player.is_auto_activate()
    if is_auto_activate: await player.activate_automatic_acquisition(activate_time=player.auto_activate_time)

    # ソートを実行
    if hasattr(player, 'brawlers') and isinstance(player.brawlers, list):
        if sort == 'highest_trophies':
            player.brawlers.sort(key=lambda b: (-b.highest_trophies, -b.trophies))
        elif sort == 'rank':
            player.brawlers.sort(key=lambda b: (-b.acc_rank, -b.highest_trophies, -b.trophies))
        elif sort == 'power':
            player.brawlers.sort(key=lambda b: (-b.power, -len(b.hyper_charge_ids), -b.star_power, -b.gadget, -b.gear, -b.highest_trophies))
        elif sort == 'brawler_id':
            player.brawlers.sort(key=lambda b: b.id)
        elif sort == 'mastery':
            player.brawlers.sort(key=lambda b: (-b.mastery, -b.highest_trophies))
        elif sort == 'name':
            player.brawlers.sort(key=lambda b: b.name_ja if lang == "ja" else b.name_en)
        elif sort == 'rarity_asc':
            player.brawlers.sort(key=lambda b: (b.rarity is None, b.rarity, b.id))
        elif sort == 'rarity_desc':
            player.brawlers.sort(key=lambda b: (b.rarity is None, b.rarity, -b.id), reverse=True)
        elif sort == 'win_streak_max':
            player.brawlers.sort(key=lambda b: (-(b.max_win_streak or 0), -b.highest_trophies))
        elif sort == 'win_streak_current':
            player.brawlers.sort(key=lambda b: (-(b.current_win_streak or 0), -b.highest_trophies))
        else:
            sort = 'trophies'
            player.brawlers.sort(key=lambda b: (-b.trophies, -b.highest_trophies))
    else:
        player.brawlers = []

    icon_path_rel = get_icon_path(player.icon_id)
    add_log_info(request, f"プレイヤー(fragment): {player.name} - {player.tag}")

    # 閲覧履歴追加
    current_login_user: Optional[User] = getattr(request.state, "current_user", None)
    if current_login_user:
        try:
            await current_login_user.add_to_viewed_accounts(player.tag, db)
        except Exception as e:
            logger.error(f"閲覧履歴追加エラー: {e}", exc_info=True)
        if player.tag == current_login_user.main_account:
            await try_claim_tutorial_mission(current_login_user, db, "view_main_profile")

    max_accessory_counts = await get_max_accessory_counts(db)
    skin_catalog_stats = await get_skin_catalog_stats(db)
    rating_data = await build_player_rating_data(player, db)

    context = {
        "request": request,
        "lang": lang,
        "player": player,
        "num_of_available_brawlers": num_of_available_brawlers,
        "max_accessory_counts": max_accessory_counts,
        "skin_catalog_stats": skin_catalog_stats,
        "previous_clubs": previous_clubs,
        "player_icon_path": "/" + icon_path_rel,
        "rating_data": rating_data,
        "current_sort": sort,
        "current_page": "home",
        "current_user": current_login_user,
        "titles": await get_all_titles(db),
    }
    return templates.TemplateResponse("player/fragments/profile_fragment.html", context)


# バトル履歴フラグメント
@api_router.get("/{tag}/battles", name="get_player_battles_fragment")
async def get_player_battles_fragment(
    request: Request,
    lang: str,
    tag: str,
    db: asyncpg.Connection = Depends(get_shared_db),
    tab: str = Query("all"),
    brawler_id: QueryInt = Query(None),
    mode_id: QueryInt = Query(None),
    event_id: QueryInt = Query(None),
    map_id: QueryInt = Query(None),
    before_date_str: str | None = Query(None, alias="before"),
    after_date_str: str | None = Query(None, alias="after"),
    page: int = Query(1, ge=1)
):
    formatted_tag = format_tag(tag)
    if not confirm_tag(formatted_tag):
        raise HTTPException(status_code=400, detail="Invalid player tag format.")

    try:
        player = await get_player(formatted_tag, db)
    except BrawlStarsAPIError as e:
        logger.debug(f"Player {formatted_tag} not found from API for battles fragment, trying DB fallback: {e}")
        player = await get_player_from_db(formatted_tag, db)
        if not player:
            return templates.TemplateResponse(
                "player/fragments/battles_error.html",
                {"request": request, "lang": lang, "tag": formatted_tag, "error_type": "not_found"},
                status_code=404
            )
    except DataBaseError as e:
        logger.error(f"Database error fetching player {formatted_tag} for battles fragment: {e}", exc_info=True)
        return templates.TemplateResponse(
            "player/fragments/battles_error.html",
            {"request": request, "lang": lang, "tag": formatted_tag, "error_type": "server_error"},
            status_code=500
        )
    except Exception as e:
        logger.error(f"Unexpected error fetching player {formatted_tag} for battles fragment: {e}", exc_info=True)
        return templates.TemplateResponse(
            "player/fragments/battles_error.html",
            {"request": request, "lang": lang, "tag": formatted_tag, "error_type": "server_error"},
            status_code=500
        )

    # 取得後に高レベルなプレイヤーは高頻度自動取得を有効化
    is_auto_activate = await player.is_auto_activate()
    if is_auto_activate: await player.activate_automatic_acquisition(activate_time=player.auto_activate_time)

    tab = _normalize_battles_tab(tab)
    selected_brawler_id = brawler_id
    filter_unknown_mode = mode_id == 0
    selected_mode_id = None if filter_unknown_mode or mode_id is None else mode_id
    selected_event_id = _positive_id(event_id if event_id is not None else map_id)

    # 日時フィルターのパース (JST to UTC 変換を lang='ja' の場合に追加)
    before_datetime_utc: Optional[datetime.datetime] = None
    if before_date_str:
        try:
            dt_obj_naive = datetime.datetime.strptime(before_date_str, "%Y-%m-%d")
            if lang == "ja":
                jst = datetime.timezone(datetime.timedelta(hours=9))
                dt_obj_jst = datetime.datetime.combine(dt_obj_naive.date(), datetime.time.max, tzinfo=jst)
                before_datetime_utc = dt_obj_jst.astimezone(datetime.timezone.utc)
            else:
                before_datetime_utc = datetime.datetime.combine(dt_obj_naive.date(), datetime.time.max, tzinfo=datetime.timezone.utc)
        except ValueError:
            logger.warning(f"Invalid 'before_date' format: {before_date_str}. Ignoring filter.")
            before_date_str = None

    after_datetime_utc: Optional[datetime.datetime] = None
    if after_date_str:
        try:
            dt_obj_naive = datetime.datetime.strptime(after_date_str, "%Y-%m-%d")
            if lang == "ja":
                jst = datetime.timezone(datetime.timedelta(hours=9))
                dt_obj_jst = datetime.datetime.combine(dt_obj_naive.date(), datetime.time.min, tzinfo=jst)
                after_datetime_utc = dt_obj_jst.astimezone(datetime.timezone.utc)
            else:
                after_datetime_utc = datetime.datetime.combine(dt_obj_naive.date(), datetime.time.min, tzinfo=datetime.timezone.utc)
        except ValueError:
            logger.warning(f"Invalid 'after_date' format: {after_date_str}. Ignoring filter.")
            after_date_str = None

    # search_battles のパラメータ準備
    search_params = {
        "db": db,
        "tag": player.tag,
        "limit": 100000,
        "until": before_datetime_utc,
        "after": after_datetime_utc,
    }

    # [この部分は公開用リポジトリでは非公開にされています]

    context = {
        "request": request,
        "lang": lang,
        "player": player,
        "player_icon_path": "/" + icon_path_rel,
        "current_tab": tab,
        "selected_brawler_id": selected_brawler_id,
        "selected_mode_id": 0 if filter_unknown_mode else selected_mode_id,
        "selected_event_id": selected_event_id,
        "brawler_filter_groups": brawler_filter_groups,
        "uncategorized_brawlers": uncategorized_brawlers,
        "mode_filter_options": mode_filter_options,
        "map_filter_options": map_filter_options,
        "has_unknown_mode_option": has_unknown_mode_option,
        "show_brawler_usage": show_brawler_usage,
        "show_mode_usage": show_mode_usage,
        "show_teammate_usage": show_teammate_usage,
        "show_map_graphs": show_map_graphs,
        "show_map_pick_rates": show_map_pick_rates,
        "show_brawler_trophy_trend": show_brawler_trophy_trend,
        "show_ranked_trend": show_ranked_trend,
        "show_new_player_notice": show_new_player_notice,
        "raw_battle_count": raw_battle_count,
        "has_mode_filter": has_mode_filter,
        "filter_scope_ja": filter_scope_ja,
        "filter_scope_en": filter_scope_en,
        "filter_query": filter_query,
        "filter_query_without_brawler": filter_query_without_brawler,
        "shared_filter_query": shared_filter_query,
        "battles_on_page": battles_on_page,
        "win": battles_on_page_win,
        "lose": battles_on_page_lose,
        "draw": battles_on_page_draw,
        "stats_data": stats_data,
        "graphs_data": graphs_data,
        "pagination": pagination_info,
        "current_page_num": page,
        "before_date_filter": before_date_str,
        "after_date_filter": after_date_str,
        "current_page": "home",
        "should_display_ranked_score_trend": should_display_ranked_score_trend,
        "selected_season_number": selected_season_number,
        "seasons": seasons_for_filter,
        "current_user": current_login_user,
    }
    return templates.TemplateResponse("player/fragments/battles_fragment.html", context)


# 推移グラフフラグメント
@api_router.get("/{tag}/stats", name="get_player_stats_fragment")
async def get_player_stats_fragment(
    request: Request,
    lang: str,
    tag: str,
    period: str | None = Query("30d"),
    db: asyncpg.Connection = Depends(get_shared_db)
):
    tag = format_tag(tag)
    if not confirm_tag(tag):
        raise HTTPException(status_code=400, detail="Invalid player tag format.")

    try:
        player = await get_player(tag, db)
    except BrawlStarsAPIError as e:
        logger.debug(f"{tag}のプレイヤーデータ取得中にAPIエラー。DBフォールバック: {e}")
        player = await get_player_from_db(tag, db)
        if not player:
            return templates.TemplateResponse(
                "player/fragments/stats_error.html",
                {"request": request, "lang": lang, "tag": tag, "error_type": "not_found"},
                status_code=404
            )
    except Exception as e:
        logger.error(f"{tag}のプレイヤーデータ取得中にエラー: {e}", exc_info=True)
        return templates.TemplateResponse(
            "player/fragments/stats_error.html",
            {"request": request, "lang": lang, "tag": tag, "error_type": "server_error"},
            status_code=500
        )

    # 期間パラメータを解釈
    period_days_for_query: int | None = None
    if period == "all":
        period_days_for_query = None
    elif period.endswith("d"):
        try:
            period_days_for_query = int(period[:-1])
        except ValueError:
            period_days_for_query = 30
            period = "30d"
    elif period.endswith("y"):
        try:
            period_days_for_query = int(period[:-1]) * 365
        except ValueError:
            period_days_for_query = 30
            period = "30d"
    else:
        period_days_for_query = 30
        period = "30d"

    try:
        player_stats_data: PlayerStatsPageData = await get_player_log_trends(db, player.tag, period_days_for_query)
    except DataBaseError as e:
        logger.error(f"プレイヤー({player.tag})の統計推移データ取得中にエラー: {e}", exc_info=True)
        player_stats_data = {"log_trends": None, "oldest_log_date": None, "latest_log_date": None, "total_log_days": 0}
    except Exception as e:
        logger.error(f"プレイヤー({player.tag})の統計推移データ取得中に予期せぬエラー: {e}", exc_info=True)
        player_stats_data = {"log_trends": None, "oldest_log_date": None, "latest_log_date": None, "total_log_days": 0}

    is_auto_activate = await player.is_auto_activate()
    if is_auto_activate: await player.activate_automatic_acquisition(activate_time=player.auto_activate_time)

    icon_path_rel = get_icon_path(player.icon_id)
    add_log_info(request, f"プレイヤー(stats fragment): {player.name} - {player.tag}")

    # 期間選択メニューの選択肢を生成
    available_periods_options = []
    actual_total_days = 0

    if player_stats_data["oldest_log_date"]:
        try:
            oldest_date_obj = datetime.date.fromisoformat(player_stats_data["oldest_log_date"])
            today = datetime.datetime.now(datetime.timezone.utc).date()
            actual_total_days = (today - oldest_date_obj).days + 1
            if actual_total_days < 0: actual_total_days = 0
        except ValueError:
            actual_total_days = 0

    def format_all_period_label(total_days: int, lang: str, is_default: bool) -> str:
        years = total_days // 365
        days_remaining = total_days % 365
        default_suffix_ja = " (デフォルト)" if is_default else ""
        default_suffix_en = " (Default)" if is_default else ""
        if lang == "ja":
            if years > 0 and days_remaining > 0:
                return f"全期間 (過去{years}年{days_remaining}日){default_suffix_ja}"
            elif years > 0:
                return f"全期間 (過去{years}年){default_suffix_ja}"
            else:
                return f"全期間 (過去{days_remaining}日){default_suffix_ja}"
        else:
            if years > 0 and days_remaining > 0:
                return f"All Time ({years}y {days_remaining}d){default_suffix_en}"
            elif years > 0:
                return f"All Time ({years}y){default_suffix_en}"
            else:
                return f"All Time ({days_remaining}d){default_suffix_en}"

    is_default_for_all = actual_total_days < 30 and actual_total_days > 0
    period_definitions_master = []

    if actual_total_days > 1:
        period_definitions_master.append(
            (f"{actual_total_days}d", f"過去{actual_total_days}日", f"Last {actual_total_days} Days", actual_total_days, is_default_for_all)
        )

    fixed_periods_config = [
        (30, "30d", "過去30日", "Last 30 Days"),
        (90, "90d", "過去90日", "Last 90 Days"),
        (180, "180d", "過去180日", "Last 180 Days"),
        (365, "1y", "過去1年", "Last 1 Year"),
    ]
    for i in range(2, 6):
        fixed_periods_config.append((365 * i, f"{i}y", f"過去{i}年", f"Last {i} Years"))

    for days, val, label_ja, label_en in fixed_periods_config:
        if actual_total_days >= days:
            is_default = (days == 30 and actual_total_days >= 30)
            label_ja_final = label_ja + (" (デフォルト)" if is_default and not is_default_for_all else "")
            label_en_final = label_en + (" (Default)" if is_default and not is_default_for_all else "")
            period_definitions_master.append((val, label_ja_final, label_en_final, days, is_default))

    is_all_period_default = actual_total_days < 30
    all_label_ja = format_all_period_label(actual_total_days, "ja", is_all_period_default)
    all_label_en = format_all_period_label(actual_total_days, "en", is_all_period_default)
    period_definitions_master.append(("all", all_label_ja, all_label_en, actual_total_days, is_all_period_default))

    unique_options_dict = {}
    for val, l_ja, l_en, d, is_def in sorted(period_definitions_master, key=lambda x: (x[3], len(x[0]))):
        if d not in unique_options_dict or (val != "all" and len(val) < len(unique_options_dict[d][0])):
            unique_options_dict[d] = (val, l_ja, l_en, d, is_def)
        elif val == "all" and d not in unique_options_dict:
            unique_options_dict[d] = (val, l_ja, l_en, d, is_def)

    temp_options_from_dict = []
    for _val, _l_ja, _l_en, _d, _is_def in unique_options_dict.values():
        temp_options_from_dict.append((_val, _l_ja, _l_en, _d))
    available_periods_options = sorted(temp_options_from_dict, key=lambda x: x[3])

    current_period_is_valid = any(opt[0] == period for opt in available_periods_options)
    if not current_period_is_valid or not available_periods_options:
        if actual_total_days >= 30 and any(opt[0] == "30d" for opt in available_periods_options):
            period = "30d"
        elif actual_total_days > 0 and any(opt[0] == f"{actual_total_days}d" for opt in available_periods_options):
            period = f"{actual_total_days}d"
        elif available_periods_options:
            period = available_periods_options[0][0]
        else:
            period = "all"

    current_login_user: Optional[User] = getattr(request.state, "current_user", None)
    if current_login_user:
        try:
            await current_login_user.add_to_viewed_accounts(player.tag, db)
        except Exception as e:
            logger.error(f"閲覧履歴追加エラー: {e}", exc_info=True)
        if player.tag == current_login_user.main_account:
            await try_claim_tutorial_mission(current_login_user, db, "view_main_profile")

    context = {
        "request": request,
        "lang": lang,
        "player": player,
        "player_icon_path": "/" + icon_path_rel,
        "current_page": "home",
        "player_stats_data": player_stats_data,
        "current_period": period,
        "available_periods": available_periods_options,
        "current_user": current_login_user,
    }
    return templates.TemplateResponse("player/fragments/stats_fragment.html", context)


# プレイヤー検索結果ページのエンドポイント（シェルHTML即時返却）
@router.get("/search", name="search_players_page")
async def search_players_page(
    request: Request,
    lang: str,
    q: str | None = Query(None, min_length=1, max_length=50, description="プレイヤー検索語句"),
    page: int = Query(1, ge=1, description="表示ページ番号"),
    exact: bool | None = Query(False, description="完全一致検索のみ"),
    old: bool | None = Query(False, description="改名前の名前も検索対象"),
):
    # 検索語句のチェックとリダイレクト
    query = q.strip() if q else None
    if not query:
        logger.debug("Player search query is empty. Redirecting to homepage.")
        try:
            home_url = request.url_for('home', lang=lang)
        except Exception as e:
            logger.warning(f"Could not generate URL for 'home', falling back to '/': {e}")
            home_url = f"/{lang}"
        return RedirectResponse(url=home_url, status_code=status.HTTP_302_FOUND)

    # シェルHTMLを即時返却（検索処理なし）
    context = {
        "request": request,
        "lang": lang,
        "query": query,
        "page": page,
        "exact_match": exact,
        "include_old_names": old,
        "current_page": "home",
    }
    return templates.TemplateResponse("player/search_results.html", context)


# プレイヤー検索結果フラグメント（実際の検索処理はここで行う）
@api_router.get("/search", name="get_player_search_fragment")
async def get_player_search_fragment(
    request: Request,
    lang: str,
    q: str | None = Query(None, min_length=1, max_length=50),
    page: int = Query(1, ge=1),
    exact: bool | None = Query(False),
    old: bool | None = Query(False),
    db: asyncpg.Connection = Depends(get_shared_db),
):
    query = q.strip() if q else None
    if not query:
        return templates.TemplateResponse(
            "player/fragments/search_error.html",
            {"request": request, "lang": lang, "error_type": "no_query"},
            status_code=400
        )

    players_on_page: list[dict[str, Any]] = []
    total_players = 0
    per_page = 60
    pagination_info = {}
    result_capped = False
    exact_on = bool(exact)
    old_on = bool(old)
    search_started = time.perf_counter()

    try:
        players_on_page, total_players, result_capped = await search_players_fast(
            query=query,
            db=db,
            page=page,
            per_page=per_page,
            exact_match=exact_on,
            include_previous_names=old_on
        )
        for player in players_on_page:
            player["icon_path"] = "/" + get_icon_path(player["icon_id"])

        total_pages = math.ceil(total_players / per_page) if total_players > 0 else 1
        current_page = min(page, total_pages) if total_pages > 0 else 1
        current_page = max(1, current_page)
        pagination_info = {
            "current_page": current_page,
            "total_pages": total_pages,
            "per_page": per_page,
            "total_items": total_players,
            "has_prev": current_page > 1,
            "has_next": current_page < total_pages,
            "prev_num": current_page - 1 if current_page > 1 else None,
            "next_num": current_page + 1 if current_page < total_pages else None,
            "page_numbers": [p for p in range(max(1, current_page - 2), min(total_pages, current_page + 2) + 1)]
        }

    except TimeoutError:
        elapsed = time.perf_counter() - search_started
        logger.warning(
            f"Player search timed out for query '{query}' "
            f"(exact={exact_on}, old={old_on}, page={page}, len={len(query)}, elapsed={elapsed:.2f}s)"
        ) # タイムアウトエラーはWARNINGに
        return templates.TemplateResponse(
            "player/fragments/search_error.html",
            {"request": request, "lang": lang, "error_type": "timeout"},
            status_code=200
        )
    except DataBaseError as db_err:
        elapsed = time.perf_counter() - search_started
        logger.error(
            f"Database error during player search fragment for query '{query}' "
            f"(exact={exact_on}, old={old_on}, page={page}, elapsed={elapsed:.2f}s): {db_err}"
        )
        return templates.TemplateResponse(
            "player/fragments/search_error.html",
            {"request": request, "lang": lang, "error_type": "server_error"},
            status_code=500
        )
    except Exception as e:
        elapsed = time.perf_counter() - search_started
        logger.error(
            f"Error in player search fragment for query '{query}' "
            f"(exact={exact_on}, old={old_on}, page={page}, elapsed={elapsed:.2f}s): {e}",
            exc_info=True
        )
        return templates.TemplateResponse(
            "player/fragments/search_error.html",
            {"request": request, "lang": lang, "error_type": "server_error"},
            status_code=500
        )

    add_log_info(request, f"クエリ: {query} | ページ: {pagination_info.get('current_page', 1)}/{pagination_info.get('total_pages', 1)}")
    if exact_on: add_log_info(request, "完全一致: オン")
    if old_on: add_log_info(request, "改名前を含める: オン")

    context = {
        "request": request,
        "lang": lang,
        "query": query,
        "players": players_on_page,
        "pagination": pagination_info,
        "exact_match": exact,
        "include_old_names": old,
        "result_capped": result_capped,
        "current_page": "home",
    }
    return templates.TemplateResponse("player/fragments/search_fragment.html", context)



async def _extend_player_tracking(
    *,
    formatted_tag: str,
    db: asyncpg.Connection,
    current_user: User,
    lang: str,
    token_cost: int,
    elixir_cost: int,
    hours: int,
    success_message_ja: str,
    success_message_en: str,
) -> JSONResponse:
    player = await get_player_for_tracking_extension(formatted_tag, db)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    use_elixir = (current_user.auto_track_elixirs or 0) >= elixir_cost
    if not use_elixir and current_user.tokens < token_cost:
        message = f"トークンが足りません。(現在所持: {current_user.tokens})" if lang == "ja" else f"Not enough tokens. (You have: {current_user.tokens})"
        return JSONResponse({"success": False, "message": message}, status_code=400)

    try:
        async with db.transaction():
            if use_elixir:
                success_spend = await current_user.spend_elixirs(db, elixir_cost)
                if not success_spend:
                    raise ValueError("エリクサー不足により処理を中断しました。")
            else:
                success_spend = await current_user.spend_tokens(db, token_cost)
                if not success_spend:
                    raise ValueError("トークン不足により処理を中断しました。")
            await add_auto_tracking_time(player, hours)
    except ValueError as e:
        logger.warning(f"自動追跡時間の延長処理を中断 (User: {current_user.name}): {e}")
        if use_elixir:
            message = "エリクサーが足りません。" if lang == "ja" else "Not enough elixir."
        else:
            message = "トークンが足りません。" if lang == "ja" else "Not enough tokens."
        return JSONResponse({"success": False, "message": message}, status_code=400)
    except Exception as e:
        logger.error(f"自動追跡時間の延長処理中にエラー (User: {current_user.name}, Player: {player.tag}): {e}", exc_info=True)
        message = "処理中にエラーが発生しました。" if lang == "ja" else "An error occurred during processing."
        return JSONResponse({"success": False, "message": message}, status_code=500)

    message = (success_message_ja if lang == "ja" else success_message_en).format(name=player.name)
    return JSONResponse({"success": True, "message": message})


@router.post("/extend-tracking/{tag}", name="extend_player_tracking")
async def extend_player_tracking(
    request: Request,
    tag: str,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user) # ログイン必須
):
    lang = request.path_params.get("lang", "ja") # リクエストから言語を取得

    formatted_tag = format_tag(tag)
    if not confirm_tag(formatted_tag):
        raise HTTPException(status_code=400, detail="Invalid player tag format.")

    return await _extend_player_tracking(
        formatted_tag=formatted_tag,
        db=db,
        current_user=current_user,
        lang=lang,
        token_cost=10,
        elixir_cost=24,
        hours=24,
        success_message_ja="プレイヤー「{name}」の自動追跡時間を24時間延長しました。",
        success_message_en="Successfully extended auto-tracking for '{name}' by 24 hours.",
    )


# プレイヤー自動追跡時間延長(10日間)のエンドポイント
@router.post("/extend-tracking-10days/{tag}", name="extend_player_tracking_10days")
async def extend_player_tracking_10days(
    request: Request,
    tag: str,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user) # ログイン必須
):
    lang = request.path_params.get("lang", "ja") # リクエストから言語を取得

    formatted_tag = format_tag(tag)
    if not confirm_tag(formatted_tag):
        raise HTTPException(status_code=400, detail="Invalid player tag format.")

    return await _extend_player_tracking(
        formatted_tag=formatted_tag,
        db=db,
        current_user=current_user,
        lang=lang,
        token_cost=90,
        elixir_cost=216,
        hours=240,
        success_message_ja="プレイヤー「{name}」の自動追跡時間を10日間追加しました。",
        success_message_en="Successfully extended auto-tracking for '{name}' by 10 days.",
    )


# [この部分は公開用リポジトリでは非公開にされています]


# バトル履歴保存期間延長のエンドポイント
MAX_BATTLE_LOG_RETENTION_MONTHS = 120

@router.post("/extend-retention/{tag}", name="extend_player_retention")
async def extend_player_retention(
    request: Request,
    tag: str,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user) # ログイン必須
):
    lang = request.path_params.get("lang", "ja")

    # 1. プレイヤータグを検証
    formatted_tag = format_tag(tag)
    if not confirm_tag(formatted_tag):
        raise HTTPException(status_code=400, detail="Invalid player tag format.")

    # 2. プレイヤー情報を取得
    try:
        player = await get_player(formatted_tag, db)
    except BrawlStarsAPIError:
        raise HTTPException(status_code=404, detail="Player not found")

    # 3. 現在の保存期間を取得し、上限チェック
    current_retention = await get_battle_log_retention_months(db=db, tag=formatted_tag)
    current_months = current_retention if current_retention is not None else settings.DEFAULT_BATTLE_LOG_RETENTION_MONTHS

    if current_months >= MAX_BATTLE_LOG_RETENTION_MONTHS:
        message = "保存期間は既に上限に達しています。" if lang == "ja" else "Storage period has already reached the maximum."
        return JSONResponse({"success": False, "message": message}, status_code=400)

    # 4. エリクサーまたはトークンが足りるかチェック
    TOKEN_COST = 100
    ELIXIR_COST = 240
    use_elixir = (current_user.auto_track_elixirs or 0) >= ELIXIR_COST
    if not use_elixir and current_user.tokens < TOKEN_COST:
        message = f"トークンが足りません。(現在所持: {current_user.tokens})" if lang == "ja" else f"Not enough tokens. (You have: {current_user.tokens})"
        return JSONResponse({"success": False, "message": message}, status_code=400)

    # 5. トランザクションを開始
    new_months = min(current_months + 1, MAX_BATTLE_LOG_RETENTION_MONTHS)
    try:
        async with db.transaction():
            if use_elixir:
                success_spend = await current_user.spend_elixirs(db, ELIXIR_COST)
                if not success_spend:
                    raise ValueError("エリクサー不足により処理を中断しました。")
            else:
                success_spend = await current_user.spend_tokens(db, TOKEN_COST)
                if not success_spend:
                    raise ValueError("トークン不足により処理を中断しました。")

            # 7. 保存期間を更新
            await extend_battle_log_retention(db=db, tag=formatted_tag, new_months=new_months)

    except ValueError as e:
        logger.warning(f"バトル履歴保存期間の延長処理を中断 (User: {current_user.name}): {e}")
        if use_elixir:
            message = "エリクサーが足りません。" if lang == "ja" else "Not enough elixir."
        else:
            message = "トークンが足りません。" if lang == "ja" else "Not enough tokens."
        return JSONResponse({"success": False, "message": message}, status_code=400)
    except Exception as e:
        logger.error(f"バトル履歴保存期間の延長処理中にエラー (User: {current_user.name}, Player: {player.tag}): {e}", exc_info=True)
        message = "処理中にエラーが発生しました。" if lang == "ja" else "An error occurred during processing."
        return JSONResponse({"success": False, "message": message}, status_code=500)

    # 8. プレイヤーキャッシュ内の保存期間を部分更新（API再呼び出し不要）
    try:
        player_cache_key = f"player:{formatted_tag}"
        cached_player_data = await get_cache(player_cache_key)
        if cached_player_data and isinstance(cached_player_data, dict):
            cached_player_data["battle_log_retention_months"] = new_months
            await set_cache(key=player_cache_key, value=cached_player_data, ttl=1800)
    except Exception as e:
        logger.warning(f"プレイヤーキャッシュの部分更新に失敗 (Player: {formatted_tag}): {e}")

    # 9. 成功レスポンス
    # 延長後の期間を表示用にフォーマット
    new_years = new_months // 12
    new_remaining = new_months % 12
    if lang == "ja":
        if new_years > 0 and new_remaining > 0:
            period_str = f"{new_years}年 {new_remaining}ヶ月間"
        elif new_years > 0:
            period_str = f"{new_years}年間"
        else:
            period_str = f"{new_remaining}ヶ月間"
        message = f"プレイヤー「{player.name}」のバトル履歴保存期間を{period_str}に延長しました。"
    else:
        if new_years > 0 and new_remaining > 0:
            period_str = f"{new_years} year{'s' if new_years > 1 else ''} {new_remaining} month{'s' if new_remaining > 1 else ''}"
        elif new_years > 0:
            period_str = f"{new_years} year{'s' if new_years > 1 else ''}"
        else:
            period_str = f"{new_remaining} month{'s' if new_remaining > 1 else ''}"
        message = f"Successfully extended battle log storage for '{player.name}' to {period_str}."

    return JSONResponse({"success": True, "message": message})
