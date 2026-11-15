"""バトル履歴ページの読み込みと集計（app/services/battle_history.py）。DB を使わない部分。"""
import asyncio
import datetime

from app.services import battle_history as bh

NOW = datetime.datetime(2026, 11, 14, 3, 0, tzinfo=datetime.timezone.utc)


def _row(minutes_ago=0, *, battle_type="soloRanked", result="w", event_map="Map A", brawler=16000001, score=None, mates='["a","b"]',
         opps='["x","y","z"]', mate_brawlers="[1,2]", opp_brawlers="[3,4,5]", kills=None, duration=None, trophy_change=None, star=False,
         rank=None, team_size=3, num_of_teams=2, battle_mode="brawlBall", trophies=None, brawlers=None, at=None):
    return {
        "datetime": at or NOW - datetime.timedelta(minutes=minutes_ago), "event_id": 15000001, "event_mode": "brawlBall", "event_map": event_map,
        "battle_mode": battle_mode, "mode_id": 48000005, "battle_type": battle_type, "result": result, "rank": rank, "is_starplayer": star,
        "brawler": brawler, "trophies": trophies, "trophy_change": trophy_change, "team_size": team_size, "num_of_teams": num_of_teams,
        "ranked_score_after": score, "kills": kills, "deaths": 1 if kills is not None else None, "damage_dealt": 1000 if kills is not None else None,
        "healing": 0 if kills is not None else None, "damage_taken": 500 if kills is not None else None, "duration": duration,
        "brawlers": brawlers, "teammate_tags": '["#AAAAA","#BBBBB"]', "teammate_names": mates, "opponent_names": opps,
        "teammate_brawlers": mate_brawlers, "opponent_brawlers": opp_brawlers, "opponent_tags": None,
    }


def _sets(rows, types=bh.SET_BATTLE_TYPES):
    return bh.group_into_sets(rows, types)


def test_consecutive_games_with_same_members_become_one_set():
    rows = [_row(0, result="w"), _row(3, result="l"), _row(6, result="w"), _row(40, result="l")]
    sets = _sets(rows)
    assert [len(s.rows) for s in sets] == [3, 1]
    assert (sets[0].set_w, sets[0].set_d, sets[0].set_l, sets[0].result) == (2, 0, 1, "w")
    assert (sets[1].set_w, sets[1].result) == (None, "l")  # 1試合だけのものはセットの勝敗を持たない


def test_member_order_does_not_matter_but_different_members_split():
    same = [_row(0), _row(3, mates='["b","a"]', opp_brawlers="[5,4,3]")]
    assert [len(s.rows) for s in _sets(same)] == [2]
    other = [_row(0), _row(3, opps='["x","y","w"]')]
    assert [len(s.rows) for s in _sets(other)] == [1, 1]
    other_brawlers = [_row(0), _row(3, opp_brawlers="[3,4,6]")]
    assert [len(s.rows) for s in _sets(other_brawlers)] == [1, 1]
    assert "opponent_brawlers" in bh._MEMBER_COLUMNS  # 読み込みの列を変えても、セットの判定からは外さない


def test_set_breaks_on_map_brawler_type_and_time():
    assert [len(s.rows) for s in _sets([_row(0), _row(3, event_map="Map B")])] == [1, 1]
    assert [len(s.rows) for s in _sets([_row(0), _row(3, brawler=16000002)])] == [1, 1]
    assert [len(s.rows) for s in _sets([_row(0), _row(3, battle_type="friendly")])] == [1, 1]
    # 代表の試合から30分を超えたら別のセット（1つ前の試合からではない）
    assert [len(s.rows) for s in _sets([_row(0), _row(20), _row(31)])] == [2, 1]


def test_only_listed_types_are_grouped():
    trophy = [_row(0, battle_type="ranked"), _row(3, battle_type="ranked")]
    assert [len(s.rows) for s in _sets(trophy)] == [1, 1]            # すべてタブ: トロフィーはまとめない
    assert [len(s.rows) for s in _sets(trophy, None)] == [2]          # 種別の指定なし: まとめる
    assert [len(s.rows) for s in _sets(trophy, frozenset())] == [1, 1]  # トロフィータブ: 何もまとめない
    no_map = [_row(0, event_map=None), _row(3, event_map=None)]
    assert [len(s.rows) for s in _sets(no_map)] == [1, 1]


def test_ranked_score_in_older_games_moves_to_the_next_set():
    # 新しい順。1つ目のセットの古いほうの試合にあるポイントは、次に出てくるセットへ回る（Battles.group_into_sets と同じ）
    rows = [_row(0, score=5000), _row(3, score=4980), _row(60, score=None, opps='["p","q","r"]'), _row(63, opps='["p","q","r"]'),
            _row(120, score=4000, opps='["s","t","u"]')]
    sets = _sets(rows)
    assert [len(s.rows) for s in sets] == [2, 2, 1]
    assert [s.ranked_score_after for s in sets] == [5000, 4980, 4000]


