"""チャットのメンションの規則（大会のチャット・掲示板のチャットで共通）。

本文の「@名前」（全角の「＠」も）で成立を決める。候補から入れたか手で打ったかは問わない。
画面側の同じ規則（表示の色付け・候補の検索）は static/js/mentions.js。片方だけ変えない。
"""
from __future__ import annotations

MENTION_MAX = 5  # 1メッセージでメンションできる人数
MENTION_MARKS = "@＠"


def has_mention_mark(body: str | None) -> bool:
    """本文に「@」か「＠」があるか（ないときは、範囲の人を DB で調べない）。"""
    return bool(body) and any(mark in body for mark in MENTION_MARKS)


def name_in_text_sql(text_param: str, name_column: str = "u.name") -> str:
    """本文（text_param）に「@名前」「＠名前」が入っているかを調べる SQL の条件（英字の大文字・小文字は区別しない）。
    候補を本文に入っている人だけに絞るためのもので、最終的な判定は pick_mentions で行う。"""
    return (
        f"(strpos(lower({text_param}), lower('@' || {name_column})) > 0"
        f" OR strpos(lower({text_param}), lower('＠' || {name_column})) > 0)"
    )


def pick_mentions(body: str, candidates: list[dict], self_id: int | None = None) -> list[dict]:
    """本文の「@名前」（全角の「＠」も。英字の大文字・小文字は区別しない）から、メンションした人を本文の順に選ぶ。
    同じ位置で複数の名前に当たるとき（「@テスト2」に「テスト」と「テスト2」）はいちばん長い名前。
    直前が英数字の「@」（メールアドレスなど）は数えない。自分は除き、最大5人。"""
    lowered = body.lower()
    names = sorted(((c["name"].lower(), c) for c in candidates if c.get("name")), key=lambda x: -len(x[0]))
    picked: list[dict] = []
    for i, ch in enumerate(lowered):
        if ch not in MENTION_MARKS or (i > 0 and lowered[i - 1].isascii() and lowered[i - 1].isalnum()):
            continue
        hit = next((c for name, c in names if lowered.startswith(name, i + 1)), None)
        if hit and hit["id"] != self_id and all(p["id"] != hit["id"] for p in picked):
            picked.append({"id": hit["id"], "name": hit["name"]})
    return picked[:MENTION_MAX]
