"""公式APIのバトル履歴（players/{tag}/battlelog の1件）を battles テーブルの1行にする処理と、その保存。

バトルの形はモードによって違う。
- 対戦モード（teams が2チーム）: result・duration がある。全員に stats（キル・デス・ダメージなど）が入る。
- バトルロイヤル系（ソロは players、デュオ・トリオは teams）: rank がある。result・duration・stats は無い。
  味方・敵のパワーとトロフィーだけは teammate_stats / opponent_stats に保存する（スタッツの6個は NULL）。
- デュエル（players が2人で、1人ずつ brawlers を持つ）: result・duration がある。stats は無い。
- ボスファイトなど（players と、あれば bigBrawler）: 全員が味方。
"""
import datetime
from dataclasses import dataclass, field
from typing import Any

import asyncpg

from app.core.logger import logger
from app.exceptions.custom_exceptions import DataBaseError
from app.services.map_mode_catalog import normalize_official_mode_id, prepare_battle_mode_id
from app.utils.utils import parse_api_utc_datetime

# battles / archived_battles の列。ここが唯一の一覧で、保存・アーカイブへの移管・復元はここを見る
# (archived_battles にだけある archived_at は含めない)。列を足すときは models.py とここに足す。
RESULT_DATA_COLUMNS = (
    "duration", "kills", "deaths", "damage_dealt", "healing", "damage_taken", "mode_stat_type", "mode_stat",
    "banned_brawler", "star_player_slot", "teammate_stats", "opponent_stats",
    "teammate_banned_brawlers", "opponent_banned_brawlers",
)
BATTLE_COLUMNS = (
    "tag", "datetime", "event_id", "event_mode", "event_map", "battle_mode", "mode_id", "battle_type", "result", "rank",
    "is_starplayer", "brawler", "power", "trophies", "trophy_change", "brawlers", "team_size", "num_of_teams",
    "teammate_tags", "opponent_tags", "teammate_names", "opponent_names", "teammate_brawlers", "opponent_brawlers",
    "ranked_score_after",
    *RESULT_DATA_COLUMNS,
)

# teammate_stats / opponent_stats は、1人ぶんの値をこの順に並べた整数の配列（teammate_tags / opponent_tags と同じ人の順）。
# 値が無いところは NULL（mode_stat はノックアウトなど modeStat の無いモード、power・trophies はフレンドバトルなどで -1）。
# バトルロイヤル系は stats が無いので、power・trophies だけが入り、ほかの6個は NULL。
# 配列があってもスタッツがあるとは限らないので、スタッツの有無は自分の kills で見る。
PLAYER_STATS_FIELDS = ("kills", "deaths", "damage_dealt", "healing", "damage_taken", "mode_stat", "power", "trophies")
PLAYER_STATS_WIDTH = len(PLAYER_STATS_FIELDS)

# modeStat.type → mode_stat_type の番号。APIに知らない type が増えたら 0 で保存し、ここに番号を足す
MODE_STAT_TYPES = {"gemsHeld": 1, "goals": 2, "bountyStars": 3, "safeDamage": 4, "hotZoneTime": 5}
MODE_STAT_TYPE_UNKNOWN = 0

# これより前のバトルは、リザルトのデータを保存していなかった頃に保存された行かもしれないので、APIに残っている分で埋める。
# 保存を始めた日より後ろに置いてある。この日を過ぎて十分に経ったら、補完の処理ごと消してよい。
RESULT_DATA_FILL_BEFORE = datetime.datetime(2026, 12, 1, tzinfo=datetime.timezone.utc)

_INT16_MAX = 32767
_INT32_MAX = 2147483647
_warned_mode_stat_types: set[str] = set()


@dataclass
class APIBattle:
    """APIのバトル1件を、battles の列の形にしたもの。"""
    datetime: datetime.datetime
    event_id: int | None  # おそらく必ず存在する。マップメーカーのマップでは0
    event_mode: str | None
    event_map: str | None  # 英語のマップ名。マップメーカーのマップでは None
    battle_mode: str | None  # バトル自体のモード。5対5は3対3と同じ名前、トリオ・メガデュオは duoShowdown になる
    mode_id: int | None
    battle_type: str | None  # 通常='ranked', ガチバトル='soloRanked', フレンドバトル='friendly', ほか 'challenge' 'tournament' など
    result: str  # 勝利 "w", 引き分け "d", 敗北 "l"。順位のあるモードは順位から決める
    rank: int | None
    is_starplayer: bool | None  # スタープレイヤーのいないバトルは None
    trophy_change: int | None  # APIに項目が無いときは 0（変動が0のバトルでも項目ごと無いことがある）
    team_size: int
    num_of_teams: int
    brawler: int | None = None  # デュエルでは None（brawlers に入る）
    power: int | None = None
    trophies: int | None = None  # 変動前のトロフィー。ガチバトルではランク
    brawlers: list = field(default_factory=list)  # デュエルで使ったキャラ。(ID, パワー, トロフィー, トロフィー変動) をトロフィーの多い順に
    teammate_tags: list[str] = field(default_factory=list)
    opponent_tags: list[str] = field(default_factory=list)
    teammate_names: list[str] = field(default_factory=list)
    opponent_names: list[str] = field(default_factory=list)
    teammate_brawlers: list[int] = field(default_factory=list)
    opponent_brawlers: list[int] = field(default_factory=list)
    ranked_score_after: int | None = None  # 保存の直前に、最新のガチバトルにだけ入れる
    # ここから下はリザルトのデータ（RESULT_DATA_COLUMNS）
    duration: int | None = None  # 秒。開始前に終わったバトルは負の値で来る。バトルロイヤル系には無い
    kills: int | None = None
    deaths: int | None = None
    damage_dealt: int | None = None
    healing: int | None = None
    damage_taken: int | None = None
    mode_stat_type: int | None = None  # MODE_STAT_TYPES の番号
    mode_stat: int | None = None
    banned_brawler: int | None = None  # 自分がBANしたキャラ（ダイヤモンド以上のガチバトル・パワーマッチのフレンドバトルなど）
    star_player_slot: int | None = None  # スタープレイヤーの位置。0=自分、1〜=味方（teammate_tags の順）、その後ろ=敵（opponent_tags の順）
    teammate_stats: list[int | None] | None = None
    opponent_stats: list[int | None] | None = None
    teammate_banned_brawlers: list[int | None] | None = None
    opponent_banned_brawlers: list[int | None] | None = None


