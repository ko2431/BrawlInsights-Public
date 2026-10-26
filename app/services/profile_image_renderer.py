from __future__ import annotations

import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.core.logger import logger
from app.services.map_mode_catalog import mode_icon_asset_relpaths
from app.services.brawl_service import Player, Brawler
from app.services.image_generation_service import ImageGenerationJobData
from app.services.text_renderer import (  # noqa: F401
    FONT_CONFIG,
    TextStyle,
    draw_text,
    draw_text_ellipsis,
    draw_text_multiline,
    get_font,
    measure_text,
)
from app.services.renderer_utils import (  # noqa: F401
    Anchor,
    BorderConfig,
    ClipRegion,
    Color,
    DropShadow,
    Fill,
    LinearGradient,
    parse_color,
    RadialGradient,
    RadiusConfig,
)
from app.services.render_cache import ImageComponentCache
from app.services.cached_shape_renderer import (
    draw_parallelogram_cached,
    draw_rect_cached,
    draw_rect_with_clip_cached,
    draw_line_cached,
)
from app.services.image_renderer import (  # noqa: F401
    IMAGES_DIR,
    ImageStyle,
    paste_image,
    paste_image_with_clip,
)
from app.services.shape_renderer import (  # noqa: F401
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
    draw_ellipse_with_clip,
    draw_line,
    draw_polygon,
    draw_rect,
    draw_star,
    draw_triangle,
)
# 重要：ヘルパー関数のマニュアルは notes_public/text_renderer_guide.md, image_renderer_guide.md, shape_renderer_guide.md を参照

GENERATED_ROOT_DIR = Path(__file__).resolve().parent.parent.parent / "generated"
PROFILE_IMAGE_OUTPUT_DIR = GENERATED_ROOT_DIR / "profile_images"

PROFILE_BASE_IMAGE_SIZES: dict[str, tuple[int, int]] = {
    "portrait": (1080, 650+400+15),
    "landscape": (2160, 330+200+15),
}

# 画像生成部品のローカルキャッシュ（プロセス内）
PROFILE_BACKGROUND_CACHE = ImageComponentCache(max_bytes=120 * 1024 * 1024, ttl_seconds=30 * 60)
PROFILE_HEADER_CACHE = ImageComponentCache(max_bytes=96 * 1024 * 1024, ttl_seconds=5 * 60)

def get_profile_image_size(orientation: str, brawler_grid_rows: int, brawler_card_height: int, brawler_grid_gap: int, footer_height: int) -> tuple[int, int]:
    base_size = PROFILE_BASE_IMAGE_SIZES.get(orientation, (0, 0))
    if base_size == (0, 0):
        raise ValueError(f"Invalid orientation: {orientation}")
    
    image_height = base_size[1] + brawler_grid_rows * brawler_card_height + (brawler_grid_rows - 1) * brawler_grid_gap + footer_height
    return (base_size[0], image_height)


def ensure_profile_image_output_dir() -> Path:
    PROFILE_IMAGE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return PROFILE_IMAGE_OUTPUT_DIR


def build_profile_image_filename(job: ImageGenerationJobData) -> str:
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
    normalized_tag = job.player_tag.replace("#", "").upper()
    return f"{normalized_tag}_{job.image_type}_{job.orientation}_{job.id}_{timestamp}.png"


def _build_blank_profile_image_template(size: tuple[int, int], background_image: int) -> Image.Image:
    # 1. background_image: int を 2桁の文字列に変換 (例: 1 -> '01')
    prefix = f"{background_image:02d}"

    # 2. フォルダ内からその2桁で始まる画像を探す
    bg_dir = IMAGES_DIR / "background"
    bg_files = list(bg_dir.glob(f"{prefix}_*.webp"))
    
    color_code = "000000" # デフォルト
    bg_path = None

    if bg_files:
        bg_path = bg_files[0]
        # 3. ファイル名の後半部分（カラーコード）を取り出す (例: '01_500532.webp' -> '500532')
        # stem は拡張子なしのファイル名。それを '_' で分割して最後の要素を取得。
        color_code = bg_path.stem.split("_")[-1]

    # 4. カラーコードの色で新しい画像を作成（不透明）
    bg_color = parse_color(f"#{color_code}")
    image = Image.new("RGBA", size, color=bg_color)

    # 5. 背景画像をペースト (width を canvas width に合わせ、height は auto)
    if bg_path:
        # paste_image は IMAGES_DIR からの相対パスを受け取れる
        relative_bg_path = f"background/{bg_path.name}"
        paste_image(
            image,
            path=relative_bg_path,
            xy=(0, 0),
            style=ImageStyle(width=size[0]),
            anchor=("left", "top")
        )
    
    # 6. 全面にすりガラス効果を持つ長方形を配置
    draw_rect(
        image,
        xy=(0, 0),
        width=size[0],
        height=size[1],
        style=RectStyle(
            fill="#fff",
            opacity=0.1,
            backdrop_blur=10
        ),
        anchor=("left", "top")
    )

    return image


