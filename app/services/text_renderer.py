"""
テキスト描画ヘルパー関数群。
フォント管理・スマート描画・高レベル描画関数を全て提供する自己完結モジュール。

公開関数:
    get_font             ... フォントオブジェクトのロード
    draw_text            ... 1行テキスト描画 (英数字・複合を自動判別)
    draw_text_multiline  ... 複数行テキスト描画 (自動折り返し・省略対応)
    draw_text_ellipsis   ... 省略テキスト描画 (1行固定・幅オーバー時に … 置換)

型:
    TextStyle ... テキストスタイル設定 (dataclass)
"""
from __future__ import annotations

import unicodedata
import re
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.core.logger import logger
from app.services.renderer_utils import (
    Anchor,
    Color,
    apply_anchor as _apply_anchor_util,
    parse_color,
)

# ---------------------------------------------------------------------------
# フォント設定
# ---------------------------------------------------------------------------

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
FONT_DIR = STATIC_DIR / "fonts"

#: フォント設定辞書
#: key: エイリアス名 / file: ファイル名 / is_variable: バリアブルか否か / default_weight: デフォルト太さ
FONT_CONFIG: dict[str, dict] = {
    "nougat":       {"file": "Nougat-ExtraBlack.ttf",                   "is_variable": False},
    "lilita":       {"file": "LilitaOne-Regular.ttf",                   "is_variable": False},
    "inter":        {"file": "Inter-VariableFont_opsz,wght.ttf",         "is_variable": True, "default_weight": 400},
    "inter_italic": {"file": "Inter-Italic-VariableFont_opsz,wght.ttf",  "is_variable": True, "default_weight": 400},
    "jp":           {"file": "NotoSansJP-VariableFont_wght.ttf",         "is_variable": True, "default_weight": 400},
    "kr":           {"file": "NotoSansKR-VariableFont_wght.ttf",         "is_variable": True, "default_weight": 400},
    "emoji":        {"file": "NotoColorEmoji-Regular.ttf",               "is_variable": False},
    "emoji_outline":{"file": "NotoEmoji-VariableFont_wght.ttf",          "is_variable": True, "default_weight": 400},
}

#: フォントごとの見かけ調整 (scale: サイズ倍率, y_offset: 垂直位置調整)
FALLBACK_METRICS: dict[str, dict[str, float]] = {
    "nougat":       {"scale": 1.0, "y_offset":  0.0},
    "lilita":       {"scale": 1.0, "y_offset":  0.0},
    "inter":        {"scale": 1.0, "y_offset":  0.0},
    "inter_italic": {"scale": 1.0, "y_offset":  0.0},
    "jp":           {"scale": 0.9, "y_offset": -0.1},
    "kr":           {"scale": 0.9, "y_offset": -0.1},
    "emoji":        {"scale": 0.95, "y_offset": 0.05},
    "emoji_outline":{"scale": 0.95, "y_offset": 0.05},
}


@lru_cache(maxsize=256)
def _get_font_cached(font_key: str, size: int, weight: int | None = None) -> ImageFont.FreeTypeFont:
    """フォントをロードして返す。

    Args:
        font_key: FONT_CONFIG のキー ("nougat", "inter" など)
        size:     フォントサイズ (px)
        weight:   バリアブルフォントの太さ (例: 400, 700)。非バリアブルでは無効。

    Returns:
        FreeTypeFont オブジェクト。失敗時はデフォルトフォントを返す。
    """
    config = FONT_CONFIG.get(font_key)
    if not config:
        logger.warning(f"指定されたフォントキー '{font_key}' が見つかりません。デフォルトを使用します。")
        return ImageFont.load_default()

    font_path = FONT_DIR / config["file"]
    if not font_path.exists():
        logger.error(f"フォントファイルが見つかりません: {font_path}")
        return ImageFont.load_default()

    try:
        font = ImageFont.truetype(str(font_path), size)
        if config.get("is_variable"):
            try:
                axes = font.get_variation_axes()
                values = [axis.get("default", 0) for axis in axes]
                target_weight = weight if weight is not None else config.get("default_weight", 400)
                for i, axis in enumerate(axes):
                    axis_name = axis.get("name")
                    if axis_name in (b"Weight", "Weight"):
                        values[i] = target_weight
                        break
                font.set_variation_by_axes(values)
            except Exception as ve:
                logger.warning(f"バリアブルフォントの太さ設定に失敗しました ({font_key}): {ve}")
        return font
    except Exception as e:
        logger.error(f"フォントの読み込みに失敗しました ({font_key}): {e}")
        return ImageFont.load_default()


