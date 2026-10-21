#!/usr/bin/env python3
"""リアクションの絵文字ピッカーに渡す絵文字データ（app/static/reaction_emojis/{ja,en}.json）を作る。

元のデータは npm の emoji-picker-element-data（emojibase の CLDR 版）。サーバーが受け付ける絵文字もこのデータで決まる
（app/services/reaction_rules.py）。次のものを除く:
- reaction_rules.NG_EMOJIS（表記の揺れも含めて）
- 国旗（group 9）・肌の色や髪型の部品（group 2）
- 肌の色の違い（skins。肌の色は選べないようにする）
- MAX_VERSION より新しい絵文字（古い端末で □ に見えないように）
検索語には絵文字そのもの（異体字セレクタなしの表記・肌の色違いも）を足す（検索欄に絵文字を入れても探せるように）。
日本語版の検索語には、さらにカタカナの語のひらがな（「イヌ」に「いぬ」）と、英語版の名前・検索語を足す（ひらがな・英語でも探せるように）。

使い方:
  cd "$(mktemp -d)" && npm pack emoji-picker-element-data@1.8.0 && tar xzf emoji-picker-element-data-1.8.0.tgz
  python3 scripts/build_reaction_emoji_data.py <展開した package ディレクトリ>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.reaction_rules import DATA_DIR, NG_EMOJIS, emoji_key

LOCALES = ("ja", "en")
EXCLUDED_GROUPS = {2, 9}
MAX_VERSION = 13.1
# 画面側（app/static/js/reactions.js）の固定の7つと「最近使った」の初期値。データの表記と同じでなければならない
REQUIRED = ["❤️", "👀", "😄", "😲", "🥲", "😭", "😡", "👍️", "🤲", "🥳", "🧐", "😪", "😨"]


def to_hiragana(text: str) -> str:
    return "".join(chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヶ" else ch for ch in text)


def emoji_words(emoji: str, skins: list[str]) -> list[str]:
    """絵文字そのものの検索語。ピッカーは2文字未満（JS の UTF-16 の長さ）の語を使わないので、それより短い表記は入れない。"""
    words = []
    for word in [emoji, emoji_key(emoji), *skins]:
        if len(word.encode("utf-16-le")) // 2 >= 2 and word not in words:
            words.append(word)
    return words


def add_search_words(ja_items: list[dict], en_items: list[dict]) -> None:
    en_by_emoji = {item["emoji"]: item for item in en_items}
    for item in ja_items:
        words = [item["annotation"], *item.get("tags", [])]
        extra = [to_hiragana(w) for w in words]
        en = en_by_emoji.get(item["emoji"])
        if en:
            extra += [en["annotation"], *en.get("tags", [])]
        tags = list(item.get("tags", []))
        for word in extra:
            if word and word not in tags and word != item["annotation"]:
                tags.append(word)
        item["tags"] = tags


def build(source: Path, locale: str) -> list[dict]:
    items = json.loads((source / locale / "cldr" / "data.json").read_text(encoding="utf-8"))
    ng_keys = {emoji_key(e) for e in NG_EMOJIS}
    result = []
    seen: set[str] = set()
    for item in items:
        if item.get("group") in EXCLUDED_GROUPS or item.get("version", 0) > MAX_VERSION:
            continue
        key = emoji_key(item["emoji"])
        if key in ng_keys:
            continue
        if key in seen:
            raise SystemExit(f"表記の揺れをそろえると重なる絵文字があります: {item['emoji']}")
        seen.add(key)
        skins = [skin["emoji"] for skin in item.get("skins", [])]
        item = {k: v for k, v in item.items() if k != "skins"}
        tags = list(item.get("tags", []))
        item["tags"] = tags + [w for w in emoji_words(item["emoji"], skins) if w not in tags]
        result.append(item)
    emojis = {item["emoji"] for item in result}
    missing = [e for e in REQUIRED if e not in emojis]
    if missing:
        raise SystemExit(f"{locale}: 画面側の絵文字がデータの表記と合いません: {missing}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="emoji-picker-element-data の package ディレクトリ")
    args = parser.parse_args()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    results = {locale: build(args.source, locale) for locale in LOCALES}
    if [i["emoji"] for i in results["ja"]] != [i["emoji"] for i in results["en"]]:
        raise SystemExit("ja と en で絵文字の並びが違います")
    add_search_words(results["ja"], results["en"])
    for locale, items in results.items():
        path = DATA_DIR / f"{locale}.json"
        path.write_text(json.dumps(items, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"{path.relative_to(ROOT)}: {len(items)}件 ({path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
