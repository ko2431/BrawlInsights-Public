"""
画像アセット貼り付けヘルパー関数群。
PNG / WebP / SVG を透過情報を保持したまま RGBA ベース画像に合成する。

公開クラス:
    ImageStyle ... 画像貼り付けスタイル設定 (dataclass)

公開関数:
    paste_image            ... 画像アセットをベース画像に貼り付ける
    paste_image_with_clip  ... 親要素のクリッピング付きで画像をベース画像に貼り付ける
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter

try:
    import cairosvg
except ImportError:
    cairosvg = None

from app.core.logger import logger
from app.services.render_cache import ImageComponentCache
from app.services.renderer_utils import (
    Anchor,
    ClipRegion,
    Color,
    DropShadow,
    RadiusConfig,
    apply_anchor,
    apply_clip_region,
    apply_drop_shadow,
    apply_opacity,
    create_rounded_rect_mask,
    parse_color,
)

# ---------------------------------------------------------------------------
# パス定数
# ---------------------------------------------------------------------------

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
IMAGES_DIR = STATIC_DIR / "images"

# 画像アセットのデコード結果をプロセス内キャッシュする。
# maxsize は概算バイト数で管理される。
ASSET_IMAGE_CACHE = ImageComponentCache(max_bytes=250 * 1024 * 1024, ttl_seconds=30 * 60)

# ---------------------------------------------------------------------------
# ImageStyle dataclass
# ---------------------------------------------------------------------------


@dataclass
class ImageStyle:
    """画像アセット貼り付けのスタイル設定。

    Attributes:
        width:         貼り付け幅 (px)。None で高さに基づいてアスペクト比を維持。
        height:        貼り付け高さ (px)。None で幅に基づいてアスペクト比を維持。
                       width/height を両方 None にすることはできない。
        min_width:     最小幅 (px)。width 計算後に CSS 的に下限として適用。
        max_width:     最大幅 (px)。width 計算後に CSS 的に上限として適用。
        min_height:    最小高さ (px)。height 計算後に CSS 的に下限として適用。
        max_height:    最大高さ (px)。height 計算後に CSS 的に上限として適用。
        scale:         width/height の代わりに倍率で指定する場合に使用 (例: 0.5 で半分)。
                       width/height が指定されていない場合のみ有効。
        radius:        角丸の半径 (px)。int で4隅均一、RadiusConfig で個別指定。
        rotate:        回転角 (度)。デフォルト 0。
        opacity:       不透明度 0.0〜1.0。デフォルト 1.0。
        color_overlay: カラーオーバーレイの色。指定すると画像の RGB チャンネルを
                       この色で置き換え、元のアルファ形状を保持する。
        shadow:        ドロップシャドウ設定。None でシャドウなし。
        flip_x:        水平反転。デフォルト False。
        flip_y:        垂直反転。デフォルト False。
        blend_mode:    ブレンドモード。現在は "normal" のみ対応。将来の拡張予定。
    """
    width: int | None = None
    height: int | None = None
    min_width: int | None = None
    max_width: int | None = None
    min_height: int | None = None
    max_height: int | None = None
    scale: float | None = None
    radius: int | RadiusConfig = 0
    rotate: float = 0.0
    opacity: float = 1.0
    color_overlay: Color | None = None
    shadow: DropShadow | None = None
    flip_x: bool = False
    flip_y: bool = False
    blend_mode: str = "normal"  # 将来の拡張: "multiply", "screen", "overlay" 等


# ---------------------------------------------------------------------------
# 内部ユーティリティ
# ---------------------------------------------------------------------------

def _resolve_path(path: str | Path) -> Path:
    """パスを解決する。相対パスの場合は IMAGES_DIR からの相対として扱う。"""
    p = Path(path)
    if p.is_absolute():
        return p
    return IMAGES_DIR / p


def _build_asset_cache_key(path: Path) -> str:
    """ファイル更新を考慮したアセットキャッシュキーを生成する。"""
    resolved = path.resolve()
    try:
        stat = resolved.stat()
        return f"{resolved}:{stat.st_mtime_ns}:{stat.st_size}"
    except FileNotFoundError:
        # 存在しない場合でもキーを返して処理を継続できるようにする
        return str(resolved)


def _load_image(path: Path) -> Image.Image | None:
    """拡張子を判定して画像をロードし、RGBA 画像として返す。

    対応形式: PNG / WebP / SVG
    失敗時は None を返す。
    """
    cache_key = _build_asset_cache_key(path)
    cached = ASSET_IMAGE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    suffix = path.suffix.lower()
    try:
        if suffix == ".svg":
            img = _load_svg(path)
        else:
            loaded = Image.open(path)
            img = loaded.convert("RGBA")

        if img is not None:
            ASSET_IMAGE_CACHE.set(cache_key, img)
            return img

        return None
    except FileNotFoundError:
        logger.warning(f"画像ファイルが見つかりません: {path}")
        return None
    except Exception as e:
        logger.error(f"画像ファイルのロードに失敗しました ({path}): {e}")
        return None


def _load_svg(path: Path) -> Image.Image | None:
    """SVG ファイルを PIL Image (RGBA) に変換する。

    cairosvg を使用。インストールされていない場合は警告を出して None を返す。
    """
    if cairosvg is None:
        logger.error(
            "SVG の読み込みには cairosvg が必要です。"
            "`pip install cairosvg` でインストールしてください。"
        )
        return None

    try:
        png_bytes = cairosvg.svg2png(url=str(path))
        img = Image.open(io.BytesIO(png_bytes))
        return img.convert("RGBA")
    except Exception as e:
        logger.error(f"SVG のラスタライズに失敗しました ({path}): {e}")
        return None


def _resize_image(img: Image.Image, style: ImageStyle) -> Image.Image:
    """ImageStyle に従って画像をリサイズする。"""
    orig_w, orig_h = img.size

    def _normalize_bound(value: int | None) -> int | None:
        if value is None:
            return None
        return max(1, int(value))

    def _clamp_axis(value: int, lower: int | None, upper: int | None) -> int:
        if lower is not None and value < lower:
            value = lower
        if upper is not None and value > upper:
            value = upper
        return max(1, value)

    min_w = _normalize_bound(style.min_width)
    max_w = _normalize_bound(style.max_width)
    min_h = _normalize_bound(style.min_height)
    max_h = _normalize_bound(style.max_height)

    # CSS と同様に min > max の矛盾時は min を優先する。
    if min_w is not None and max_w is not None and min_w > max_w:
        max_w = min_w
    if min_h is not None and max_h is not None and min_h > max_h:
        max_h = min_h

    def _resize_keep_ratio(preferred_scale: float) -> Image.Image:
        scale_min = 0.0
        scale_max = float("inf")

        if min_w is not None:
            scale_min = max(scale_min, min_w / orig_w)
        if min_h is not None:
            scale_min = max(scale_min, min_h / orig_h)
        if max_w is not None:
            scale_max = min(scale_max, max_w / orig_w)
        if max_h is not None:
            scale_max = min(scale_max, max_h / orig_h)

        if scale_max < scale_min:
            scale_max = scale_min

        scale = min(max(preferred_scale, scale_min), scale_max)
        new_w = max(1, int(round(orig_w * scale)))
        new_h = max(1, int(round(orig_h * scale)))

        # 丸め誤差で上下限を外れる可能性があるため最終 clamp を行う。
        new_w = _clamp_axis(new_w, min_w, max_w)
        new_h = _clamp_axis(new_h, min_h, max_h)
        return img.resize((new_w, new_h), Image.LANCZOS)

    # scale が指定されていて width/height がない場合
    if style.scale is not None and style.width is None and style.height is None:
        return _resize_keep_ratio(style.scale)

    target_w = style.width
    target_h = style.height

    if target_w is None and target_h is None:
        # サイズ指定なし: オリジナルサイズを基準に min/max を適用。
        return _resize_keep_ratio(1.0)

    if target_w is None:
        # 高さ固定、幅は自動 (アスペクト比維持)
        assert target_h is not None
        return _resize_keep_ratio(target_h / orig_h)
    elif target_h is None:
        # 幅固定、高さは自動 (アスペクト比維持)
        return _resize_keep_ratio(target_w / orig_w)
    else:
        # width/height 両方明示時は CSS と同様に各軸を独立して clamp する。
        new_w = _clamp_axis(target_w, min_w, max_w)
        new_h = _clamp_axis(target_h, min_h, max_h)

    return img.resize((new_w, new_h), Image.LANCZOS)


def _apply_color_overlay(img: Image.Image, color: Color) -> Image.Image:
    """画像の RGB チャンネルを指定色で置き換え、アルファ形状を保持する。"""
    r, g, b, a = img.split()
    rgba = parse_color(color)
    colored = Image.new("RGBA", img.size, rgba)
    colored.putalpha(a)
    return colored


def _apply_rounded_corners(img: Image.Image, radius: int | RadiusConfig) -> Image.Image:
    """画像に角丸マスクを適用する。"""
    r = RadiusConfig.from_value(radius)
    if r.is_zero:
        return img
    mask = create_rounded_rect_mask(img.width, img.height, r)
    result = img.copy()
    # 既存のアルファと角丸マスクの AND を取る
    existing_alpha = result.split()[3]
    new_alpha = ImageChops.multiply(existing_alpha, mask)
    result.putalpha(new_alpha)
    return result


def _apply_transformations(img: Image.Image, style: ImageStyle) -> Image.Image:
    """flip / rotate / color_overlay / rounded_corners / opacity を順に適用する。"""
    # 反転
    if style.flip_x:
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    if style.flip_y:
        img = img.transpose(Image.FLIP_TOP_BOTTOM)

    # 回転
    if style.rotate != 0.0:
        img = img.rotate(-style.rotate, expand=True, resample=Image.BICUBIC)

    # カラーオーバーレイ
    if style.color_overlay is not None:
        img = _apply_color_overlay(img, style.color_overlay)

    # 角丸
    r = RadiusConfig.from_value(style.radius)
    if not r.is_zero:
        img = _apply_rounded_corners(img, style.radius)

    # 不透明度
    if style.opacity < 1.0:
        img = apply_opacity(img, style.opacity)

    return img


# ---------------------------------------------------------------------------
# 公開関数
# ---------------------------------------------------------------------------

def paste_image(
    image: Image.Image,
    path: str | Path,
    xy: tuple[int, int],
    style: ImageStyle | None = None,
    anchor: Anchor = ("left", "top"),
    fallback_path: str | Path | None = None,
) -> tuple[int, int]:
    """画像アセットをベース画像に貼り付ける。

    Args:
        image:  貼り付け先 RGBA ベース画像
        path:   画像パス。IMAGES_DIR からの相対パス、または絶対パス。
                PNG / WebP / SVG に対応。
        xy:     アンカー基準座標
        style:  ImageStyle オブジェクト。None でデフォルト設定。
        anchor: 9方向アンカー。デフォルト ("left", "top")。
        fallback_path: 画像が見つからない場合に使用するパス。

    Returns:
        (貼り付けた画像の幅, 高さ) のタプル
    """
    if style is None:
        style = ImageStyle()

    resolved = _resolve_path(path)
    asset = _load_image(resolved)

    if asset is None and fallback_path is not None:
        fallback_resolved = _resolve_path(fallback_path)
        asset = _load_image(fallback_resolved)

    if asset is None:
        return (0, 0)

    asset = _resize_image(asset, style)
    asset = _apply_transformations(asset, style)

    w, h = asset.size
    ox, oy = apply_anchor(xy, w, h, anchor)

    if style.shadow is not None:
        apply_drop_shadow(image, asset, (ox, oy), style.shadow)

    image.alpha_composite(asset, (ox, oy))
    return w, h


def paste_image_with_clip(
    image: Image.Image,
    path: str | Path,
    xy: tuple[int, int],
    clip: ClipRegion,
    style: ImageStyle | None = None,
    anchor: Anchor = ("left", "top"),
    fallback_path: str | Path | None = None,
) -> tuple[int, int]:
    """親要素のクリッピング付きで画像アセットをベース画像に貼り付ける。

    親の角丸・枠線に沿って、はみ出た部分が自動的にくり抜かれる。

    Args:
        image:  貼り付け先 RGBA ベース画像
        path:   画像パス。IMAGES_DIR からの相対パス、または絶対パス。
        xy:     アンカー基準座標
        clip:   親要素の ClipRegion
        style:  ImageStyle オブジェクト。None でデフォルト設定。
        anchor: 9方向アンカー。デフォルト ("left", "top")。
        fallback_path: 画像が見つからない場合に使用するパス。

    Returns:
        (貼り付けた画像の幅, 高さ) のタプル
    """
    if style is None:
        style = ImageStyle()

    resolved = _resolve_path(path)
    asset = _load_image(resolved)

    if asset is None and fallback_path is not None:
        fallback_resolved = _resolve_path(fallback_path)
        asset = _load_image(fallback_resolved)

    if asset is None:
        return (0, 0)

    asset = _resize_image(asset, style)
    asset = _apply_transformations(asset, style)

    w, h = asset.size
    ox, oy = apply_anchor(xy, w, h, anchor)

    if style.shadow is not None:
        apply_drop_shadow(image, asset, (ox, oy), style.shadow)

    # クリッピング適用
    clipped = apply_clip_region(asset, (ox, oy), clip)
    image.alpha_composite(clipped, (ox, oy))
    return w, h
