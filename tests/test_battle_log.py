"""公式APIのバトル履歴1件を battles の1行にする処理（app/services/battle_log.py）を検証する。

APIの応答の形はモードごとに違うので、種別ごとの小さなデータで確かめる。
"""

import copy
import datetime

import pytest

from app.services import battle_log
from app.services.battle_log import PLAYER_STATS_WIDTH, parse_api_battle, result_from_rank

ME = "#ME"


def _player(tag, brawler_id=16000000, *, power=11, trophies=500, stats=None, banned=None, name=None):
    player = {"tag": tag, "name": name or f"name{tag}", "brawler": {"id": brawler_id, "name": "X", "power": power, "trophies": trophies}}
    if stats is not None:
        player["stats"] = stats
    if banned is not None:
        player["bannedBrawler"] = {"id": banned, "name": "B"}
    return player


def _stats(kills, deaths, dealt, healing, taken, mode_stat=None):
    stats = {"kills": kills, "deaths": deaths, "damageDealt": dealt, "healing": healing, "damageTaken": taken}
    if mode_stat is not None:
        stats["modeStat"] = {"type": mode_stat[0], "value": mode_stat[1]}
    return stats


def _item(battle, *, mode="brawlBall", mode_id=5, event_id=15000024, event_map="Backyard Bowl"):
    return {"battleTime": "20261112T142059.000Z", "event": {"id": event_id, "mode": mode, "modeId": mode_id, "map": event_map}, "battle": battle}


def _team_battle(*, battle_type="ranked", mode="brawlBall", result="victory", with_stats=True, with_bans=False, star="#T1"):
    def stats(i):
        return _stats(i, i + 1, 1000 * i, 10 * i, 500 * i, ("goals", i % 2)) if with_stats else None

    def ban(i):
        return 16000030 + i if with_bans else None

    teams = [
        [_player("#T1", 16000001, stats=stats(1), banned=ban(1)), _player(ME, 16000002, power=10, trophies=777, stats=stats(2), banned=ban(2)),
         _player("#T2", 16000003, stats=stats(3), banned=ban(3))],
        [_player("#O1", 16000004, stats=stats(4), banned=ban(4)), _player("#O2", 16000005, stats=stats(5), banned=ban(5)),
         _player("#O3", 16000006, power=9, trophies=321, stats=stats(6), banned=ban(6))],
    ]
    battle = {"mode": mode, "type": battle_type, "result": result, "duration": 95, "trophyChange": 8, "teams": teams,
              "starPlayer": {"tag": star, "name": "s", "brawler": {"id": 1, "name": "X", "power": 11, "trophies": 1}} if star else None}
    return _item(battle, mode=mode)


def test_team_battle_keeps_existing_fields():
    item = _team_battle()
    before = copy.deepcopy(item)
    battle = parse_api_battle(ME, item)
    assert item == before  # 入力は書き換えない
    assert battle.datetime == datetime.datetime(2026, 11, 12, 14, 20, 59, tzinfo=datetime.timezone.utc)
    assert (battle.event_id, battle.event_mode, battle.event_map, battle.battle_mode) == (15000024, "brawlBall", "Backyard Bowl", "brawlBall")
    assert battle.mode_id == 48000005
    assert (battle.battle_type, battle.result, battle.rank, battle.trophy_change) == ("ranked", "w", None, 8)
    assert (battle.brawler, battle.power, battle.trophies) == (16000002, 10, 777)
    assert (battle.team_size, battle.num_of_teams) == (3, 2)
    assert battle.teammate_tags == ["#T1", "#T2"] and battle.opponent_tags == ["#O1", "#O2", "#O3"]
    assert battle.teammate_names == ["name#T1", "name#T2"]
    assert battle.teammate_brawlers == [16000001, 16000003] and battle.opponent_brawlers == [16000004, 16000005, 16000006]
    assert battle.is_starplayer is False and battle.brawlers == [] and battle.ranked_score_after is None