def _create_blank_profile_image(size: tuple[int, int], background_image: int = 1) -> Image.Image:
    key = ("blank-profile", size[0], size[1], background_image)
    return PROFILE_BACKGROUND_CACHE.get_or_create(
        key,
        lambda: _build_blank_profile_image_template(size, background_image),
    )

# [この部分は公開用リポジトリでは非公開にされています]

def _render_standard_profile_image(job: ImageGenerationJobData, player: Player | None, available_brawlers: list[Brawler], num_of_available_brawlers: int, max_accessory_counts: dict[str, int], size: tuple[int, int]) -> Image.Image:
    image = _create_blank_profile_image(size, background_image=12)
    _render_common_header(job, image, player)
    _render_common_banner(job, image)
    _render_basic_data(job, image, player)
    _render_player_data(job, image, player, num_of_available_brawlers, max_accessory_counts)
    player.brawlers.sort(key=lambda b: (-b.acc_rank, -b.highest_trophies, -b.trophies, b.rarity is None, b.rarity, b.id))
    if job.orientation == "portrait":
        _render_brawler_grid(job, image, player, available_brawlers, base_x=14, base_y=650+345+15, card_width=205, card_height=80, gap=7, columns=5)
    else:
        _render_brawler_grid(job, image, player, available_brawlers, base_x=19, base_y=330+200+15, card_width=205, card_height=80, gap=8, columns=10)
    _render_common_footer(job, image)
    return image


def _render_highest_trophies_image(job: ImageGenerationJobData, player: Player | None, available_brawlers: list[Brawler], num_of_available_brawlers: int, max_accessory_counts: dict[str, int], size: tuple[int, int]) -> Image.Image:
    image = _create_blank_profile_image(size, background_image=9)
    _render_common_header(job, image, player)
    _render_common_banner(job, image)
    _render_basic_data(job, image, player)
    _render_player_data(job, image, player, num_of_available_brawlers, max_accessory_counts)
    player.brawlers.sort(key=lambda b: (-b.highest_trophies, -b.acc_rank, -b.trophies, b.rarity is None, b.rarity, b.id))
    if job.orientation == "portrait":
        _render_brawler_grid(job, image, player, available_brawlers, base_x=14, base_y=650+345+15, card_width=205, card_height=80, gap=7, columns=5)
    else:
        _render_brawler_grid(job, image, player, available_brawlers, base_x=19, base_y=330+200+15, card_width=205, card_height=80, gap=8, columns=10)
    _render_common_footer(job, image)
    return image


def _render_current_trophies_image(job: ImageGenerationJobData, player: Player | None, available_brawlers: list[Brawler], num_of_available_brawlers: int, max_accessory_counts: dict[str, int], size: tuple[int, int]) -> Image.Image:
    image = _create_blank_profile_image(size, background_image=3)
    _render_common_header(job, image, player)
    _render_common_banner(job, image)
    _render_basic_data(job, image, player)
    _render_player_data(job, image, player, num_of_available_brawlers, max_accessory_counts)
    player.brawlers.sort(key=lambda b: (-b.trophies, -b.acc_rank, -b.highest_trophies, b.rarity is None, b.rarity, b.id))
    if job.orientation == "portrait":
        _render_brawler_grid(job, image, player, available_brawlers, base_x=14, base_y=650+345+15, card_width=205, card_height=80, gap=7, columns=5)
    else:
        _render_brawler_grid(job, image, player, available_brawlers, base_x=19, base_y=330+200+15, card_width=205, card_height=80, gap=8, columns=10)
    _render_common_footer(job, image)
    return image


