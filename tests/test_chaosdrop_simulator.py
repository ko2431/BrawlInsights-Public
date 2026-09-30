import json
from pathlib import Path
from types import SimpleNamespace

from app.routers.tools import (
    _build_chaos_reveal_steps,
    _build_choice_pick_item_counts,
    _classify_value_drop_effect_tier,
    _materialize_reward_events,
    _pick_reward_target_item,
    simulate_drop_box_open,
)


class DummyRequest:
    def url_for(self, name, **params):
        assert name == "static"
        return f"/static/{params['path']}"


def test_chaosdrop_open_uses_one_combined_reveal_script():
    raw_data = json.loads(Path("app/data/drop_boxes.json").read_text(encoding="utf-8"))

    result = simulate_drop_box_open(None, raw_data, "chaosdrop")

    chaos_events = [event for event in result["events"] if event["type"] == "chaos_reveal"]
    assert len(chaos_events) == 1
    assert not any(event["type"] == "split" for event in result["events"])
    assert not any(event["type"] == "rarity" for event in result["events"])

    chaos_event = chaos_events[0]
    assert chaos_event["final_count"] == result["split_count"]
    assert len(chaos_event["steps"]) >= 1

    reward_events = [event for event in result["events"] if event["type"] == "reward"]
    assert len(reward_events) == result["split_count"]
    assert {event["rarity_key"] for event in reward_events} == {chaos_event["final_rarity_key"]}


def test_chaosdrop_reveal_steps_use_atomic_splits_and_upgrades():
    steps = _build_chaos_reveal_steps(8, "ultra")

    assert len(steps) >= 8
    assert [step["action"] for step in steps].count("split") == 3
    assert [step["action"] for step in steps].count("upgrade") == 5
    assert all(step.get("jump") in {None, 1} for step in steps)
    assert steps[0]["from_rarity_key"] == "rare"
    assert steps[0]["to_rarity_key"] == "superRare"
    assert steps[-1]["display_count"] == 8
    assert steps[-1]["display_rarity_key"] == "ultra"


def test_accessory_reward_target_items_are_selected_from_matching_table_rows(monkeypatch):
    request = DummyRequest()
    accessories = [
        SimpleNamespace(id=1, type="gadget", brawler_id=16000000, en="Spark Plug", ja="スパークプラグ", rarity=None, is_invalid=False),
        SimpleNamespace(id=2, type="starPower", brawler_id=16000000, en="Energize", ja="元気注入", rarity=None, is_invalid=False),
        SimpleNamespace(id=3, type="hyperCharge", brawler_id=16000000, en="Scrappy 2.0", ja="スクラッピー2.0", rarity=None, is_invalid=False),
        SimpleNamespace(id=4, type="gadget", brawler_id=16000001, en="Invalid Gadget", ja=None, rarity=None, is_invalid=True),
    ]
    dynamic_targets = {"accessories": accessories}
    monkeypatch.setattr("app.routers.tools.random.choice", lambda candidates: candidates[0])

    gadget = _pick_reward_target_item(request, {}, "gadgets", dynamic_targets)
    star_power = _pick_reward_target_item(request, {}, "starPowers", dynamic_targets)
    hypercharge = _pick_reward_target_item(request, {}, "hypercharges", dynamic_targets)

    assert gadget["id"] == 1
    assert gadget["kind"] == "gadget"
    assert gadget["name_en"] == "Spark Plug"
    assert gadget["image"] == "/static/images/gadgets/1.png"
    assert gadget["fallback_image"] == "/static/images/accessories/gadget.png"

    assert star_power["id"] == 2
    assert star_power["kind"] == "starPower"
    assert star_power["image"] == "/static/images/starpowers/2.png"
    assert star_power["fallback_image"] == "/static/images/accessories/starpower.png"

    assert hypercharge["id"] == 3
    assert hypercharge["kind"] == "hypercharge"
    assert hypercharge["image"] == "/static/images/hypercharge/3.png"
    assert hypercharge["fallback_image"] == "/static/images/ui/hypercharge.webp"


def test_brawler_reward_target_items_are_selected_from_matching_rarity(monkeypatch):
    request = DummyRequest()
    brawlers = [
        SimpleNamespace(id=16000001, name_en="Colt", name_ja="コルト", rarity=2, is_temporary=False),
        SimpleNamespace(id=16000002, name_en="Bull", name_ja="ブル", rarity=2, is_temporary=True),
        SimpleNamespace(id=16000003, name_en="Jessie", name_ja="ジェシー", rarity=3, is_temporary=False),
        SimpleNamespace(id=16000004, name_en="Piper", name_ja="エリザベス", rarity=4, is_temporary=False),
    ]
    dynamic_targets = {"brawlers": brawlers}
    monkeypatch.setattr("app.routers.tools.random.choice", lambda candidates: candidates[0])

    rare = _pick_reward_target_item(request, {}, "rareBrawlers", dynamic_targets)
    super_rare = _pick_reward_target_item(request, {}, "superRareBrawlers", dynamic_targets)
    epic = _pick_reward_target_item(request, {}, "epicBrawlers", dynamic_targets)

    assert rare["id"] == 16000001
    assert rare["kind"] == "brawler"
    assert rare["name_en"] == "Colt"
    assert rare["image"] == "/static/images/brawler_portraits/16000001.png"
    assert rare["fallback_image"] == "/static/images/brawler_portraits/16000001.png"

    assert super_rare["id"] == 16000003
    assert super_rare["rarity"] == 3

    assert epic["id"] == 16000004
    assert epic["rarity"] == 4


