"""お知らせ（アナウンス）の本文の HTML の決まり。

検討メモ notes_private/announcement_plan_2026-10-29.md の 4章（形式・表示のときの処理）と 10章（旧形式からの変換）。
DB・Redis には触れない。

- 保存の前に `sanitize_body` / `sanitize_title` で許可リストの消毒をかける（エディターの決まりと同じ書式だけを残す）。
- 表示のときは `render_html` で端末に合わない「◯◯だけ」の部分を除き、リンク・画像の属性を足し、iOS では文言を置き換える。
- `convert_legacy_body` / `convert_legacy_title` は、旧テーブル（`announcements`）の HTML を新しい形に写すときだけ使う。
"""
from __future__ import annotations

import html as html_lib
import re
from html.parser import HTMLParser

import nh3

PLATFORMS = ("web", "ios", "android")
# 「◯◯だけ」に使える組み合わせ（3つとも＝制限なしなので持たない）。並びは PLATFORMS の順にそろえる
ONLY_VALUES = frozenset({"web", "ios", "android", "web,ios", "web,android", "ios,android"})
COLOR_TAGS = frozenset(f"c{i}" for i in range(10))
SIZE_CLASSES = frozenset({"fs-s", "fs-l", "fs-xl"})
FIGURE_SIZES = frozenset({"full", "half", "auto"})
VOID_TAGS = frozenset({"br", "hr", "img"})
INTERNAL_HOSTS = ("brawlinsights.com", "www.brawlinsights.com")
# 画像はサイト内の静的ファイルと、アップロードした画像（画像の土台ができてから）だけ
IMAGE_SRC_PREFIXES = ("/static/images/", "/media/")
MAX_IMAGE_DIMENSION = 8192

_INLINE_TAGS = frozenset({"b", "strong", "i", "em", "s", "u", "span"}) | COLOR_TAGS
BODY_TAGS = _INLINE_TAGS | frozenset({
    "p", "br", "a", "h3", "ul", "ol", "li", "hr", "div", "details", "summary", "figure", "img",
})
TITLE_TAGS = _INLINE_TAGS

_BODY_ATTRIBUTES = {
    "a": {"href"},
    "img": {"src", "alt", "width", "height"},
}
_BODY_CLASSES = {
    "p": {"ta-center"},
    "h3": {"ta-center"},
    "span": set(SIZE_CLASSES),
    "a": {"bi-btn"},
    "div": {"bi-note", "bi-fold__body"},
    "details": {"bi-fold"},
    "figure": {"bi-figure"},
}
_BODY_ATTRIBUTE_VALUES = {
    "span": {"data-only": set(ONLY_VALUES)},
    "div": {"data-only": set(ONLY_VALUES)},
    "figure": {"data-size": set(FIGURE_SIZES)},
}
_TITLE_CLASSES = {"span": set()}
_TITLE_ATTRIBUTE_VALUES = {"span": {"data-only": set(ONLY_VALUES)}}


def _attribute_filter(tag: str, attr: str, value: str) -> str | None:
    if tag == "img" and attr == "src":
        return value if _is_allowed_image_src(value) else None
    if tag == "img" and attr in ("width", "height"):
        return value if value.isdigit() and 0 < int(value) <= MAX_IMAGE_DIMENSION else None
    if tag == "a" and attr == "href":
        # プロトコル相対の URL（//example.com）は外部へのリンクとして紛らわしいので落とす
        return None if value.startswith("//") else value
    return value


def _is_allowed_image_src(src: str) -> bool:
    return src.startswith(IMAGE_SRC_PREFIXES) and ".." not in src


def sanitize_body(value: str | None) -> str:
    """本文を許可リストで消毒する。エディターの出す形（4.3）以外のタグ・属性・クラスは落ちる。"""
    if not value:
        return ""
    return nh3.clean(
        value,
        tags=set(BODY_TAGS),
        attributes=_BODY_ATTRIBUTES,
        allowed_classes=_BODY_CLASSES,
        tag_attribute_values=_BODY_ATTRIBUTE_VALUES,
        attribute_filter=_attribute_filter,
        url_schemes={"http", "https", "mailto"},
        link_rel=None,
        strip_comments=True,
    ).strip()