def get_font(font_key: str, size: int, weight: int | None = None) -> ImageFont.FreeTypeFont:
    """フォントをロードして返す（内部で LRU キャッシュを利用）。"""
    return _get_font_cached(font_key, size, weight)


# ---------------------------------------------------------------------------
# TextStyle dataclass
# ---------------------------------------------------------------------------

# Color / Anchor は renderer_utils から再エクスポート (後方互換)
# Color = str | tuple[int, int, int] | tuple[int, int, int, int]
# Anchor = tuple[str, str]

ELLIPSIS_CHAR = "…"
_ASCII_THRESHOLD = 0x0100
ColorCodeMode = Literal["light", "dark"]

_COLOR_TAG_PATTERN = re.compile(r"</c>|<c([2-9])>")
_COLOR_CODE_PALETTE: dict[str, dict[str, Color]] = {
    "light": {
        "2": (242, 39, 39),
        "3": (18, 181, 18),
        "4": (60, 60, 253),
        "5": (4, 177, 186),
        "6": (219, 81, 127),
        "7": (194, 194, 22),
        "8": (179, 15, 179),
        "9": (218, 95, 50),
    },
    "dark": {
        "2": "#ff6666",
        "3": "#5af05a",
        "4": "#80aaff",
        "5": "#4eeee3",
        "6": "#ff85ab",
        "7": "#fef04e",
        "8": "#e080ff",
        "9": "#ff9966",
    },
}


@dataclass
class TextStyle:
    """テキストの描画スタイル設定。

    Attributes:
        font_key:      FONT_CONFIG のキー ("nougat", "inter" など)
        size:          フォントサイズ (px)
        weight:        バリアブルフォント太さ (例: 400, 700)。非バリアブルでは無効。
        fill:          テキスト色 (RGBA タプル, Hex 文字列, CSS 色名 すべて可)
        stroke_width:  枠線の太さ (0: なし)。外側のみ適用 (イラレ方式)。
        stroke_fill:   枠線の色
        shadow_offset: ドロップシャドウの (dx, dy)。(0, 0) ならシャドウなし。
        shadow_fill:   シャドウの色。アルファ値で強さを調整する。
        shadow_blur:   シャドウのぼかし量 (px)。0 はシャープな影。
    """
    font_key: str
    size: int
    weight: int | None = None
    fill: Color = (255, 255, 255, 255)
    stroke_width: int = 0
    stroke_fill: Color = (0, 0, 0, 255)
    shadow_offset: tuple[int, int] = field(default_factory=lambda: (0, 0))
    shadow_fill: Color = (0, 0, 0, 180)
    shadow_blur: int = 0

    @property
    def has_stroke(self) -> bool:
        return self.stroke_width > 0

    @property
    def has_shadow(self) -> bool:
        return self.shadow_offset != (0, 0)


# ---------------------------------------------------------------------------
# スマート描画 (内部用・低レベル)
# ---------------------------------------------------------------------------

def _is_skippable_char(char: str) -> bool:
    """描画すると豆腐になりやすい不可視文字（異体字選択子など）か判定する。

    改行は Pillow の textlength() が ValueError を送出するため、1行描画経路では捨てる。
    """
    if char in "\n\r":
        return True
    code = ord(char)
    if (0xFE00 <= code <= 0xFE0F) or (0xE0100 <= code <= 0xE01EF):
        return True
    return False


def _collapse_newlines(text: str) -> str:
    """1行描画用に改行を空白へ畳む。"""
    if not text or ("\n" not in text and "\r" not in text):
        return text
    return " ".join(line for line in text.splitlines() if line)