def test_brawler_reward_target_item_uses_default_skin_image(monkeypatch):
    request = DummyRequest()
    brawlers = [SimpleNamespace(id=16000001, name_en="Colt", name_ja="コルト", rarity=2, is_temporary=False)]
    dynamic_targets = {"brawlers": brawlers, "default_skin_ids": {16000001: 29000001}}
    monkeypatch.setattr("app.routers.tools.random.choice", lambda candidates: candidates[0])

    rare = _pick_reward_target_item(request, {}, "rareBrawlers", dynamic_targets)

    # デフォルトスキンがあれば、スキンと同じ見た目になるようその画像を使い、肖像画像は予備にする
    assert rare["image"] == "/static/images/skins/29000001.webp"
    assert rare["fallback_image"] == "/static/images/brawler_portraits/16000001.png"


def test_spray_reward_target_items_exclude_event_sprays_without_prices(monkeypatch):
    request = DummyRequest()
    sprays = [
        SimpleNamespace(id=1, rarity=40, bling_price=None, gems_price=None),
        SimpleNamespace(id=2, rarity=10, bling_price=29, gems_price=None),
        SimpleNamespace(id=3, rarity=0, bling_price=None, gems_price=19),
    ]
    dynamic_targets = {"sprays": sprays}
    monkeypatch.setattr("app.routers.tools.random.choice", lambda candidates: candidates[0])

    spray = _pick_reward_target_item(request, {}, "sprays", dynamic_targets)

    assert spray["id"] == 2
    assert spray["kind"] == "spray"
    assert spray["name_en"] == "Spray"
    assert spray["image"] == "/static/images/spray/2.webp"
    assert spray["fallback_image"] == "/static/images/ui/sprays.webp"


def test_general_spray_reward_target_items_include_sprays_explicitly_targeted_elsewhere(monkeypatch):
    request = DummyRequest()
    # 他のボックスで明示指定されているスプレーでも、価格があれば一般枠の抽選対象になる
    sprays = [
        SimpleNamespace(id=68000519, rarity=10, bling_price=750, gems_price=19),
        SimpleNamespace(id=2, rarity=10, bling_price=29, gems_price=None),
        SimpleNamespace(id=3, rarity=0, bling_price=None, gems_price=None),
    ]
    candidate_ids = []
    monkeypatch.setattr(
        "app.routers.tools.random.choice",
        lambda candidates: candidate_ids.extend(candidate.id for candidate in candidates) or candidates[0],
    )

    spray = _pick_reward_target_item(request, {}, "sprays", {"sprays": sprays})

    assert candidate_ids == [68000519, 2]
    assert spray["id"] == 68000519
    assert spray["kind"] == "spray"
    assert spray["name_en"] == "Spray"


def test_spray_reward_target_items_use_explicit_event_targets(monkeypatch):
    request = DummyRequest()
    sprays_by_id = {
        68000517: SimpleNamespace(id=68000517, rarity=10, bling_price=None, gems_price=None),
        68000002: SimpleNamespace(id=68000002, rarity=10, bling_price=750, gems_price=19),
    }
    reward = {
        "spray_targets": [
            {
                "id": 68000517,
                "name_ja": "レコードプレイヤー",
                "name_en": "Record Player",
            }
        ]
    }
    dynamic_targets = {
        "sprays_by_id": sprays_by_id,
        "sprays": list(sprays_by_id.values()),
    }
    monkeypatch.setattr("app.routers.tools.random.choice", lambda candidates: candidates[0])

    spray = _pick_reward_target_item(request, reward, "sprays", dynamic_targets)

    assert spray["id"] == 68000517
    assert spray["kind"] == "spray"
    assert spray["name_ja"] == "レコードプレイヤー"
    assert spray["name_en"] == "Record Player"
    assert spray["image"] == "/static/images/spray/68000517.webp"
    assert spray["fallback_image"] == "/static/images/ui/sprays.webp"


def test_general_pin_reward_target_items_include_pins_explicitly_targeted_elsewhere(monkeypatch):
    request = DummyRequest()
    # 他のボックスで明示指定されているピンズでも、レア度が合えば一般枠の抽選対象になる
    pins = [
        SimpleNamespace(id=52009999, rarity=10, description_en=None, description_ja=None),
        SimpleNamespace(id=52000002, rarity=10, description_en=None, description_ja=None),
        SimpleNamespace(id=52000003, rarity=20, description_en=None, description_ja=None),
    ]
    candidate_ids = []
    monkeypatch.setattr(
        "app.routers.tools.random.choice",
        lambda candidates: candidate_ids.extend(candidate.id for candidate in candidates) or candidates[1],
    )

    pin = _pick_reward_target_item(request, {}, "normalPins", {"pins": pins})

    assert candidate_ids == [52009999, 52000002]
    assert pin["id"] == 52000002
    assert pin["kind"] == "pin"
    assert pin["name_en"] == "Pin"
    assert pin["image"] == "/static/images/pins/52000002.webp"


