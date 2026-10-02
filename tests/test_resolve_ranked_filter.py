from app.services.brawl_service import resolve_ranked_filter

POOL = [
    {"mode": "gemGrab", "maps": [{"map_en": "Hard Rock Mine"}, {"map_en": "Undermine"}]},
    {"mode": "knockout", "maps": [{"map_en": "Belle's Rock"}]},
]


def test_keeps_values_in_pool():
    assert resolve_ranked_filter(POOL, "gemGrab", "Undermine") == ("gemGrab", "Undermine")
    assert resolve_ranked_filter(POOL, "knockout", None) == ("knockout", None)
    assert resolve_ranked_filter(POOL, None, None) == (None, None)


def test_unknown_map_falls_back_to_mode():
    assert resolve_ranked_filter(POOL, "gemGrab", "No Such Map") == ("gemGrab", None)
    # 別モードのマップもそのモードには無いので外す
    assert resolve_ranked_filter(POOL, "gemGrab", "Belle's Rock") == ("gemGrab", None)


def test_unknown_mode_falls_back_to_overall():
    assert resolve_ranked_filter(POOL, "duoShowdown", "Hard Rock Mine") == (None, None)
    assert resolve_ranked_filter(POOL, None, "Hard Rock Mine") == (None, None)


def test_empty_pool_passes_through():
    assert resolve_ranked_filter([], "gemGrab", "Anything") == ("gemGrab", "Anything")