def _get_font_for_char(
    char: str, primary_key: str, size: int, weight: int | None = None
) -> tuple[ImageFont.FreeTypeFont, str]:
    """1文字に対して最適なフォントとそのキーを返す。"""
    code = ord(char)

    # 英数字/記号 (Latin-1)
    if code < 0x0100:
        return get_font(primary_key, size, weight), primary_key

    # 日本語 (平仮名・片仮名・漢字・全角記号)
    if (0x3000 <= code <= 0x30FF) or (0x4E00 <= code <= 0x9FFF) or (0xFF00 <= code <= 0xFFEF):
        return get_font("jp", size, weight), "jp"

    # 韓国語 (ハングル)
    if (0xAC00 <= code <= 0xD7AF) or (0x1100 <= code <= 0x11FF) or (0x3130 <= code <= 0x318F):
        return get_font("kr", size, weight), "kr"

    # 絵文字・記号 (広範な判定: 国旗 RI, Dingbats, Arrows, Symbols etc.)
    if (0x1F000 <= code <= 0x1FFFF) or (0x2000 <= code <= 0x2BFF) or (0x2600 <= code <= 0x27BF):
        return get_font("emoji_outline", size, weight), "emoji_outline"

    # Unicode 名前ベースの判定
    try:
        name = unicodedata.name(char)
        if any(x in name for x in ("EMOJI", "SYMBOL", "SIGN", "HEART", "STAR", "MARK", "DASH", "ARROW")):
            return get_font("emoji_outline", size, weight), "emoji_outline"
    except (ValueError, KeyError):
        if code > 0x20:
            return get_font("emoji_outline", size, weight), "emoji_outline"

    return get_font("jp", size, weight), "jp"


def _draw_smart_text_raw(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    primary_font_key: str,
    size: int,
    fill: Color = (255, 255, 255, 255),
    weight: int | None = None,
) -> tuple[int, int]:
    """複数フォントを横並びに描画する内部関数（チャンク描画方式）。

    Returns:
        (total_width, total_height)
    """
    if not text:
        return (0, 0)

    # チャンク分け: 同じフォントを使う文字をまとめる
    chunks: list[tuple[str, str]] = []
    current_key: str | None = None
    current_chunk = ""

    for char in text:
        if _is_skippable_char(char):
            continue
        _, key = _get_font_for_char(char, primary_font_key, size, weight)
        if current_key is None:
            current_key = key
            current_chunk = char
        elif current_key == key:
            current_chunk += char
        else:
            chunks.append((current_key, current_chunk))
            current_key = key
            current_chunk = char

    if current_key:
        chunks.append((current_key, current_chunk))

    x, y = xy
    current_x = x
    max_h = 0

    for key, chunk_text in chunks:
        metrics = FALLBACK_METRICS.get(key, {"scale": 1.0, "y_offset": 0.0})
        scaled_size = int(size * metrics["scale"])
        font = get_font(key, scaled_size, weight)
        char_y = y + int(size * metrics["y_offset"])
        draw.text((current_x, char_y), chunk_text, font=font, fill=fill, anchor="la")
        chunk_w = draw.textlength(chunk_text, font=font)
        current_x += chunk_w
        max_h = max(max_h, size)

    return (int(current_x - x), max_h)


# ---------------------------------------------------------------------------
# 内部ユーティリティ (高レベル関数から使用)
# ---------------------------------------------------------------------------

def _is_smart_text(text: str) -> bool:
    """ASCII の範囲を超える文字が含まれる場合 True を返す。"""
    return any(ord(c) >= _ASCII_THRESHOLD for c in text)


def _measure_text(text: str, style: TextStyle) -> tuple[int, int]:
    """テキストの描画サイズ (width, height) を返す。"""
    return _measure_text_cached(text, style.font_key, style.size, style.weight)


def _normalize_color_code_mode(color_code_mode: str) -> ColorCodeMode:
    """カラーコード描画モードを正規化する。"""
    return "dark" if str(color_code_mode).lower() == "dark" else "light"


def _parse_color_code_chars(text: str, enable_color_codes: bool) -> list[tuple[str, str | None]]:
    """`<c2>`〜`<c9>` と `</c>` を解釈して、文字ごとの色コード情報を返す。"""
    if not enable_color_codes:
        return [(char, None) for char in text]

    chars: list[tuple[str, str | None]] = []
    current_code: str | None = None
    cursor = 0

    for match in _COLOR_TAG_PATTERN.finditer(text):
        segment = text[cursor:match.start()]
        if segment:
            chars.extend((char, current_code) for char in segment)

        if match.group(0) == "</c>":
            current_code = None
        else:
            current_code = match.group(1)

        cursor = match.end()

    tail = text[cursor:]
    if tail:
        chars.extend((char, current_code) for char in tail)

    return chars


