"""
レンダラー共通ユーティリティ。
image_renderer / shape_renderer / text_renderer が共有するデータクラス・関数群。

公開クラス:
    Color            ... 色の型エイリアス
    Anchor           ... アンカー指定の型エイリアス
    RadiusConfig     ... 4隅角丸設定
    LinearGradient   ... 線形グラデーション
    RadialGradient   ... 放射グラデーション
    Fill             ... 塗り色の型エイリアス (単色 / グラデーション)
    BorderConfig     ... 枠線設定
    DropShadow       ... ドロップシャドウ設定
    ClipRegion       ... 親子クリッピング情報

公開関数:
    apply_anchor           ... アンカーを考慮した左上座標を計算
    parse_color            ... 色値を RGBA タプルに正規化
    create_rounded_rect_mask  ... 角丸マスク画像を生成
    apply_opacity          ... アルファチャンネルに係数を乗算
    apply_drop_shadow      ... ドロップシャドウをベース画像に合成
    apply_clip_region      ... クリッピングマスクを適用
    render_fill            ... Fill（単色 / グラデーション）をレイヤー画像として生成
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw, ImageFilter

# ---------------------------------------------------------------------------
# 基本型エイリアス
# ---------------------------------------------------------------------------

#: 色の型エイリアス。RGBA タプル / RGB タプル / Hex 文字列 / CSS 色名 すべて可。
Color = str | tuple[int, int, int] | tuple[int, int, int, int]

#: アンカー指定: (x: "left"/"center"/"right", y: "top"/"middle"/"bottom")
Anchor = tuple[str, str]


# ---------------------------------------------------------------------------
# RadiusConfig — 4隅角丸
# ---------------------------------------------------------------------------

@dataclass
class RadiusConfig:
    """CSS の border-radius と同じ概念。4隅を個別指定できる。

    Attributes:
        tl: top-left の半径 (px)
        tr: top-right の半径 (px)
        br: bottom-right の半径 (px)
        bl: bottom-left の半径 (px)
    """
    tl: int = 0
    tr: int = 0
    br: int = 0
    bl: int = 0

    @classmethod
    def uniform(cls, r: int) -> "RadiusConfig":
        """4隅均一の RadiusConfig を返す。"""
        return cls(tl=r, tr=r, br=r, bl=r)

    @classmethod
    def from_value(cls, v: int | "RadiusConfig") -> "RadiusConfig":
        """int または RadiusConfig を受け取り、RadiusConfig を返す。"""
        if isinstance(v, int):
            return cls.uniform(v)
        return v

    @property
    def is_zero(self) -> bool:
        return self.tl == 0 and self.tr == 0 and self.br == 0 and self.bl == 0


# ---------------------------------------------------------------------------
# グラデーション型
# ---------------------------------------------------------------------------

@dataclass
class LinearGradient:
    """線形グラデーション。

    Attributes:
        colors: カラーストップの色リスト (最低2色)
        stops:  各色の位置 0.0〜1.0。None で等間隔。
        angle:  グラデーション角度 (度)。
                0 = 上→下, 90 = 左→右, 180 = 下→上, 270 = 右→左。
    """
    colors: list[Color]
    stops: list[float] | None = None
    angle: float = 0.0


@dataclass
class RadialGradient:
    """放射グラデーション。

    Attributes:
        colors: カラーストップの色リスト (最低2色)
        stops:  各色の位置 0.0〜1.0。None で等間隔。
        center: グラデーション中心座標 (相対値 0.0〜1.0)。
        radius: 半径 (相対値 0.0〜1.0)。
    """
    colors: list[Color]
    stops: list[float] | None = None
    center: tuple[float, float] = (0.5, 0.5)
    radius: float = 0.5


#: 塗り色の型エイリアス。単色 / グラデーション / None (透明) のいずれか。
Fill = Color | LinearGradient | RadialGradient | None


# ---------------------------------------------------------------------------
# BorderConfig — 枠線
# ---------------------------------------------------------------------------

@dataclass
class BorderConfig:
    """枠線設定。

    Attributes:
        color:  枠線の色 / グラデーション
        width:  枠線の太さ (px)。図形の内側に描画される。
        top:    上辺を描画するか
        right:  右辺を描画するか
        bottom: 下辺を描画するか
        left:   左辺を描画するか
    """
    color: Fill = (255, 255, 255, 255)
    width: int = 1
    top: bool = True
    right: bool = True
    bottom: bool = True
    left: bool = True


# ---------------------------------------------------------------------------
# DropShadow — ドロップシャドウ
# ---------------------------------------------------------------------------

@dataclass
class DropShadow:
    """ドロップシャドウ設定。

    Attributes:
        offset: シャドウのオフセット (dx, dy)
        color:  シャドウの色
        blur:   ぼかし量 (px)。0 = シャープ。
    """
    offset: tuple[int, int] = (4, 4)
    color: Color = (0, 0, 0, 120)
    blur: int = 8


# ---------------------------------------------------------------------------
# ClipRegion — 親子クリッピング情報
# ---------------------------------------------------------------------------

@dataclass
class ClipRegion:
    """親要素のクリッピング情報。子要素の描画時に渡すことで、
    親の角丸や枠線に沿ってはみ出た部分が自動的にくり抜かれる。

    Attributes:
        x:            親要素の left-top 座標 x (ベース画像上の絶対座標)
        y:            親要素の left-top 座標 y (ベース画像上の絶対座標)
        width:        親要素の幅 (px)
        height:       親要素の高さ (px)
        radius:       親要素の角丸 (int または RadiusConfig)
        border_width: 親要素の枠線幅 (px)。この分だけ内側にクリップされる。
    """
    x: int
    y: int
    width: int
    height: int
    radius: int | RadiusConfig = 0
    border_width: int = 0


# ---------------------------------------------------------------------------
# ユーティリティ関数
# ---------------------------------------------------------------------------

def apply_anchor(
    xy: tuple[int, int],
    w: int,
    h: int,
    anchor: Anchor,
) -> tuple[int, int]:
    """アンカーを考慮して左上描画座標を計算する。

    Args:
        xy:     アンカー基準点座標
        w:      描画物の幅 (px)
        h:      描画物の高さ (px)
        anchor: (x方向, y方向) の基準点指定

    Returns:
        左上原点の (x, y) 座標
    """
    x, y = xy
    ax, ay = anchor
    match ax:
        case "center": x -= w // 2
        case "right":  x -= w
    match ay:
        case "middle": y -= h // 2
        case "bottom": y -= h
    return x, y


def parse_color(color: Color) -> tuple[int, int, int, int]:
    """色値を RGBA タプルに正規化する。

    Args:
        color: str (Hex / CSS 色名) / (R, G, B) / (R, G, B, A) タプル

    Returns:
        (R, G, B, A) タプル
    """
    if isinstance(color, tuple):
        if len(color) == 3:
            return (color[0], color[1], color[2], 255)
        return tuple(color[:4])  # type: ignore[return-value]
    # 文字列: Pillow に任せて変換
    tmp = Image.new("RGBA", (1, 1), color)  # type: ignore[arg-type]
    return tmp.getpixel((0, 0))  # type: ignore[return-value]


def create_smooth_mask(width: int, height: int, draw_func) -> Image.Image:
    """汎用的なスーパーサンプリング・マスク生成。
    
    draw_func(draw, scale) を受け取り、4倍サイズで描画した後に縮小する。
    """
    scale = 4
    mw, mh = width * scale, height * scale
    mask = Image.new("L", (mw, mh), 0)
    draw = ImageDraw.Draw(mask)
    draw_func(draw, scale)
    return mask.resize((width, height), resample=Image.LANCZOS)


def create_smooth_ellipse_mask(width: int, height: int) -> Image.Image:
    """指定サイズの滑らかな楕円マスクを生成する。"""
    def draw_ellipse_func(draw, scale):
        draw.ellipse((0, 0, width * scale - 1, height * scale - 1), fill=255)
    return create_smooth_mask(width, height, draw_ellipse_func)


def create_rounded_rect_mask(
    width: int,
    height: int,
    radius: int | RadiusConfig,
) -> Image.Image:
    """指定サイズ・角丸の白黒マスク画像 (L モード) を生成する (アンチエイリアス対応)。

    内部的に 4 倍のサイズで描画してから縮小（スーパーサンプリング）することで
    滑らかな境界を実現している。

    Args:
        width:  マスクの幅 (px)
        height: マスクの高さ (px)
        radius: 角丸半径。int で4隅均一、RadiusConfig で個別指定。

    Returns:
        L モードのマスク画像
    """
    # 早期リターン
    if width <= 0 or height <= 0:
        return Image.new("L", (max(1, width), max(1, height)), 0)

    r = RadiusConfig.from_value(radius)

    # CSS の仕様に基づき、半径が重なる場合に縮小調整する (オーバーラップ防止)
    # https://www.w3.org/TR/css-backgrounds-3/#corner-overlap
    f = 1.0
    if r.tl + r.tr > width: f = min(f, width / (r.tl + r.tr))
    if r.bl + r.br > width: f = min(f, width / (r.bl + r.br))
    if r.tl + r.bl > height: f = min(f, height / (r.tl + r.bl))
    if r.tr + r.br > height: f = min(f, height / (r.tr + r.br))
    
    if f < 1.0:
        r = RadiusConfig(
            tl=int(r.tl * f), tr=int(r.tr * f),
            br=int(r.br * f), bl=int(r.bl * f)
        )

    scale = 4
    mw, mh = width * scale, height * scale
    mask = Image.new("L", (mw, mh), 0)
    draw = ImageDraw.Draw(mask)
    
    # スケールに合わせ半径を調整
    rt = RadiusConfig(
        tl=r.tl * scale, tr=r.tr * scale, br=r.br * scale, bl=r.bl * scale
    )

    if rt.is_zero:
        draw.rectangle((0, 0, mw - 1, mh - 1), fill=255)
    else:
        # 中央の十字領域 (座標が正しく重なっている場合のみ描画)
        x0, x1 = rt.tl, mw - rt.tr - 1
        if x1 >= x0:
            draw.rectangle((x0, 0, x1, mh - 1), fill=255)
        
        y0, y1 = rt.tl, mh - rt.bl - 1
        if y1 >= y0:
            draw.rectangle((0, y0, mw - 1, y1), fill=255)

        # 4隅の弧
        if rt.tl > 0:
            draw.ellipse((0, 0, rt.tl * 2 - 1, rt.tl * 2 - 1), fill=255)
        if rt.tr > 0:
            draw.ellipse((mw - rt.tr * 2, 0, mw - 1, rt.tr * 2 - 1), fill=255)
        if rt.br > 0:
            draw.ellipse((mw - rt.br * 2, mh - rt.br * 2, mw - 1, mh - 1), fill=255)
        if rt.bl > 0:
            draw.ellipse((0, mh - rt.bl * 2, rt.bl * 2 - 1, mh - 1), fill=255)

    return mask.resize((width, height), resample=Image.LANCZOS)


def apply_opacity(img: Image.Image, opacity: float) -> Image.Image:
    """画像のアルファチャンネルに不透明度係数を乗算する。

    Args:
        img:     RGBA 画像
        opacity: 0.0 (完全透明) 〜 1.0 (完全不透明)

    Returns:
        RGBA 画像 (in-place 変更ではなく新しい画像)
    """
    if opacity >= 1.0:
        return img
    r, g, b, a = img.split()
    a = a.point(lambda v: int(v * max(0.0, min(1.0, opacity))))
    return Image.merge("RGBA", (r, g, b, a))


def apply_drop_shadow(
    base: Image.Image,
    layer: Image.Image,
    pos: tuple[int, int],
    shadow: DropShadow,
) -> None:
    """ドロップシャドウをベース画像に alpha_composite で合成する (in-place)。

    Args:
        base:   合成先ベース画像 (RGBA)
        layer:  シャドウを投げかけるソース画像 (RGBA)
        pos:    layer をベース画像上に貼り付けるときの左上座標
        shadow: DropShadow 設定
    """
    sc = parse_color(shadow.color)
    # シャドウ用レイヤー: ソース画像のアルファ形状を shadow.color で塗りつぶす
    shadow_layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    shape_alpha = layer.split()[3]  # アルファチャンネルのみ
    colored = Image.new("RGBA", layer.size, sc)
    colored.putalpha(shape_alpha)
    sx = pos[0] + shadow.offset[0]
    sy = pos[1] + shadow.offset[1]
    shadow_layer.paste(colored, (sx, sy))
    if shadow.blur > 0:
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(shadow.blur))
    base.alpha_composite(shadow_layer)


def apply_clip_region(
    layer: Image.Image,
    layer_pos: tuple[int, int],
    clip: ClipRegion,
) -> Image.Image:
    """レイヤー画像に ClipRegion のクリッピングマスクを適用する。

    親要素の角丸・枠線に沿ってはみ出た部分のアルファを 0 にする。
    layer 自体を変更せず、マスク適用済みの新しい画像を返す。

    Args:
        layer:     クリッピングを適用するレイヤー (RGBA)
        layer_pos: layer をベース画像上に配置するときの左上座標
        clip:      親要素の ClipRegion

    Returns:
        クリッピング済みの RGBA 画像
    """
    # クリップ領域を枠線幅分だけ内側に縮める
    bw = clip.border_width
    clip_x = clip.x + bw
    clip_y = clip.y + bw
    clip_w = clip.width - bw * 2
    clip_h = clip.height - bw * 2

    # クリップ領域全体のマスクを生成
    full_mask = create_rounded_rect_mask(clip_w, clip_h, clip.radius)

    # full_mask をベース画像サイズのキャンバスに配置
    # (これにより layer の座標系と合わせやすくなる)
    # layer 座標系でのクリップ領域の相対位置を計算
    rel_x = clip_x - layer_pos[0]
    rel_y = clip_y - layer_pos[1]

    # layer と同サイズのマスクキャンバスを作成
    layer_mask_canvas = Image.new("L", layer.size, 0)
    layer_mask_canvas.paste(full_mask, (rel_x, rel_y))

    # layer のアルファとクリップマスクの AND を取る
    r, g, b, a = layer.split()
    new_a = ImageChops.multiply(a, layer_mask_canvas)
    return Image.merge("RGBA", (r, g, b, new_a))


def render_fill(
    width: int,
    height: int,
    fill: Fill,
) -> Image.Image | None:
    """Fill 値から RGBA 画像を生成する。

    Args:
        width:  画像幅 (px)
        height: 画像高さ (px)
        fill:   塗り色。None の場合は None を返す。

    Returns:
        RGBA 画像。fill が None の場合は None。
    """
    if fill is None:
        return None

    if isinstance(fill, LinearGradient):
        return _render_linear_gradient(width, height, fill)

    if isinstance(fill, RadialGradient):
        return _render_radial_gradient(width, height, fill)

    # 単色
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, width - 1, height - 1), fill=parse_color(fill))
    return img


def _render_linear_gradient(width: int, height: int, grad: LinearGradient) -> Image.Image:
    """線形グラデーション画像を生成する。"""
    colors = [parse_color(c) for c in grad.colors]
    if len(colors) == 1:
        colors.append(colors[0])
    n = len(colors)
    stops = grad.stops if grad.stops and len(grad.stops) == n else [i / (n - 1) for i in range(n)]

    # グラデーション方向ベクトル (正規化)
    angle_rad = math.radians(grad.angle)
    dx = math.sin(angle_rad)
    dy = math.cos(angle_rad)  # 0度 = 上→下

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pixels = img.load()
    assert pixels is not None

    diag = math.sqrt(width ** 2 + height ** 2) / 2
    cx, cy = width / 2, height / 2

    for y in range(height):
        for x in range(width):
            # 中心からの射影距離を -0.5〜0.5 に正規化
            proj = ((x - cx) * dx + (y - cy) * dy) / (diag * 2) + 0.5
            proj = max(0.0, min(1.0, proj))
            pixels[x, y] = _interpolate_gradient(proj, colors, stops)

    return img


def _render_radial_gradient(width: int, height: int, grad: RadialGradient) -> Image.Image:
    """放射グラデーション画像を生成する。"""
    colors = [parse_color(c) for c in grad.colors]
    if len(colors) == 1:
        colors.append(colors[0])
    n = len(colors)
    stops = grad.stops if grad.stops and len(grad.stops) == n else [i / (n - 1) for i in range(n)]

    cx = grad.center[0] * width
    cy = grad.center[1] * height
    max_r = grad.radius * max(width, height)

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pixels = img.load()
    assert pixels is not None

    for y in range(height):
        for x in range(width):
            dist = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
            t = max(0.0, min(1.0, dist / max_r)) if max_r > 0 else 0.0
            pixels[x, y] = _interpolate_gradient(t, colors, stops)

    return img


def _interpolate_gradient(
    t: float,
    colors: list[tuple[int, int, int, int]],
    stops: list[float],
) -> tuple[int, int, int, int]:
    """グラデーションの t (0.0〜1.0) における補間色を返す。"""
    if t <= stops[0]:
        return colors[0]
    if t >= stops[-1]:
        return colors[-1]
    for i in range(len(stops) - 1):
        if stops[i] <= t <= stops[i + 1]:
            seg = stops[i + 1] - stops[i]
            local_t = (t - stops[i]) / seg if seg > 0 else 0.0
            c0, c1 = colors[i], colors[i + 1]
            return tuple(
                int(c0[j] + (c1[j] - c0[j]) * local_t) for j in range(4)
            )  # type: ignore[return-value]
    return colors[-1]