def test_add_set_counts_result_once_and_stats_per_game():
    battle_set = _sets([_row(0, result="w", kills=3, duration=100, star=True), _row(3, result="l", kills=5, duration=-5), _row(6, result="w")])[0]
    agg = bh._new_agg()
    bh._add_set(agg, battle_set)
    named = dict(zip(bh.AGG_FIELDS, agg))
    assert (named["n"], named["w"], named["l"], named["g"]) == (1, 1, 0, 3)
    assert (named["sn"], named["k"], named["dth"], named["sp"]) == (2, 8, 2, 1)
    assert (named["dn"], named["du"]) == (1, 100)  # 開始前に終わった試合（負の時間）は数えない


def test_streaks_break_on_draw_and_report_the_current_run():
    def results(text):
        return [bh.BattleSet([_row(i, battle_type="ranked", result=r)], None) for i, r in enumerate(text)]
    assert bh._streaks(results("wwlwwwdwllll")) == {"bw": 3, "bl": 4, "cw": 2, "cl": 0}
    assert bh._streaks(results("lldww")) == {"bw": 2, "bl": 2, "cw": 0, "cl": 2}
    assert bh._streaks(results("dww")) == {"bw": 2, "bl": 0, "cw": 0, "cl": 0}


def test_format_key_and_stats_availability():
    assert bh._format_key(_row()) == "3v3"
    assert bh._format_key(_row(team_size=1, num_of_teams=10)) == "solo"
    assert bh._format_key(_row(team_size=3, num_of_teams=4)) == "trio"
    assert bh._format_key(_row(team_size=None, num_of_teams=None)) == "other"
    assert bh._has_no_stats_by_design(_row(rank=3, num_of_teams=10))
    assert bh._has_no_stats_by_design(_row(battle_mode="duels"))
    assert not bh._has_no_stats_by_design(_row())


def test_trophy_data_sum_and_absolute():
    day = datetime.timedelta(days=1)
    rows = [_row(at=NOW, battle_type="ranked", trophies=520, trophy_change=8), _row(at=NOW - datetime.timedelta(hours=1), battle_type="ranked", trophies=510, trophy_change=10),
            _row(at=NOW - day, battle_type="ranked", trophies=515, trophy_change=-5, brawler=16000002)]
    sets = bh.group_into_sets(rows, frozenset())
    summed = bh._trophy_data(sets, None)
    assert summed["mode"] == "sum" and [p[1] for p in summed["points"]] == [-5, 13]  # 1日に1点（その日の最後）
    assert sorted(summed["byBrawler"]) == [[16000001, 2, 18], [16000002, 1, -5]]
    absolute = bh._trophy_data([s for s in sets if s.head["brawler"] == 16000001], 16000001)
    assert absolute["mode"] == "abs" and [p[1] for p in absolute["points"]] == [510, 520, 528]  # バトル前の値と、最後のバトルの後の値


def test_thin_keeps_the_last_point():
    points = [[i, i] for i in range(1001)]
    thinned = bh._thin(points, 100)
    assert len(thinned) <= 102 and thinned[0] == [0, 0] and thinned[-1] == [1000, 1000]
    assert bh._thin(points[:50], 100) == points[:50]


def test_ranked_rank_borders_match_battle_rank():
    """ランクの境目の表が、Battle.ranked_rank_after（カードのランク表示）と食い違わないこと。"""
    from app.services.brawl_service import Battle
    rank_of = Battle.ranked_rank_after.fget
    for rank in range(1, 23):
        low = bh.RANKED_RANK_MIN_SCORES[rank]
        probe = type("Probe", (), {"ranked_score_after": low})()
        assert rank_of(probe) == rank
        if low > 1:
            probe.ranked_score_after = low - 1
            assert rank_of(probe) == rank - 1


class _LogDb:
    """player_logs の読み出しだけに答える DB の代わり。"""

    def __init__(self, logs=()):
        self.logs = [{"date": date, "ranked_current_score": score} for date, score in logs]

    async def fetch(self, query, *params):
        assert "player_logs" in query
        return self.logs


def _ranked(sets, logs=(), **kwargs):
    kwargs.setdefault("after", None)
    kwargs.setdefault("until", None)
    return asyncio.run(bh._ranked_data(_LogDb(logs), "#TAG", sets, **kwargs))


def _ranked_set(minutes_ago, score, rank=10):
    row = _row(minutes_ago, score=score, opps=f'["o{minutes_ago}"]')
    row["trophies"] = rank
    return bh.BattleSet([row], None)