# [この部分は公開用リポジトリでは非公開にされています]


def parse_api_battle(tag: str, item: dict) -> APIBattle:
    """APIのバトル1件を、tag のプレイヤーから見た battles の1行にする。item は書き換えない。

    バトルの中に tag のプレイヤーが見つからないなど、形が想定と違うときは例外を出す。
    """
    # [この部分は公開用リポジトリでは非公開にされています]


_INSERT_BATTLE_QUERY = f"""
    INSERT INTO battles ({", ".join(BATTLE_COLUMNS)})
    VALUES ({", ".join(f"${i}" for i in range(1, len(BATTLE_COLUMNS) + 1))})
    ON CONFLICT DO NOTHING
"""
# duration はリザルトのデータがあるバトル（対戦モード・デュエル）に必ずあるので、埋めたかどうかの目印にする
_FILL_RESULT_DATA_QUERY = f"""
    UPDATE battles
    SET {", ".join(f"{column} = ${i}" for i, column in enumerate(RESULT_DATA_COLUMNS, start=3))}
    WHERE tag = $1 AND datetime = $2 AND duration IS NULL
"""
# バトルロイヤル系は duration が無いので、味方・敵の配列（パワーとトロフィー）が空かどうかを目印にする
_FILL_SHOWDOWN_DATA_QUERY = """
    UPDATE battles
    SET teammate_stats = $3, opponent_stats = $4
    WHERE tag = $1 AND datetime = $2 AND teammate_stats IS NULL AND opponent_stats IS NULL
"""


async def insert_api_battle(db: asyncpg.Connection, tag: str, battle: APIBattle) -> bool:
    """バトルを battles テーブルに追加する（すでにあれば何もしない）。

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
    values = {column: getattr(battle, column) for column in BATTLE_COLUMNS if column != "tag"}
    values["tag"] = tag
    values["mode_id"] = mode_id
    try:
        status = await db.execute(_INSERT_BATTLE_QUERY, *(values[column] for column in BATTLE_COLUMNS))
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e
    return status != "INSERT 0 1"


async def fill_missing_result_data(db: asyncpg.Connection, tag: str, saved_battles: list[APIBattle]) -> int:
    """保存済みのバトルのうち、リザルトのデータを保存していなかった頃の行を、APIに残っている分で埋める。

    saved_battles は新しい順で、先頭が「保存済みだった最初のバトル」。埋まっている行に当たったら、それより古い行も
    埋まっているのでやめる（対戦モード・デュエルと、パワーとトロフィーだけを埋めるバトルロイヤル系は別々に数える）。
    RESULT_DATA_FILL_BEFORE より後のバトルでは何もしない。

    Returns:
        int: 埋めた行の数
    """
    filled = 0
    # 対戦モード・デュエル（duration が目印）と、バトルロイヤル系（配列が目印）は別々に進める。
    # どちらも、埋まっている行に当たったらその種類はやめる
    pending = {"result": True, "showdown": True}
    for battle in saved_battles:
        if battle.datetime >= RESULT_DATA_FILL_BEFORE or not any(pending.values()):
            break
        if battle.duration is not None:
            kind, query = "result", _FILL_RESULT_DATA_QUERY
            args = [getattr(battle, column) for column in RESULT_DATA_COLUMNS]
        elif battle.teammate_stats is not None or battle.opponent_stats is not None:
            kind, query = "showdown", _FILL_SHOWDOWN_DATA_QUERY
            args = [battle.teammate_stats, battle.opponent_stats]
        else:
            continue  # 埋めるものが無い（ボスファイトなど）
        if not pending[kind]:
            continue
        try:
            status = await db.execute(query, tag, battle.datetime, *args)
        except asyncpg.PostgresError as e:
            raise DataBaseError(e) from e
        if status == "UPDATE 1":
            filled += 1
        else:
            pending[kind] = False
    return filled


async def save_api_battles(db: asyncpg.Connection, tag: str, battles: list[APIBattle]) -> None:
    """新しい順に並んだバトルを保存する。保存済みに当たったら、それより古いバトルも保存済みなのでやめる。"""
    for index, battle in enumerate(battles):
        if await insert_api_battle(db, tag, battle):
            await fill_missing_result_data(db, tag, battles[index:])
            break
