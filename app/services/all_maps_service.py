"""マップ一覧: カタログからの全マップ・モード一覧と、マップ周期との連携用データ。"""
from __future__ import annotations

import re
from typing import Any

from app.services import map_mode_catalog
from app.services.map_mode_catalog import (
    MapInfo,
    ModeInfo,
    get_mode_theme,
    iter_maps,
    iter_modes,
    mode_icon_candidates,
    mode_sort_key,
)

SOLO_SHOWDOWN_MODE_ID = 48000006
# デュオ・トリオ バトルロイヤルは、ソロと同じマップ周期で出現する（周期にはソロのマップIDだけが載る）
SHOWDOWN_TEAM_MODE_IDS = frozenset({48000009, 48000038})
# 「HEIST_AUGCOMP2」「JUNEMAP7_PLACEHOLDER」のような、通常のゲームに出ない開発用マップの名前
_CODENAME_RE = re.compile(r"^[A-Z0-9]+(?:_[A-Z0-9]+)+$")

# カタログの世代ごとに組み立て結果を使い回す（カタログ再読込のたびに作り直す）
_memo_key: float | None = None
_memo_data: dict[str, Any] | None = None


def _is_junk_name(*names: str | None) -> bool:
    """名前が無い、ゲーム内にデータが無いことを示す ERROR 名、または開発用のコード名なら True。"""
    valid = [name.strip() for name in names if name and name.strip()]
    if not valid:
        return True
    return any(name.lower().startswith("error") or _CODENAME_RE.match(name) for name in valid)


def _is_listed_mode(mode: ModeInfo) -> bool:
    return not _is_junk_name(mode.ja, mode.en)


def _is_listed_map(map_info: MapInfo) -> bool:
    return not _is_junk_name(map_info.ja, map_info.en)


def _mode_order_key(mode: ModeInfo) -> tuple:
    # 有効モード（優先固定順 → ID順） → 無効モード（ID順）
    return (mode.disabled, *mode_sort_key(mode.id)[:2])


def _name_key(map_info: MapInfo) -> str:
    return (map_info.en or map_info.ja or "").strip().lower()


def _dedupe_adjacent(maps: list[MapInfo]) -> list[tuple[MapInfo, list[int]]]:
    """同じモード・同じ名前でIDが連番のマップは、同じマップが重複登録されたものとして1つにまとめる。
    残すのは有効なもの → IDが大きいもの。まとめた側のIDは (残すマップ, [他のID]) で返す。"""
    groups: list[list[MapInfo]] = []
    for map_info in sorted(maps, key=lambda item: item.id):
        last = groups[-1][-1] if groups else None
        if (
            last is not None
            and last.id + 1 == map_info.id
            and last.mode_id == map_info.mode_id
            and last.ja == map_info.ja
            and last.en == map_info.en
        ):
            groups[-1].append(map_info)
        else:
            groups.append([map_info])
    result: list[tuple[MapInfo, list[int]]] = []
    for group in groups:
        kept = max(group, key=lambda item: (not item.disabled, item.id))
        result.append((kept, [item.id for item in group if item.id != kept.id]))
    return result


def assemble_all_maps_data(modes: list[ModeInfo], maps: list[MapInfo]) -> dict[str, Any]:
    """カタログのモード・マップから、一覧表示用のデータを組み立てる。"""
    modes_by_id = {mode.id: mode for mode in modes if _is_listed_mode(mode)}
    listed = [
        map_info for map_info in maps
        if map_info.mode_id in modes_by_id and _is_listed_map(map_info)
    ]

    # デュオ・トリオのマップは、同名のソロマップのうち直前のIDのものと同じ周期で出現する
    solo_ids_by_name: dict[str, list[int]] = {}
    for map_info in listed:
        if map_info.mode_id == SOLO_SHOWDOWN_MODE_ID:
            solo_ids_by_name.setdefault(_name_key(map_info), []).append(map_info.id)

    def linked_solo_id(map_info: MapInfo) -> int | None:
        if map_info.mode_id not in SHOWDOWN_TEAM_MODE_IDS:
            return None
        candidates = [solo_id for solo_id in solo_ids_by_name.get(_name_key(map_info), []) if solo_id < map_info.id]
        return max(candidates) if candidates else None

    map_items: list[dict[str, Any]] = []
    used_mode_ids: set[int] = set()
    for map_info, duplicate_ids in _dedupe_adjacent(listed):
        mode = modes_by_id[map_info.mode_id]
        used_mode_ids.add(mode.id)
        item: dict[str, Any] = {
            "id": map_info.id,
            "ja": map_info.display_name("ja"),
            "en": map_info.display_name("en"),
            "mode": mode.id,
            # マップ自体、または所属モードが無効なら無効扱い
            "off": bool(map_info.disabled or mode.disabled),
        }
        # alt: 重複としてまとめたID（検索・周期判定に使う）/ link: 周期判定だけに使うソロのマップID
        if duplicate_ids:
            item["alt"] = duplicate_ids
        solo_id = linked_solo_id(map_info)
        if solo_id is not None:
            item["link"] = solo_id
        map_items.append(item)

    mode_items = [
        {
            "id": mode.id,
            "ja": mode.display_name("ja"),
            "en": mode.display_name("en"),
            "icons": mode_icon_candidates(mode.id, mode.slug),
            "theme": get_mode_theme(mode.id),
            "off": bool(mode.disabled),
        }
        for mode in sorted((modes_by_id[mode_id] for mode_id in used_mode_ids), key=_mode_order_key)
    ]
    return {"modes": mode_items, "maps": map_items}


def build_all_maps_data() -> dict[str, Any]:
    """ensure_catalog 済みであること。DBには問い合わせず、メモリ上のカタログから作る。"""
    global _memo_key, _memo_data
    key = map_mode_catalog._local_loaded_at
    if _memo_data is not None and _memo_key == key:
        return _memo_data
    data = assemble_all_maps_data(iter_modes(), iter_maps())
    _memo_key = key
    _memo_data = data
    return data


def build_rotation_hint(rotation_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """マップ周期の表示ペイロードから、出現日時の計算に必要な項目だけを抜き出す。"""
    hints: list[dict[str, Any]] = []
    for order, slot in enumerate((rotation_payload or {}).get("slots") or []):
        maps = slot.get("maps") or []
        if not maps:
            continue
        hints.append({
            "order": order,
            "durationMinutes": slot.get("durationMinutes"),
            "latestStart": slot.get("latestStart"),
            "anchorStart": slot.get("anchorStart"),
            "latestIndex": slot.get("latestIndex") or 0,
            "currentMapId": slot.get("currentMapId"),
            # 未確定枠(null)も周期の位置合わせに必要なので残す
            "maps": [item.get("map_id") for item in maps],
        })
    return hints
