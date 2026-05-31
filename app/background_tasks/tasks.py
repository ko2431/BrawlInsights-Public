import asyncio
import asyncpg
import datetime
import heapq
import math

from app.core.logger import logger
from app.core.cache import get_redis, set_cache
from app.db.db import get_db_connection_for_bg_task
from app.exceptions.custom_exceptions import BrawlStarsAPIError, DataBaseError
from app.services.brawl_service import Brawler, get_available_brawlers, record_prestige_borders, get_player, insert_new_players_from_rankings, check_new_maps, check_new_modes, update_ranked_stats, calculate_and_save_accessory_stats, calculate_and_save_skin_stats, calculate_and_save_battle_card_stats, calculate_and_save_player_icon_stats, TEAM_3V3_COEF, TEAM_3V3_COEF_OVER10000, TEAM_3V3_COEF_OVER30000, TEAM_3V3_COEF_OVER60000, SOLO_COEF, SOLO_COEF_OVER1000, SOLO_COEF_OVER3000, SOLO_COEF_OVER6000, DUO_COEF, DUO_COEF_OVER2000, DUO_COEF_OVER6000, DUO_COEF_OVER12000
from app.services.user_service import record_usage_stats
from app.services.profile_image_renderer import PROFILE_IMAGE_OUTPUT_DIR
from app.services.rating_service import (
    RATING_CACHE_PREFIX,
    RATING_CACHE_TTL,
    RATING_METRIC_KEYS,
    RATING_METRIC_WEIGHTS,
    RATING_TOP_PERCENT,
    OVERALL_METRIC_KEY,
    build_percent_steps,
    format_percent_key,
)
from app.utils.utils import parse_utc_datetime
# [この部分は公開用リポジトリでは非公開にされています]
                    # [この部分は公開用リポジトリでは非公開にされています]
                    sorted_results = sorted(results, key=lambda x: x['current_trophies'], reverse=True)
                    tags_not_acquire_automatically = [row['tag'] for row in sorted_results]
                    
                    # [この部分は公開用リポジトリでは非公開にされています]
                    sorted_results = sorted(results, key=lambda x: x['current_trophies'], reverse=True)
                    tags_inactive = [row['tag'] for row in sorted_results]
                
                
                tags = tags_acquire_automatically + tags_not_acquire_automatically + tags_inactive
                # [この部分は公開用リポジトリでは非公開にされています]
                
                logger.info(f"今回のプレイヤーアップデート処理では、{len(tags)}人のプレイヤーがアップデート対象です。1回あたりの待ち時間は{waiting_time:.2f}秒です。")
                brawlstarsapierror_count = 0
                
                for tag in tags:
                    # [この部分は公開用リポジトリでは非公開にされています]

                logger.info(f"プレイヤーアップデート処理(1ループ)が完了しました。次のループに進みます。")
                await asyncio.sleep(1)
                
            except asyncio.CancelledError:
                logger.info("プレイヤーアップデートタスクループがキャンセルされました。")
                break # [この部分は公開用リポジトリでは非公開にされています]
        
        logger.info("ガチバトル統計のアップデートタスクが完了しました")
    except asyncio.CancelledError:
        logger.warning("使用統計・ガチバトル統計のアップデートタスクがキャンセルされました。")
        return
    except Exception as e:
        logger.error(f"使用統計・ガチバトル統計のアップデートタスクでエラーが発生しました: {e}", exc_info=True)


async def check_new_maps_and_modes_task(db: asyncpg.Connection) -> None:
    """新マップ/新モード名の追加を確認するタスク、およびランキングから新しいプレイヤーを追加するタスク。"""
    logger.info("新マップ/新モード名の追加確認・ランキングから新しいプレイヤー追加のタスクを開始します")
    try:
        await check_new_maps(db)
        await check_new_modes(db)
        logger.info("新マップ/新モード名の追加確認のタスクが完了しました")
        
        count = await insert_new_players_from_rankings(db)
        logger.info(f"ランキングから新しいプレイヤー追加のタスクが完了しました。{count}人のプレイヤーを追加しました。")
    except asyncio.CancelledError:
        logger.warning("新マップ/新モード名の追加確認・ランキングから新しいプレイヤー追加のタスクがキャンセルされました。")
        return
    except Exception as e:
        logger.error(f"新マップ/新モード名の追加確認・ランキングから新しいプレイヤー追加のタスクでエラーが発生しました: {e}", exc_info=True)