def test_pin_reward_target_items_use_explicit_event_targets(monkeypatch):
    request = DummyRequest()
    pins_by_id = {
        52001707: SimpleNamespace(id=52001707, rarity=40, description_en=None, description_ja=None),
        52000002: SimpleNamespace(id=52000002, rarity=10, description_en=None, description_ja=None),
    }
    reward = {
        "pin_targets": [
            {
                "id": 52001707,
                "name_en": "Monster Egg",
            }
        ]
    }
    dynamic_targets = {
        "pins_by_id": pins_by_id,
        "pins": list(pins_by_id.values()),
    }
    monkeypatch.setattr("app.routers.tools.random.choice", lambda candidates: candidates[0])

    pin = _pick_reward_target_item(request, reward, "eventPins", dynamic_targets)

    assert pin["id"] == 52001707
    assert pin["kind"] == "pin"
    assert pin["name_en"] == "Monster Egg"
    assert pin["image"] == "/static/images/pins/52001707.webp"


def test_general_profile_icon_reward_target_items_include_icons_explicitly_targeted_elsewhere(monkeypatch):
    request = DummyRequest()
    # 他のボックスで明示指定されているアイコンでも、価格があれば一般枠の抽選対象になる
    icons = [
        SimpleNamespace(id=28009999, bling_price=750, gems_price=19),
        SimpleNamespace(id=28000002, bling_price=29, gems_price=None),
        SimpleNamespace(id=28000003, bling_price=None, gems_price=None),
    ]
    candidate_ids = []
    monkeypatch.setattr(
        "app.routers.tools.random.choice",
        lambda candidates: candidate_ids.extend(candidate.id for candidate in candidates) or candidates[1],
    )

    icon = _pick_reward_target_item(request, {}, "profileIcons", {"player_icons": icons})

    assert candidate_ids == [28009999, 28000002]

    assert icon["id"] == 28000002
    assert icon["kind"] == "playerIcon"
    assert icon["name_en"] == "Profile Icon"
    assert icon["image"] == "/static/images/player_icon/28000002.webp"


def test_profile_icon_reward_target_items_use_explicit_event_targets(monkeypatch):
    request = DummyRequest()
    icons_by_id = {
        28000449: SimpleNamespace(id=28000449, bling_price=None, gems_price=None),
        28000002: SimpleNamespace(id=28000002, bling_price=29, gems_price=None),
    }
    reward = {
        "icon_targets": [
            {
                "id": 28000449,
                "name_en": "Mutated Brawl",
            }
        ]
    }
    dynamic_targets = {
        "player_icons_by_id": icons_by_id,
        "player_icons": list(icons_by_id.values()),
    }
    monkeypatch.setattr("app.routers.tools.random.choice", lambda candidates: candidates[0])

    icon = _pick_reward_target_item(request, reward, "profileIcons", dynamic_targets)

    assert icon["id"] == 28000449
    assert icon["kind"] == "playerIcon"
    assert icon["name_en"] == "Mutated Brawl"
    assert icon["image"] == "/static/images/player_icon/28000449.webp"


def test_sushi_spray_rows_keep_event_and_general_pools_separate(monkeypatch):
    request = DummyRequest()
    event_spray = SimpleNamespace(id=68000458, rarity=10, bling_price=750, gems_price=19)
    general_spray = SimpleNamespace(id=68000003, rarity=10, bling_price=750, gems_price=19)
    sprays_by_id = {
        event_spray.id: event_spray,
        general_spray.id: general_spray,
    }
    dynamic_targets = {
        "sprays_by_id": sprays_by_id,
        "sprays": list(sprays_by_id.values()),
    }
    sushi_event_reward = {
        "display_name_en": "Event Spray",
        "spray_targets": [
            {
                "id": event_spray.id,
                "name_en": "Ice Cream Cactus",
            }
        ],
    }
    sushi_general_reward = {
        "display_name_en": "Spray (19 Gems)",
    }
    candidate_ids = []
    monkeypatch.setattr(
        "app.routers.tools.random.choice",
        lambda candidates: candidate_ids.append([
            (candidate[0] if isinstance(candidate, tuple) else candidate).id for candidate in candidates
        ]) or candidates[-1],
    )

    event_result = _pick_reward_target_item(request, sushi_event_reward, "sprays", dynamic_targets)
    general_result = _pick_reward_target_item(request, sushi_general_reward, "sprays", dynamic_targets)

    # イベント枠は指定のスプレーのみ、一般枠は価格のあるスプレーすべて(イベント枠で指定されたものも含む)
    assert candidate_ids == [[event_spray.id], [event_spray.id, general_spray.id]]
    assert event_result["id"] == event_spray.id
    assert event_result["name_en"] == "Ice Cream Cactus"
    assert general_result["id"] == general_spray.id
    assert general_result["name_en"] == "Spray"


