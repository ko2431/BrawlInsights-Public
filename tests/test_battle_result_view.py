"""バトルカードで開くリザルトのデータ（app/services/battle_result_view.py）の組み立て。"""
import datetime
from types import SimpleNamespace

from app.services.battle_log import rank_result_bounds
from app.services.battle_result_view import STATS_SINCE, build_result_view

ME = "#ME"
NOW = datetime.datetime(2026, 11, 13, 3, 0, tzinfo=datetime.timezone.utc)


def _battle(*, minutes_ago=0, battle_type="soloRanked", result="w", rank=None, team_size=3, num_of_teams=2,
            mates=("#T1", "#T2"), opps=("#O1", "#O2", "#O3"), event_mode="brawlBall", battle_mode="brawlBall", brawlers=None,
            power=11, trophies=18, at=None):
    return SimpleNamespace(
        tag=ME, datetime=at or NOW - datetime.timedelta(minutes=minutes_ago), battle_type=battle_type, result=result, rank=rank,
        team_size=team_size, num_of_teams=num_of_teams, event_mode=event_mode, battle_mode=battle_mode,
        brawler=None if brawlers else 16000001, power=power, trophies=trophies, brawlers=brawlers,
        teammate_tags=list(mates), opponent_tags=list(opps),
        teammate_names=[f"mate{i}" for i in range(len(mates))], opponent_names=[f"opp{i}" for i in range(len(opps))],
        teammate_brawlers=[16000010 + i for i in range(len(mates))], opponent_brawlers=[16000020 + i for i in range(len(opps))],
        set_battles=None, game_result=None,
    )


def _row(battle, *, kills=3, duration=95, mode_stat_type=2, bans=False, star=None, mate_stats=None, opp_stats=None):
    """battles の1行。味方・敵のスタッツは、指定が無ければ「何人目か」で見分けられる値にする。"""
    def stats(count, base):
        return [value for i in range(count) for value in (base + i, 1, 1000 * (base + i), 10, 500, i, 11, 20 - i)]
    return {
        "datetime": battle.datetime, "duration": duration, "kills": kills, "deaths": 2, "damage_dealt": 9000, "healing": 100,
        "damage_taken": 4000, "mode_stat_type": mode_stat_type, "mode_stat": 1, "banned_brawler": 16000050 if bans else None,
        "star_player_slot": star,
        "teammate_stats": mate_stats if mate_stats is not None else stats(len(battle.teammate_tags), 10),
        "opponent_stats": opp_stats if opp_stats is not None else stats(len(battle.opponent_tags), 20),
        "teammate_banned_brawlers": [16000051 + i for i in range(len(battle.teammate_tags))] if bans else None,
        "opponent_banned_brawlers": [16000061 + i for i in range(len(battle.opponent_tags))] if bans else None,
    }


def test_team_battle_with_stats_and_bans():
    battle = _battle()
    view = build_result_view(battle, "me", {battle.datetime: _row(battle, bans=True, star=4)})
    assert (view["k"], view["t"], view["ts"], view["ms"]) == ("s", "soloRanked", 3, 2)
    # [タグ, 名前, キャラ, パワー, トロフィー, BANしたキャラ]
    assert view["me"] == [ME, "me", 16000001, 11, 18, 16000050]
    assert view["mates"][1] == ["#T2", "mate1", 16000011, 11, 19, 16000052]
    assert view["opps"][2] == ["#O3", "opp2", 16000022, 11, 18, 16000063]
    game = view["games"][0]
    assert (game["r"], game["d"], game["sp"]) == ("w", 95, 4)
    assert game["s"][0] == [3, 2, 9000, 100, 4000, 1]
    assert game["s"][1][:3] == [10, 1, 10000] and game["s"][5][:3] == [22, 1, 22000]


def test_set_games_are_oldest_first_and_aligned_by_tag():
    newest, older = _battle(minutes_ago=0, result="w"), _battle(minutes_ago=4, result="l")
    # 古い試合は、味方・敵の並びが代表のバトルと逆
    older.teammate_tags.reverse()
    older.opponent_tags.reverse()
    newest.set_battles, newest.game_result = [newest, older], "l"  # セットとしては勝ち、この試合そのものは負け
    rows = {newest.datetime: _row(newest, star=1), older.datetime: _row(older, kills=7, duration=120, star=1)}
    view = build_result_view(newest, "me", rows)
    assert [(game["r"], game["d"]) for game in view["games"]] == [("l", 120), ("l", 95)]
    first = view["games"][0]
    assert first["s"][0][0] == 7
    # 古い試合の1人目の味方は #T2。代表の並び（自分, #T1, #T2, …）では3番目に来る
    assert first["s"][2][0] == 10 and first["s"][1][0] == 11
    assert first["s"][3][0] == 22 and first["s"][5][0] == 20
    assert first["sp"] == 2 and view["games"][1]["sp"] == 1


