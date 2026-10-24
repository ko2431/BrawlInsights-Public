from app.routers.boards import _normalize_general_board_query


def test_all_filters_exclude_tournament_and_offtopic():
    assert _normalize_general_board_query("all", "latest") == ("latest", "all", None, ())
    assert _normalize_general_board_query("all_except_tournament", "latest")[3] == ("tournament",)
    assert _normalize_general_board_query("all_except_offtopic", "latest")[3] == ("offtopic",)
    assert _normalize_general_board_query("all_except_tournament_offtopic", "trending")[3] == ("tournament", "offtopic")


def test_category_filter_and_unknown_filter():
    assert _normalize_general_board_query("tournament", "latest") == ("latest", "tournament", "tournament", ())
    assert _normalize_general_board_query("unknown", "latest") == ("latest", "all", None, ())