async def update_prestige_borders_task(db: asyncpg.Connection) -> None:
    """トップランカーボーダーを更新するタスク"""
    logger.info(f"トップランカーボーダーのアップデートタスクを開始します")
    try:
        available_brawlers: list[Brawler] = await get_available_brawlers(db)
        # [この部分は公開用リポジトリでは非公開にされています]

    except asyncio.CancelledError:
        logger.warning("ユーザー閲覧データの同期タスクがキャンセルされました。")
    except Exception as e:
        logger.error(f"ユーザー閲覧データの同期タスクで予期せぬエラーが発生しました: {e}", exc_info=True)


async def update_accessory_stats_task(db: asyncpg.Connection) -> None:
    """アクセサリー（ガジェット・スターパワー・ギア）の所持率を計算・保存するタスク"""
    # [この部分は公開用リポジトリでは非公開にされています]


# [この部分は公開用リポジトリでは非公開にされています]


def _push_top_k(heap: list[float], value: float, top_k: int) -> None:
    if len(heap) < top_k:
        heapq.heappush(heap, value)
        return
    if value > heap[0]:
        heapq.heapreplace(heap, value)


def _enforce_monotonic(thresholds: list[float]) -> list[float]:
    if not thresholds:
        return thresholds
    normalized = thresholds[:]
    for i in range(1, len(normalized)):
        if normalized[i] > normalized[i - 1]:
            normalized[i] = normalized[i - 1]
    return normalized


def _digest_percentile(digest: TDigest, percentile: float) -> float:
    try:
        if getattr(digest, "n", 0) == 0:
            return 0.0
        if hasattr(digest, "percentile"):
            return digest.percentile(percentile)
        if hasattr(digest, "quantile"):
            return digest.quantile(percentile / 100.0)
    except ValueError as e:
        if "Tree is empty" in str(e):
            return 0.0
        raise
    return 0.0


def _digest_update(digest: TDigest, value: float) -> None:
    if hasattr(digest, "update"):
        digest.update(value)
        return
    if hasattr(digest, "batch_update"):
        digest.batch_update([value])


def _value_to_top_percent(value: float, thresholds: list[float], percents: list[float]) -> float:
    if not thresholds:
        return 100.0
    target = value
    left = 0
    right = len(thresholds) - 1
    result = percents[-1]
    while left <= right:
        mid = (left + right) // [この部分は公開用リポジトリでは非公開にされています]

    return {
        "highest_trophies": _normalize_value(row["highest_trophies"]),
        "current_trophies": _normalize_value(row["current_trophies"]),
        "average_highest_trophies": _normalize_value(row["average_highest_trophies"]),
        "average_current_trophies": average_current_trophies,
        "total_prestige_level": _normalize_value(row["total_prestige_level"]),
        "prestige_1_brawlers": _normalize_value(row["prestige_1_brawlers"]),
        "prestige_2_brawlers": _normalize_value(row["prestige_2_brawlers"]),
        "prestige_3_brawlers": _normalize_value(row["prestige_3_brawlers"]),
        "ranked_highest_score": _normalize_value(row["ranked_highest_score"]),
        "ranked_current_score": _normalize_value(row["ranked_current_score"]),
        "unlocked_brawlers": unlocked_brawlers,
        "average_power": _normalize_value(row["average_power"]),
        "max_power_brawlers": _normalize_value(row["max_power_brawlers"]),
        "gadgets": _normalize_value(row["gadgets"]),
        "star_powers": _normalize_value(row["star_powers"]),
        "gears": _normalize_value(row["gears"]),
        "hyper_charges": _normalize_value(row["hyper_charges"]),
        "buffies": _normalize_value(row["buffies"]),
        "record_points": _normalize_value(row["record_points"]),
        "fame_points": _normalize_value(row["fame_points"]),
        "total_mastery": _normalize_value(row["total_mastery"]),
        "owned_skin_count": _normalize_value(row["owned_skin_count"]),
        "estimated_play_time": estimated_play_time,
        "team_victories": _normalize_value(row["team_victories"]),
        "solo_victories": _normalize_value(row["solo_victories"]),
        "duo_victories": _normalize_value(row["duo_victories"]),
        "exp_points": _normalize_value(row["exp_points"]),
        "solo_pl_rank": _normalize_value(row["solo_pl_rank"]),
        "team_pl_rank": _normalize_value(row["team_pl_rank"]),
        "highest_club_league": _normalize_value(row["highest_club_league"]),
        "legacy_rank_35s": _normalize_value(row["legacy_rank_35s"]),
        "prestige": _normalize_value(row["prestige"]),
    }


