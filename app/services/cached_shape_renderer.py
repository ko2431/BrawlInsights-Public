from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from app.services.render_cache import ImageComponentCache
from app.services.renderer_utils import Anchor, ClipRegion, DropShadow, apply_anchor, apply_clip_region
from app.services.shape_renderer import (
    ArcStyle,
    EllipseStyle,
    LineStyle,
    ParallelogramStyle,
    PolygonStyle,
    RectStyle,
    StarStyle,
    TriangleStyle,
    draw_arc,
    draw_ellipse,
    draw_line,
    draw_parallelogram,
    draw_polygon,
    draw_rect,
    draw_star,
    draw_triangle,
)


SHAPE_COMPONENT_CACHE = ImageComponentCache(max_bytes=128 * 1024 * 1024, ttl_seconds=60 * 60)


def _freeze_value(value: Any) -> Any:
    if is_dataclass(value):
        return _freeze_value(asdict(value))
    if isinstance(value, dict):
        return tuple(sorted((k, _freeze_value(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple, set)):
        return tuple(_freeze_value(v) for v in value)
    if isinstance(value, Path):
        return str(value)
    return value


def _shadow_padding(shadow: DropShadow | None) -> int:
    if shadow is None:
        return 0
    dx, dy = shadow.offset
    return max(abs(dx), abs(dy)) + shadow.blur * 2 + 2


def _composite_cached_shape(
    image: Image.Image,
    layer: Image.Image,
    xy: tuple[int, int],
    shape_size: tuple[int, int],
    padding: int,
    anchor: Anchor,
    clip: ClipRegion | None = None,
) -> None:
    ox, oy = apply_anchor(xy, shape_size[0], shape_size[1], anchor)
    layer_pos = (ox - padding, oy - padding)
    if clip is not None:
        layer = apply_clip_region(layer, layer_pos, clip)
    image.alpha_composite(layer, layer_pos)


def draw_rect_cached(
    image: Image.Image,
    xy: tuple[int, int],
    width: int,
    height: int,
    style: RectStyle | None = None,
    anchor: Anchor = ("left", "top"),
    clip: ClipRegion | None = None,
) -> tuple[int, int]:
    if style is None:
        style = RectStyle()

    if width <= 0 or height <= 0:
        return width, height

    if style.backdrop_blur > 0:
        return draw_rect(image, xy, width, height, style=style, anchor=anchor, clip=clip)

    padding = _shadow_padding(style.shadow)
    key = ("rect-v1", width, height, _freeze_value(style), padding)

    def _build() -> Image.Image:
        layer = Image.new("RGBA", (width + padding * 2, height + padding * 2), (0, 0, 0, 0))
        draw_rect(
            layer,
            xy=(padding, padding),
            width=width,
            height=height,
            style=style,
            anchor=("left", "top"),
        )
        return layer

    cached = SHAPE_COMPONENT_CACHE.get_or_create(key, _build)
    _composite_cached_shape(image, cached, xy, (width, height), padding, anchor, clip=clip)
    return width, height


def draw_rect_with_clip_cached(
    image: Image.Image,
    xy: tuple[int, int],
    width: int,
    height: int,
    style: RectStyle | None = None,
    anchor: Anchor = ("left", "top"),
    clip: ClipRegion | None = None,
) -> ClipRegion:
    if style is None:
        style = RectStyle()

    draw_rect_cached(image, xy, width, height, style=style, anchor=anchor, clip=clip)
    ox, oy = apply_anchor(xy, width, height, anchor)
    border_width = style.border.width if style.border is not None else 0
    return ClipRegion(
        x=ox,
        y=oy,
        width=width,
        height=height,
        radius=style.radius,
        border_width=border_width,
    )


def draw_ellipse_cached(
    image: Image.Image,
    xy: tuple[int, int],
    width: int,
    height: int,
    style: EllipseStyle | None = None,
    anchor: Anchor = ("left", "top"),
    clip: ClipRegion | None = None,
) -> tuple[int, int]:
    if style is None:
        style = EllipseStyle()

    if width <= 0 or height <= 0:
        return width, height

    padding = _shadow_padding(style.shadow)
    key = ("ellipse-v1", width, height, _freeze_value(style), padding)

    def _build() -> Image.Image:
        layer = Image.new("RGBA", (width + padding * 2, height + padding * 2), (0, 0, 0, 0))
        draw_ellipse(
            layer,
            xy=(padding, padding),
            width=width,
            height=height,
            style=style,
            anchor=("left", "top"),
        )
        return layer

    cached = SHAPE_COMPONENT_CACHE.get_or_create(key, _build)
    _composite_cached_shape(image, cached, xy, (width, height), padding, anchor, clip=clip)
    return width, height


def draw_triangle_cached(
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
    if style is None:
        style = TriangleStyle()

    if width <= 0 or height <= 0:
        return width, height

    points_key = tuple(points) if points is not None else None
    padding = _shadow_padding(style.shadow)
    key = ("triangle-v1", width, height, direction, points_key, _freeze_value(style), padding)

    def _build() -> Image.Image:
        layer = Image.new("RGBA", (width + padding * 2, height + padding * 2), (0, 0, 0, 0))
        local_points = None
        if points is not None:
            local_points = [(px + padding, py + padding) for px, py in points]
        draw_triangle(
            layer,
            xy=(padding, padding),
            width=width,
            height=height,
            style=style,
            anchor=("left", "top"),
            direction=direction,
            points=local_points,
        )
        return layer

    cached = SHAPE_COMPONENT_CACHE.get_or_create(key, _build)
    _composite_cached_shape(image, cached, xy, (width, height), padding, anchor, clip=clip)
    return width, height


def draw_parallelogram_cached(
    image: Image.Image,
    xy: tuple[int, int],
    width: int,
    height: int,
    skew: int,
    style: ParallelogramStyle | None = None,
    anchor: Anchor = ("left", "top"),
    clip: ClipRegion | None = None,
) -> tuple[int, int]:
    if style is None:
        style = ParallelogramStyle()

    if width <= 0 or height <= 0:
        return width, height

    shape_w = width + abs(skew)
    padding = _shadow_padding(style.shadow)
    key = ("parallelogram-v1", width, height, skew, _freeze_value(style), padding)

    def _build() -> Image.Image:
        layer = Image.new("RGBA", (shape_w + padding * 2, height + padding * 2), (0, 0, 0, 0))
        draw_parallelogram(
            layer,
            xy=(padding, padding),
            width=width,
            height=height,
            skew=skew,
            style=style,
            anchor=("left", "top"),
        )
        return layer

    cached = SHAPE_COMPONENT_CACHE.get_or_create(key, _build)
    _composite_cached_shape(image, cached, xy, (shape_w, height), padding, anchor, clip=clip)
    return shape_w, height


def draw_arc_cached(
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
    if style is None:
        style = ArcStyle()

    if width <= 0 or height <= 0:
        return width, height

    padding = _shadow_padding(style.shadow)
    key = (
        "arc-v1",
        width,
        height,
        float(start_angle),
        float(end_angle),
        _freeze_value(style),
        padding,
    )

    def _build() -> Image.Image:
        layer = Image.new("RGBA", (width + padding * 2, height + padding * 2), (0, 0, 0, 0))
        draw_arc(
            layer,
            xy=(padding, padding),
            width=width,
            height=height,
            start_angle=start_angle,
            end_angle=end_angle,
            style=style,
            anchor=("left", "top"),
        )
        return layer

    cached = SHAPE_COMPONENT_CACHE.get_or_create(key, _build)
    _composite_cached_shape(image, cached, xy, (width, height), padding, anchor, clip=clip)
    return width, height


def draw_star_cached(
    image: Image.Image,
    xy: tuple[int, int],
    width: int,
    height: int,
    style: StarStyle | None = None,
    anchor: Anchor = ("left", "top"),
    clip: ClipRegion | None = None,
) -> tuple[int, int]:
    if style is None:
        style = StarStyle()

    if width <= 0 or height <= 0:
        return width, height

    padding = _shadow_padding(style.shadow)
    key = ("star-v1", width, height, _freeze_value(style), padding)

    def _build() -> Image.Image:
        layer = Image.new("RGBA", (width + padding * 2, height + padding * 2), (0, 0, 0, 0))
        draw_star(
            layer,
            xy=(padding, padding),
            width=width,
            height=height,
            style=style,
            anchor=("left", "top"),
        )
        return layer

    cached = SHAPE_COMPONENT_CACHE.get_or_create(key, _build)
    _composite_cached_shape(image, cached, xy, (width, height), padding, anchor, clip=clip)
    return width, height


def draw_line_cached(
    image: Image.Image,
    start: tuple[int, int],
    end: tuple[int, int],
    style: LineStyle | None = None,
    clip: ClipRegion | None = None,
) -> None:
    if style is None:
        style = LineStyle()

    min_x = min(start[0], end[0])
    min_y = min(start[1], end[1])
    norm_start = (start[0] - min_x, start[1] - min_y)
    norm_end = (end[0] - min_x, end[1] - min_y)

    padding = _shadow_padding(style.shadow)
    key = ("line-v1", norm_start, norm_end, _freeze_value(style), padding)

    def _build() -> Image.Image:
        local_w = max(norm_start[0], norm_end[0]) + 1 + padding * 2
        local_h = max(norm_start[1], norm_end[1]) + 1 + padding * 2
        layer = Image.new("RGBA", (local_w, local_h), (0, 0, 0, 0))
        draw_line(
            layer,
            start=(norm_start[0] + padding, norm_start[1] + padding),
            end=(norm_end[0] + padding, norm_end[1] + padding),
            style=style,
        )
        return layer

    cached = SHAPE_COMPONENT_CACHE.get_or_create(key, _build)
    layer_pos = (min_x - padding, min_y - padding)
    if clip is not None:
        cached = apply_clip_region(cached, layer_pos, clip)
    image.alpha_composite(cached, layer_pos)


def draw_polygon_cached(
    image: Image.Image,
    vertices: list[tuple[int, int]],
    style: PolygonStyle | None = None,
    clip: ClipRegion | None = None,
) -> None:
    if style is None:
        style = PolygonStyle()

    if len(vertices) < 3:
        draw_polygon(image, vertices, style=style, clip=clip)
        return

    xs = [p[0] for p in vertices]
    ys = [p[1] for p in vertices]
    min_x, min_y = min(xs), min(ys)
    normalized = tuple((p[0] - min_x, p[1] - min_y) for p in vertices)

    padding = _shadow_padding(style.shadow)
    key = ("polygon-v1", normalized, _freeze_value(style), padding)

    def _build() -> Image.Image:
        local_w = max(p[0] for p in normalized) + 1 + padding * 2
        local_h = max(p[1] for p in normalized) + 1 + padding * 2
        layer = Image.new("RGBA", (local_w, local_h), (0, 0, 0, 0))
        shifted = [(p[0] + padding, p[1] + padding) for p in normalized]
        draw_polygon(layer, shifted, style=style)
        return layer

    cached = SHAPE_COMPONENT_CACHE.get_or_create(key, _build)
    layer_pos = (min_x - padding, min_y - padding)
    if clip is not None:
        cached = apply_clip_region(cached, layer_pos, clip)
    image.alpha_composite(cached, layer_pos)


def get_cached_shape_stats() -> dict[str, int]:
    return SHAPE_COMPONENT_CACHE.stats()


def clear_cached_shapes() -> None:
    SHAPE_COMPONENT_CACHE.clear()