def test_sushi_generic_pin_row_uses_nine_gem_pool_and_icon_row_uses_all_priced_icons(monkeypatch):
    request = DummyRequest()
    event_pin = SimpleNamespace(id=52002404, rarity=10, gems_price=9, bling_price=None, description_en=None, description_ja=None)
    wrong_price_pin = SimpleNamespace(id=52000001, rarity=10, gems_price=19, bling_price=None, description_en=None, description_ja=None)
    nine_gem_pin = SimpleNamespace(id=52000002, rarity=10, gems_price=9, bling_price=None, description_en=None, description_ja=None)
    event_icon = SimpleNamespace(id=28000819, gems_price=9, bling_price=None)
    unpriced_icon = SimpleNamespace(id=28000003, gems_price=None, bling_price=None)
    shop_icon = SimpleNamespace(id=28000001, gems_price=19, bling_price=750)
    dynamic_targets = {
        "pins_by_id": {event_pin.id: event_pin},
        "pins": [wrong_price_pin, nine_gem_pin, event_pin],
        "player_icons_by_id": {event_icon.id: event_icon},
        "player_icons": [unpriced_icon, shop_icon, event_icon],
    }
    sushi_event_pin_reward = {
        "display_name_en": "Event Pin",
        "pin_targets": [{"id": event_pin.id, "name_en": "Katana Kingdom Ninja Bao"}],
    }
    sushi_general_pin_reward = {
        "display_name_en": "Normal Pin",
        "target_gems_price": 9,
    }
    sushi_event_icon_reward = {
        "display_name_en": "Event Profile Icon",
        "icon_targets": [{"id": event_icon.id, "name_en": "Bento Box"}],
    }
    # 寿司のアイコン一般枠には価格の指定がない
    sushi_general_icon_reward = {}
    monkeypatch.setattr("app.routers.tools.random.choice", lambda candidates: candidates[0])

    event_pin_result = _pick_reward_target_item(request, sushi_event_pin_reward, "eventPins", dynamic_targets)
    general_pin_result = _pick_reward_target_item(request, sushi_general_pin_reward, "normalPins", dynamic_targets)
    event_icon_result = _pick_reward_target_item(request, sushi_event_icon_reward, "profileIcons", dynamic_targets)
    general_icon_result = _pick_reward_target_item(request, sushi_general_icon_reward, "profileIcons", dynamic_targets)

    assert event_pin_result["id"] == event_pin.id
    assert event_pin_result["name_en"] == "Katana Kingdom Ninja Bao"
    assert general_pin_result["id"] == nine_gem_pin.id
    assert event_icon_result["id"] == event_icon.id
    assert event_icon_result["name_en"] == "Bento Box"
    assert general_icon_result["id"] == shop_icon.id

    # イベント枠で指定されたピンズ・アイコンも、条件が合えば一般枠の抽選対象になる
    monkeypatch.setattr("app.routers.tools.random.choice", lambda candidates: candidates[-1])
    assert _pick_reward_target_item(request, sushi_general_pin_reward, "normalPins", dynamic_targets)["id"] == event_pin.id
    assert _pick_reward_target_item(request, sushi_general_icon_reward, "profileIcons", dynamic_targets)["id"] == event_icon.id


def test_ranked_drop_spray_uses_explicit_ranked_pool(monkeypatch):
    request = DummyRequest()
    raw_data = json.loads(Path("app/data/drop_boxes.json").read_text(encoding="utf-8"))
    ranked_spray_reward = next(
        reward
        for reward in raw_data["boxes"]["rankeddrop"]["rewards"]
        if reward.get("display_name_en") == "Ranked Spray"
    )
    target_ids = [target["id"] for target in ranked_spray_reward["spray_targets"]]
    sprays_by_id = {
        target_id: SimpleNamespace(id=target_id, rarity=10, bling_price=None, gems_price=None)
        for target_id in target_ids
    }
    dynamic_targets = {
        "sprays_by_id": sprays_by_id,
        "sprays": list(sprays_by_id.values()),
    }
    monkeypatch.setattr("app.routers.tools.random.choice", lambda candidates: candidates[0])

    ranked_spray = _pick_reward_target_item(request, ranked_spray_reward, "sprays", dynamic_targets)

    assert len(target_ids) == 19
    assert target_ids[:5] == [68000156, 68000180, 68000194, 68000215, 68000237]
    assert ranked_spray["id"] == target_ids[0]
    assert ranked_spray["name_en"] == "Power League Season 14"


def test_mega_box_uses_explicit_classic_event_pin_pool():
    raw_data = json.loads(Path("app/data/drop_boxes.json").read_text(encoding="utf-8"))
    mega_pin_reward = next(
        reward
        for reward in raw_data["boxes"]["megabox"]["rewards"]
        if reward.get("type") == "eventPins"
    )

    assert [target["id"] for target in mega_pin_reward["pin_targets"]] == [
        52001801,
        52001800,
        52001802,
        52001805,
        52001803,
        52001804,
    ]