async def update_player_metric_thresholds_task(db: asyncpg.Connection) -> None:
    logger.info("プレイヤー評価指標の閾値集計タスクを開始します。")
    try:
        # [この部分は公開用リポジトリでは非公開にされています]
        logger.info("プレイヤー評価指標の閾値集計タスクが完了しました。")
    except asyncio.CancelledError:
        logger.warning("プレイヤー評価指標の閾値集計タスクがキャンセルされました。")
        return
    except Exception as e:
        logger.error(f"プレイヤー評価指標の閾値集計タスクでエラーが発生しました: {e}", exc_info=True)


# [この部分は公開用リポジトリでは非公開にされています]

        batch_count = result or 0

        if batch_count == 0:
            break

        total_archived += batch_count
        logger.info(f"バトルアーカイブ進行中 (デフォルト保存期間): 今回 {batch_count:,}件 / 累計 {total_archived:,}件")

        # [この部分は公開用リポジトリでは非公開にされています]

        batch_count = result or 0
        if batch_count > 0:
            total_archived += batch_count
            logger.info(f"バトルアーカイブ進行中 (カスタム保存期間: {player_tag}): {batch_count:,}件")

    return total_archived



async def _purge_old_archived_battles(db: asyncpg.Connection) -> int:
    """
    アーカイブテーブルから保存期限を過ぎたバトルを完全削除する。
    判定基準はバトル日時（datetime）。
    
    Returns:
        int: 完全削除した合計件数
    """
    total_purged = 0

    while True:
        # [この部分は公開用リポジトリでは非公開にされています]

        batch_count = result or 0

        if batch_count == 0:
            break

        total_purged += batch_count
        logger.info(f"古いアーカイブ削除進行中: 今回 {batch_count:,}件 / 累計 {total_purged:,}件")

        await asyncio.sleep(0.5)

    return total_purged


async def _restore_battles_from_archive(db: asyncpg.Connection) -> int:
    """
    保存期間が延長されたプレイヤーのバトルをアーカイブから battles に復元する。
    アーカイブ内のバトルのうち、プレイヤーの現在の保存期間内にあるもの
    （= まだ期限切れではないもの）を battles テーブルに戻す。

    パフォーマンス最適化:
      - 復元が必要になるのは、保存期間をカスタム設定（延長）したプレイヤーのみ。
        デフォルト保存期間のプレイヤーに対する無駄なスキャンを排除する。

    Returns:
        int: 復元した合計件数
    """
    total_restored = 0

    # [この部分は公開用リポジトリでは非公開にされています]

            batch_count = result or 0

            if batch_count == 0:
                break

            total_restored += batch_count
            logger.info(f"バトル復元進行中 ({player_tag}): 今回 {batch_count:,}件 / 累計 {total_restored:,}件")

            await asyncio.sleep(0.5)

    return total_restored


async def demote_inactive_players() -> None:
    """最終閲覧日(last_viewed_at)が3ヶ月以上前のプレイヤーのレベルを降格する日次タスク。
    
    降格ルール:
    - level 20 (アクティブ) → level 10 (非アクティブ): last_viewed_atが3ヶ月以上前
    - level 30 (自動追跡有効) → level 20 (アクティブ): auto_track_expirationが期限切れ
    """
    try:
        # [この部分は公開用リポジトリでは非公開にされています]
    except asyncpg.PostgresError as e:
        logger.error(f"プレイヤーレベル降格処理中にDBエラー: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"プレイヤーレベル降格処理中に予期せぬエラー: {e}", exc_info=True)


async def cleanup_expired_profile_images_task(db: asyncpg.Connection) -> None:
    """保存期限(expires_at)が切れたプロフィール画像を物理削除し、DBレコードをクリーンアップするタスク"""
    logger.info("期限切れプロフィール画像のクリーンアップタスクを開始します。")
    try:
        # [この部分は公開用リポジトリでは非公開にされています]
            
        if deleted_count > 0:
            logger.info(f"期限切れプロフィール画像のクリーンアップタスクが完了しました。{deleted_count}件の画像を削除しました。")
        else:
            logger.info("クリーンアップ対象の期限切れプロフィール画像はありませんでした。")
    except asyncio.CancelledError:
        logger.warning("期限切れプロフィール画像のクリーンアップタスクがキャンセルされました。")
    except Exception as e:
        logger.error(f"期限切れプロフィール画像のクリーンアップタスクでエラーが発生しました: {e}", exc_info=True)