def _render_power_progress_image(job: ImageGenerationJobData, player: Player | None, available_brawlers: list[Brawler], num_of_available_brawlers: int, max_accessory_counts: dict[str, int], size: tuple[int, int]) -> Image.Image:
    image = _create_blank_profile_image(size, background_image=1)
    _render_common_header(job, image, player)
    _render_common_banner(job, image)
    _render_basic_data(job, image, player)
    _render_player_data(job, image, player, num_of_available_brawlers, max_accessory_counts)
    player.brawlers.sort(key=lambda b: (-b.power, -bool(b.hyper_charge_ids), -b.star_power, -b.gadget, -b.gear, b.rarity is None, b.rarity, b.id))
    if job.orientation == "portrait":
        _render_brawler_grid(job, image, player, available_brawlers, base_x=14, base_y=650+345+15, card_width=205, card_height=80, gap=7, columns=5)
    else:
        _render_brawler_grid(job, image, player, available_brawlers, base_x=19, base_y=330+200+15, card_width=205, card_height=80, gap=8, columns=10)
    _render_common_footer(job, image)
    return image


def _render_max_winstreak_image(job: ImageGenerationJobData, player: Player | None, available_brawlers: list[Brawler], num_of_available_brawlers: int, max_accessory_counts: dict[str, int], size: tuple[int, int]) -> Image.Image:
    image = _create_blank_profile_image(size, background_image=2)
    _render_common_header(job, image, player)
    _render_common_banner(job, image)
    _render_basic_data(job, image, player)
    _render_player_data(job, image, player, num_of_available_brawlers, max_accessory_counts)
    player.brawlers.sort(key=lambda b: (-(b.max_win_streak or 0), -(b.current_win_streak or 0), b.rarity is None, b.rarity, b.id))
    if job.orientation == "portrait":
        _render_brawler_grid(job, image, player, available_brawlers, base_x=14, base_y=650+345+15, card_width=205, card_height=80, gap=7, columns=5)
    else:
        _render_brawler_grid(job, image, player, available_brawlers, base_x=19, base_y=330+200+15, card_width=205, card_height=80, gap=8, columns=10)
    _render_common_footer(job, image)
    return image


def _render_current_winstreak_image(job: ImageGenerationJobData, player: Player | None, available_brawlers: list[Brawler], num_of_available_brawlers: int, max_accessory_counts: dict[str, int], size: tuple[int, int]) -> Image.Image:
    image = _create_blank_profile_image(size, background_image=7)
    _render_common_header(job, image, player)
    _render_common_banner(job, image)
    _render_basic_data(job, image, player)
    _render_player_data(job, image, player, num_of_available_brawlers, max_accessory_counts)
    player.brawlers.sort(key=lambda b: (-(b.current_win_streak or 0), -(b.max_win_streak or 0), b.rarity is None, b.rarity, b.id))
    if job.orientation == "portrait":
        _render_brawler_grid(job, image, player, available_brawlers, base_x=14, base_y=650+345+15, card_width=205, card_height=80, gap=7, columns=5)
    else:
        _render_brawler_grid(job, image, player, available_brawlers, base_x=19, base_y=330+200+15, card_width=205, card_height=80, gap=8, columns=10)
    _render_common_footer(job, image)
    return image


def _render_legacy_mastery_image(job: ImageGenerationJobData, player: Player | None, available_brawlers: list[Brawler], num_of_available_brawlers: int, max_accessory_counts: dict[str, int], size: tuple[int, int]) -> Image.Image:
    image = _create_blank_profile_image(size, background_image=5)
    _render_common_header(job, image, player)
    _render_common_banner(job, image)
    _render_basic_data(job, image, player)
    _render_player_data(job, image, player, num_of_available_brawlers, max_accessory_counts)
    player.brawlers.sort(key=lambda b: (-(b.mastery or 0), b.rarity is None, b.rarity, b.id))
    if job.orientation == "portrait":
        _render_brawler_grid(job, image, player, available_brawlers, base_x=14, base_y=650+345+15, card_width=205, card_height=80, gap=7, columns=5)
    else:
        _render_brawler_grid(job, image, player, available_brawlers, base_x=19, base_y=330+200+15, card_width=205, card_height=80, gap=8, columns=10)
    _render_common_footer(job, image)
    return image