def test_nano_drop_uses_choice_reveal_capsules():
    raw_data = json.loads(Path("app/data/drop_boxes.json").read_text(encoding="utf-8"))

    result = simulate_drop_box_open(None, raw_data, "nanodrop")

    choice_events = [event for event in result["events"] if event["type"] == "choice_reveal"]
    assert len(choice_events) == 1
    choice_event = choice_events[0]
    assert choice_event["choice_count"] == 3
    assert len(choice_event["capsules"]) == 5
    assert choice_event["total_items"] == result["split_count"]
    assert choice_event["total_items"] >= 1
    assert len(choice_event["pick_item_counts"]) == choice_event["choice_count"]
    assert sum(choice_event["pick_item_counts"]) == choice_event["total_items"]
    assert all(0 <= count <= choice_event["max_items_per_capsule"] for count in choice_event["pick_item_counts"])

    reward_events = [event for event in result["events"] if event["type"] == "reward"]
    assert all("choice_slot" not in event for event in reward_events)
    assert len(reward_events) == result["split_count"]


def test_choice_pick_item_counts_are_fixed_to_total_items():
    counts = _build_choice_pick_item_counts(9)

    assert len(counts) == 3
    assert sum(counts) == 9
    assert all(0 <= count <= 3 for count in counts)


def test_value_drop_effect_tier_classifies_resource_amounts_by_box_values():
    box = {
        "rewards": [
            {"type": "coins", "amount": 100},
            {"type": "coins", "amount": 200},
            {"type": "coins", "amount": 500},
            {"type": "coins", "amount": 1000},
        ]
    }

    assert _classify_value_drop_effect_tier(box, {"type": "coins", "amount": 100}, 100) == "short"
    assert _classify_value_drop_effect_tier(box, {"type": "coins", "amount": 200}, 200) == "middle"
    assert _classify_value_drop_effect_tier(box, {"type": "coins", "amount": 500}, 500) == "middle"
    assert _classify_value_drop_effect_tier(box, {"type": "coins", "amount": 1000}, 1000) == "long"


def test_value_drop_effect_tier_classifies_cosmetics_middle_and_others_long():
    box = {"rewards": []}

    assert _classify_value_drop_effect_tier(box, {"type": "xpDoublers", "amount": "100-300"}, 174) == "short"
    assert _classify_value_drop_effect_tier(box, {"type": "eventPins", "amount": 1}, 1) == "middle"
    assert _classify_value_drop_effect_tier(box, {"type": "profileIcons", "amount": 1}, 1) == "middle"
    assert _classify_value_drop_effect_tier(box, {"type": "sprays", "amount": 1}, 1) == "middle"
    assert _classify_value_drop_effect_tier(box, {"type": "hypercharges", "amount": 1}, 1) == "long"


def _build_dedupe_raw_data(rewards):
    return {
        "reward_types": {},
        "boxes": {
            "example": {
                "items_per_open": 6,
                "rewards": rewards,
            }
        },
    }


def test_same_target_item_is_not_dropped_twice_in_one_open():
    request = DummyRequest()
    skins = [
        SimpleNamespace(id=29000000 + index, en=f"Skin {index}", ja=None, rarity=25, is_limited=False)
        for index in range(6)
    ]
    dynamic_targets = {
        "skins_by_id": {skin.id: skin for skin in skins},
        "skins": skins,
    }
    raw_data = _build_dedupe_raw_data([{"type": "epicSkins", "amount": 1, "probability": 1}])

    for _ in range(50):
        result = simulate_drop_box_open(request, raw_data, "example", dynamic_targets)
        skin_ids = [event["target_item"]["id"] for event in result["events"] if event["type"] == "reward"]
        assert sorted(skin_ids) == sorted(skin.id for skin in skins)


def test_exhausted_target_reward_becomes_substitute_without_redrawing():
    """候補を出し切った報酬は他の報酬に引き直さず、そのまま抽選した上で代替報酬になる"""
    request = DummyRequest()
    skin = SimpleNamespace(id=29000001, en="Only Skin", ja=None, rarity=45, is_limited=False)
    dynamic_targets = {
        "skins_by_id": {skin.id: skin},
        "skins": [skin],
    }
    raw_data = _build_dedupe_raw_data([
        {"type": "legendarySkins", "amount": 1, "probability": 90, "skin_targets": [{"id": skin.id}]},
        {"type": "coins", "amount": 100, "probability": 10},
    ])
    raw_data["reward_types"] = {
        "coins": {"name_ja": "コイン", "name_en": "Coins"},
        "bling": {"name_ja": "ジュエルチップ", "name_en": "Bling"},
        "legendarySkins": {"name_ja": "レジェンドレアスキン", "substitute": {"type": "bling", "amount": 1000}},
    }

    substitute_count = 0
    for _ in range(50):
        result = simulate_drop_box_open(request, raw_data, "example", dynamic_targets)
        reward_events = [event for event in result["events"] if event["type"] == "reward"]
        assert len(reward_events) == 6
        assert sum(event["reward_type"] == "legendarySkins" for event in reward_events) <= 1
        for event in reward_events:
            if event["reward_type"] == "bling":
                substitute_count += 1
                assert event["amount"] == 1000
                assert event["substitute_for"]["reward_type"] == "legendarySkins"
                assert event["substitute_for"]["target_item"]["id"] == skin.id
            else:
                assert event["reward_type"] in {"legendarySkins", "coins"}
    # スキン枠(90%)の大半は2回目以降に代替報酬になる
    assert substitute_count > 50 * 3