def test_team_battle_result_data():
    battle = parse_api_battle(ME, _team_battle())
    assert battle.duration == 95
    assert (battle.kills, battle.deaths, battle.damage_dealt, battle.healing, battle.damage_taken) == (2, 3, 2000, 20, 1000)
    assert (battle.mode_stat_type, battle.mode_stat) == (battle_log.MODE_STAT_TYPES["goals"], 0)
    # 1人8個（キル・デス・与ダメージ・回復・被ダメージ・modeStat・パワー・トロフィー）を、タグと同じ順に
    assert len(battle.teammate_stats) == PLAYER_STATS_WIDTH * 2 and len(battle.opponent_stats) == PLAYER_STATS_WIDTH * 3
    assert battle.teammate_stats[:PLAYER_STATS_WIDTH] == [1, 2, 1000, 10, 500, 1, 11, 500]
    assert battle.opponent_stats[-PLAYER_STATS_WIDTH:] == [6, 7, 6000, 60, 3000, 0, 9, 321]
    assert battle.star_player_slot == 1  # 味方の1人目
    assert battle.banned_brawler is None and battle.teammate_banned_brawlers is None and battle.opponent_banned_brawlers is None


def test_star_player_slot_counts_self_teammates_then_opponents():
    assert parse_api_battle(ME, _team_battle(star=ME)).star_player_slot == 0
    assert parse_api_battle(ME, _team_battle(star=ME)).is_starplayer is True
    assert parse_api_battle(ME, _team_battle(star="#T2")).star_player_slot == 2
    assert parse_api_battle(ME, _team_battle(star="#O1")).star_player_slot == 3
    assert parse_api_battle(ME, _team_battle(star="#O3")).star_player_slot == 5
    no_star = parse_api_battle(ME, _team_battle(star=None))
    assert no_star.star_player_slot is None and no_star.is_starplayer is None


def test_banned_brawlers_follow_tag_order_and_bots_have_none():
    item = _team_battle(battle_type="soloRanked", with_bans=True)
    del item["battle"]["teams"][1][1]["bannedBrawler"]  # ボットはBANしない
    battle = parse_api_battle(ME, item)
    assert battle.banned_brawler == 16000032
    assert battle.teammate_banned_brawlers == [16000031, 16000033]
    assert battle.opponent_banned_brawlers == [16000034, None, 16000036]


def test_mode_without_mode_stat_and_battle_without_stats():
    item = _team_battle(mode="knockout")
    for team in item["battle"]["teams"]:
        for player in team:
            del player["stats"]["modeStat"]
    battle = parse_api_battle(ME, item)
    assert battle.kills == 2 and battle.mode_stat_type is None and battle.mode_stat is None
    assert battle.teammate_stats[:PLAYER_STATS_WIDTH] == [1, 2, 1000, 10, 500, None, 11, 500]

    old = parse_api_battle(ME, _team_battle(with_stats=False))
    assert old.duration == 95 and old.kills is None and old.teammate_stats is None and old.opponent_stats is None


def test_unknown_mode_stat_type_is_saved_as_zero():
    item = _team_battle()
    for team in item["battle"]["teams"]:
        for player in team:
            player["stats"]["modeStat"]["type"] = "somethingNew"
    battle = parse_api_battle(ME, item)
    assert battle.mode_stat_type == battle_log.MODE_STAT_TYPE_UNKNOWN and battle.mode_stat == 0


def test_out_of_range_values_become_null():
    item = _team_battle()
    item["battle"]["duration"] = 99999
    item["battle"]["teams"][0][1]["stats"]["kills"] = 40000
    item["battle"]["teams"][0][0]["stats"]["damageDealt"] = 2 ** 40
    battle = parse_api_battle(ME, item)
    assert battle.duration is None and battle.kills is None and battle.deaths == 3
    assert battle.teammate_stats[2] is None


def test_aborted_friendly_battle_keeps_negative_duration():
    item = _team_battle(battle_type="friendly", result="draw", star=None)
    item["battle"]["duration"] = -7
    item["event"].update(id=0, map=None, modeId=0)
    battle = parse_api_battle(ME, item)
    assert battle.duration == -7 and battle.result == "d" and battle.event_id == 0 and battle.event_map is None