def test_missing_stats_becomes_members_only_with_note():
    old = _battle(battle_type="ranked", at=STATS_SINCE - datetime.timedelta(hours=1))
    row = _row(old, kills=None, duration=None, mode_stat_type=None, mate_stats=[], opp_stats=[])
    row.update(teammate_stats=None, opponent_stats=None)
    view = build_result_view(old, "me", {old.datetime: row})
    assert (view["k"], view["note"]) == ("m", "old") and view["games"][0]["s"] is None
    assert view["mates"][0] == ["#T1", "mate0", 16000010, None, None, None]

    recent = _battle(battle_type="ranked")
    assert build_result_view(recent, "me", {})["note"] == "none"


def test_battle_ended_before_start_shows_members_only():
    battle = _battle(battle_type="friendly", result="d", power=-1, trophies=-1)
    row = _row(battle, kills=0, duration=-5)
    view = build_result_view(battle, "me", {battle.datetime: row})
    assert (view["k"], view["note"]) == ("m", "aborted") and view["games"][0]["s"] is None
    assert view["me"][3:5] == [None, None]  # フレンドバトルのパワーとトロフィーは -1 で来る


def test_showdown_lists_every_team_in_rank_order():
    battle = _battle(battle_type="ranked", result="w", rank=2, team_size=2, num_of_teams=5, mates=("#T1",),
                     opps=tuple(f"#O{i}" for i in range(8)), event_mode="duoShowdown", battle_mode="duoShowdown", trophies=640)
    extras = lambda count: [value for i in range(count) for value in (None, None, None, None, None, None, 9, 600 + i)]  # noqa: E731
    row = _row(battle, kills=None, duration=None, mode_stat_type=None, mate_stats=extras(1), opp_stats=extras(8))
    view = build_result_view(battle, "me", {battle.datetime: row})
    assert (view["k"], view["rank"], view["ts"], view["rule"]) == ("r", 2, 2, [2, 3])
    assert len(view["teams"]) == 5 and [bool(team.get("me")) for team in view["teams"]] == [False, True, False, False, False]
    assert view["teams"][1]["p"][0][:5] == [ME, "me", 16000001, 11, 640]
    assert view["teams"][1]["p"][1][:5] == ["#T1", "mate0", 16000010, 9, 600]
    assert view["teams"][0]["p"][0][0] == "#O0" and view["teams"][2]["p"][0][:5] == ["#O2", "opp2", 16000022, 9, 602]

    # パワーとトロフィーを保存する前のバトル
    assert build_result_view(battle, "me", {})["teams"][0]["p"][0][3:5] == [None, None]


def test_duel_and_battles_without_tags():
    duel = _battle(battle_type="ranked", team_size=1, mates=(), opps=("#O1",), battle_mode="duels", event_mode="tagTeam",
                   brawlers=[(16000001, 11, 900, 3), (16000002, 11, 800, 3), (16000003, 10, 700, 3)])
    duel.opponent_brawlers = [16000020, 16000021, 16000022]
    row = _row(duel, kills=None, duration=128, mode_stat_type=None, mate_stats=[], opp_stats=[])
    view = build_result_view(duel, "me", {duel.datetime: row})
    assert view["k"] == "d" and view["me"] == [ME, "me", [16000001, 16000002, 16000003]]
    assert view["opps"] == [["#O1", "opp0", [16000020, 16000021, 16000022]]] and view["games"] == [{"r": "w", "d": 128}]

    assert build_result_view(_battle(mates=(), opps=()), "me", {}) is None  # タグを保存していなかった頃のバトル


def test_rank_result_bounds():
    assert rank_result_bounds(event_mode="soloShowdown", battle_mode="soloShowdown", team_size=1, num_of_teams=10) == (4, 5)
    assert rank_result_bounds(event_mode="megaShowdown", battle_mode="soloShowdown", team_size=1, num_of_teams=20) == (8, 10)
    assert rank_result_bounds(event_mode="megaDuoShowdown", battle_mode="duoShowdown", team_size=2, num_of_teams=10) == (4, 6)
    assert rank_result_bounds(event_mode="trioShowdown", battle_mode="duoShowdown", team_size=3, num_of_teams=4) == (2, 2)
    assert rank_result_bounds(event_mode="brawlBall", battle_mode="brawlBall", team_size=3, num_of_teams=2) is None