def test_ranged_skin_substitute_uses_duplicated_skin_rarity():
    request = DummyRequest()
    skin = SimpleNamespace(id=29000002, en="Epic Skin", ja=None, rarity=25, is_limited=False)
    reward_types = {
        "bling": {"name_ja": "ジュエルチップ", "name_en": "Bling"},
        "epicSkins": {"substitute": {"type": "bling", "amount": 500}},
        "rare-mythicSkins": {"substitute": {"type": "bling", "amount": "100-1000"}},
    }
    event = {"type": "reward", "reward_type": "rare-mythicSkins", "amount": 1, "display_name_ja": "スキン"}
    picked_target_keys = {("skin", skin.id)}

    events = _materialize_reward_events(
        request,
        event,
        {"skin_targets": [{"id": skin.id}]},
        {"skins_by_id": {skin.id: skin}, "skins": [skin]},
        picked_target_keys,
        reward_types,
    )

    assert len(events) == 1
    assert events[0]["reward_type"] == "bling"
    assert events[0]["amount"] == 500


def test_brawler_targets_limit_new_brawler_box_rewards(monkeypatch):
    request = DummyRequest()
    brawlers = [
        SimpleNamespace(id=16000108, name_en="Wendy", name_ja="ウェンディ", rarity=5, is_temporary=False),
        SimpleNamespace(id=16000109, name_en="Cosmo", name_ja="コスモ", rarity=5, is_temporary=False),
    ]
    monkeypatch.setattr("app.routers.tools.random.choice", lambda candidates: candidates[0])

    target = _pick_reward_target_item(request, {"brawler_targets": [{"id": 16000109}]}, "mythicBrawlers", {"brawlers": brawlers})

    assert target["id"] == 16000109


def test_new_brawler_boxes_define_brawler_targets():
    raw_data = json.loads(Path("app/data/drop_boxes.json").read_text(encoding="utf-8"))
    for box_key in ("siriusbox", "najiabox", "damianbox", "starrnovabox", "boltbox", "noribox", "wendybox", "cosmobox", "vincebox"):
        named_brawler_rewards = [
            reward for reward in raw_data["boxes"][box_key]["rewards"]
            if reward["type"].endswith("Brawlers") and reward.get("display_name_en")
        ]
        assert len(named_brawler_rewards) == 1, box_key
        assert len(named_brawler_rewards[0].get("brawler_targets", [])) == 1, box_key


def test_multi_amount_reward_does_not_repeat_same_target_item():
    request = DummyRequest()
    brawlers = [
        SimpleNamespace(id=16000001, name_en="Colt", name_ja="コルト", rarity=2, is_temporary=False),
        SimpleNamespace(id=16000002, name_en="Bull", name_ja="ブル", rarity=2, is_temporary=False),
    ]
    picked_target_keys = set()
    event = {"type": "reward", "reward_type": "rareBrawlers", "amount": 3}
    reward_types = {
        "credits": {"name_ja": "クレジット", "name_en": "Credits"},
        "rareBrawlers": {"substitute": {"type": "credits", "amount": 100}},
    }

    events = _materialize_reward_events(request, event, {}, {"brawlers": brawlers}, picked_target_keys, reward_types)

    brawler_events = [item for item in events if item["reward_type"] == "rareBrawlers"]
    assert sorted(item["target_item"]["id"] for item in brawler_events) == [16000001, 16000002]
    assert picked_target_keys == {("brawler", 16000001), ("brawler", 16000002)}
    # 候補を出し切った3個目は代替報酬になる
    substitute_events = [item for item in events if item["reward_type"] == "credits"]
    assert len(substitute_events) == 1
    assert substitute_events[0]["amount"] == 100
    assert substitute_events[0]["substitute_for"]["amount"] == 1


def test_coffin_box_has_nine_normal_and_one_premium_slot():
    raw_data = json.loads(Path("app/data/drop_boxes.json").read_text(encoding="utf-8"))
    box = raw_data["boxes"]["coffinbox"]

    assert box["items_per_open"] == 10
    assert box["premium_drops"] == 1
    assert abs(sum(reward["probability"] for reward in box["rewards"]) - 1) < 1e-6
    assert abs(sum(reward["probability"] for reward in box["premium_rewards"]) - 1) < 1e-6

    # 確定枠のスキンは、全レア度のスキン36種が同じ確率で出る
    all_skin_reward = next(reward for reward in box["premium_rewards"] if reward["type"] == "rare-hyperchargeSkins")
    all_skin_ids = {skin["id"] for skin in all_skin_reward["skin_targets"]}
    normal_skin_ids = {
        skin["id"]
        for reward in box["rewards"] if reward["type"].endswith("Skins")
        for skin in reward["skin_targets"]
    }
    assert len(all_skin_ids) == 36
    assert normal_skin_ids | {29001251} == all_skin_ids

    result = simulate_drop_box_open(None, raw_data, "coffinbox")
    reward_events = [event for event in result["events"] if event["type"] == "reward"]
    assert len(reward_events) == 10
    assert [event.get("is_premium") for event in reward_events].count(True) == 1