def _solo(rank, count, *, trophy_change=None, mode="soloShowdown", me_at=None):
    players = [_player(f"#P{i}", 16000000 + i) for i in range(1, count + 1)]
    players[(me_at or rank) - 1] = _player(ME, 16000099, trophies=640)
    battle = {"mode": "soloShowdown", "type": "ranked", "rank": rank, "players": players}
    if trophy_change is not None:
        battle["trophyChange"] = trophy_change
    return _item(battle, mode=mode, mode_id=6)


def test_solo_showdown():
    battle = parse_api_battle(ME, _solo(3, 10, trophy_change=5))
    assert (battle.team_size, battle.num_of_teams, battle.rank, battle.result) == (1, 10, 3, "w")
    assert battle.brawler == 16000099 and battle.trophies == 640
    assert battle.teammate_tags == [] and battle.opponent_tags == [f"#P{i}" for i in (1, 2, 4, 5, 6, 7, 8, 9, 10)]
    assert battle.opponent_brawlers == [16000000 + i for i in (1, 2, 4, 5, 6, 7, 8, 9, 10)]
    assert battle.duration is None and battle.kills is None and battle.star_player_slot is None
    # スタッツは無いが、敵のパワーとトロフィーは残す（スタッツの6個は NULL）
    assert battle.teammate_stats is None and len(battle.opponent_stats) == PLAYER_STATS_WIDTH * 9
    assert battle.opponent_stats[:PLAYER_STATS_WIDTH] == [None] * 6 + [11, 500]
    # 自分はタグで探す（並びが順位とずれていても取り違えない）
    assert parse_api_battle(ME, _solo(3, 10, trophy_change=5, me_at=7)).brawler == 16000099


@pytest.mark.parametrize("rank, trophy_change, expected", [
    (4, 3, "w"), (4, -1, "d"), (5, 0, "d"), (6, -4, "l"), (6, 2, "d"), (10, -8, "l"),
    (1, None, "w"),  # 変動が0のバトルは trophyChange の項目ごと無い → 0 として扱う
])
def test_solo_showdown_results(rank, trophy_change, expected):
    assert parse_api_battle(ME, _solo(rank, 10, trophy_change=trophy_change)).result == expected


@pytest.mark.parametrize("rank, trophy_change, expected", [
    (1, 9, "w"), (8, 2, "w"), (8, -1, "d"), (9, 1, "d"), (10, -2, "d"), (11, -3, "l"), (11, 1, "d"), (20, -9, "l"),
])
def test_mega_showdown_results(rank, trophy_change, expected):
    battle = parse_api_battle(ME, _solo(rank, 20, trophy_change=trophy_change, mode="megaShowdown"))
    assert battle.num_of_teams == 20 and battle.result == expected


def _ranked_teams(rank, team_size, team_count, *, trophy_change, mode):
    teams = [[_player(f"#P{t}{m}", 16000000 + t) for m in range(team_size)] for t in range(1, team_count + 1)]
    teams[rank - 1][0] = _player(ME, 16000099)
    battle = {"mode": "duoShowdown", "type": "ranked", "rank": rank, "trophyChange": trophy_change, "teams": teams}
    return _item(battle, mode=mode, mode_id=9)


