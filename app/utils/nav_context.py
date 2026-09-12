"""共有ページ（マップ個別・キャラ図鑑）のタブ所属と戻る先。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlencode

from app.utils.utils import confirm_tag, format_tag

VALID_TABS = frozenset({"home", "stats", "tools"})
VALID_FROM_SOURCES = frozenset({
    "battles",
    "profile",
    "map_rotation",
    "guide_menu",
    "map",
    "multiplayer_tier",
    "ranked_tier",
})
VALID_BATTLES_TABS = frozenset({"all", "trophy", "powerleague", "friendly"})
FROM_DEFAULT_TAB: dict[str, str | None] = {
    "battles": "home",
    "profile": "home",
    "map_rotation": "tools",
    "guide_menu": "tools",
    "map": None,
    "multiplayer_tier": "stats",
    "ranked_tier": "stats",
}
PageKind = Literal["map", "brawler_guide"]


@dataclass(frozen=True, slots=True)
class NavContext:
    tab: str
    from_source: str | None
    player: str | None
    battles_tab: str | None
    map_id: int | None
    back_fallback_url: str
    query: str
    hidden_fields: dict[str, str]
    child_nav_query: str


def build_nav_query(
    *,
    tab: str,
    from_source: str | None = None,
    player: str | None = None,
    battles_tab: str | None = None,
    map_id: int | None = None,
) -> str:
    """テンプレートのリンク用クエリ（先頭の ? なし）。"""
    return urlencode(_query_params(
        tab=tab,
        from_source=from_source,
        player=player,
        battles_tab=battles_tab,
        map_id=map_id,
    ))


def resolve_nav_context(
    *,
    lang: str,
    page_kind: PageKind,
    tab: str | None = None,
    from_source: str | None = None,
    is_stats_tab: bool = False,
    is_tools_tab: bool = False,
    player: str | None = None,
    battles_tab: str | None = None,
    map_id: int | None = None,
    current_map_id: int | None = None,
) -> NavContext:
    """クエリから所属タブと戻る先を決める。"""
    normalized_from = _normalize_from(from_source)
    if normalized_from is None and page_kind == "map":
        if is_stats_tab:
            normalized_from = "multiplayer_tier"
        elif is_tools_tab:
            normalized_from = "map_rotation"

    normalized_tab = _normalize_tab(tab)
    if normalized_tab is None and normalized_from is not None:
        normalized_tab = FROM_DEFAULT_TAB.get(normalized_from)
    if normalized_tab is None:
        if is_stats_tab:
            normalized_tab = "stats"
        elif is_tools_tab:
            normalized_tab = "tools"
        else:
            normalized_tab = "tools"

    normalized_player = _normalize_player(player)
    normalized_battles_tab = _normalize_battles_tab(battles_tab)
    if normalized_from == "battles" and normalized_battles_tab is None:
        normalized_battles_tab = "all"
    normalized_map_id = _normalize_map_id(map_id)

    hidden_fields = _query_params(
        tab=normalized_tab,
        from_source=normalized_from,
        player=normalized_player,
        battles_tab=normalized_battles_tab,
        map_id=normalized_map_id,
    )
    query = urlencode(hidden_fields)
    back_fallback_url = _build_fallback_url(
        lang=lang,
        page_kind=page_kind,
        tab=normalized_tab,
        from_source=normalized_from,
        player=normalized_player,
        battles_tab=normalized_battles_tab,
        map_id=normalized_map_id,
    )

    if page_kind == "map" and current_map_id is not None:
        child_nav_query = build_nav_query(
            tab=normalized_tab,
            from_source="map",
            map_id=current_map_id,
        )
    else:
        child_nav_query = query

    return NavContext(
        tab=normalized_tab,
        from_source=normalized_from,
        player=normalized_player,
        battles_tab=normalized_battles_tab,
        map_id=normalized_map_id,
        back_fallback_url=back_fallback_url,
        query=query,
        hidden_fields=hidden_fields,
        child_nav_query=child_nav_query,
    )


def nav_template_vars(ctx: NavContext) -> dict[str, object]:
    """ルーターのテンプレートコンテキストへ載せる値。"""
    return {
        "current_page": ctx.tab,
        "nav_tab": ctx.tab,
        "nav_from": ctx.from_source,
        "nav_query": ctx.query,
        "nav_hidden_fields": ctx.hidden_fields,
        "back_fallback_url": ctx.back_fallback_url,
        "brawler_guide_nav_query": ctx.child_nav_query,
    }


def _query_params(
    *,
    tab: str,
    from_source: str | None,
    player: str | None,
    battles_tab: str | None,
    map_id: int | None,
) -> dict[str, str]:
    params: dict[str, str] = {"tab": tab}
    if from_source:
        params["from"] = from_source
    if from_source in {"battles", "profile"} and player:
        params["player"] = player
    if from_source == "battles" and battles_tab:
        params["battles_tab"] = battles_tab
    if from_source == "map" and map_id is not None:
        params["map_id"] = str(map_id)
    return params


def _normalize_tab(raw: str | None) -> str | None:
    if not raw:
        return None
    value = raw.strip().lower()
    return value if value in VALID_TABS else None


def _normalize_from(raw: str | None) -> str | None:
    if not raw:
        return None
    value = raw.strip().lower()
    return value if value in VALID_FROM_SOURCES else None


def _normalize_battles_tab(raw: str | None) -> str | None:
    if not raw:
        return None
    value = raw.strip().lower()
    if value == "brawler":
        return "trophy"
    return value if value in VALID_BATTLES_TABS else None


def _normalize_player(raw: str | None) -> str | None:
    if not raw or not raw.strip():
        return None
    formatted = format_tag(raw.strip())
    if not confirm_tag(formatted):
        return None
    return formatted[1:]


def _normalize_map_id(raw: int | None) -> int | None:
    if raw is None or raw <= 0:
        return None
    return raw


def _build_fallback_url(
    *,
    lang: str,
    page_kind: PageKind,
    tab: str,
    from_source: str | None,
    player: str | None,
    battles_tab: str | None,
    map_id: int | None,
) -> str:
    prefix = f"/{lang}" if lang else "/ja"
    if from_source == "battles":
        if player:
            return f"{prefix}/player/battles/{player}?tab={battles_tab or 'all'}"
        return prefix
    if from_source == "profile":
        if player:
            return f"{prefix}/player/profile/{player}"
        return prefix
    if from_source == "map_rotation":
        return f"{prefix}/tools/map_rotation"
    if from_source == "guide_menu":
        return f"{prefix}/tools/brawler_guide/menu"
    if from_source == "map":
        if map_id is not None:
            return f"{prefix}/tools/map/{map_id}?tab={tab}"
        return _default_fallback_url(prefix, page_kind)
    if from_source == "multiplayer_tier":
        return f"{prefix}/stats/multiplayer_tier_list"
    if from_source == "ranked_tier":
        return f"{prefix}/stats/ranked_tier_list"
    return _default_fallback_url(prefix, page_kind)


def _default_fallback_url(prefix: str, page_kind: PageKind) -> str:
    if page_kind == "brawler_guide":
        return f"{prefix}/tools/brawler_guide/menu"
    return f"{prefix}/tools"