def test_all_rarity_skin_reward_draws_every_rarity_including_skins_missing_in_db(monkeypatch):
    request = DummyRequest()
    skins = [
        SimpleNamespace(id=29000001 + index, en=f"Skin {index}", ja=None, rarity=rarity, is_limited=True)
        for index, rarity in enumerate((1, 11, 21, 31, 41, 51))
    ]
    reward = {
        "skin_targets": [{"id": skin.id} for skin in skins] + [
            # DB未登録でも、名前・レア度を書いておけば先行して抽選対象になる
            {"id": 29009998, "name_ja": "新スキン", "name_en": "New Skin", "rarity": 40},
            # レア度が分からないDB未登録のスキンは抽選対象にしない
            {"id": 29009999},
        ],
    }
    dynamic_targets = {"skins_by_id": {skin.id: skin for skin in skins}, "skins": skins}
    drawn = {}
    monkeypatch.setattr("app.routers.tools.random.choice", lambda candidates: candidates[len(drawn) % len(candidates)])

    for _ in range(len(skins) + 1):
        target = _pick_reward_target_item(request, reward, "rare-hyperchargeSkins", dynamic_targets)
        drawn[target["id"]] = target

    assert set(drawn) == {skin.id for skin in skins} | {29009998}
    assert drawn[29009998]["name_ja"] == "新スキン"
    assert drawn[29009998]["rarity"] == 40
    assert drawn[29009998]["image"] == "/static/images/skins/29009998.webp"


def test_skin_in_db_takes_precedence_over_json_fallback():
    request = DummyRequest()
    skin = SimpleNamespace(id=29009998, en="DB Name", ja="DB名", rarity=45, is_limited=True)
    reward = {"skin_targets": [{"id": skin.id, "name_ja": "仮名", "name_en": "Temp", "rarity": 40}]}

    target = _pick_reward_target_item(request, reward, "mythicSkins", {"skins_by_id": {skin.id: skin}, "skins": [skin]})

    assert target["name_ja"] == "DB名"
    assert target["rarity"] == 45


def test_general_skin_targets_skip_limited_and_variant_skins():
    request = DummyRequest()
    limited = SimpleNamespace(id=29000748, en="Blue King Frank", ja=None, rarity=50, is_limited=True, gems_price=299)
    variant = SimpleNamespace(id=29000581, en="Light Mecha Mortis", ja=None, rarity=50, is_limited=None, gems_price=49)
    normal = SimpleNamespace(id=29000558, en="Mecha Mortis", ja=None, rarity=50, is_limited=None, gems_price=299)
    skins = [limited, variant, normal]
    dynamic_targets = {"skins_by_id": {skin.id: skin for skin in skins}, "skins": skins}

    for _ in range(20):
        target = _pick_reward_target_item(request, {"type": "legendarySkins"}, "legendarySkins", dynamic_targets)
        assert target["id"] == normal.id


def test_variant_skin_is_detected_by_rarity_and_gems_price():
    from app.routers.tools import _is_available_box_skin

    def skin(rarity, gems_price):
        return SimpleNamespace(id=1, rarity=rarity, is_limited=None, gems_price=gems_price)

    # ウルトラレア・レジェンドレアの49エメラルドは色違い
    assert not _is_available_box_skin(skin(40, 49))
    assert not _is_available_box_skin(skin(50, 49))
    # 通常価格や、ハイパーレア以下の安いスキン、価格未取得のスキンは排出対象
    assert _is_available_box_skin(skin(50, 299))
    assert _is_available_box_skin(skin(30, 39))
    assert _is_available_box_skin(skin(20, 29))
    assert _is_available_box_skin(skin(50, None))


def test_trophy_boxes_draw_skins_from_general_pool():
    # トロフィーボックスは「限定スキンを除く全スキン」から排出されるため、明示指定リストを持たない
    raw_data = json.loads(Path("app/data/drop_boxes.json").read_text(encoding="utf-8"))
    for box_key, box in raw_data["boxes"].items():
        if not box_key.endswith("trophybox"):
            continue
        for reward in box["rewards"]:
            if reward["type"].endswith("Skins"):
                assert not reward.get("skin_targets"), (box_key, reward["type"])


def test_event_pin_missing_in_db_is_drawn_with_json_name():
    request = DummyRequest()
    reward = {"pin_targets": [{"id": 52009999, "name_en": "New Pin"}]}

    target = _pick_reward_target_item(request, reward, "eventPins", {"pins_by_id": {}, "pins": []})

    assert target["id"] == 52009999
    assert target["name_en"] == "New Pin"
    assert target["image"] == "/static/images/pins/52009999.webp"


