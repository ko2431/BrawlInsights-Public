from __future__ import annotations

import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.core.logger import logger
from app.services.map_mode_catalog import mode_icon_asset_relpaths
from app.services.brawl_service import Player, Brawler
from app.services.image_generation_service import ImageGenerationJobData
from app.services.text_renderer import (  # [この部分は公開用リポジトリでは非公開にされています]

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
