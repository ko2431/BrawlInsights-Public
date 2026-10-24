"""チャットのメンションの規則（大会のチャット・掲示板のチャットで共通）。

本文の「@名前」（全角の「＠」も）で成立を決める。候補から入れたか手で打ったかは問わない。
大会のチャットでは、全員へのメンション「@everyone」も同じ規則で読み取る（mentions_everyone）。
画面側の同じ規則（表示の色付け・候補の検索）は static/js/mentions.js。片方だけ変えない。
"""
from __future__ import annotations

MENTION_MAX = 5  # 1メッセージでメンションできる人数
MENTION_MARKS = "@＠"
EVERYONE = "everyone"  # 「@everyone」（大会のチャットだけ。掲示板のチャットにはない）


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


def _mark_positions(lowered: str):
    """「@」「＠」の位置（直前が英数字のもの、メールアドレスなどは除く）。"""
    for i, ch in enumerate(lowered):
        if ch in MENTION_MARKS and not (i > 0 and lowered[i - 1].isascii() and lowered[i - 1].isalnum()):
            yield i


def pick_mentions(body: str, candidates: list[dict], self_id: int | None = None) -> list[dict]:
    """本文の「@名前」（全角の「＠」も。英字の大文字・小文字は区別しない）から、メンションした人を本文の順に選ぶ。
    同じ位置で複数の名前に当たるとき（「@テスト2」に「テスト」と「テスト2」）はいちばん長い名前。
    直前が英数字の「@」（メールアドレスなど）は数えない。自分は除き、最大5人。"""
    lowered = body.lower()
    names = sorted(((c["name"].lower(), c) for c in candidates if c.get("name")), key=lambda x: -len(x[0]))
    picked: list[dict] = []
    for i in _mark_positions(lowered):
        hit = next((c for name, c in names if lowered.startswith(name, i + 1)), None)
        if hit and hit["id"] != self_id and all(p["id"] != hit["id"] for p in picked):
            picked.append({"id": hit["id"], "name": hit["name"]})
    return picked[:MENTION_MAX]


def mentions_everyone(body: str | None, candidates: list[dict] = ()) -> bool:
    """本文に「@everyone」（全角の「＠」も。英字の大文字・小文字は区別しない）があるか。
    直前が英数字の「@」と、すぐ後に英数字・「_」が続くもの（「@everyones」）は数えない。
    同じ位置に「everyone」より長い名前の人がいれば（「@everyoneさん」と「everyoneさん」）、その人へのメンションにする。"""
    if not has_mention_mark(body):
        return False
    lowered = body.lower()
    longer = [c["name"].lower() for c in candidates if c.get("name") and len(c["name"]) > len(EVERYONE)]
    for i in _mark_positions(lowered):
        end = i + 1 + len(EVERYONE)
        if not lowered.startswith(EVERYONE, i + 1):
            continue
        if end < len(lowered) and lowered[end].isascii() and (lowered[end].isalnum() or lowered[end] == "_"):
            continue
        if any(lowered.startswith(name, i + 1) for name in longer):
            continue
        return True
    return False