def test_summary_items_are_grouped_by_category_and_rarity():
    from app.routers.tools import _build_summary_item, _merge_summary_items, _sort_summary_items

    def skin(skin_id, rarity):
        return {"kind": "skin", "id": skin_id, "rarity": rarity}

    # (reward_type, amount, target_item) を出た順に並べる
    drawn = [
        ("bling", 50, None),
        ("rareSkins", 1, skin(1, 5)),
        ("coins", 100, None),
        ("sprays", 1, {"kind": "spray", "id": 10}),
        ("gadgets", 1, {"kind": "gadget", "id": 20}),
        ("legendarySkins", 1, skin(2, 45)),
        ("nanoPowers", 3, None),
        ("eventPins", 1, {"kind": "pin", "id": 30, "rarity": None}),
        ("profileIcons", 1, {"kind": "playerIcon", "id": 40}),
        ("buffies", 1, None),
        ("rareSkins", 1, skin(3, 8)),
        ("gems", 10, None),
        ("epicBrawlers", 1, {"kind": "brawler", "id": 50, "rarity": 4}),
        ("mythicBrawlers", 1, {"kind": "brawler", "id": 51, "rarity": 5}),
        ("hyperchargeSkins", 1, skin(4, 55)),
        ("coins", 200, None),
        ("xpDoublers", 300, None),
        ("powerPoints", 20, None),
        ("hypercharges", 1, {"kind": "hypercharge", "id": 60}),
        ("starPowers", 1, {"kind": "starPower", "id": 70}),
        ("credits", 30, None),
        ("proPassXp", 100, None),
        ("rarePins", 1, {"kind": "pin", "id": 31, "rarity": 20}),
        ("proPassSkinUpgrade", 1, None),
    ]
    summary = {}
    for reveal_index, (reward_type, amount, target_item) in enumerate(drawn, start=1):
        event = {"reward_type": reward_type, "amount": amount, "target_item": target_item, "reveal_index": reveal_index}
        item = _build_summary_item(event, [])
        summary[item["summary_key"]] = _merge_summary_items(summary.get(item["summary_key"]), item)

    ordered = [
        (item["reward_type"], (item.get("target_item") or {}).get("id"), item["amount"])
        for item in _sort_summary_items(summary)
    ]
    assert ordered == [
        ("mythicBrawlers", 51, 1),
        ("epicBrawlers", 50, 1),
        ("buffies", None, 1),
        ("hypercharges", 60, 1),
        ("starPowers", 70, 1),
        ("gadgets", 20, 1),
        ("proPassSkinUpgrade", None, 1),
        ("hyperchargeSkins", 4, 1),
        ("legendarySkins", 2, 1),
        # 種別もレア度も同じものは出た順
        ("rareSkins", 1, 1),
        ("rareSkins", 3, 1),
        ("rarePins", 31, 1),
        ("eventPins", 30, 1),
        ("profileIcons", 40, 1),
        ("sprays", 10, 1),
        ("nanoPowers", None, 3),
        ("gems", None, 10),
        ("coins", None, 300),
        ("powerPoints", None, 20),
        ("credits", None, 30),
        ("bling", None, 50),
        ("xpDoublers", None, 300),
        ("proPassXp", None, 100),
    ]


def _buffy(buffy_id, buffy_type, name_ja="シェリーのガジェットバフィー", name_en="SHELLY'S GADGET BUFFIE"):
    return SimpleNamespace(id=buffy_id, brawler_id=16000000, type=buffy_type, ja=name_ja, en=name_en, is_invalid=False)


def test_buffy_reward_draws_only_gadget_star_and_hyper_buffies(monkeypatch):
    request = DummyRequest()
    buffies = [
        _buffy(29001421, "default"),
        _buffy(29001434, "gadget"),
        _buffy(29001435, "starPower"),
        _buffy(29001436, "hypercharge"),
        _buffy(29001452, "bling"),
        _buffy(29001836, "ghostly"),
        SimpleNamespace(id=29009999, brawler_id=16000000, type="gadget", ja=None, en=None, is_invalid=True),
    ]
    candidate_ids = []
    monkeypatch.setattr(
        "app.routers.tools.random.choice",
        lambda candidates: candidate_ids.extend(candidate.id for candidate in candidates) or candidates[0],
    )

    target = _pick_reward_target_item(request, {}, "buffies", {"buffies": buffies})

    assert candidate_ids == [29001434, 29001435, 29001436]
    assert target == {
        "id": 29001434,
        "kind": "buffy",
        "reward_type": "buffies",
        "name_ja": "シェリーのガジェットバフィー",
        "name_en": "SHELLY'S GADGET BUFFIE",
        "brawler_id": 16000000,
        "buffy_type": "gadget",
        "image": "/static/images/buffies/29001434.png",
        "fallback_image": "/static/images/ui/buddy.png",
    }


def test_exhausted_buffies_become_power_point_substitute():
    request = DummyRequest()
    buffy = _buffy(29001434, "gadget")
    reward_types = {
        "powerPoints": {"name_ja": "パワーポイント", "name_en": "Power Points"},
        "buffies": {"substitute": {"type": "powerPoints", "amount": 1000}},
    }
    picked_target_keys = set()
    event = {"type": "reward", "reward_type": "buffies", "amount": 2, "display_name_ja": "バフィー"}

    events = _materialize_reward_events(request, event, {}, {"buffies": [buffy]}, picked_target_keys, reward_types)

    assert [item["reward_type"] for item in events] == ["buffies", "powerPoints"]
    assert events[0]["target_item"]["id"] == buffy.id
    assert picked_target_keys == {("buffy", buffy.id)}
    assert events[1]["amount"] == 1000
    assert events[1]["substitute_for"]["reward_type"] == "buffies"
    assert events[1]["substitute_for"]["target_item"]["id"] == buffy.id
