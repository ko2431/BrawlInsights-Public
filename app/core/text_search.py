"""掲示板の投稿検索で使う文字列正規化。

Python・SQL・JS（general_board.js）で同じ規則を使うこと:
    1. NFKC（全角英数→半角、半角カナ→全角カナ）
    2. ASCII の A-Z → a-z（DB の照合順序に依存しないよう lower() は使わない）
    3. カタカナ U+30A1-U+30F6 → ひらがな U+3041-U+3096
"""
import unicodedata
from dataclasses import dataclass

_ASCII_UPPER = "".join(chr(c) for c in range(ord("A"), ord("Z") + 1))
_ASCII_LOWER = _ASCII_UPPER.lower()
_KATAKANA = "".join(chr(c) for c in range(0x30A1, 0x30F6 + 1))
_HIRAGANA = "".join(chr(c - 0x60) for c in range(0x30A1, 0x30F6 + 1))

_TRANSLATE_FROM = _ASCII_UPPER + _KATAKANA
_TRANSLATE_TO = _ASCII_LOWER + _HIRAGANA
_TRANSLATE_TABLE = str.maketrans(_TRANSLATE_FROM, _TRANSLATE_TO)
_CONTROL_CHARS_TABLE = {c: " " for c in (*range(0x00, 0x20), 0x7F)}

# 検索クエリの上限
GENERAL_BOARD_SEARCH_MAX_LENGTH = 50
GENERAL_BOARD_SEARCH_MAX_TERMS = 5

# pg_bigm の LIKE ESCAPE（E'\\' はバックスラッシュ1文字）
LIKE_ESCAPE_SQL = "E'\\\\'"


def normalize_search_text(text: str) -> str:
    """検索用に文字列を正規化する（SQL の search_normalize_sql と同じ規則）。"""
    return unicodedata.normalize("NFKC", text).translate(_TRANSLATE_TABLE)


def search_normalize_sql(column: str) -> str:
    """検索用正規化の SQL 式。式インデックスと完全に同じ文字列で使うこと。"""
    return f"translate(normalize({column}, NFKC), '{_TRANSLATE_FROM}', '{_TRANSLATE_TO}')"


def escape_like_literal(text: str) -> str:
    """ユーザー入力の % / _ / \\ を LIKE のリテラルにする。"""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass(frozen=True)
class GeneralBoardSearch:
    """なんでも掲示板の検索条件。

    mode:
        "text": 投稿本文の検索（terms を AND）
        "user": 投稿者名の完全一致（user_name）
    """

    mode: str
    raw: str
    terms: tuple[str, ...] = ()
    normalized_terms: tuple[str, ...] = ()
    user_name: str = ""
    normalized_user_name: str = ""

    @property
    def cache_key(self) -> str:
        if self.mode == "user":
            return f"user:{self.normalized_user_name}"
        return "text:" + "\x1f".join(self.normalized_terms)


def parse_general_board_search(q: str | None) -> GeneralBoardSearch | None:
    """検索クエリを解釈する。検索条件が空の場合は None。"""
    if not q:
        return None
    # NUL(0x00)等の制御文字はPostgreSQLのtextで扱えず500になるため、空白に置き換える
    q = q.translate(_CONTROL_CHARS_TABLE)
    raw = q.strip()[:GENERAL_BOARD_SEARCH_MAX_LENGTH].strip()
    if not raw:
        return None

    if raw[0] in ("@", "＠"):
        user_name = raw[1:].strip()
        if not user_name:
            return None
        return GeneralBoardSearch(
            mode="user",
            raw=raw,
            user_name=user_name,
            normalized_user_name=normalize_search_text(user_name),
        )

    terms: list[str] = []
    normalized_terms: list[str] = []
    for term in raw.replace("　", " ").split():
        normalized = normalize_search_text(term)
        if not normalized.strip() or normalized in normalized_terms:
            continue
        terms.append(term)
        normalized_terms.append(normalized)
        if len(terms) >= GENERAL_BOARD_SEARCH_MAX_TERMS:
            break
    if not terms:
        return None
    return GeneralBoardSearch(
        mode="text",
        raw=raw,
        terms=tuple(terms),
        normalized_terms=tuple(normalized_terms),
    )