def _chars_to_plain_text(chars: list[tuple[str, str | None]]) -> str:
    """色コード情報を除いた可視文字列に変換する。"""
    return "".join(char for char, _ in chars)


def _chars_to_segments(chars: list[tuple[str, str | None]]) -> list[tuple[str, str | None]]:
    """同じ色コードが連続する文字列にまとめる。"""
    if not chars:
        return []

    segments: list[tuple[str, str | None]] = []
    current_code = chars[0][1]
    buffer: list[str] = []

    for char, code in chars:
        if code == current_code:
            buffer.append(char)
            continue

        segments.append(("".join(buffer), current_code))
        current_code = code
        buffer = [char]

    if buffer:
        segments.append(("".join(buffer), current_code))

    return segments


def _resolve_color_code_fill(color_code: str | None, color_code_mode: ColorCodeMode) -> Color | None:
    """色コード番号から描画色を返す。未対応コードは None。"""
    if not color_code:
        return None
    return _COLOR_CODE_PALETTE[color_code_mode].get(color_code)


def _draw_text_chars(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    chars: list[tuple[str, str | None]],
    style: TextStyle,
    color_code_mode: ColorCodeMode,
) -> None:
    """色コードを考慮して文字列を描画する。"""
    current_x, current_y = xy

    for segment_text, color_code in _chars_to_segments(chars):
        if not segment_text:
            continue

        fill_override = _resolve_color_code_fill(color_code, color_code_mode)
        segment_style = replace(style, fill=fill_override) if fill_override is not None else style

        _draw_shadow(image, (current_x, current_y), segment_text, segment_style)
        _draw_raw(draw, (current_x, current_y), segment_text, segment_style)

        segment_w, _ = _measure_text(segment_text, style)
        current_x += segment_w


def _split_chars_by_newline(chars: list[tuple[str, str | None]]) -> list[list[tuple[str, str | None]]]:
    """文字列を改行単位に分割する。"""
    paragraphs: list[list[tuple[str, str | None]]] = [[]]
    for char, code in chars:
        if char == "\n":
            paragraphs.append([])
        else:
            paragraphs[-1].append((char, code))
    return paragraphs


def _wrap_paragraph_chars(
    paragraph_chars: list[tuple[str, str | None]],
    style: TextStyle,
    max_width: int,
) -> list[list[tuple[str, str | None]]]:
    """色コード付き1段落（改行なし）を折り返してライン配列を返す。"""
    if not paragraph_chars:
        return [[]]

    lines: list[list[tuple[str, str | None]]] = []
    current_line: list[tuple[str, str | None]] = []

    for char, code in paragraph_chars:
        test_line = current_line + [(char, code)]
        test_text = _chars_to_plain_text(test_line)
        w, _ = _measure_text_cached(test_text, style.font_key, style.size, style.weight)

        if w <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = [(char, code)]

    if current_line:
        lines.append(current_line)

    return lines if lines else [[]]


def _truncate_line_chars_to_ellipsis(
    line_chars: list[tuple[str, str | None]],
    style: TextStyle,
    max_width: int,
) -> list[tuple[str, str | None]]:
    """色コード付き1行を幅内に収め、末尾を … に置換した行データを返す。"""
    line_text = _chars_to_plain_text(line_chars)
    w, _ = _measure_text(line_text, style)
    if w <= max_width:
        return line_chars

    ellipsis_font = get_font("jp", style.size, style.weight)
    ellipsis_w = int(ellipsis_font.getlength(ELLIPSIS_CHAR))
    available = max(0, max_width - ellipsis_w)

    truncated = line_chars[:]
    while truncated:
        tw, _ = _measure_text(_chars_to_plain_text(truncated), style)
        if tw <= available:
            break
        truncated = truncated[:-1]

    return truncated + [(ELLIPSIS_CHAR, None)]