def sanitize_title(value: str | None) -> str:
    """タイトルを消毒する。1行の文字の書式（太字・斜体・取り消し線・下線・色・端末別）だけを残す。"""
    if not value:
        return ""
    value = re.sub(r"<br\s*/?>|[\r\n]+", " ", value, flags=re.IGNORECASE)
    return nh3.clean(
        value,
        tags=set(TITLE_TAGS),
        attributes={},
        allowed_classes=_TITLE_CLASSES,
        tag_attribute_values=_TITLE_ATTRIBUTE_VALUES,
        link_rel=None,
        strip_comments=True,
    ).strip()


def normalize_only_value(value: str | None) -> str | None:
    """「◯◯だけ」の値を PLATFORMS の順にそろえる。3つとも・空・不正なら None（制限なし）。"""
    if not value:
        return None
    picked = {part.strip() for part in value.split(",")}
    ordered = ",".join(p for p in PLATFORMS if p in picked)
    return ordered if ordered in ONLY_VALUES else None


def only_matches(only: str | None, platform: str) -> bool:
    """「◯◯だけ」の部分を、その端末に出すか。OS の分からないアプリは iOS・Android のどちらかがあれば出す。"""
    if not only:
        return True
    picked = set(only.split(","))
    if platform == "app":
        return bool(picked & {"ios", "android"})
    if platform not in PLATFORMS:
        platform = "web"
    return platform in picked


# --- iOS の表示用の置き換え -------------------------------------------------------------
# iOSのユーザー向け表示用。DB原本は書き換えず、表示用コピーにだけ適用する。
# 長い語（ギフトカード）を先に畳み、その後にストア名、最後にOS名を置換する。
_IOS_GIFT_CARD_JA_RE = re.compile(r"Google\s*Play\s*ギフトカード", re.IGNORECASE)
_IOS_GIFT_CARD_EN_RE = re.compile(r"Google\s*Play\s*Gift\s*Cards?", re.IGNORECASE)
_IOS_PLAY_STORE_JA_RE = re.compile(
    r"Google\s*Play\s*Store|Google\s*Play|Play\s*ストア|プレイストア|Play\s*Store",
    re.IGNORECASE,
)
_IOS_PLAY_STORE_EN_RE = re.compile(
    r"(?:the\s+)?(?:Google\s*Play\s*Store|Google\s*Play|Play\s*Store)|Play\s*ストア|プレイストア",
    re.IGNORECASE,
)
_IOS_ANDROID_KATAKANA_RE = re.compile(r"アンドロイド")
_IOS_ANDROID_RE = re.compile(r"Android", re.IGNORECASE)


def _replace_ios_android_en(match: re.Match[str]) -> str:
    return "Other OS" if match.group(0)[0].isupper() else "other OS"


def _replace_ios_gift_card_en(match: re.Match[str]) -> str:
    return "gift cards" if match.group(0).lower().rstrip().endswith("s") else "gift card"


def sanitize_text_for_ios_display(text: str | None, *, japanese: bool) -> str | None:
    """iOSアプリ向けに、他モバイルOS・他ストア名を表示用文言から除く。None/空はそのまま。"""
    if not text:
        return text
    if japanese:
        text = _IOS_GIFT_CARD_JA_RE.sub("ギフトカード", text)
        text = _IOS_GIFT_CARD_EN_RE.sub("ギフトカード", text)
        text = _IOS_PLAY_STORE_JA_RE.sub("アプリストア", text)
        text = _IOS_ANDROID_KATAKANA_RE.sub("他OS", text)
        return _IOS_ANDROID_RE.sub("他OS", text)
    text = _IOS_GIFT_CARD_EN_RE.sub(_replace_ios_gift_card_en, text)
    text = _IOS_GIFT_CARD_JA_RE.sub("gift card", text)
    text = _IOS_PLAY_STORE_EN_RE.sub("the app store", text)
    text = _IOS_ANDROID_KATAKANA_RE.sub("other OS", text)
    return _IOS_ANDROID_RE.sub(_replace_ios_android_en, text)