@pytest.mark.parametrize("mode, team_size, team_count, rank, trophy_change, expected", [
    ("duoShowdown", 2, 5, 2, 6, "w"), ("duoShowdown", 2, 5, 3, 0, "d"), ("duoShowdown", 2, 5, 4, -3, "l"), ("duoShowdown", 2, 5, 5, -6, "l"),
    # トリオも battle.mode は duoShowdown で来る。3位からが負け
    ("trioShowdown", 3, 4, 2, 5, "w"), ("trioShowdown", 3, 4, 3, -2, "l"), ("trioShowdown", 3, 4, 3, 1, "d"), ("trioShowdown", 3, 4, 4, -6, "l"),
    # メガデュオ（2人×10チーム）: 4位までが勝ち、5・6位が引き分け、7位からが負け
    ("megaDuoShowdown", 2, 10, 4, 3, "w"), ("megaDuoShowdown", 2, 10, 5, 1, "d"), ("megaDuoShowdown", 2, 10, 6, -1, "d"),
    ("megaDuoShowdown", 2, 10, 7, -2, "l"), ("megaDuoShowdown", 2, 10, 10, -8, "l"), ("megaDuoShowdown", 2, 10, 2, -1, "d"),
])
def test_team_showdown_results(mode, team_size, team_count, rank, trophy_change, expected):
    battle = parse_api_battle(ME, _ranked_teams(rank, team_size, team_count, trophy_change=trophy_change, mode=mode))
    assert (battle.team_size, battle.num_of_teams, battle.result) == (team_size, team_count, expected)
    assert len(battle.teammate_tags) == team_size - 1 and len(battle.opponent_tags) == team_size * (team_count - 1)
    assert len(battle.teammate_stats) == PLAYER_STATS_WIDTH * (team_size - 1)
    assert len(battle.opponent_stats) == PLAYER_STATS_WIDTH * team_size * (team_count - 1)
    assert battle.teammate_stats[-2:] == [11, 500] and battle.kills is None


def test_result_from_rank_without_rule_or_rank_is_draw():
    assert result_from_rank(None, 1, 5) == "d"
    assert result_from_rank("solo", None, 0) == "d"


def test_duel():
    def duelist(tag, changes):
        return {"tag": tag, "name": f"name{tag}", "brawlers": [
            {"id": 16000010 + i, "name": "X", "power": 9 + i, "trophies": trophies, "trophyChange": change}
            for i, (trophies, change) in enumerate(changes)
        ]}
    battle_dict = {"mode": "duels", "type": "ranked", "result": "victory", "duration": 90,
                   "players": [duelist(ME, [(220, 5), (1144, 5), (722, 4)]), duelist("#OPP", [(1142, -6), (1134, 0), (1129, 0)])]}
    battle = parse_api_battle(ME, _item(battle_dict, mode="duels", mode_id=24))
    assert battle.result == "w" and battle.duration == 90 and battle.trophy_change == 14
    assert battle.brawler is None and battle.power is None and battle.trophies is None
    # トロフィーの多い順
    assert battle.brawlers == [(16000011, 10, 1144, 5), (16000012, 11, 722, 4), (16000010, 9, 220, 5)]
    assert battle.opponent_tags == ["#OPP"] and battle.opponent_brawlers == [16000010, 16000011, 16000012]
    assert (battle.team_size, battle.num_of_teams) == (1, 2)
    assert battle.kills is None and battle.opponent_stats is None


def test_boss_fight_like_battle_treats_everyone_as_teammate():
    battle_dict = {"mode": "bossFight", "result": "victory", "duration": 120,
                   "players": [_player("#A", 16000001), _player(ME, 16000002)], "bigBrawler": _player("#BIG", 16000003)}
    battle = parse_api_battle(ME, _item(battle_dict, mode="bossFight", mode_id=10))
    assert battle.result == "w" and battle.brawler == 16000002 and battle.battle_type is None
    assert battle.teammate_tags == ["#A", "#BIG"] and battle.opponent_tags == []


def test_missing_player_raises():
    with pytest.raises(ValueError):
        parse_api_battle("#NOBODY", _team_battle())
    with pytest.raises(ValueError):
        parse_api_battle(ME, _item({"mode": "x", "type": "ranked"}))


def test_columns_cover_every_field_once():
    assert len(set(battle_log.BATTLE_COLUMNS)) == len(battle_log.BATTLE_COLUMNS)
    fields = set(battle_log.APIBattle.__dataclass_fields__)
    assert set(battle_log.BATTLE_COLUMNS) - {"tag"} == fields
    assert set(battle_log.RESULT_DATA_COLUMNS) <= fields


def test_columns_match_models():
    from app.db import models
    for model in (models.Battle, models.ArchivedBattle):
        table_columns = {column.name for column in model.__table__.columns} - {"archived_at"}
        assert table_columns == set(battle_log.BATTLE_COLUMNS), model.__tablename__


