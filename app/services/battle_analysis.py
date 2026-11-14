"""バトル履歴ページの「詳細分析」タブ。

ログイン中は、そのプレイヤーのバトルを1試合1行の小さな数値データ（列ごとの配列）で1回だけ返し、絞り込み・集計・並べ替えはブラウザで行う
（static/js/pages/battle_analysis.js）。セットの判定と勝敗だけはここで付ける。
未ログインには、画面に見せるぶんの集計（上位のキャラなど）だけを返す。1人ぶんをまとめて返す出口は、ログインと回数制限で守る。
"""
import asyncio
import datetime
from typing import Any

import asyncpg

from app.core.cache import get_redis
from app.core.logger import logger
from app.services.battle_history import (
    AGG_FIELDS, MIN_PLAYER_TAG_LENGTH, RANKED_RANK_MIN_SCORES, SET_BATTLE_TYPES, BattleSet,
    _PAGE_QUERY, _add_set, _has_mvp, _loads, _mode_color, _new_agg, group_into_sets, load_brawler_names,
)
from app.services.battle_result_view import STATS_SINCE
from app.services.brawl_service import Battle, ensure_catalog
from app.utils.utils import get_ranked_seasons_for_filter
from app.services.map_mode_catalog import mode_icon_candidates, resolve_map_filter_label, resolve_mode_filter_label

# [この部分は公開用リポジトリでは非公開にされています]
