"""バトル履歴ページのバトルカードで開く「リザルト」のデータを組み立てる。

全件の読み込み（search_battles）にはリザルトの列を足さず、表示するページのバトル（セットは中の試合も）だけを
主キーで読む。組み立てた値は小さな JSON としてページに埋め込み、カードを開いたときに JS（battle_results.js）が描く。
"""
import datetime
from typing import Any

import asyncpg
import orjson

from app.services.battle_log import PLAYER_STATS_WIDTH, rank_result_bounds

# スタッツ（stats）が公式APIのバトル履歴に入り始めた日時（2026年11月のアップデート）。これより前のバトルには無い
STATS_SINCE = datetime.datetime(2026, 11, 3, 9, 41, tzinfo=datetime.timezone.utc)

_RESULT_ROW_QUERY = """
    SELECT datetime, duration, kills, deaths, damage_dealt, healing, damage_taken, mode_stat_type, mode_stat,
           banned_brawler, star_player_slot, teammate_stats, opponent_stats, teammate_banned_brawlers, opponent_banned_brawlers
    FROM battles
    WHERE tag = $1 AND datetime = ANY($2::timestamptz[])
"""


def _chunks(values: list | None, width: int = PLAYER_STATS_WIDTH) -> list[list]:
    values = values or []
    return [values[i:i + width] for i in range(0, len(values), width)]


def _positive(value: Any) -> int | None:
    """パワー・トロフィーは、値が無いと -1 で来る（フレンドバトルなど）。"""
    return value if isinstance(value, int) and value >= 0 else None


def _at(values: list | None, index: int) -> Any:
    return values[index] if values and index < len(values) else None


def _brawler_name(row: asyncpg.Record, lang: str) -> str:
    name_en = row["en"] or str(row["id"])
    if lang != "ja":
        return name_en
    ja = row["ja"]
    if isinstance(ja, str) and ja.startswith("["):
        try:
            ja = orjson.loads(ja)
        except orjson.JSONDecodeError:
            pass
    if isinstance(ja, list):
        ja = ja[0] if ja else None
    return ja if isinstance(ja, str) and ja.strip() else name_en


# [この部分は公開用リポジトリでは非公開にされています]


async def attach_battle_results(
    db: asyncpg.Connection, tag: str, self_name: str, battles: list, lang: str, rows: dict[Any, Any] | None = None,
) -> dict[str, Any]:
    """表示するページのバトルに、カードとダイジェストで使う値を付け、開いたときのデータ（ページに埋め込む）を返す。

    バトルに付ける値:
        result_view_kind: 開くボタンの種類。"result"（スタッツか順位がある）/ "members" / None（ボタンを出さない）
        my_ban: 自分がBANしたキャラ
        tip_kills / tip_deaths: 自分のキル・デス（セットは合計）。スタッツが無ければ None
        tip_brawler_names: 使ったキャラの名前

    rows: リザルトの列を読み込み済みなら渡す（日時 → 行）。渡さなければここで読む。
    """
    for battle in battles:
        battle.result_view_kind = None
        battle.my_ban = None
        battle.my_ban_name = ""
        battle.tip_kills = battle.tip_deaths = None
        battle.tip_brawler_names = []
    cards: dict[str, Any] = {}
    ban_names: dict[str, str] = {}
    if not battles:
        return {"cards": cards, "names": ban_names}

    # [この部分は公開用リポジトリでは非公開にされています]
    return {"cards": cards, "names": ban_names}