@lru_cache(maxsize=5000)
def _measure_text_cached(
    text: str,
    font_key: str,
    size: int,
    weight: int | None,
) -> tuple[int, int]:
    """描画サイズ計測をキャッシュする。"""
    font = get_font(font_key, size, weight)
    if not _is_smart_text(text):
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]

    dummy_img = Image.new("RGBA", (1, 1))
    dummy_draw = ImageDraw.Draw(dummy_img)
    w, _ = _draw_smart_text_raw(dummy_draw, (0, 0), text, font_key, size, weight=weight)
    return w, size


def _apply_anchor(
    xy: tuple[int, int], w: int, h: int, anchor: Anchor
) -> tuple[int, int]:
    """アンカーを考慮して左上描画座標を計算する。renderer_utils へ委譲。"""
    return _apply_anchor_util(xy, w, h, anchor)


def _draw_shadow(target: Image.Image, xy: tuple[int, int], text: str, style: TextStyle) -> None:
    """ドロップシャドウを target に描画する。"""
    if not style.has_shadow:
        return
    sx, sy = xy[0] + style.shadow_offset[0], xy[1] + style.shadow_offset[1]
    shadow_layer = Image.new("RGBA", target.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_layer)
    shadow_style = TextStyle(font_key=style.font_key, size=style.size,
                             weight=style.weight, fill=style.shadow_fill)
    _draw_raw(shadow_draw, (sx, sy), text, shadow_style)
    if style.shadow_blur > 0:
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(style.shadow_blur))
    target.alpha_composite(shadow_layer)


def _draw_raw(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, style: TextStyle) -> None:
    """装飾なし（枠線のみ含む）テキストを描画する。"""
    font = get_font(style.font_key, style.size, style.weight)
    if _is_smart_text(text):
        if style.has_stroke:
            # パス1: シャドー色で幅広描画 (外側枠の近似)
            _draw_smart_text_raw(draw, xy, text, style.font_key, style.size,
                                 fill=style.stroke_fill, weight=style.weight)
            # パス2: 本体色で上書き
            _draw_smart_text_raw(draw, xy, text, style.font_key, style.size,
                                 fill=style.fill, weight=style.weight)
        else:
            _draw_smart_text_raw(draw, xy, text, style.font_key, style.size,
                                 fill=style.fill, weight=style.weight)
    else:
        if style.has_stroke:
            # イラレ方式「外側のみ枠線」: 2パス描画
            draw.text(xy, text, font=font, fill=style.stroke_fill,
                      stroke_width=style.stroke_width * 2, stroke_fill=style.stroke_fill)
            draw.text(xy, text, font=font, fill=style.fill)
        else:
            draw.text(xy, text, font=font, fill=style.fill)


# ---------------------------------------------------------------------------
# 公開関数
# ---------------------------------------------------------------------------

def measure_text(text: str, style: TextStyle) -> tuple[int, int]:
    """テキストの描画サイズ (width, height) を返す。実際に描画は行わない。

    Args:
        text:  計測対象のテキスト
        style: TextStyle オブジェクト

    Returns:
        (描画幅, 描画高さ) のタプル
    """
    return _measure_text(str(text), style)


def draw_text(
    image: Image.Image,
    xy: tuple[int, int],
    text: str,
    style: TextStyle,
    anchor: Anchor = ("left", "top"),
    enable_color_codes: bool = False,
    color_code_mode: ColorCodeMode = "light",
) -> tuple[int, int]:
    """1行テキストを描画する。英数字/複合マルチ言語を自動判別する。

    Args:
        image:  描画対象の RGBA 画像 (完全に透明な場合は None も可)
        xy:     アンカー基準点
        text:   描画テキスト
        style:  TextStyle オブジェクト
        anchor: (x方向, y方向) の基準点指定

    Returns:
        (描画幅, 描画高さ) のタプル
    """
    text = _collapse_newlines(str(text))
    color_mode = _normalize_color_code_mode(color_code_mode)
    parsed_chars = _parse_color_code_chars(text, enable_color_codes)
    plain_text = _chars_to_plain_text(parsed_chars)
    w, h = _measure_text(plain_text, style)

    # 完全に透明（alpha=0）かつ装飾（枠線・影）がない場合は、計測のみで終了する
    fill_rgba = parse_color(style.fill)
    if not enable_color_codes and fill_rgba[3] == 0 and not style.has_stroke and not style.has_shadow:
        return w, h

    if image is None:
        return w, h

    ox, oy = _apply_anchor(xy, w, h, anchor)
    draw = ImageDraw.Draw(image)
    if enable_color_codes:
        _draw_text_chars(image, draw, (ox, oy), parsed_chars, style, color_mode)
    else:
        _draw_shadow(image, (ox, oy), plain_text, style)
        _draw_raw(draw, (ox, oy), plain_text, style)
    return w, h