def test_ranked_data_prefers_scores_and_falls_back_to_ranks():
    with_scores = [_ranked_set(0, 5200), _ranked_set(60, None), _ranked_set(120, 5100)]
    data = _ranked(with_scores, show_trend=True, show_current=True)
    assert data["mode"] == "score" and [p[1] for p in data["points"]] == [5100, 5200] and data["current"][1] == 5200
    one_score = [_ranked_set(0, 5200, rank=14), _ranked_set(60, None, rank=13)]
    data = _ranked(one_score, show_trend=True, show_current=False)
    assert data["mode"] == "rank" and [p[1] for p in data["points"]] == [13, 14] and "current" not in data
    assert "mode" not in _ranked(with_scores, show_trend=False, show_current=False)


def test_ranked_data_uses_daily_logs_for_changes_without_battles():
    # 最後のバトルは40日前（7000）。その後はプレイしていないが、シーズンの切り替わりで下がった値が日ごとの記録に残っている
    old = 40 * 24 * 60
    sets = [_ranked_set(old, 7000), _ranked_set(old + 60, 6950)]
    day = NOW.date()
    logs = [(day - datetime.timedelta(days=40), 7000), (day - datetime.timedelta(days=30), 7000), (day - datetime.timedelta(days=20), 7000),
            (day - datetime.timedelta(days=10), 5500), (day, 5500)]
    data = _ranked(sets, logs, show_trend=True, show_current=True)
    # 同じ値が続く途中の記録（30日前）は省かれ、下がる直前（20日前）と下がった後は残る
    assert [p[1] for p in data["points"]] == [6950, 7000, 7000, 5500, 5500]
    assert data["current"][1] == 5500
    times = [p[0] for p in data["points"]]
    assert times == sorted(times) and times[-1] <= int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    assert all(data["points"][0][0] < start < data["points"][-1][0] for start in data["seasons"])


def test_ranked_points_carry_set_positions_for_the_per_set_axis():
    # 3セット（古い順に 0, 1, 2）。2番目はポイントが分からない
    sets = [_ranked_set(0, 5200), _ranked_set(60, None), _ranked_set(120, 5100)]
    data = _ranked(sets, show_trend=True, show_current=True)
    assert [p[2] for p in data["points"]] == [0, 2] and data["current"] == [data["points"][-1][0], 5200]


def test_ranked_log_points_are_placed_between_sets_only_when_the_value_changes():
    old = 40 * 24 * 60
    sets = [_ranked_set(old, 7000), _ranked_set(old + 60, 6950)]
    day = NOW.date()
    logs = [(day - datetime.timedelta(days=40), 7000), (day - datetime.timedelta(days=20), 7000),
            (day - datetime.timedelta(days=10), 5500), (day - datetime.timedelta(days=5), 5400), (day, 5400)]
    data = _ranked(sets, logs, show_trend=True, show_current=True)
    assert [p[1] for p in data["points"]] == [6950, 7000, 7000, 5500, 5400, 5400]
    # 値の変わらない記録の点はセット順には出さず、変わった2点はセットと同じく1つずつ場所を取る
    assert [p[2] for p in data["points"]] == [0, 1, None, 2, 3, None]
    assert len(data["seasonsSeq"]) == len(data["seasons"]) and all(1 < x < 3 for x in data["seasonsSeq"])


def test_ranked_log_points_before_the_first_set_are_left_out_of_the_per_set_axis():
    sets = [_ranked_set(0, 5200), _ranked_set(60, 5100)]
    day = NOW.date()
    logs = [(day - datetime.timedelta(days=30), 4000), (day - datetime.timedelta(days=20), 4500), (day - datetime.timedelta(days=10), 4500)]
    data = _ranked(sets, logs, show_trend=True, show_current=True)
    assert [p[1] for p in data["points"]] == [4000, 4500, 4500, 5100, 5200]
    assert [p[2] for p in data["points"]] == [None, None, None, 0, 1]


def test_season_starts_are_third_thursdays_inside_the_range():
    utc = datetime.timezone.utc
    first = int(datetime.datetime(2026, 9, 1, tzinfo=utc).timestamp())
    last = int(datetime.datetime(2026, 11, 14, tzinfo=utc).timestamp())
    starts = [datetime.datetime.fromtimestamp(t, utc) for t in bh._season_starts(first, last)]
    assert starts == [datetime.datetime(2026, 9, 17, 8, tzinfo=utc), datetime.datetime(2026, 10, 15, 8, tzinfo=utc)]


