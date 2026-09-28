from app.core.text_search import (
    escape_like_literal,
    normalize_search_text,
    parse_general_board_search,
    search_normalize_sql,
)


def test_normalize_kana_case_width():
    assert normalize_search_text("ブロスタ") == "ぶろすた"
    assert normalize_search_text("ﾌﾞﾛｽﾀ") == "ぶろすた"
    assert normalize_search_text("ぶろすた") == "ぶろすた"
    assert normalize_search_text("BrawlStars") == "brawlstars"
    assert normalize_search_text("ＢＲＡＷＬ１") == "brawl1"
    assert normalize_search_text("ヴァンパイア") == "ゔぁんぱいあ"
    # 長音やひらがな以外の文字はそのまま
    assert normalize_search_text("ー漢字!") == "ー漢字!"


def test_search_normalize_sql_translate_table():
    sql = search_normalize_sql("comment")
    prefix = "translate(normalize(comment, NFKC), '"
    assert sql.startswith(prefix) and sql.endswith("')")
    from_chars, to_chars = sql[len(prefix):-2].split("', '")
    # translate() は同じ位置の文字同士を置換するため、長さが一致している必要がある
    assert len(from_chars) == len(to_chars)
    assert all(normalize_search_text(a) == b for a, b in zip(from_chars, to_chars))


def test_escape_like_literal():
    assert escape_like_literal("100%_\\") == "100\\%\\_\\\\"


def test_parse_empty():
    assert parse_general_board_search(None) is None
    assert parse_general_board_search("") is None
    assert parse_general_board_search("   　 ") is None
    assert parse_general_board_search("@") is None
    assert parse_general_board_search("＠  ") is None


def test_parse_text_terms():
    search = parse_general_board_search("  ブロスタ　大会 ぶろすた  ")
    assert search is not None
    assert search.mode == "text"
    # 正規化後に重複する語は除かれる
    assert search.terms == ("ブロスタ", "大会")
    assert search.normalized_terms == ("ぶろすた", "大会")


def test_parse_text_terms_limit():
    search = parse_general_board_search("a b c d e f g")
    assert search is not None
    assert search.normalized_terms == ("a", "b", "c", "d", "e")


def test_parse_user_mode():
    search = parse_general_board_search("@Taro Yamada")
    assert search is not None
    assert search.mode == "user"
    assert search.user_name == "Taro Yamada"
    assert search.normalized_user_name == "taro yamada"

    search = parse_general_board_search("＠タロウ")
    assert search is not None
    assert search.mode == "user"
    assert search.normalized_user_name == "たろう"


def test_parse_truncates_long_query():
    search = parse_general_board_search("あ" * 80)
    assert search is not None
    assert search.raw == "あ" * 50


def test_cache_key_distinguishes_modes():
    text = parse_general_board_search("abc")
    user = parse_general_board_search("@abc")
    assert text is not None and user is not None
    assert text.cache_key != user.cache_key