def draw_text_multiline(
    image: Image.Image,
    xy: tuple[int, int],
    width: int,
    text: str,
    style: TextStyle,
    align: str = "left",
    anchor: Anchor = ("left", "top"),
    line_spacing: float = 1.3,
    max_height: int | None = None,
    enable_color_codes: bool = False,
    color_code_mode: ColorCodeMode = "light",
) -> tuple[int, int]:
    """複数行テキストを描画する（自動折り返し・省略対応）。

    Args:
        image:        描画対象の RGBA 画像
        xy:           アンカー基準点
        width:        テキストボックスの横幅 (px)
        text:         描画テキスト
        style:        TextStyle オブジェクト
        align:        行内揃え "left" / "center" / "right"
        anchor:       テキストブロック全体の基準点
        line_spacing: 行間の倍率 (1.0 = ピッタリ詰め, 1.3 = 余裕あり)
        max_height:   テキストブロックの最大高さ (px)。None=制限なし。
                      小さすぎる値でも最低1行は必ず描画される。

    Returns:
        (ブロック幅, ブロック高さ) のタプル
    """
    text = str(text)
    color_mode = _normalize_color_code_mode(color_code_mode)
    line_h = int(style.size * line_spacing)

    if enable_color_codes:
        parsed_chars = _parse_color_code_chars(text, True)
        lines_with_codes: list[list[tuple[str, str | None]]] = []
        for paragraph_chars in _split_chars_by_newline(parsed_chars):
            lines_with_codes.extend(_wrap_paragraph_chars(paragraph_chars, style, width))

        if max_height is not None:
            visible_lines: list[list[tuple[str, str | None]]] = []
            accumulated_h = 0
            for i, line_chars in enumerate(lines_with_codes):
                if i == 0:
                    visible_lines.append(line_chars)
                    accumulated_h += line_h
                else:
                    if accumulated_h + line_h <= max_height:
                        visible_lines.append(line_chars)
                        accumulated_h += line_h
                    else:
                        if i < len(lines_with_codes) and visible_lines:
                            visible_lines[-1] = _truncate_line_chars_to_ellipsis(visible_lines[-1], style, width)
                        break
            lines_with_codes = visible_lines

        block_h = line_h * len(lines_with_codes)
        block_w = width
        bx, by = _apply_anchor(xy, block_w, block_h, anchor)
        draw = ImageDraw.Draw(image)

        for i, line_chars in enumerate(lines_with_codes):
            line_text = _chars_to_plain_text(line_chars)
            lw, _ = _measure_text(line_text, style)
            lx = bx
            if align == "center":
                lx = bx + (block_w - lw) // 2
            elif align == "right":
                lx = bx + block_w - lw
            ly = by + i * line_h
            _draw_text_chars(image, draw, (lx, ly), line_chars, style, color_mode)

        return block_w, block_h

    lines = _wrap_text(text, style, width)

    if max_height is not None:
        visible_lines: list[str] = []
        accumulated_h = 0
        for i, line in enumerate(lines):
            if i == 0:
                visible_lines.append(line)
                accumulated_h += line_h
            else:
                if accumulated_h + line_h <= max_height:
                    visible_lines.append(line)
                    accumulated_h += line_h
                else:
                    if i < len(lines):
                        visible_lines[-1] = _truncate_to_ellipsis(visible_lines[-1], style, width)
                    break
        lines = visible_lines

    block_h = line_h * len(lines)
    block_w = width
    bx, by = _apply_anchor(xy, block_w, block_h, anchor)
    draw = ImageDraw.Draw(image)

    for i, line in enumerate(lines):
        lw, _ = _measure_text(line, style)
        lx = bx
        if align == "center":
            lx = bx + (block_w - lw) // 2
        elif align == "right":
            lx = bx + block_w - lw
        ly = by + i * line_h
        _draw_shadow(image, (lx, ly), line, style)
        _draw_raw(draw, (lx, ly), line, style)

    return block_w, block_h