def test_mvp_rate_counts_only_battles_that_have_an_mvp():
    agg = bh._new_agg()
    for row in (_row(0, battle_type="ranked", star=True), _row(40, battle_type="ranked", opps='["q"]'),
                _row(80, battle_type="ranked", rank=3, num_of_teams=10, team_size=1, battle_mode="soloShowdown"),
                _row(120, battle_type="ranked", team_size=1, battle_mode="duels"),
                _row(160, battle_type="ranked", num_of_teams=3, team_size=1, battle_mode="lastStand")):
        bh._add_set(agg, bh.BattleSet([row], None))
    named = dict(zip(bh.AGG_FIELDS, agg))
    assert (named["g"], named["mg"], named["sp"]) == (5, 2, 1)
    # セットではMVPは1回だけ選ばれる（最後の試合に付く）。3試合のセットでも分母は1
    agg = bh._new_agg()
    bh._add_set(agg, _sets([_row(0, star=True), _row(3), _row(6)])[0])
    bh._add_set(agg, _sets([_row(100, opps='["q"]'), _row(103, opps='["q"]')])[0])
    named = dict(zip(bh.AGG_FIELDS, agg))
    assert (named["n"], named["g"], named["mg"], named["sp"]) == (2, 5, 2, 1)


def test_ban_data_counts_per_set():
    def banned(minutes_ago, mine, opp, team):
        row = _row(minutes_ago, opps=f'["o{minutes_ago}"]')
        row.update(banned_brawler=mine, opponent_banned_brawlers=opp, teammate_banned_brawlers=team)
        return bh.BattleSet([row], None)
    sets = [banned(0, 7, [1, 2, 3], [4, None]), banned(60, 7, [1, 9, 9], None), banned(120, None, None, None)]
    data = bh._ban_data(sets)
    assert data["n"] == 2 and data["mine"] == [(7, 2)] and data["opp"][0] in ((1, 2), (9, 2)) and dict(data["team"]) == {4: 1}
    assert bh._ban_data([banned(0, None, None, None)]) is None


def _agg(n, w, l):
    agg = bh._new_agg()
    agg[0], agg[1], agg[3] = n, w, l
    return agg


def test_pick_highlights_needs_two_candidates_and_skips_the_same_worst():
    groups = [
        ("brawler", None, {1: _agg(10, 8, 2), 2: _agg(10, 5, 5), 3: _agg(4, 4, 0)}),  # 3 は回数が足りない
        ("mapGood", "mapBad", {7: _agg(6, 3, 3), 8: _agg(6, 1, 5)}),
        ("oppGood", "oppBad", {9: _agg(20, 10, 10)}),                                 # 候補が1つだけ
        ("mate", None, {"#AAAAA": _agg(5, 0, 0), "#BBBBB": _agg(5, 3, 2), "#CCCCC": _agg(5, 2, 3)}),  # 引き分けだけのものは外す
    ]
    assert bh.pick_highlights(groups, 5) == [["brawler", 1, 8, 2], ["mapGood", 7, 3, 3], ["mapBad", 8, 1, 5], ["mate", "#BBBBB", 3, 2]]
    # 勝率が同じなら、よいほうも悪いほうも回数の多いものを選ぶ。どちらも同じものになるときは悪いほうを出さない
    tie = [("mapGood", "mapBad", {1: _agg(5, 5, 0), 2: _agg(9, 9, 0)})]
    assert bh.pick_highlights(tie, 5) == [["mapGood", 2, 9, 0]]
    assert bh.highlight_min_count(100) == 5 and bh.highlight_min_count(1000) == 10


class _MateDb:
    async def fetch(self, query, *params):
        return [{"teammate_tags": '["#AAAAA","#BBBBB"]', "teammate_names": '["Alice","Bob"]', "opponent_tags": None, "opponent_names": None}]


def test_highlights_for_normal_tabs_leave_out_the_filtered_brawler_and_map():
    rows = [_row(i * 40, result="l" if i in (0, 2, 4, 3) else "w", opp_brawlers="[16000003]" if i % 2 else "[16000004]") for i in range(12)]
    sets = _sets(rows)
    by_brawler = {16000001: _agg(12, 8, 4), 16000002: _agg(6, 1, 5)}
    by_map = {(48000005, 15000001): _agg(12, 8, 4), (48000005, 15000002): _agg(6, 5, 1)}
    data = asyncio.run(bh._highlights(_MateDb(), "#ME000", sets, by_brawler, by_map, None, None))
    kinds = {item[0]: item for item in data}
    assert kinds["brawler"][1] == 16000001 and kinds["mapGood"][1] == [48000005, 15000002]
    assert {kinds["oppGood"][1], kinds["oppBad"][1]} == {16000003, 16000004}
    assert kinds["mate"][1][1] in ("Alice", "Bob")  # 味方はタグと名前
    filtered = asyncio.run(bh._highlights(_MateDb(), "#ME000", sets, by_brawler, by_map, 16000001, 15000002))
    assert not any(item[0] in ("brawler", "mapGood", "mapBad") for item in filtered)  # 残る候補が1つだけになる
