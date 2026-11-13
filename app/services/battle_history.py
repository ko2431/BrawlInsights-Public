"""バトル履歴ページの読み込みと集計。

そのプレイヤーの全バトルを毎回読むが、集計に要る列だけにして、1行をオブジェクトにしない。
バトルカードに出す分（1ページぶん）だけ、全部の列を主キーで読んで Battle にする。
"""
import datetime
from types import SimpleNamespace
from typing import Any

import asyncpg
import orjson

from app.services.battle_result_view import STATS_SINCE
from app.services.brawl_service import Battle, _BATTLE_SEARCH_COLUMNS, _same_members, ensure_catalog
from app.services.map_mode_catalog import (
    get_map_by_en, get_map_by_id, get_mode_by_id, get_mode_by_slug, mode_icon_candidates, resolve_map_filter_label, resolve_mode_filter_label,
)

MAX_BATTLES = 100000
ITEMS_PER_PAGE = 60
NEW_PLAYER_NOTICE_MAX = 25  # API が1回で返すバトルの数。保存済みがこれ以下なら「これから蓄積されます」の案内を出す
MATES_LIMIT = 40
TREND_MAX_POINTS = 600
RECENT_DAYS = 93  # 日ごと・週ごとの勝敗に使う範囲（13週 + 端数）
MIN_PLAYER_TAG_LENGTH = 5  # "#" を含む。これより短いタグはボット

# [この部分は公開用リポジトリでは非公開にされています]


class BattleSet:
    """一覧の1件。セットにまとめた複数の試合、またはまとめていない1試合。"""
    __slots__ = ("rows", "result", "set_w", "set_d", "set_l", "ranked_score_after", "mode_id")

    def __init__(self, rows: list[asyncpg.Record], mode_id: int | None) -> None:
        self.rows = rows  # 新しい順。先頭が代表（最後の試合）
        self.result: str = rows[0]["result"]
        self.set_w: int | None = None
        self.set_d: int | None = None
        self.set_l: int | None = None
        self.ranked_score_after: int | None = rows[0]["ranked_score_after"]
        self.mode_id = mode_id

    @property
    def head(self) -> asyncpg.Record:
        return self.rows[0]


# [この部分は公開用リポジトリでは非公開にされています]


class BattleScan:
    """1人ぶんのバトルを読んでセットにまとめたもの。絞り込み・件数・ページの切り出しを受け持つ。"""

    def __init__(self, tag: str, rows: list[asyncpg.Record], battle_types: frozenset[str] | None) -> None:
        self.tag = tag
        self.raw_count = len(rows)
        self.sets = group_into_sets(rows, battle_types)

    @property
    def total(self) -> int:
        return len(self.sets)

    # [この部分は公開用リポジトリでは非公開にされています]


async def scan_battles(
    db: asyncpg.Connection, tag: str, *, tab: str,
    after: datetime.datetime | None = None, until: datetime.datetime | None = None,
) -> BattleScan:
    """タブと期間に合うバトルを新しい順に読み、セットにまとめる。"""
    # [この部分は公開用リポジトリでは非公開にされています]


async def count_saved_battles(db: asyncpg.Connection, tag: str, limit: int = NEW_PLAYER_NOTICE_MAX + 1) -> int:
    """保存済みのバトルの数（limit まで数えたら打ち切る）。"""
    return await db.fetchval("SELECT count(*) FROM (SELECT 1 FROM battles WHERE tag = $1 LIMIT $2) AS saved", tag, limit) or 0


# ===== 集計（カードに出すデータ） =====

# 集計1つぶんの並び。JS（battle_stats.js）の AGG と同じ順にする
AGG_FIELDS = ("n", "w", "d", "l", "sp", "sn", "k", "dth", "dd", "he", "dt", "dn", "du", "tc", "g")
_N, _W, _D, _L, _SP, _SN, _K, _DTH, _DD, _HE, _DT, _DN, _DU, _TC, _G = range(len(AGG_FIELDS))
_RESULT_INDEX = {"w": _W, "d": _D, "l": _L}


def _new_agg() -> list[int]:
    return [0] * len(AGG_FIELDS)


# [この部分は公開用リポジトリでは非公開にされています]


__all__ = [
    "AGG_FIELDS", "BattleScan", "BattleSet", "ITEMS_PER_PAGE", "NEW_PLAYER_NOTICE_MAX",
    "build_stats", "count_saved_battles", "group_into_sets", "scan_battles",
]