def draw_text_ellipsis(
    image: Image.Image,
    xy: tuple[int, int],
    max_width: int,
    text: str,
    style: TextStyle,
    anchor: Anchor = ("left", "top"),
    enable_color_codes: bool = False,
    color_code_mode: ColorCodeMode = "light",
) -> tuple[int, int]:
    """省略テキストを描画する（1行固定・幅オーバー時に … 置換）。

    Args:
        image:     描画対象の RGBA 画像
        xy:        アンカー基準点
        max_width: テキストボックスの最大横幅 (px)
        text:      描画テキスト
        style:     TextStyle オブジェクト
        anchor:    基準点指定

    Returns:
        (描画幅, 描画高さ) のタプル
    """
    text = _collapse_newlines(str(text))
    color_mode = _normalize_color_code_mode(color_code_mode)

    if enable_color_codes:
        parsed_chars = _parse_color_code_chars(text, True)
        plain_text = _chars_to_plain_text(parsed_chars)
        w, _ = _measure_text(plain_text, style)
        if w <= max_width:
            return draw_text(
                image,
                xy,
                text,
                style,
                anchor,
                enable_color_codes=True,
                color_code_mode=color_mode,
            )

        truncated_chars = _truncate_line_chars_to_ellipsis(parsed_chars, style, max_width)
        truncated_text = _chars_to_plain_text(truncated_chars)
        total_w, total_h = _measure_text(truncated_text, style)
        ox, oy = _apply_anchor(xy, total_w, total_h, anchor)
        draw = ImageDraw.Draw(image)
        _draw_text_chars(image, draw, (ox, oy), truncated_chars, style, color_mode)
        return total_w, total_h

    w, _ = _measure_text(text, style)
    if w <= max_width:
        return draw_text(image, xy, text, style, anchor)

    # … は JP フォントで確実に表示
    ellipsis_font = get_font("jp", style.size, style.weight)
    ellipsis_w = int(ellipsis_font.getlength(ELLIPSIS_CHAR))
    available = max_width - ellipsis_w

    truncated = text
    while truncated:
        tw, _ = _measure_text(truncated, style)
        if tw <= available:
            break
        truncated = truncated[:-1]

    tw, _ = _measure_text(truncated, style) if truncated else (0, style.size)
    total_w = tw + ellipsis_w
    ox, oy = _apply_anchor(xy, total_w, style.size, anchor)
    draw = ImageDraw.Draw(image)

    if truncated:
        _draw_shadow(image, (ox, oy), truncated, style)
        _draw_raw(draw, (ox, oy), truncated, style)

    draw.text((ox + tw, oy), ELLIPSIS_CHAR, font=ellipsis_font, fill=style.fill, anchor="la")
    return total_w, style.size


# ---------------------------------------------------------------------------
# 内部ユーティリティ (折り返し・省略)
# ---------------------------------------------------------------------------

def _wrap_text(text: str, style: TextStyle, max_width: int) -> list[str]:
    """テキストを max_width に収まるように折り返してラインのリストを返す。"""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        lines.extend(_wrap_paragraph(paragraph, style, max_width))
    return lines if lines else [""]


def _wrap_paragraph(text: str, style: TextStyle, max_width: int) -> list[str]:
    """1段落（改行なし）を折り返してラインのリストを返す。"""
    if not text:
        return [""]

    chars = list(text)
    lines: list[str] = []
    current_line = ""

    for char in chars:
        test_line = current_line + char
        w, _ = _measure_text_cached(test_line, style.font_key, style.size, style.weight)

        if w <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = char

    if current_line:
        lines.append(current_line)
    return lines if lines else [""]


def _truncate_to_ellipsis(text: str, style: TextStyle, max_width: int) -> str:
    """テキストが max_width を超える場合、末尾を … に置換して返す。(内部用)"""
    w, _ = _measure_text(text, style)
    if w <= max_width:
        return text

    ellipsis_font = get_font("jp", style.size)
    ellipsis_w = int(ellipsis_font.getlength(ELLIPSIS_CHAR))
    available = max_width - ellipsis_w

    truncated = text
    while truncated:
        tw, _ = _measure_text(truncated, style)
        if tw <= available:
            break
        truncated = truncated[:-1]

    return truncated + ELLIPSIS_CHAR
