"""バトル履歴の全件読み込みを速くした部分(メンバー構成の比較・JSONB の読み込み・日本時間)が、これまでと同じ結果になることを検証する。"""

import datetime
import json
import random
from collections import Counter

from app.services import brawl_service


def test_same_members_matches_counter_comparison():
    rng = random.Random(0)
    pools = [["a", "b", "c", "テスト", "#2PP"], [16000000, 16000001, 16000002], [None, 1, 2], [None, "a"]]
    for _ in range(3000):
        pool = rng.choice(pools)
        a = [rng.choice(pool) for _ in range(rng.randint(0, 4))]
        b = list(a) if rng.random() < 0.5 else [rng.choice(pool) for _ in range(rng.randint(0, 4))]
        rng.shuffle(b)
        assert brawl_service._same_members(a, b) == (Counter(a) == Counter(b)), (a, b)
    # None(列が空)は空の配列と同じに扱う(Counter(None) と同じ)
    assert brawl_service._same_members(None, [])
    assert not brawl_service._same_members(None, ["a"])


def test_load_battle_jsonb_matches_json_loads():
    values = [
        [[16000000, 11], [16000001, None]],
        ["#2PP", "#ABC"],
        ["名前", "😀", 'a"b\\c', "あ"],
        [16000000, 16000001],
        [],
    ]
    for value in values:
        raw = json.dumps(value, ensure_ascii=False)
        assert brawl_service._load_battle_jsonb(raw) == json.loads(raw)
    # デコード済みの値・NULL はそのまま
    assert brawl_service._load_battle_jsonb([1, 2]) == [1, 2]
    assert brawl_service._load_battle_jsonb(None) is None


def test_datetime_jst_is_computed_on_access():
    row = {
        "tag": "#PCJJPRP", "datetime": datetime.datetime(2026, 11, 4, 15, 30, tzinfo=datetime.timezone.utc),
        "event_id": 15000000, "event_mode": "gemGrab", "event_map": "Hard Rock Mine", "battle_mode": "gemGrab",
        "mode_id": 48000000, "battle_type": "ranked", "result": "w", "rank": None, "is_starplayer": True,
        "brawler": 16000000, "power": 11, "trophies": 1000, "trophy_change": 8, "brawlers": None,
        "team_size": 3, "num_of_teams": 2, "ranked_score_after": None,
        "teammate_tags": '["#A", "#B"]', "opponent_tags": [], "teammate_names": [], "opponent_names": [],
        "teammate_brawlers": [], "opponent_brawlers": [],
    }
    battle = brawl_service.Battle(row)
    assert "datetime_jst" not in vars(battle)
    assert battle.datetime_jst == datetime.datetime(2026, 11, 5, 0, 30, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))
    assert battle.datetime_jst.utcoffset() == datetime.timedelta(hours=9)
    # 文字列で来た JSONB は読み込まれ、デコード済みの値はそのまま
    assert battle.teammate_tags == ["#A", "#B"]
    assert battle.opponent_tags == []