def _render_equipment_skins_image(job: ImageGenerationJobData, player: Player | None, available_brawlers: list[Brawler], num_of_available_brawlers: int, max_accessory_counts: dict[str, int], size: tuple[int, int]) -> Image.Image:
    image = _create_blank_profile_image(size, background_image=13)
    _render_common_header(job, image, player)
    _render_common_banner(job, image)
    _render_basic_data(job, image, player)
    _render_player_data(job, image, player, num_of_available_brawlers, max_accessory_counts)
    player.brawlers.sort(key=lambda b: (b.rarity is None, b.rarity, b.id))
    _render_brawler_grid(job, image, player, available_brawlers, base_x=16, base_y=330+200+15, card_width=170, card_height=180, gap=8, columns=12)
    _render_common_footer(job, image)
    return image


def render_profile_image(job: ImageGenerationJobData, player: Player | None, available_brawlers: list[Brawler], num_of_available_brawlers: int, max_accessory_counts: dict[str, int]) -> tuple[str, str]:
    ensure_profile_image_output_dir()
    filename = build_profile_image_filename(job)
    output_path = PROFILE_IMAGE_OUTPUT_DIR / filename

    brawler_grid_columns = 5 if job.orientation == "portrait" else 10
    brawler_grid_rows = (num_of_available_brawlers + brawler_grid_columns - 1) // brawler_grid_columns

    brawler_card_height = 80
    brawler_grid_gap = 7 if job.orientation == "portrait" else 8
    footer_height = 40

    match job.image_type:
        case "standard_profile":
            size = get_profile_image_size(job.orientation, brawler_grid_rows, brawler_card_height, brawler_grid_gap, footer_height)
            image = _render_standard_profile_image(job, player, available_brawlers, num_of_available_brawlers, max_accessory_counts, size)
        case "highest_trophies":
            size = get_profile_image_size(job.orientation, brawler_grid_rows, brawler_card_height, brawler_grid_gap, footer_height)
            image = _render_highest_trophies_image(job, player, available_brawlers, num_of_available_brawlers, max_accessory_counts, size)
        case "current_trophies":
            size = get_profile_image_size(job.orientation, brawler_grid_rows, brawler_card_height, brawler_grid_gap, footer_height)
            image = _render_current_trophies_image(job, player, available_brawlers, num_of_available_brawlers, max_accessory_counts, size)
        case "power_progress":
            size = get_profile_image_size(job.orientation, brawler_grid_rows, brawler_card_height, brawler_grid_gap, footer_height)
            image = _render_power_progress_image(job, player, available_brawlers, num_of_available_brawlers, max_accessory_counts, size)
        case "max_winstreak":
            size = get_profile_image_size(job.orientation, brawler_grid_rows, brawler_card_height, brawler_grid_gap, footer_height)
            image = _render_max_winstreak_image(job, player, available_brawlers, num_of_available_brawlers, max_accessory_counts, size)
        case "current_winstreak":
            size = get_profile_image_size(job.orientation, brawler_grid_rows, brawler_card_height, brawler_grid_gap, footer_height)
            image = _render_current_winstreak_image(job, player, available_brawlers, num_of_available_brawlers, max_accessory_counts, size)
        case "legacy_mastery":
            size = get_profile_image_size(job.orientation, brawler_grid_rows, brawler_card_height, brawler_grid_gap, footer_height)
            image = _render_legacy_mastery_image(job, player, available_brawlers, num_of_available_brawlers, max_accessory_counts, size)
        case "equipment_skins":
            # 特別なキャラカード高さを設定する
            brawler_card_height = 180
            brawler_grid_columns = 12
            brawler_grid_rows = (num_of_available_brawlers + brawler_grid_columns - 1) // brawler_grid_columns
            size = get_profile_image_size(job.orientation, brawler_grid_rows, brawler_card_height, brawler_grid_gap, footer_height)
            image = _render_equipment_skins_image(job, player, available_brawlers, num_of_available_brawlers, max_accessory_counts, size)
        case _:
            raise ValueError(f"未対応のプロフィール画像種別です: {job.image_type}")

    image.save(output_path, format="PNG")
    logger.debug(f"プロフィール画像を生成しました。job_id={job.id}, path={output_path}")
    return filename, f"/generated/profile_images/{filename}"
