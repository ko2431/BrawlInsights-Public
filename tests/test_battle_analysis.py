"""バトル履歴ページの「詳細分析」タブのデータ（app/services/battle_analysis.py）。DB を使わない部分。"""
import datetime

from app.services import battle_analysis as ba
from app.services import battle_history as bh

NOW = datetime.datetime(2026, 11, 14, 3, 0, tzinfo=datetime.timezone.utc)


def _row(minutes_ago=0, *, battle_type="soloRanked", result="w", event_map="Map A", brawler=16000001, score=None, star=False, rank=None,
         team_size=3, num_of_teams=2, battle_mode="brawlBall", mate_tags='["#AAAAA","#BBBBB"]', mate_names='["a","b"]', opps='["x","y","z"]',
         opp_brawlers="[3,4,5]", kills=None, brawlers=None, banned=None):
    return {
        "datetime": NOW - datetime.timedelta(minutes=minutes_ago), "event_id": 15000001, "event_mode": "brawlBall", "event_map": event_map,
        "battle_mode": battle_mode, "mode_id": 48000005, "battle_type": battle_type, "result": result, "rank": rank, "is_starplayer": star,
        "brawler": brawler, "trophies": 10, "trophy_change": None, "team_size": team_size, "num_of_teams": num_of_teams,
        "ranked_score_after": score, "kills": kills, "deaths": 1 if kills is not None else None, "damage_dealt": 1000 if kills is not None else None,
        "healing": 0 if kills is not None else None, "damage_taken": 500 if kills is not None else None, "duration": 120, "banned_brawler": banned,
        "brawlers": brawlers, "teammate_tags": mate_tags, "teammate_names": mate_names, "opponent_names": opps,
        "teammate_brawlers": "[1,2]", "opponent_brawlers": opp_brawlers, "opponent_tags": None,
    }


def _columns(rows):
    return ba.build_columns("#ME", bh.group_into_sets(rows, bh.SET_BATTLE_TYPES))


def test_columns_are_oldest_first_with_set_number_and_set_result():
    # 新しい順。2試合のセット（1勝1敗なのでセットは引き分け）と、その前の1試合
    rows = [_row(0, result="w", score=5000, star=True), _row(3, result="l"), _row(60, result="l", opps='["p","q","r"]')]
    data = _columns(rows)
    cols = data["cols"]
    assert data["n"] == 3 and cols["t"] == sorted(cols["t"])
    assert cols["sid"] == [0, 1, 1]
    assert cols["r"] == [2, 2, 0]          # 試合ごとの勝敗
    assert cols["sr"] == [2, 1, 1]         # セットの勝敗（1勝1敗は引き分け）
    assert cols["rs"] == [None, None, 5000]  # セットの終わりのポイントは最後の試合にだけ
    assert cols["sp"] == [0, 0, 1] and cols["mv"] == [1, 1, 1]
    assert set(cols) == set(ba._COLUMN_KEYS)


def test_battle_types_and_mvp_flag():
    rows = [
        _row(0, battle_type="ranked"), _row(40, battle_type="ranked", event_map=None), _row(80, battle_type="friendly", opps='["f"]'),
        _row(120, battle_type="ranked", rank=2, num_of_teams=10, team_size=1, battle_mode="soloShowdown"),
        _row(160, battle_type="ranked", team_size=1, battle_mode="duels", brawler=None, brawlers='[[16000002, 11], [16000003, 11]]'),
    ]
    cols = _columns(rows)["cols"]
    assert cols["ty"] == [0, 0, 2, 3, 0]      # 古い順: デュエル・バトルロイヤル（トロフィー）・フレンド・マップなし（その他）・トロフィー
    assert cols["mv"] == [0, 0, 1, 1, 1]      # デュエルとバトルロイヤルにはMVPが無い
    assert cols["b"][0] == 16000002           # デュエルは1体目


def test_teammates_keep_only_recurring_players_without_tags():
    rows = [
        _row(0, mate_tags='["#AAAAA","#CCCCC"]', mate_names='["a-new","c"]', opps='["1"]'),
        _row(60, mate_tags='["#AAAAA","#8GQ"]', mate_names='["a-old","Bot 1"]', opps='["2"]'),
        _row(120, mate_tags='["#ME","#DDDDD"]', mate_names='["me","d"]', opps='["3"]'),
    ]
    data = _columns(rows)
    # 2回一緒になった #AAAAA だけが残り、名前は新しいバトルのもの。タグは返さない。ボット（短いタグ）と自分は数えない
    assert data["players"] == ["a-new"]
    assert data["cols"]["tm"] == [[], [0], [0]]


def test_enemy_and_ally_brawlers_and_ban():
    data = _columns([_row(0, opp_brawlers="[7,8,9]", banned=16000009)])
    assert data["cols"]["ob"] == [[7, 8, 9]] and data["cols"]["tb"] == [[1, 2]] and data["cols"]["bn"] == [16000009]
    assert data["mapModes"] == {15000001: 48000005}


def test_card_groups_are_parsed_and_limited():
    groups = ba.parse_card_groups("1794000000.1794000200:7425:7530,1794000400,1794000600:7000")
    assert groups == [
        {"times": [1794000000, 1794000200], "score": 7425, "previous": 7530},
        {"times": [1794000400], "score": None, "previous": None},
        {"times": [1794000600], "score": 7000, "previous": None},
    ]
    for bad in ("", "abc", "1794000000:x", "1794000000:1:2:3", "0", "1794000000:999999", ",".join(["1794000000"] * (ba.CARDS_MAX_SETS + 1)),
                ".".join(["1794000000"] * 11)):
        try:
            ba.parse_card_groups(bad)
        except ValueError:
            continue
        raise AssertionError(f"通ってしまった: {bad[:40]}")


def test_rank_of_matches_the_rank_borders():
    assert ba._rank_of(None) is None and ba._rank_of(0) is None
    assert ba._rank_of(1) == 1 and ba._rank_of(249) == 1 and ba._rank_of(250) == 2 and ba._rank_of(11250) == 22
