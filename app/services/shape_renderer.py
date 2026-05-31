"""
図形描画ヘルパー関数群。
長方形・楕円・三角形・平行四辺形・多角形・線分・円弧・星形を RGBA ベース画像に描画する。

公開クラス:
    RectStyle          ... 長方形スタイル設定 (dataclass)
    EllipseStyle       ... 楕円スタイル設定 (dataclass)
    TriangleStyle      ... 三角形スタイル設定 (dataclass)
    ParallelogramStyle ... 平行四辺形スタイル設定 (dataclass)
    PolygonStyle       ... 任意多角形スタイル設定 (dataclass)
    LineStyle          ... 線分スタイル設定 (dataclass)
    ArcStyle           ... 円弧スタイル設定 (dataclass)
    StarStyle          ... 星形スタイル設定 (dataclass)

公開関数:
    draw_rect               ... 長方形を描画
    draw_rect_with_clip     ... 長方形を描画し ClipRegion を返す (親要素として使用)
    draw_ellipse            ... 楕円 / 円を描画
    draw_ellipse_with_clip  ... 楕円 / 円を描画し ClipRegion を返す
    draw_triangle           ... 三角形を描画
    draw_parallelogram      ... 平行四辺形を描画
    draw_polygon            ... 任意多角形を描画
    draw_line               ... 線分を描画
    draw_arc                ... 円弧 / ドーナツ型を描画
    draw_star               ... 星形を描画
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from app.core.logger import logger
from app.services.renderer_utils import (
    Anchor,
    BorderConfig,
    ClipRegion,
    Color,
    DropShadow,
    Fill,
    LinearGradient,
    RadialGradient,
    RadiusConfig,
    apply_anchor,
    apply_clip_region,
    apply_drop_shadow,
    apply_opacity,
    create_rounded_rect_mask,
    create_smooth_ellipse_mask,
    create_smooth_mask,
    parse_color,
    render_fill,
)


# ---------------------------------------------------------------------------
# スタイル dataclass 群
# ---------------------------------------------------------------------------

@dataclass
class RectStyle:
    """長方形スタイル設定。

    Attributes:
        fill:           塗り色。単色 / グラデーション / None (透明)。
        radius:         角丸半径。int で4隅均一、RadiusConfig で個別指定。
        border:         枠線設定。None でなし。枠線は内側に描画される。
        rotate:         回転角 (度)。デフォルト 0。
        opacity:        不透明度 0.0〜1.0。デフォルト 1.0。
        shadow:         ドロップシャドウ設定。None でなし。
        inner_shadow:   インナーシャドウ設定。None でなし。
        backdrop_blur:  背景ぼかし量 (px)。0 でなし。すりガラス効果。
    """
    fill: Fill = None
    radius: int | RadiusConfig = 0
    border: BorderConfig | None = None
    rotate: float = 0.0
    opacity: float = 1.0
    shadow: DropShadow | None = None
    inner_shadow: DropShadow | None = None
    backdrop_blur: int = 0


@dataclass
class EllipseStyle:
    """楕円 / 円スタイル設定。

    Attributes:
        fill:    塗り色。単色 / グラデーション / None (透明)。
        border:  枠線設定。None でなし。
        opacity: 不透明度 0.0〜1.0。デフォルト 1.0。
        shadow:  ドロップシャドウ設定。None でなし。
    """
    fill: Fill = None
    border: BorderConfig | None = None
    opacity: float = 1.0
    shadow: DropShadow | None = None


@dataclass
class TriangleStyle:
    """三角形スタイル設定。

    Attributes:
        fill:    塗り色。単色 / グラデーション / None (透明)。
        border:  枠線設定。None でなし。
        opacity: 不透明度 0.0〜1.0。デフォルト 1.0。
        shadow:  ドロップシャドウ設定。None でなし。
    """
    fill: Fill = None
    border: BorderConfig | None = None
    opacity: float = 1.0
    shadow: DropShadow | None = None


@dataclass
class ParallelogramStyle:
    """平行四辺形スタイル設定。

    Attributes:
        fill:    塗り色。単色 / グラデーション / None (透明)。
        border:  枠線設定。None でなし。
        opacity: 不透明度 0.0〜1.0。デフォルト 1.0。
        shadow:  ドロップシャドウ設定。None でなし。
    """
    fill: Fill = None
    border: BorderConfig | None = None
    opacity: float = 1.0
    shadow: DropShadow | None = None


@dataclass
class PolygonStyle:
    """任意多角形スタイル設定。

    Attributes:
        fill:    塗り色。単色 / グラデーション / None (透明)。
        border:  枠線設定。None でなし。
        opacity: 不透明度 0.0〜1.0。デフォルト 1.0。
        shadow:  ドロップシャドウ設定。None でなし。
    """
    fill: Fill = None
    border: BorderConfig | None = None
    opacity: float = 1.0
    shadow: DropShadow | None = None


@dataclass
class LineStyle:
    """線分スタイル設定。

    Attributes:
        color:    線の色。
        width:    線の太さ (px)。デフォルト 1。
        cap:      端点の形状。"butt" / "round" / "square"。デフォルト "butt"。
        opacity:  不透明度 0.0〜1.0。デフォルト 1.0。
        shadow:   ドロップシャドウ設定。None でなし。
    """
    color: Color = (255, 255, 255, 255)
    width: int = 1
    cap: str = "butt"
    opacity: float = 1.0
    shadow: DropShadow | None = None


@dataclass
class ArcStyle:
    """円弧 / ドーナツ型スタイル設定。

    Attributes:
        fill:         塗り色 (ドーナツ型の塗り)。None でなし。
        stroke_color: 弧の線色。
        stroke_width: 弧の線太さ (px)。
        opacity:      不透明度 0.0〜1.0。デフォルト 1.0。
        shadow:       ドロップシャドウ設定。None でなし。
        cap:          端点の形状。"butt" / "round"。デフォルト "butt"。
    """
    fill: Fill = None
    stroke_color: Color = (255, 255, 255, 255)
    stroke_width: int = 8
    opacity: float = 1.0
    shadow: DropShadow | None = None
    cap: str = "butt"


@dataclass
class StarStyle:
    """星形スタイル設定。

    Attributes:
        fill:         塗り色。単色 / グラデーション / None (透明)。
        border:       枠線設定。None でなし。
        points:       頂点数 (星の先端の数)。デフォルト 5。
        inner_ratio:  内接円半径の外接円半径に対する比率。
                      小さいほど，星の先端が細くなる。デフォルト 0.45 (標準的な5角星)。
        rotate:       回転角 (度)。デフォルト 0。
        opacity:      不透明度 0.0〜1.0。デフォルト 1.0。
        shadow:       ドロップシャドウ設定。None でなし。
    """
    fill: Fill = None
    border: BorderConfig | None = None
    points: int = 5
    inner_ratio: float = 0.45
    rotate: float = 0.0
    opacity: float = 1.0
    shadow: DropShadow | None = None


# ---------------------------------------------------------------------------
# 内部ユーティリティ
# ---------------------------------------------------------------------------

def _make_layer(width: int, height: int) -> Image.Image:
    """透明な RGBA レイヤーを生成する。"""
    return Image.new("RGBA", (width, height), (0, 0, 0, 0))


def _apply_fill_to_shape(
    width: int,
    height: int,
    fill: Fill,
    mask: Image.Image,
) -> Image.Image:
    """塗り色画像にシェイプマスクをかけて RGBA レイヤーを返す。

    Args:
        width, height: キャンバスサイズ
        fill:          塗り色 / グラデーション
        mask:          L モードのシェイプマスク (白=表示, 黒=透明)

    Returns:
        RGBA 画像
    """
    layer = _make_layer(width, height)
    if fill is None:
        return layer
    fill_img = render_fill(width, height, fill)
    if fill_img is None:
        return layer
    fill_img.putalpha(mask)
    layer.alpha_composite(fill_img)
    return layer


def _apply_border_to_shape(
    layer: Image.Image,
    outer_mask: Image.Image,
    inner_mask: Image.Image,
    border: BorderConfig,
    top_mask: Image.Image | None = None,
    right_mask: Image.Image | None = None,
    bottom_mask: Image.Image | None = None,
    left_mask: Image.Image | None = None,
) -> None:
    """枠線をレイヤーに alpha_composite で描画する (in-place)。

    outer_mask - inner_mask の差分領域が枠線エリアになる。
    top/right/bottom/left の各辺マスクで、描画する辺を絞る。
    """
    w, h = layer.size
    border_fill_img = render_fill(w, h, border.color)
    if border_fill_img is None:
        border_fill_img = Image.new("RGBA", (w, h), parse_color(border.color if isinstance(border.color, (str, tuple)) else (255, 255, 255, 255)))

    # 枠線エリアマスク (外枠 - 内枠)
    border_mask = ImageChops.subtract(outer_mask, inner_mask)

    # 4辺の選択マスク
    if not (border.top and border.right and border.bottom and border.left):
        side_canvas = Image.new("L", (w, h), 0)
        side_draw = ImageDraw.Draw(side_canvas)
        bw = border.width
        if border.top:
            side_draw.rectangle((0, 0, w - 1, bw - 1), fill=255)
        if border.bottom:
            side_draw.rectangle((0, h - bw, w - 1, h - 1), fill=255)
        if border.left:
            side_draw.rectangle((0, 0, bw - 1, h - 1), fill=255)
        if border.right:
            side_draw.rectangle((w - bw, 0, w - 1, h - 1), fill=255)
        border_mask = ImageChops.multiply(border_mask, side_canvas)

    border_fill_img.putalpha(border_mask)
    layer.alpha_composite(border_fill_img)


def _apply_inner_shadow(layer: Image.Image, shadow: DropShadow) -> None:
    """インナーシャドウをレイヤーに適用する (in-place)。

    レイヤーのアルファ形状の内側にシャドウを描画する。
    """
    w, h = layer.size
    # 1. アルファ形状の反転マスクを影レイヤーとして作成
    alpha = layer.split()[3]
    inner_shadow_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sc = parse_color(shadow.color)
    shadow_color_img = Image.new("RGBA", (w, h), sc)
    # 反転アルファ: 透明部分を影色で塗る
    inverted = ImageOps.invert(alpha)
    shadow_color_img.putalpha(inverted)
    inner_shadow_layer.alpha_composite(shadow_color_img,
                                        (shadow.offset[0], shadow.offset[1]))
    if shadow.blur > 0:
        inner_shadow_layer = inner_shadow_layer.filter(
            ImageFilter.GaussianBlur(shadow.blur)
        )
    # シェイプ内にだけ表示 (アルファでマスク)
    inner_shadow_layer.putalpha(
        ImageChops.multiply(inner_shadow_layer.split()[3], alpha)
    )
    layer.alpha_composite(inner_shadow_layer)


def _apply_backdrop_blur(
    base: Image.Image,
    x: int, y: int, width: int, height: int,
    blur: int,
    mask: Image.Image,
) -> None:
    """長方形エリアを背景からぼかしてベース画像に合成する (in-place)。

    Args:
        base:   ベース RGBA 画像
        x, y:  エリアの左上座標
        width, height: エリアのサイズ
        blur:  ぼかし量 (px)
        mask:  シェイプマスク (L モード) — ぼかし効果を限定する形状
    """
    # ベース画像から該当エリアをクロップ
    crop = base.crop((x, y, x + width, y + height))
    blurred = crop.filter(ImageFilter.GaussianBlur(blur))
    blurred_rgba = blurred.convert("RGBA")
    blurred_rgba.putalpha(mask)
    # ベース画像に合成 (元の背景を上書き)
    base.alpha_composite(blurred_rgba, (x, y))


def _rotate_layer(layer: Image.Image, angle: float) -> tuple[Image.Image, int, int]:
    """レイヤーを回転し、(回転後画像, 幅オフセット, 高さオフセット) を返す。"""
    if angle == 0.0:
        return layer, 0, 0
    rotated = layer.rotate(-angle, expand=True, resample=Image.BICUBIC)
    dw = (rotated.width - layer.width) // 2
    dh = (rotated.height - layer.height) // 2
    return rotated, dw, dh


# ---------------------------------------------------------------------------
# 長方形
# ---------------------------------------------------------------------------

def draw_rect(
    image: Image.Image,
    xy: tuple[int, int],
    width: int,
    height: int,
    style: RectStyle | None = None,
    anchor: Anchor = ("left", "top"),
    clip: ClipRegion | None = None,
) -> tuple[int, int]:
    """長方形をベース画像に描画する。

    Args:
        image:  描画先 RGBA ベース画像
        xy:     アンカー基準座標
        width:  長方形の幅 (px)
        height: 長方形の高さ (px)
        style:  RectStyle オブジェクト。None でデフォルト設定。
        anchor: 9方向アンカー。デフォルト ("left", "top")。

    Returns:
        (width, height) のタプル
    """
    if style is None:
        style = RectStyle()

    if width <= 0 or height <= 0:
        return width, height

    ox, oy = apply_anchor(xy, width, height, anchor)

    # Backdrop Blur (先にベース画像に適用)
    if style.backdrop_blur > 0:
        r = RadiusConfig.from_value(style.radius)
        mask = create_rounded_rect_mask(width, height, r)
        _apply_backdrop_blur(image, ox, oy, width, height, style.backdrop_blur, mask)

    # レイヤー生成
    layer = _make_layer(width, height)

    # 角丸マスク生成
    r = RadiusConfig.from_value(style.radius)
    outer_mask = create_rounded_rect_mask(width, height, r)

    # 塗り
    fill_layer = _apply_fill_to_shape(width, height, style.fill, outer_mask)
    layer.alpha_composite(fill_layer)

    # 枠線
    if style.border is not None:
        bw = style.border.width
        inner_w = width - bw * 2
        inner_h = height - bw * 2
        inner_mask_shifted = Image.new("L", (width, height), 0)
        
        if inner_w > 0 and inner_h > 0:
            inner_radius = RadiusConfig(
                tl=max(0, r.tl - bw),
                tr=max(0, r.tr - bw),
                br=max(0, r.br - bw),
                bl=max(0, r.bl - bw),
            )
            inner_mask = create_rounded_rect_mask(inner_w, inner_h, inner_radius)
            # 内側に縮める (枠線幅分)
            inner_mask_shifted.paste(inner_mask, (bw, bw))
            
        _apply_border_to_shape(layer, outer_mask, inner_mask_shifted, style.border)

    # インナーシャドウ
    if style.inner_shadow is not None:
        _apply_inner_shadow(layer, style.inner_shadow)

    # 不透明度
    if style.opacity < 1.0:
        layer = apply_opacity(layer, style.opacity)

    # 回転
    layer, dw, dh = _rotate_layer(layer, style.rotate)

    layer_pos = (ox - dw, oy - dh)

    # ドロップシャドウ
    if style.shadow is not None:
        apply_drop_shadow(image, layer, layer_pos, style.shadow)

    if clip is not None:
        layer = apply_clip_region(layer, layer_pos, clip)

    image.alpha_composite(layer, layer_pos)
    return width, height


def draw_rect_with_clip(
    image: Image.Image,
    xy: tuple[int, int],
    width: int,
    height: int,
    style: RectStyle | None = None,
    anchor: Anchor = ("left", "top"),
    clip: ClipRegion | None = None,
) -> ClipRegion:
    """長方形を描画し、子要素のクリッピング用 ClipRegion を返す。

    draw_rect() と同じ描画を行い、加えて ClipRegion を返す。
    子要素に clip_region として渡すことで、親の角丸・枠線に沿って
    子要素が自動的にくり抜かれる。

    Args:
        image:  描画先 RGBA ベース画像
        xy:     アンカー基準座標
        width:  長方形の幅 (px)
        height: 長方形の高さ (px)
        style:  RectStyle オブジェクト。None でデフォルト設定。
        anchor: 9方向アンカー。デフォルト ("left", "top")。

    Returns:
        ClipRegion — 子要素の paste_image_with_clip や draw_*_with_clip に渡す。
    """
    if style is None:
        style = RectStyle()

    ox, oy = apply_anchor(xy, width, height, anchor)
    draw_rect(image, (ox, oy), width, height, style, anchor=("left", "top"), clip=clip)

    border_width = style.border.width if style.border is not None else 0
    return ClipRegion(
        x=ox,
        y=oy,
        width=width,
        height=height,
        radius=style.radius,
        border_width=border_width,
    )


# ---------------------------------------------------------------------------
# 楕円 / 円
# ---------------------------------------------------------------------------

def draw_ellipse(
    image: Image.Image,
    xy: tuple[int, int],
    width: int,
    height: int,
    style: EllipseStyle | None = None,
    anchor: Anchor = ("left", "top"),
    clip: ClipRegion | None = None,
) -> tuple[int, int]:
    """楕円 / 円をベース画像に描画する。

    width == height で円になる。

    Args:
        image:  描画先 RGBA ベース画像
        xy:     アンカー基準座標
        width:  楕円の幅 (直径・px)
        height: 楕円の高さ (直径・px)
        style:  EllipseStyle オブジェクト。None でデフォルト設定。
        anchor: 9方向アンカー。デフォルト ("left", "top")。

    Returns:
        (width, height) のタプル
    """
    if style is None:
        style = EllipseStyle()

    if width <= 0 or height <= 0:
        return width, height

    ox, oy = apply_anchor(xy, width, height, anchor)

    layer = _make_layer(width, height)
    draw = ImageDraw.Draw(layer)

    # シェイプマスク (4倍スーパーサンプリングによりアンチエイリアス適用)
    ellipse_mask = create_smooth_ellipse_mask(width, height)

    # 塗り
    fill_layer = _apply_fill_to_shape(width, height, style.fill, ellipse_mask)
    layer.alpha_composite(fill_layer)

    # 枠線
    if style.border is not None:
        bw = style.border.width
        border_fill_img = render_fill(width, height, style.border.color)
        if border_fill_img is None:
            border_fill_img = Image.new("RGBA", (width, height), parse_color(
                style.border.color if isinstance(style.border.color, (str, tuple)) else (255, 255, 255, 255)
            ))
        # 内側枠線マスク
        def draw_inner_ellipse(draw, scale):
            bw_s = bw * scale
            draw.ellipse((bw_s, bw_s, width * scale - 1 - bw_s, height * scale - 1 - bw_s), fill=255)
        
        inner_mask_val = create_smooth_mask(width, height, draw_inner_ellipse)
        border_mask = ImageChops.subtract(ellipse_mask, inner_mask_val)
        border_fill_img.putalpha(border_mask)
        layer.alpha_composite(border_fill_img)

    # 不透明度
    if style.opacity < 1.0:
        layer = apply_opacity(layer, style.opacity)

    layer_pos = (ox, oy)

    # ドロップシャドウ
    if style.shadow is not None:
        apply_drop_shadow(image, layer, layer_pos, style.shadow)

    if clip is not None:
        layer = apply_clip_region(layer, layer_pos, clip)

    image.alpha_composite(layer, layer_pos)
    return width, height


def draw_ellipse_with_clip(
    image: Image.Image,
    xy: tuple[int, int],
    width: int,
    height: int,
    style: EllipseStyle | None = None,
    anchor: Anchor = ("left", "top"),
    clip: ClipRegion | None = None,
) -> ClipRegion:
    """楕円 / 円を描画し、子要素のクリッピング用 ClipRegion を返す。

    楕円の場合、角丸として短辺の半分を用いた近似クリッピングになる。

    Returns:
        ClipRegion
    """
    if style is None:
        style = EllipseStyle()

    ox, oy = apply_anchor(xy, width, height, anchor)
    draw_ellipse(image, (ox, oy), width, height, style, anchor=("left", "top"), clip=clip)

    min_radius = min(width, height) // 2
    border_width = style.border.width if style.border is not None else 0
    return ClipRegion(
        x=ox,
        y=oy,
        width=width,
        height=height,
        radius=min_radius,
        border_width=border_width,
    )


# ---------------------------------------------------------------------------
# 三角形
# ---------------------------------------------------------------------------

def draw_triangle(
    image: Image.Image,
    xy: tuple[int, int],
    width: int,
    height: int,
    style: TriangleStyle | None = None,
    anchor: Anchor = ("left", "top"),
    direction: str = "up",
    points: list[tuple[int, int]] | None = None,
    clip: ClipRegion | None = None,
) -> tuple[int, int]:
    """三角形をベース画像に描画する。

    Args:
        image:     描画先 RGBA ベース画像
        xy:        アンカー基準座標
        width:     バウンディングボックスの幅 (px)
        height:    バウンディングボックスの高さ (px)
        style:     TriangleStyle オブジェクト。None でデフォルト設定。
        anchor:    9方向アンカー。デフォルト ("left", "top")。
        direction: 三角形の向き。"up" / "down" / "left" / "right" / "custom"。
        points:    direction="custom" のとき、3頂点 [(x,y), (x,y), (x,y)] を指定。
                   座標はバウンディングボックス内の相対座標 (px)。

    Returns:
        (width, height) のタプル
    """
    if style is None:
        style = TriangleStyle()

    if width <= 0 or height <= 0:
        return width, height

    ox, oy = apply_anchor(xy, width, height, anchor)
    layer = _make_layer(width, height)

    # 頂点計算
    if direction == "custom" and points is not None:
        vertices = [(p[0], p[1]) for p in points]
    else:
        cx = width // 2
        match direction:
            case "up":
                vertices = [(cx, 0), (width - 1, height - 1), (0, height - 1)]
            case "down":
                vertices = [(0, 0), (width - 1, 0), (cx, height - 1)]
            case "left":
                vertices = [(width - 1, 0), (width - 1, height - 1), (0, height // 2)]
            case "right":
                vertices = [(0, 0), (0, height - 1), (width - 1, height // 2)]
            case _:
                vertices = [(cx, 0), (width - 1, height - 1), (0, height - 1)]

    # シェイプマスク (4倍スーパーサンプリングによりアンチエイリアス適用)
    def draw_tri_func(draw, scale):
        scaled_verts = [(v[0] * scale, v[1] * scale) for v in vertices]
        draw.polygon(scaled_verts, fill=255)
    tri_mask = create_smooth_mask(width, height, draw_tri_func)

    # 塗り
    fill_layer = _apply_fill_to_shape(width, height, style.fill, tri_mask)
    layer.alpha_composite(fill_layer)

    # 枠線
    if style.border is not None:
        border_draw = ImageDraw.Draw(layer)
        bc = parse_color(style.border.color if isinstance(style.border.color, (str, tuple)) else (255, 255, 255, 255))
        border_draw.polygon(vertices, outline=bc, width=style.border.width)

    # 不透明度
    if style.opacity < 1.0:
        layer = apply_opacity(layer, style.opacity)

    layer_pos = (ox, oy)

    # ドロップシャドウ
    if style.shadow is not None:
        apply_drop_shadow(image, layer, layer_pos, style.shadow)

    if clip is not None:
        layer = apply_clip_region(layer, layer_pos, clip)

    image.alpha_composite(layer, layer_pos)
    return width, height


# ---------------------------------------------------------------------------
# 平行四辺形
# ---------------------------------------------------------------------------

def draw_parallelogram(
    image: Image.Image,
    xy: tuple[int, int],
    width: int,
    height: int,
    skew: int,
    style: ParallelogramStyle | None = None,
    anchor: Anchor = ("left", "top"),
    clip: ClipRegion | None = None,
) -> tuple[int, int]:
    """平行四辺形をベース画像に描画する。

    Args:
        image:  描画先 RGBA ベース画像
        xy:     アンカー基準座標
        width:  バウンディングボックスの幅 (px)
        height: 平行四辺形の高さ (px)
        skew:   傾き量 (px)。正で右上がり (上辺が右にずれる)、負で左上がり。
        style:  ParallelogramStyle オブジェクト。None でデフォルト設定。
        anchor: 9方向アンカー。デフォルト ("left", "top")。

    Returns:
        (width, height) のタプル
    """
    if style is None:
        style = ParallelogramStyle()

    if width <= 0 or height <= 0:
        return width, height

    total_w = width + abs(skew)
    ox, oy = apply_anchor(xy, total_w, height, anchor)
    layer = _make_layer(total_w, height)

    s = abs(skew)
    if skew >= 0:
        # 上辺が右にずれる (右上がり)
        vertices = [(s, 0), (total_w - 1, 0), (total_w - 1 - s, height - 1), (0, height - 1)]
    else:
        # 上辺が左にずれる (左上がり)
        vertices = [(0, 0), (total_w - 1 - s, 0), (total_w - 1, height - 1), (s, height - 1)]

    # シェイプマスク
    para_mask = Image.new("L", (total_w, height), 0)
    mask_draw = ImageDraw.Draw(para_mask)
    mask_draw.polygon(vertices, fill=255)

    # 塗り
    fill_layer = _apply_fill_to_shape(total_w, height, style.fill, para_mask)
    layer.alpha_composite(fill_layer)

    # 枠線
    if style.border is not None:
        border_draw = ImageDraw.Draw(layer)
        bc = parse_color(style.border.color if isinstance(style.border.color, (str, tuple)) else (255, 255, 255, 255))
        border_draw.polygon(vertices, outline=bc, width=style.border.width)

    # 不透明度
    if style.opacity < 1.0:
        layer = apply_opacity(layer, style.opacity)

    layer_pos = (ox, oy)

    # ドロップシャドウ
    if style.shadow is not None:
        apply_drop_shadow(image, layer, layer_pos, style.shadow)

    if clip is not None:
        layer = apply_clip_region(layer, layer_pos, clip)

    image.alpha_composite(layer, layer_pos)
    return total_w, height


# ---------------------------------------------------------------------------
# 任意多角形
# ---------------------------------------------------------------------------

def draw_polygon(
    image: Image.Image,
    vertices: list[tuple[int, int]],
    style: PolygonStyle | None = None,
    clip: ClipRegion | None = None,
) -> None:
    """任意多角形をベース画像に描画する。

    Args:
        image:    描画先 RGBA ベース画像
        vertices: 頂点リスト [(x, y), ...] — ベース画像上の絶対座標
        style:    PolygonStyle オブジェクト。None でデフォルト設定。
    """
    if style is None:
        style = PolygonStyle()
    if len(vertices) < 3:
        logger.warning("draw_polygon: 頂点は3つ以上必要です。")
        return

    # バウンディングボックスを計算
    xs = [p[0] for p in vertices]
    ys = [p[1] for p in vertices]
    min_x, min_y = min(xs), min(ys)
    max_x, max_y = max(xs), max(ys)
    w = max_x - min_x + 1
    h = max_y - min_y + 1

    layer = _make_layer(w, h)
    # 相対座標に変換
    rel_verts = [(p[0] - min_x, p[1] - min_y) for p in vertices]

    # シェイプマスク (4倍スーパーサンプリングによりアンチエイリアス適用)
    def draw_poly_func(draw, scale):
        scaled_verts = [(v[0] * scale, v[1] * scale) for v in rel_verts]
        draw.polygon(scaled_verts, fill=255)
    poly_mask = create_smooth_mask(w, h, draw_poly_func)

    fill_layer = _apply_fill_to_shape(w, h, style.fill, poly_mask)
    layer.alpha_composite(fill_layer)

    if style.border is not None:
        border_draw = ImageDraw.Draw(layer)
        bc = parse_color(style.border.color if isinstance(style.border.color, (str, tuple)) else (255, 255, 255, 255))
        border_draw.polygon(rel_verts, outline=bc, width=style.border.width)

    if style.opacity < 1.0:
        layer = apply_opacity(layer, style.opacity)

    layer_pos = (min_x, min_y)

    if style.shadow is not None:
        apply_drop_shadow(image, layer, layer_pos, style.shadow)

    if clip is not None:
        layer = apply_clip_region(layer, layer_pos, clip)

    image.alpha_composite(layer, layer_pos)


# ---------------------------------------------------------------------------
# 線分
# ---------------------------------------------------------------------------

def draw_line(
    image: Image.Image,
    start: tuple[int, int],
    end: tuple[int, int],
    style: LineStyle | None = None,
    clip: ClipRegion | None = None,
) -> None:
    """線分をベース画像に描画する。

    Args:
        image: 描画先 RGBA ベース画像
        start: 始点座標
        end:   終点座標
        style: LineStyle オブジェクト。None でデフォルト設定。
    """
    if style is None:
        style = LineStyle()

    xs = [start[0], end[0]]
    ys = [start[1], end[1]]
    min_x = min(xs) - style.width
    min_y = min(ys) - style.width
    max_x = max(xs) + style.width
    max_y = max(ys) + style.width
    w = max(max_x - min_x + 1, 1)
    h = max(max_y - min_y + 1, 1)

    layer = _make_layer(w, h)
    draw = ImageDraw.Draw(layer, "RGBA")
    rel_start = (start[0] - min_x, start[1] - min_y)
    rel_end = (end[0] - min_x, end[1] - min_y)

    draw.line([rel_start, rel_end], fill=parse_color(style.color), width=style.width)

    if style.opacity < 1.0:
        layer = apply_opacity(layer, style.opacity)

    layer_pos = (min_x, min_y)

    if style.shadow is not None:
        apply_drop_shadow(image, layer, layer_pos, style.shadow)

    if clip is not None:
        layer = apply_clip_region(layer, layer_pos, clip)

    image.alpha_composite(layer, layer_pos)


# ---------------------------------------------------------------------------
# 円弧 / ドーナツ型
# ---------------------------------------------------------------------------

def draw_arc(
    image: Image.Image,
    xy: tuple[int, int],
    width: int,
    height: int,
    start_angle: float,
    end_angle: float,
    style: ArcStyle | None = None,
    anchor: Anchor = ("left", "top"),
    clip: ClipRegion | None = None,
) -> tuple[int, int]:
    """円弧 / ドーナツ型をベース画像に描画する。

    Args:
        image:        描画先 RGBA ベース画像
        xy:           アンカー基準座標
        width:        楕円の幅 (直径・px)
        height:       楕円の高さ (直径・px)
        start_angle:  弧の開始角度 (度)。0 = 右 (3時方向)、時計回りで増加。
        end_angle:    弧の終了角度 (度)。
        style:        ArcStyle オブジェクト。None でデフォルト設定。
        anchor:       9方向アンカー。デフォルト ("left", "top")。

    Returns:
        (width, height) のタプル
    """
    if style is None:
        style = ArcStyle()

    ox, oy = apply_anchor(xy, width, height, anchor)
    layer = _make_layer(width, height)
    draw = ImageDraw.Draw(layer, "RGBA")

    # 塗り (ドーナツ型の塗り — 楕円全体)
    if style.fill is not None:
        ellipse_mask = Image.new("L", (width, height), 0)
        mask_draw = ImageDraw.Draw(ellipse_mask)
        mask_draw.ellipse((0, 0, width - 1, height - 1), fill=255)
        fill_layer = _apply_fill_to_shape(width, height, style.fill, ellipse_mask)
        layer.alpha_composite(fill_layer)

    # 弧の線
    sc = parse_color(style.stroke_color)
    draw.arc(
        (0, 0, width - 1, height - 1),
        start=start_angle,
        end=end_angle,
        fill=sc,
        width=style.stroke_width,
    )

    if style.opacity < 1.0:
        layer = apply_opacity(layer, style.opacity)

    layer_pos = (ox, oy)

    if style.shadow is not None:
        apply_drop_shadow(image, layer, layer_pos, style.shadow)

    if clip is not None:
        layer = apply_clip_region(layer, layer_pos, clip)

    image.alpha_composite(layer, layer_pos)
    return width, height


# ---------------------------------------------------------------------------
# 星形
# ---------------------------------------------------------------------------

def draw_star(
    image: Image.Image,
    xy: tuple[int, int],
    width: int,
    height: int,
    style: StarStyle | None = None,
    anchor: Anchor = ("left", "top"),
    clip: ClipRegion | None = None,
) -> tuple[int, int]:
    """星形をベース画像に描画する。

    Args:
        image:  描画先 RGBA ベース画像
        xy:     アンカー基準座標
        width:  バウンディングボックスの幅 (px)
        height: バウンディングボックスの高さ (px)
        style:  StarStyle オブジェクト。None でデフォルト設定。
        anchor: 9方向アンカー。デフォルト ("left", "top")。

    Returns:
        (width, height) のタプル
    """
    if style is None:
        style = StarStyle()

    ox, oy = apply_anchor(xy, width, height, anchor)
    layer = _make_layer(width, height)

    cx, cy = width / 2, height / 2
    outer_rx, outer_ry = width / 2, height / 2
    inner_rx = outer_rx * style.inner_ratio
    inner_ry = outer_ry * style.inner_ratio
    n = style.points
    rotate_rad = math.radians(style.rotate - 90)  # -90 で頂点が上向きにデフォルト

    vertices: list[tuple[float, float]] = []
    for i in range(n * 2):
        angle = rotate_rad + math.pi * i / n
        if i % 2 == 0:
            rx, ry = outer_rx, outer_ry
        else:
            rx, ry = inner_rx, inner_ry
        vertices.append((cx + rx * math.cos(angle), cy + ry * math.sin(angle)))

    int_vertices = [(int(v[0]), int(v[1])) for v in vertices]

    # シェイプマスク (4倍スーパーサンプリングによりアンチエイリアス適用)
    def draw_star_func(draw, scale):
        scaled_verts = [(v[0] * scale, v[1] * scale) for v in int_vertices]
        draw.polygon(scaled_verts, fill=255)
    star_mask = create_smooth_mask(width, height, draw_star_func)

    fill_layer = _apply_fill_to_shape(width, height, style.fill, star_mask)
    layer.alpha_composite(fill_layer)

    if style.border is not None:
        border_draw = ImageDraw.Draw(layer)
        bc = parse_color(style.border.color if isinstance(style.border.color, (str, tuple)) else (255, 255, 255, 255))
        border_draw.polygon(int_vertices, outline=bc, width=style.border.width)

    if style.opacity < 1.0:
        layer = apply_opacity(layer, style.opacity)

    layer_pos = (ox, oy)

    if style.shadow is not None:
        apply_drop_shadow(image, layer, layer_pos, style.shadow)

    if clip is not None:
        layer = apply_clip_region(layer, layer_pos, clip)

    image.alpha_composite(layer, layer_pos)
    return width, height