class _FakeDB:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = []

    async def execute(self, query, *args):
        self.calls.append((query, args))
        return self.statuses.pop(0)


def _saved(minutes_ago, *, duration=95, year=2026):
    battle = parse_api_battle(ME, _team_battle())
    battle.datetime = datetime.datetime(year, 11, 12, 12, 0, tzinfo=datetime.timezone.utc) - datetime.timedelta(minutes=minutes_ago)
    battle.duration = duration
    return battle


def _saved_showdown(minutes_ago):
    battle = parse_api_battle(ME, _solo(3, 10, trophy_change=5))
    battle.datetime = datetime.datetime(2026, 11, 12, 12, 0, tzinfo=datetime.timezone.utc) - datetime.timedelta(minutes=minutes_ago)
    return battle


async def test_fill_stops_at_first_row_that_already_has_result_data():
    db = _FakeDB(["UPDATE 1", "UPDATE 0"])
    boss = _saved(2, duration=None)
    boss.teammate_stats = boss.opponent_stats = None
    battles = [_saved(1), boss, _saved(3), _saved(4)]
    assert await battle_log.fill_missing_result_data(db, ME, battles) == 1
    # 埋めるものの無いバトルは飛ばし、埋まっている行に当たったらやめる
    assert [args[1] for _, args in db.calls] == [battles[0].datetime, battles[2].datetime]
    assert db.calls[0][1][2:] == tuple(getattr(battles[0], column) for column in battle_log.RESULT_DATA_COLUMNS)


async def test_fill_handles_showdown_separately_from_other_battles():
    # 対戦モードは1件目で埋まっていると分かっても、バトルロイヤル系は別に進める
    db = _FakeDB(["UPDATE 0", "UPDATE 1", "UPDATE 0"])
    battles = [_saved(1), _saved(2), _saved_showdown(3), _saved(4), _saved_showdown(5), _saved_showdown(6)]
    assert await battle_log.fill_missing_result_data(db, ME, battles) == 1
    assert [args[1] for _, args in db.calls] == [battles[0].datetime, battles[2].datetime, battles[4].datetime]
    assert db.calls[1][1][2:] == (None, battles[2].opponent_stats)
    assert "duration IS NULL" not in db.calls[1][0] and "opponent_stats IS NULL" in db.calls[1][0]


async def test_fill_does_nothing_for_battles_after_cutoff():
    db = _FakeDB([])
    assert await battle_log.fill_missing_result_data(db, ME, [_saved(1, year=2027), _saved(2)]) == 0
    assert db.calls == []


async def _row(db, tag, battle):
    return await db.fetchrow("SELECT * FROM battles WHERE tag = $1 AND datetime = $2", tag, battle.datetime)


@pytest.mark.asyncio(loop_scope="session")  # テスト用DBのプール（セッションで共有）と同じループで動かす
async def test_insert_saves_result_data_and_reports_existing_rows(test_db_pool):
    tag = "#BLTEST1"
    battle = parse_api_battle(ME, _team_battle(battle_type="soloRanked", with_bans=True))
    async with test_db_pool.acquire() as db:
        await db.execute("DELETE FROM battles WHERE tag = $1", tag)
        assert await battle_log.insert_api_battle(db, tag, battle) is False
        assert await battle_log.insert_api_battle(db, tag, battle) is True  # 2回目は保存済み
        row = await _row(db, tag, battle)
        assert (row["result"], row["brawler"], row["trophies"], row["teammate_tags"]) == ("w", 16000002, 777, ["#T1", "#T2"])
        for column in battle_log.RESULT_DATA_COLUMNS:
            assert row[column] == getattr(battle, column), column
        assert row["opponent_stats"][-PLAYER_STATS_WIDTH:] == [6, 7, 6000, 60, 3000, 0, 9, 321]
        assert row["opponent_banned_brawlers"] == [16000034, 16000035, 16000036]

        showdown = parse_api_battle(ME, _solo(3, 10, trophy_change=5))
        showdown.datetime -= datetime.timedelta(minutes=5)
        assert await battle_log.insert_api_battle(db, tag, showdown) is False
        row = await _row(db, tag, showdown)
        assert all(row[column] is None for column in battle_log.RESULT_DATA_COLUMNS if column != "opponent_stats")
        assert row["opponent_stats"] == showdown.opponent_stats and row["opponent_stats"][6:8] == [11, 500]
        await db.execute("DELETE FROM battles WHERE tag = $1", tag)


