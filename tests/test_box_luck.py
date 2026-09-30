import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.routers.tools import (
    BOX_LUCK_GRADE_THRESHOLDS,
    _box_luck_powers,
    _box_luck_grade_from_top_percent,
    _box_luck_top_percent,
    _build_box_luck_distribution,
    _build_box_luck_multi_table,
    _build_box_luck_table,
    _get_simulatable_boxes,
    _reward_entry_luck_distribution,
    _reward_event_luck_value,
    evaluate_box_luck,
    simulate_drop_box_multi_open,
    simulate_drop_box_open,
)


def _load_raw_data():
    return json.loads(Path("app/data/drop_boxes.json").read_text(encoding="utf-8"))


def test_all_box_distributions_sum_to_one():
    boxes = _get_simulatable_boxes(_load_raw_data())
    assert boxes
    for box_key, box in boxes.items():
        distribution = _build_box_luck_distribution(box_key, box, None)
        assert sum(distribution.values()) == pytest.approx(1.0, abs=1e-6), box_key


def test_top_percent_is_monotonic_and_bounded():
    box = _get_simulatable_boxes(_load_raw_data())["megabox"]
    table = _build_box_luck_table("megabox", box, None)
    values = table["values"]

    lowest = _box_luck_top_percent(table, values[0])
    highest = _box_luck_top_percent(table, values[-1])
    assert lowest > 90
    assert highest < 0.1
    # 分布外の値(最小値未満・最大値超)も範囲内に収まる
    assert _box_luck_top_percent(table, values[0] - 1) == 100.0
    assert _box_luck_top_percent(table, values[-1] + 1) == 0.01

    previous = 101.0
    for value in values[:: max(1, len(values) // 50)]:
        current = _box_luck_top_percent(table, value)
        assert current <= previous
        previous = current


def test_grade_thresholds():
    assert _box_luck_grade_from_top_percent(0.01) == "SS"
    assert _box_luck_grade_from_top_percent(0.1) == "SS"
    assert _box_luck_grade_from_top_percent(0.11) == "S+"
    assert _box_luck_grade_from_top_percent(10.0) == "A"
    assert _box_luck_grade_from_top_percent(50.0) == "C+"
    assert _box_luck_grade_from_top_percent(100.0) == "D-"


def test_ranged_amount_is_valued_by_actual_amount():
    low = _reward_event_luck_value({"reward_type": "coins", "amount": 30})
    high = _reward_event_luck_value({"reward_type": "coins", "amount": 70})
    assert high > low

    distribution = _reward_entry_luck_distribution({"type": "coins", "amount": "30-70"}, None)
    assert min(distribution) == low
    assert max(distribution) == high
    assert sum(distribution.values()) == pytest.approx(1.0)


def test_skin_value_uses_actual_skin_rarity():
    rare_skin = SimpleNamespace(id=1, rarity=5, is_limited=False)
    mythic_skin = SimpleNamespace(id=2, rarity=35, is_limited=False)
    dynamic_targets = {"skins_by_id": {1: rare_skin, 2: mythic_skin}, "skins": [rare_skin, mythic_skin]}

    distribution = _reward_entry_luck_distribution({"type": "rare-mythicSkins", "amount": 1}, dynamic_targets)
    rare_value = _reward_event_luck_value({"reward_type": "rare-mythicSkins", "amount": 1, "target_item": {"rarity": 5}})
    mythic_value = _reward_event_luck_value({"reward_type": "rare-mythicSkins", "amount": 1, "target_item": {"rarity": 35}})

    assert mythic_value > rare_value
    assert distribution == {rare_value: pytest.approx(0.5), mythic_value: pytest.approx(0.5)}


def test_evaluate_box_luck_returns_grade_for_simulated_results():
    raw_data = _load_raw_data()
    boxes = _get_simulatable_boxes(raw_data)
    grades = {grade for grade, _max_top_percent in BOX_LUCK_GRADE_THRESHOLDS}

    for box_key in ("starrdrop", "chaosdrop", "sushi", "nanodrop", "megabox", "ultratrophybox"):
        result = simulate_drop_box_open(None, raw_data, box_key)
        luck = asyncio.run(evaluate_box_luck(box_key, boxes[box_key], result, None))
        assert luck["grade"] in grades
        assert 0.01 <= luck["top_percent"] <= 100
        assert set(luck) == {"grade", "top_percent"}


def test_multi_open_luck_distribution_sums_to_one():
    boxes = _get_simulatable_boxes(_load_raw_data())
    multi_open_box_keys = (
        "starrdrop", "chaosdrop", "rankeddrop", "angelicdrop", "demonicdrop", "sushi", "nanodrop",
        "smoothiedrop", "novadrop", "present", "megabox", "siriusbox", "cosmobox", "ultratrophybox",
    )
    for box_key in multi_open_box_keys:
        for open_count in (2, 12):
            table = _build_box_luck_table(box_key, boxes[box_key], None, open_count)
            assert table["total"] == pytest.approx(1.0, abs=1e-6), (box_key, open_count)
            assert table["values"] == sorted(table["values"])


def test_multi_open_luck_matches_exact_distribution():
    """ビンにまとめた合算分布でも、厳密に畳み込んだ分布とほぼ同じ上位%になる"""
    box = _get_simulatable_boxes(_load_raw_data())["starrdrop"]
    open_count = 3
    exact_distribution = _box_luck_powers(_build_box_luck_distribution("starrdrop", box, None), open_count)[-1]
    values = sorted(exact_distribution)
    cumulative = []
    running = 0.0
    for value in values:
        running += exact_distribution[value]
        cumulative.append(running)
    exact_table = {"values": values, "cumulative": cumulative, "total": running}
    binned_table = _build_box_luck_table("starrdrop", box, None, open_count)
    assert binned_table["bin_width"] > 1

    for value in values[:: max(1, len(values) // 200)]:
        exact = _box_luck_top_percent(exact_table, value)
        binned = _box_luck_top_percent(binned_table, value)
        assert abs(exact - binned) <= max(2.0, exact * 0.25), (value, exact, binned)


def test_multi_open_result_merges_all_opens():
    raw_data = _load_raw_data()
    boxes = _get_simulatable_boxes(raw_data)
    grades = {grade for grade, _max_top_percent in BOX_LUCK_GRADE_THRESHOLDS}

    for box_key in ("starrdrop", "chaosdrop", "sushi", "nanodrop", "angelicdrop", "present", "megabox", "cosmobox"):
        result = simulate_drop_box_multi_open(None, raw_data, box_key, 5)
        assert result["open_count"] == 5
        assert [open_result["open_index"] for open_result in result["opens"]] == list(range(5))
        # 演出イベントは開封ごと、報酬は全開封を通しで並べる
        assert all(event["type"] != "reward" for open_result in result["opens"] for event in open_result["events"])
        assert all(event["type"] == "reward" for event in result["events"])
        assert [event["reveal_index"] for event in result["events"]] == list(range(1, len(result["events"]) + 1))
        assert result["total_reveals"] == len(result["events"])
        assert len(result["events"]) >= 5
        assert {event["open_index"] for event in result["events"]} == set(range(5))

        summary_total = sum(item["amount"] for item in result["summary"])
        assert summary_total == sum(event["amount"] for event in result["events"] if event["amount"] > 0)

        luck = asyncio.run(evaluate_box_luck(box_key, boxes[box_key], result, None, 5))
        assert luck["grade"] in grades
        assert 0.01 <= luck["top_percent"] <= 100


def test_multi_open_luck_table_reuses_single_table():
    box = _get_simulatable_boxes(_load_raw_data())["megabox"]
    single_table = _build_box_luck_table("megabox", box, None)
    assert _build_box_luck_multi_table(single_table, 3) == _build_box_luck_table("megabox", box, None, 3)


def test_substitute_reward_is_valued_as_drawn_item():
    substitute = {
        "reward_type": "bling",
        "amount": 1000,
        "substitute_for": {"reward_type": "mythicSkins", "amount": 1, "target_item": {"rarity": 35}},
    }
    assert _reward_event_luck_value(substitute) == _reward_event_luck_value(
        {"reward_type": "mythicSkins", "amount": 1, "target_item": {"rarity": 35}}
    )


def test_buffy_luck_value_uses_drawn_buffy_type_price():
    from types import SimpleNamespace

    def value(buffy_type):
        return _reward_event_luck_value({"reward_type": "buffies", "amount": 1, "target_item": {"kind": "buffy", "buffy_type": buffy_type}})

    assert value("gadget") < value("starPower") < value("hypercharge")
    # 代替報酬(1000パワーポイント)は、抽選された元のバフィーの価値で評価する
    substitute_value = _reward_event_luck_value({
        "reward_type": "powerPoints",
        "amount": 1000,
        "substitute_for": {"reward_type": "buffies", "amount": 1, "target_item": {"kind": "buffy", "buffy_type": "hypercharge"}},
    })
    assert substitute_value == value("hypercharge")

    buffies = [
        SimpleNamespace(id=1, type="gadget", is_invalid=False),
        SimpleNamespace(id=2, type="starPower", is_invalid=False),
        SimpleNamespace(id=3, type="hypercharge", is_invalid=False),
        SimpleNamespace(id=4, type="bling", is_invalid=False),
    ]
    distribution = _reward_entry_luck_distribution({"type": "buffies", "amount": 1}, {"buffies": buffies})
    assert sorted(distribution) == sorted([value("gadget"), value("starPower"), value("hypercharge")])
    assert all(abs(probability - 1 / 3) < 1e-9 for probability in distribution.values())