# --- 表示のときの処理（4.6） ------------------------------------------------------------
_LANG_PATH_RE = re.compile(r"^/(ja|en)(?=/|$|\?|#)")
_INTERNAL_ABS_RE = re.compile(r"^https?://(?:www\.)?brawlinsights\.com(?=/|$)", re.IGNORECASE)


def _localize_href(href: str, lang: str) -> tuple[str, bool]:
    """(書き換えた href, 外部のリンクか)。サイト内のリンクの言語の部分を、見ている人の言語に直す。"""
    if href.startswith("/") and not href.startswith("//"):
        return _LANG_PATH_RE.sub(f"/{lang}", href), False
    match = _INTERNAL_ABS_RE.match(href)
    if match:
        rest = href[match.end():] or "/"
        return _LANG_PATH_RE.sub(f"/{lang}", rest), False
    if href.startswith("#"):
        return href, False
    return href, True


def _attrs_to_str(attrs: list[tuple[str, str | None]]) -> str:
    parts = []
    for name, value in attrs:
        if value is None:
            parts.append(f" {name}")
        else:
            parts.append(f' {name}="{html_lib.escape(value, quote=True)}"')
    return "".join(parts)


class _Renderer(HTMLParser):
    """消毒済みの HTML（タグの対応がとれている）を1回なめて、表示用の HTML か文字列を作る。"""

    _BLOCK_TAGS = frozenset({"p", "h3", "li", "div", "details", "summary", "figure", "ul", "ol", "hr", "br"})

    def __init__(self, platform: str, lang: str, *, text_mode: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self.platform = platform
        self.lang = lang
        self.text_mode = text_mode
        self.out: list[str] = []
        self.depth = 0
        self.skip_from: int | None = None
        self.images: list[str] = []

    def _skipping(self) -> bool:
        return self.skip_from is not None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in VOID_TAGS:
            self._void(tag, attrs)
            return
        self.depth += 1
        if self._skipping():
            return
        only = dict(attrs).get("data-only")
        if only and not only_matches(only, self.platform):
            self.skip_from = self.depth
            return
        if self.text_mode:
            if tag in self._BLOCK_TAGS:
                self.out.append("\n")
            return
        kept = [(k, v) for k, v in attrs if k != "data-only"]
        if tag == "a":
            href = dict(kept).get("href")
            if href:
                new_href, external = _localize_href(href, self.lang)
                kept = [(k, new_href if k == "href" else v) for k, v in kept]
                if external:
                    kept += [("target", "_blank"), ("rel", "noopener noreferrer")]
        self.out.append(f"<{tag}{_attrs_to_str(kept)}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in VOID_TAGS:
            self._void(tag, attrs)
        else:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    def _void(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skipping():
            return
        if tag == "img":
            src = dict(attrs).get("src")
            if not src:
                return
            self.images.append(src)
            if self.text_mode:
                return
            attrs = list(attrs) + [("loading", "lazy"), ("decoding", "async")]
        elif self.text_mode:
            self.out.append("\n")
            return
        self.out.append(f"<{tag}{_attrs_to_str(attrs)}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_TAGS:
            return
        if self.skip_from is not None:
            if self.depth == self.skip_from:
                self.skip_from = None
            self.depth -= 1
            return
        self.depth -= 1
        if self.text_mode:
            if tag in self._BLOCK_TAGS:
                self.out.append("\n")
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._skipping() or not data:
            return
        if self.platform == "ios":
            data = sanitize_text_for_ios_display(data, japanese=self.lang == "ja") or ""
        self.out.append(data if self.text_mode else html_lib.escape(data, quote=False))


def render_html(value: str | None, *, platform: str, lang: str) -> str:
    """保存された本文・タイトルを、その端末・言語の表示用の HTML にする。"""
    if not value:
        return ""
    renderer = _Renderer(platform, lang)
    renderer.feed(value)
    renderer.close()
    return "".join(renderer.out)


def html_to_text(value: str | None, *, platform: str = "web", lang: str = "ja", limit: int | None = None) -> str:
    """本文・タイトルを1行の文字にする（OGP の説明・プッシュの文面・管理画面の一覧に使う）。"""
    if not value:
        return ""
    renderer = _Renderer(platform, lang, text_mode=True)
    renderer.feed(value)
    renderer.close()
    text = re.sub(r"\s+", " ", "".join(renderer.out)).strip()
    if limit is not None and len(text) > limit:
        text = text[: max(limit - 1, 0)].rstrip() + "…"
    return text


def first_image_src(value: str | None) -> str | None:
    """本文の最初の画像（どの端末でも出るもの）。個別ページの OGP の画像に使う。"""
    if not value:
        return None
    renderer = _Renderer("web", "ja", text_mode=True)
    renderer.feed(value)
    renderer.close()
    return renderer.images[0] if renderer.images else None


def is_blank_body(value: str | None) -> bool:
    """文字も画像もない本文か（エディターの空の状態は `<p></p>`）。"""
    if not value:
        return True
    return not html_to_text(value) and first_image_src(value) is None


# --- 旧形式からの変換（10章） -------------------------------------------------------------
_LEGACY_KNOWN_TAGS = frozenset({"br", "b", "strong", "i", "em", "s", "del", "strike", "u", "span", "a"}) | COLOR_TAGS
_LEGACY_UNKNOWN_TAG_RE = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9-]*)")
_LEGACY_COLOR_MAP = {
    # web6 で使われていた赤・青（rgb(209, 14, 14)・rgb(50, 50, 237)）
    (209, 14, 14): "c2",
    (50, 50, 237): "c4",
}
_RGB_RE = re.compile(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)")


def _escape_unknown_legacy_tags(value: str) -> str:
    """`<Terms and Conditions>` のように、文字として書かれていてタグとして読まれていたものを文字に戻す。"""
    def repl(match: re.Match[str]) -> str:
        if match.group(2).lower() in _LEGACY_KNOWN_TAGS:
            return match.group(0)
        return "&lt;" + match.group(1) + match.group(2)
    return _LEGACY_UNKNOWN_TAG_RE.sub(repl, value)


def _legacy_style_to_tags(style: str) -> list[tuple[str, list[tuple[str, str]]]]:
    """旧形式の style を、新しい形のタグの並び（外側から）にする。"""
    style = (style or "").lower()
    out: list[tuple[str, list[tuple[str, str]]]] = []
    if re.search(r"font-weight\s*:\s*(bold|[6-9]00)", style):
        out.append(("b", []))
    if re.search(r"text-decoration\s*:[^;]*line-through", style):
        out.append(("s", []))
    color = _RGB_RE.search(style)
    if color:
        tag = _LEGACY_COLOR_MAP.get(tuple(int(x) for x in color.groups()))
        if tag:
            out.append((tag, []))
    size = re.search(r"font-size\s*:\s*([a-z-]+)", style)
    if size:
        cls = {"larger": "fs-l", "large": "fs-l", "x-large": "fs-xl", "xx-large": "fs-xl",
               "smaller": "fs-s", "small": "fs-s"}.get(size.group(1))
        if cls:
            out.append(("span", [("class", cls)]))
    return out


class _LegacyConverter(HTMLParser):
    """`<br>` で区切った1行を1段落（`<p>`）にする。行をまたぐ文字の書式は、行ごとに閉じて開き直す。"""

    def __init__(self, *, single_line: bool) -> None:
        super().__init__(convert_charrefs=True)
        self.single_line = single_line
        # 旧形式のタグ1つにつき、出したタグの並び（外側から）
        self.stack: list[tuple[str, list[tuple[str, list[tuple[str, str]]]]]] = []
        self.lines: list[str] = []
        self.line: list[str] = []
        self.line_has_text = False
        self._open_line()

    def _open_tags(self, entries) -> str:
        return "".join(f"<{tag}{_attrs_to_str(attrs)}>" for _, outs in entries for tag, attrs in outs)

    def _close_tags(self, entries) -> str:
        return "".join(f"</{tag}>" for _, outs in reversed(entries) for tag, _ in reversed(outs))

    def _open_line(self) -> None:
        self.line = [self._open_tags(self.stack)]
        self.line_has_text = False

    def _close_line(self) -> None:
        self.line.append(self._close_tags(self.stack))
        content = "".join(self.line)
        self.lines.append(content if self.line_has_text else "")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k: (v or "") for k, v in attrs}
        if tag == "br":
            if self.single_line:
                self.handle_data(" ")
                return
            self._close_line()
            self._open_line()
            return
        outs: list[tuple[str, list[tuple[str, str]]]]
        if tag in ("b", "strong"):
            outs = [("b", [])]
        elif tag in ("i", "em"):
            outs = [("i", [])]
        elif tag in ("s", "del", "strike"):
            outs = [("s", [])]
        elif tag == "u":
            outs = [("u", [])]
        elif tag in COLOR_TAGS:
            outs = [(tag, [])]
        elif tag == "span":
            outs = _legacy_style_to_tags(values.get("style", ""))
        elif tag == "a" and not self.single_line:
            outs = [t for t in _legacy_style_to_tags(values.get("style", "")) if t[0] == "b"]
            href = values.get("href", "").strip()
            outs.append(("a", [("href", href)] if href else []))
        else:
            outs = []
        self.stack.append((tag, outs))
        self.line.append(self._open_tags([(tag, outs)]))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag != "br":
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        index = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i][0] == tag), None)
        if index is None:
            return
        above = self.stack[index + 1:]
        # 入れ子がずれているときは、上のものを閉じてから対象を閉じ、上のものを開き直す
        self.line.append(self._close_tags(self.stack[index:]))
        self.stack = self.stack[:index] + above
        self.line.append(self._open_tags(above))

    def handle_data(self, data: str) -> None:
        if not data:
            return
        if data.strip():
            self.line_has_text = True
        self.line.append(html_lib.escape(data, quote=False))

    def result(self) -> str:
        self.close()
        self._close_line()
        if self.single_line:
            return _drop_empty_inline(self.lines[0])
        lines = self.lines
        # 最後の <br> の後ろは、ブラウザでは空の行にならない（「本文<br>」は1行）ので、空の段落を足さない
        if len(lines) > 1 and not lines[-1]:
            lines = lines[:-1]
        return "".join(f"<p>{_drop_empty_inline(line)}</p>" for line in lines)


_EMPTY_INLINE_RE = re.compile(r"<(b|i|s|u|span|c\d)(?:\s[^>]*)?></\1>")


def _drop_empty_inline(value: str) -> str:
    """行の終わりで開き直した書式など、中身のないタグを除く（エディターでも残らないため）。"""
    while True:
        new = _EMPTY_INLINE_RE.sub("", value)
        if new == value:
            return value
        value = new


def convert_legacy_body(value: str | None) -> str:
    """旧形式の本文（`<br>` 区切り・style 付きの span・色のタグ）を新しい形にして消毒する。"""
    if not value:
        return ""
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")
    converter = _LegacyConverter(single_line=False)
    converter.feed(_escape_unknown_legacy_tags(value))
    return sanitize_body(converter.result())


def convert_legacy_title(value: str | None) -> str:
    """旧形式のタイトルを新しい形にして消毒する。"""
    if not value:
        return ""
    converter = _LegacyConverter(single_line=True)
    converter.feed(_escape_unknown_legacy_tags(value))
    return sanitize_title(converter.result())