@pytest.mark.asyncio(loop_scope="session")  # テスト用DBのプール（セッションで共有）と同じループで動かす
async def test_save_fills_rows_saved_before_result_data_existed(test_db_pool):
    tag = "#BLTEST2"
    battles = []
    for minutes, item in enumerate([_team_battle(), _team_battle(), _solo(2, 10, trophy_change=7), _team_battle(), _team_battle()]):
        battle = parse_api_battle(ME, item)
        battle.datetime -= datetime.timedelta(minutes=minutes)
        battles.append(battle)
    clear = ", ".join(f"{column} = NULL" for column in battle_log.RESULT_DATA_COLUMNS)
    async with test_db_pool.acquire() as db:
        await db.execute("DELETE FROM battles WHERE tag = $1", tag)
        # 古い4件は「リザルトのデータを保存していなかった頃」に保存済み。いちばん古い1件だけは、もう埋まっている
        await battle_log.save_api_battles(db, tag, battles[1:])
        await db.execute(f"UPDATE battles SET {clear}, result = 'l' WHERE tag = $1 AND datetime > $2", tag, battles[4].datetime)
        await db.execute("UPDATE battles SET kills = 99 WHERE tag = $1 AND datetime = $2", tag, battles[4].datetime)

        await battle_log.save_api_battles(db, tag, battles)
        rows = [await _row(db, tag, battle) for battle in battles]
        assert [row["kills"] for row in rows] == [2, 2, None, 2, 99]
        assert [row["duration"] for row in rows] == [95, 95, None, 95, 95]
        assert rows[1]["teammate_stats"] == battles[1].teammate_stats
        # バトルロイヤル系は、敵のパワーとトロフィーだけが埋まる
        assert rows[2]["opponent_stats"] == battles[2].opponent_stats and rows[2]["opponent_stats"][6:8] == [11, 500]
        # 埋めるのはリザルトのデータの列だけ。ほかの列（勝敗など）には触らない
        assert [row["result"] for row in rows] == ["w", "l", "l", "l", "w"]
        await db.execute("DELETE FROM battles WHERE tag = $1", tag)


@pytest.mark.asyncio(loop_scope="session")  # テスト用DBのプール（セッションで共有）と同じループで動かす
async def test_archive_and_restore_carry_every_column(test_db_pool):
    from app.background_tasks import tasks
    tag = "#BLTEST3"
    battle = parse_api_battle(ME, _team_battle(battle_type="soloRanked", with_bans=True))
    async with test_db_pool.acquire() as db:
        await db.execute("DELETE FROM battles WHERE tag = $1", tag)
        await db.execute("DELETE FROM archived_battles WHERE tag = $1", tag)
        await battle_log.insert_api_battle(db, tag, battle)
        before = dict(await _row(db, tag, battle))
        await db.execute(f"INSERT INTO archived_battles ({tasks._BATTLE_COLUMNS_SQL}) SELECT {tasks._ARCHIVE_COLUMNS_B} FROM battles b WHERE b.tag = $1", tag)
        await db.execute("DELETE FROM battles WHERE tag = $1", tag)
        await db.execute(f"INSERT INTO battles ({tasks._BATTLE_COLUMNS_SQL}) SELECT {tasks._ARCHIVE_COLUMNS_AB} FROM archived_battles ab WHERE ab.tag = $1", tag)
        assert dict(await _row(db, tag, battle)) == before
        await db.execute("DELETE FROM battles WHERE tag = $1", tag)
        await db.execute("DELETE FROM archived_battles WHERE tag = $1", tag)
