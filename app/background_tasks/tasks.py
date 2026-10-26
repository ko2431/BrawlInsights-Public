import asyncio
import asyncpg
import datetime
import heapq
import math
from typing import Any

from app.core.logger import logger
from app.core.config import settings
from app.core.cache import get_redis, set_cache, is_transient_redis_error, log_transient_redis_warning
from app.db.db import get_db_connection_for_bg_task, is_transient_pg_error, log_transient_pg_warning
from app.exceptions.custom_exceptions import BrawlStarsAPIError, DataBaseError
from app.services.brawl_service import Brawler, get_available_brawlers, record_prestige_borders, get_player, insert_new_players_from_rankings, update_ranked_stats, calculate_and_save_accessory_stats, calculate_and_save_skin_stats, calculate_and_save_battle_card_stats, calculate_and_save_player_icon_stats, TEAM_3V3_COEF, TEAM_3V3_COEF_OVER10000, TEAM_3V3_COEF_OVER30000, TEAM_3V3_COEF_OVER60000, SOLO_COEF, SOLO_COEF_OVER1000, SOLO_COEF_OVER3000, SOLO_COEF_OVER6000, DUO_COEF, DUO_COEF_OVER2000, DUO_COEF_OVER6000, DUO_COEF_OVER12000
# [この部分は公開用リポジトリでは非公開にされています]
from app.services.map_mode_catalog import fill_slugs_from_legacy_modes, sync_maps_and_modes_from_bsinfo
# [この部分は公開用リポジトリでは非公開にされています]
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
# pyrefly: ignore [missing-import]
from tdigest import TDigest

async def player_update_task_loop(ctx=None) -> None:
    """プレイヤーデータを定期的に更新するループタスク（内部で接続管理）"""
    logger.info("プレイヤーアップデートタスクループを開始します。")
    db: asyncpg.Connection | None = None # DB接続変数を初期化

    try:
        while True:
            try:
                logger.info("プレイヤーアップデート処理のループを開始します...")
                # リストを初期化 (念のため)
                tags_acquire_automatically = []
                tags_not_acquire_automatically = []
                tags_inactive = []
                tags = []
                
                # まず、今回のループでアップデートするプレイヤーのリストを取得。取得するのは無効ではなく、閲覧されたことのあるプレイヤーのみ。3種類に分かれる。
                async with get_db_connection_for_bg_task() as db: # ループ開始前に接続
                    #* 自動追跡機能が有効なプレイヤー (level >= 30)
                    # [この部分は公開用リポジトリでは非公開にされています]
                    # ループごとのリスト内順番のばらつきを減らすため、現在トロフィー順に並び替え
                    sorted_results = sorted(results, key=lambda x: x[1], reverse=True)
                    # タグだけを取り出してリストに入れる
                    tags_acquire_automatically = [row['tag'] for row in sorted_results]
                    
                    #* アクティブで、自動追跡機能が無効なプレイヤー (level = 20)
                    # [この部分は公開用リポジトリでは非公開にされています]
                    sorted_results = sorted(results, key=lambda x: x['current_trophies'], reverse=True)
                    tags_not_acquire_automatically = [row['tag'] for row in sorted_results]
                    
                    #* 非アクティブで、自動追跡機能が無効なプレイヤー (level = 10)
                    # [この部分は公開用リポジトリでは非公開にされています]
                    sorted_results = sorted(results, key=lambda x: x['current_trophies'], reverse=True)
                    tags_inactive = [row['tag'] for row in sorted_results]
                
                
                tags = tags_acquire_automatically + tags_not_acquire_automatically + tags_inactive
                # [この部分は公開用リポジトリでは非公開にされています]
                
                logger.info(f"今回のプレイヤーアップデート処理では、{len(tags)}人のプレイヤーがアップデート対象です。1回あたりの待ち時間は{waiting_time:.2f}秒です。")
                if ctx is not None:
                    await ctx.set_progress(
                        step="updating",
                        current=0,
                        total=len(tags),
                        waiting_time_sec=round(waiting_time, 2),
                        message=f"0/{len(tags)}人",
                    )
                brawlstarsapierror_count = 0
                
                for index, tag in enumerate(tags, start=1):
                    # [この部分は公開用リポジトリでは非公開にされています]

                logger.info(f"プレイヤーアップデート処理(1ループ)が完了しました。次のループに進みます。")
                await asyncio.sleep(1)
                
            except asyncio.CancelledError:
                logger.info("プレイヤーアップデートタスクループがキャンセルされました。")
                raise
            
            except asyncpg.PostgresError as db_err:
                logger.error(f"プレイヤーアップデートタスクループ DBエラー: {db_err}", exc_info=True)
                # DBエラーによっては接続再確立を試みるなどの処理も考えられる
                await asyncio.sleep(60) # 少し待って継続試行
                
            except Exception as e:
                logger.error(f"プレイヤーアップデートタスクループで予期せぬエラー: {e}", exc_info=True)
                await asyncio.sleep(60) # 少し待って継続試行
                
    finally:
        logger.info("プレイヤーアップデートタスクループが終了しました。")


async def update_usage_stats_task(db: asyncpg.Connection, ctx=None) -> None:
    """アプリの日次統計スナップショットを記録する。旧キー usage_stats の互換ハンドラでもある。"""
    logger.info("使用統計のアップデートタスクを開始します")
    try:
        if ctx is not None:
            await ctx.set_progress(step="usage_stats", message="アプリ統計を記録中です")
        await record_usage_stats(db)
        logger.info("使用統計のアップデートタスクが完了しました")
    except asyncio.CancelledError:
        logger.warning("使用統計のアップデートタスクがキャンセルされました。")
        raise
    except Exception as e:
        logger.error(f"使用統計のアップデートタスクでエラーが発生しました: {e}", exc_info=True)
        raise


# [この部分は公開用リポジトリでは非公開にされています]


# [この部分は公開用リポジトリでは非公開にされています]


async def check_new_maps_and_modes_task(db: asyncpg.Connection, ctx=None) -> None:
    """BSInfoからマップ/モードを同期する。旧キー maps_modes_and_rankings では続けてランキング追加も行う。"""
    logger.info("マップ/モード同期タスクを開始します")
    try:
        if ctx is not None:
            await ctx.set_progress(step="maps_modes", message="マップ/モードを同期中です")
        sync_result = await sync_maps_and_modes_from_bsinfo(db)
        logger.info(f"マップ/モード同期が完了しました: {sync_result}")
        slug_filled = await fill_slugs_from_legacy_modes(db)
        if slug_filled:
            logger.info(f"legacy slug を {slug_filled} 件補完しました")
        logger.info("マップ/モード同期タスクが完了しました")
    except asyncio.CancelledError:
        logger.warning("マップ/モード同期タスクがキャンセルされました。")
        raise
    except Exception as e:
        logger.error(f"マップ/モード同期タスクでエラーが発生しました: {e}", exc_info=True)
        raise


# [この部分は公開用リポジトリでは非公開にされています]


# [この部分は公開用リポジトリでは非公開にされています]


# [この部分は公開用リポジトリでは非公開にされています]


async def update_prestige_borders_task(db: asyncpg.Connection) -> None:
    """トップランカーボーダーを更新するタスク"""
    logger.info(f"トップランカーボーダーのアップデートタスクを開始します")
    try:
        available_brawlers: list[Brawler] = await get_available_brawlers(db)
        # ボーダーを記録
        await record_prestige_borders(available_brawlers, db)
        
        logger.info(f"トップランカーボーダーのアップデートタスクが完了しました")
    except asyncio.CancelledError:
        logger.warning("トップランカーボーダーのアップデートタスクがキャンセルされました。")
        raise
    except Exception as e:
        logger.error(f"トップランカーボーダーのアップデートタスクでエラーが発生しました: {e}", exc_info=True)
        raise
        

async def sync_user_view_data_from_redis(db: asyncpg.Connection) -> None:
    """
    Redisに蓄積されたユーザーのPVカウントと最終閲覧日時をDBに同期するタスク。
    """
    logger.debug("Redisからのユーザー閲覧データ同期タスクを開始します。")
    redis_client = get_redis()
    if not redis_client:
        logger.error("Redis接続が利用できないため、ユーザー閲覧データの同期をスキップします。")
        return

    pv_counts_key = "user:pv_counts"
    last_viewed_key = "user:last_viewed"

    try:
        # [この部分は公開用リポジトリでは非公開にされています]

    except asyncio.CancelledError:
        logger.warning("ユーザー閲覧データの同期タスクがキャンセルされました。")
        raise
    except Exception as e:
        if is_transient_redis_error(e) or is_transient_pg_error(e):
            if is_transient_redis_error(e):
                log_transient_redis_warning(
                    f"ユーザー閲覧データの同期タスクで一時的な接続エラー: {e}"
                )
            else:
                log_transient_pg_warning(
                    f"ユーザー閲覧データの同期タスクで一時的な接続エラー: {e}"
                )
        else:
            logger.error(f"ユーザー閲覧データの同期タスクで予期せぬエラーが発生しました: {e}", exc_info=True)
        raise


async def update_accessory_stats_task(db: asyncpg.Connection, ctx=None) -> None:
    """アクセサリー（ガジェット・スターパワー・ギア）の所持率を計算・保存するタスク"""
    # [この部分は公開用リポジトリでは非公開にされています]


#* /---*---*---*---*---*---*---*---*/
#* プレイヤー評価指標の閾値集計
#* /---*---*---*---*---*---*---*---*/

RATING_METHOD = "hybrid-top1pct-tdigest"
OWNED_SKIN_MIN_LEVEL = 20


def _normalize_value(value: float | int | None) -> float:
    if value is None:
        return 0.0
    try:
        if value < 0 or not math.isfinite(value):
            return 0.0
    except TypeError:
        return 0.0
    return float(value)


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
        mid = (left + right) // 2
        if target >= thresholds[mid]:
            result = percents[mid]
            right = mid - 1
        else:
            left = mid + 1
    return result


def _build_thresholds(
    percents: list[float],
    population: int,
    top_values: list[float],
    digest: TDigest
) -> list[float]:
    if population <= 0:
        return [0.0 for _ in percents]

    sorted_top = sorted(top_values, reverse=True)
    thresholds: list[float] = []
    for percent in percents:
        if percent <= RATING_TOP_PERCENT:
            rank = max(1, math.ceil(population * (percent / 100.0)))
            index = min(rank - 1, len(sorted_top) - 1)
            value = sorted_top[index] if sorted_top else 0.0
        else:
            value = _digest_percentile(digest, 100.0 - percent)
        thresholds.append(_normalize_value(value))
    return _enforce_monotonic(thresholds)


def _extract_metric_values(row: asyncpg.Record) -> dict[str, float]:
    temp_brawlers = _normalize_value(row["temp_brawlers"])
    unlocked_brawlers = _normalize_value(row["unlocked_brawlers"])
    denom = unlocked_brawlers - temp_brawlers
    average_current_trophies = 0.0
    if denom > 0:
        average_current_trophies = _normalize_value(row["current_trophies"]) / denom

    # [この部分は公開用リポジトリでは非公開にされています]

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
        raise
    except Exception as e:
        logger.error(f"プレイヤー評価指標の閾値集計タスクでエラーが発生しました: {e}", exc_info=True)
        raise


#* /---*---*---*---*---*---*---*---*/
#* バトル履歴アーカイブ
#* /---*---*---*---*---*---*---*---*/

# 1バッチあたりの処理件数
ARCHIVE_BATCH_SIZE = 50_000
# 削除も移管と同じバッチサイズ。50,000件はバックアップ外なら十数秒で完了する
PURGE_BATCH_SIZE = 50_000
# 1日あたりの最大アーカイブ件数
ARCHIVE_DAILY_LIMIT = 10_000_000
# 1日あたりの最大削除件数。バックアップ後の枠で終わらせる
PURGE_DAILY_LIMIT = 5_000_000
# 移管・削除クエリのコマンドタイムアウト（秒）。プール既定は 60 秒
ARCHIVE_QUERY_TIMEOUT = 300.0
# アーカイブ処理の開始日 (UTC)。この日時以降に初めてアーカイブが実行される。
# JST 2026-03-06 02:00 = UTC 2026-03-05 17:00
ARCHIVE_ACTIVATION_DATE = datetime.datetime(2026, 3, 5, 17, 0, tzinfo=datetime.timezone.utc)


async def archive_expired_battles_task(db: asyncpg.Connection) -> None:
    """保存期限切れのバトルをアーカイブテーブルに移管するタスク。削除は別ジョブ。"""
    # 開始日チェック: 開始日より前であればスキップ
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    if now_utc < ARCHIVE_ACTIVATION_DATE:
        logger.info(
            f"バトルアーカイブタスク: 開始日 ({ARCHIVE_ACTIVATION_DATE.isoformat()}) "
            f"に達していないためスキップします。"
        )
        return

    # Redisで排他ロック（二重実行防止）
    redis_client = get_redis()
    if not redis_client:
        raise RuntimeError("Redis接続が利用できないため、バトル履歴アーカイブタスクを実行できません。")
    lock_key = "lock:archive_expired_battles"
    lock_acquired = await redis_client.set(lock_key, "1", nx=True, ex=7200)  # TTL 2時間

    if not lock_acquired:
        raise RuntimeError("バトルアーカイブの Redis ロックを取得できませんでした。")

    logger.info("バトル履歴アーカイブタスクを開始します。")
    try:
        # 1. 保存期間が延長されたプレイヤーのバトルをアーカイブから復元
        total_restored = await _restore_battles_from_archive(db)
        logger.info(f"バトル復元完了: {total_restored:,}件をbattlesテーブルに復元しました。")

        # 2. 期限切れバトルをアーカイブテーブルに移管
        #    古いアーカイブの削除はバックアップ後の purge_old_archived_battles_task に分離
        total_archived = await _archive_expired_battles(db)
        logger.info(f"バトルアーカイブ移管完了: {total_archived:,}件をアーカイブしました。")

    except asyncio.CancelledError:
        logger.warning("バトル履歴アーカイブタスクがキャンセルされました。")
        raise
    except Exception as e:
        logger.error(f"バトル履歴アーカイブタスクでエラーが発生しました: {e}", exc_info=True)
        raise
    finally:
        try:
            await redis_client.delete(lock_key)
        except Exception:
            # シャットダウン時に既にRedis接続が閉じている可能性があるため、エラーは無視する
            pass


async def _archive_expired_battles(db: asyncpg.Connection) -> int:
    """
    保存期限切れのバトルを battles → archived_battles に移管する。
    プレイヤーごとの保存期間（battle_log_retention_months）を考慮し、
    NULLの場合はシステムデフォルト（settings.DEFAULT_BATTLE_LOG_RETENTION_MONTHS）を適用する。

    パフォーマンス最適化:
      - カスタム保存期間のプレイヤーのタグを事前取得し、メインクエリから除外する。
        これにより相関サブクエリを排除し、idx_battles_datetime を直接活用する。
      - カスタム保存期間のプレイヤーは別途処理する。

    Returns:
        int: アーカイブした合計件数
    """
    total_archived = 0

    # カスタム保存期間設定ありのプレイヤーのタグを事前取得（ごく少数）
    custom_retention_rows = await db.fetch("""
        SELECT tag, battle_log_retention_months
        FROM players
        WHERE battle_log_retention_months IS NOT NULL
    """)
    custom_retention_tags = [row['tag'] for row in custom_retention_rows]

    default_cutoff = await db.fetchval(
        "SELECT NOW() - make_interval(months => $1)",
        settings.DEFAULT_BATTLE_LOG_RETENTION_MONTHS,
    )
    logger.info(f"デフォルト保存期間の移管開始: cutoff={default_cutoff}")

    # ========================================
    # Phase 1: デフォルト保存期間のプレイヤー
    # ========================================
    # datetime の定数比較のみ → idx_battles_datetime を直接活用
    # NOW() を毎バッチ評価すると期限切れ境界が動き、終盤に数件バッチが1時間続く
    timed_out = False
    while total_archived < ARCHIVE_DAILY_LIMIT:
        try:
            # [この部分は公開用リポジトリでは非公開にされています]
        except TimeoutError:
            logger.error(
                f"バトルアーカイブ移管がタイムアウトしました (デフォルト保存期間)。"
                f"本日は {total_archived:,}件まで移管して中断します。"
            )
            timed_out = True
            break

        batch_count = result or 0

        if batch_count == 0:
            break

        total_archived += batch_count
        logger.info(f"バトルアーカイブ進行中 (デフォルト保存期間): 今回 {batch_count:,}件 / 累計 {total_archived:,}件")

        # DB負荷軽減のためスリープ
        await asyncio.sleep(0.5)

    # ========================================
    # Phase 2: カスタム保存期間のプレイヤー
    # ========================================
    # 対象は少数だが、1プレイヤーが ARCHIVE_BATCH_SIZE を超える期限切れを
    # 持つ場合に備え、復元・削除と同じくバッチを回し切る
    if not timed_out:
        for row in custom_retention_rows:
            if total_archived >= ARCHIVE_DAILY_LIMIT:
                break

            player_tag = row['tag']
            retention_months = row['battle_log_retention_months']
            player_cutoff = await db.fetchval(
                "SELECT NOW() - make_interval(months => $1)",
                retention_months,
            )

            while total_archived < ARCHIVE_DAILY_LIMIT:
                try:
                    # [この部分は公開用リポジトリでは非公開にされています]
                except TimeoutError:
                    logger.error(
                        f"バトルアーカイブ移管がタイムアウトしました (カスタム保存期間: {player_tag})。"
                        f"本日は {total_archived:,}件まで移管して中断します。"
                    )
                    timed_out = True
                    break

                batch_count = result or 0
                if batch_count == 0:
                    break

                total_archived += batch_count
                logger.info(f"バトルアーカイブ進行中 (カスタム保存期間: {player_tag}): {batch_count:,}件")
                # フルバッチだけスリープ。数十〜数百件のプレイヤーを数百人分 0.5秒待ちしない
                if batch_count >= ARCHIVE_BATCH_SIZE:
                    await asyncio.sleep(0.5)

            if timed_out:
                break

    if total_archived >= ARCHIVE_DAILY_LIMIT:
        logger.warning(
            f"バトルアーカイブが日次上限 ({ARCHIVE_DAILY_LIMIT:,}件) に達したため中断しました。"
        )

    return total_archived


async def purge_old_archived_battles_task(db: asyncpg.Connection) -> None:
    """保持期間を超えたアーカイブバトルを削除する。バックアップ後に単独実行する。"""
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    if now_utc < ARCHIVE_ACTIVATION_DATE:
        logger.info(
            f"アーカイブ削除タスク: 開始日 ({ARCHIVE_ACTIVATION_DATE.isoformat()}) "
            f"に達していないためスキップします。"
        )
        return

    redis_client = get_redis()
    if not redis_client:
        raise RuntimeError("Redis接続が利用できないため、アーカイブ削除タスクを実行できません。")
    lock_key = "lock:purge_old_archived_battles"
    lock_acquired = await redis_client.set(lock_key, "1", nx=True, ex=7200)

    if not lock_acquired:
        raise RuntimeError("アーカイブ削除の Redis ロックを取得できませんでした。")

    logger.info("古いアーカイブの削除タスクを開始します。")
    try:
        total_purged = await _purge_old_archived_battles(db)
        logger.info(f"古いアーカイブの削除完了: {total_purged:,}件を完全削除しました。")
    except asyncio.CancelledError:
        logger.warning("アーカイブ削除タスクがキャンセルされました。")
        raise
    except Exception as e:
        logger.error(f"アーカイブ削除タスクでエラーが発生しました: {e}", exc_info=True)
        raise
    finally:
        try:
            await redis_client.delete(lock_key)
        except Exception:
            pass


async def _purge_old_archived_battles(db: asyncpg.Connection) -> int:
    """
    アーカイブテーブルから保存期限を過ぎたバトルを完全削除する。
    判定基準はバトル日時（datetime）。
    
    Returns:
        int: 完全削除した合計件数
    """
    total_purged = 0
    oldest = await db.fetchval(
        "SELECT datetime FROM archived_battles ORDER BY datetime ASC LIMIT 1"
    )
    cutoff = await db.fetchval(
        "SELECT NOW() - make_interval(months => $1)",
        settings.ARCHIVE_RETENTION_MONTHS,
    )
    logger.info(
        f"アーカイブ削除開始: 最古={oldest}, 保持={settings.ARCHIVE_RETENTION_MONTHS}ヶ月 "
        f"(cutoff={cutoff}), バッチ={PURGE_BATCH_SIZE:,}, 日次上限={PURGE_DAILY_LIMIT:,}"
    )
    if oldest is None:
        return 0
    if oldest >= cutoff:
        logger.info("保持期間を超えるアーカイブはないため、削除をスキップします。")
        return 0

    while total_purged < PURGE_DAILY_LIMIT:
        try:
            # [この部分は公開用リポジトリでは非公開にされています]
        except TimeoutError:
            logger.error(
                f"古いアーカイブ削除がタイムアウトしました。"
                f"本日は {total_purged:,}件まで削除して中断します。"
            )
            break

        batch_count = result or 0

        if batch_count == 0:
            break

        total_purged += batch_count
        logger.info(f"古いアーカイブ削除進行中: 今回 {batch_count:,}件 / 累計 {total_purged:,}件")

        await asyncio.sleep(0.5)

    if total_purged >= PURGE_DAILY_LIMIT:
        logger.warning(
            f"古いアーカイブ削除が日次上限 ({PURGE_DAILY_LIMIT:,}件) に達したため中断しました。"
            f"残りは翌日以降に削除します。"
        )

    return total_purged


# [この部分は公開用リポジトリでは非公開にされています]


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

    # カスタム保存期間設定ありのプレイヤーのみ取得
    # デフォルト保存期間のプレイヤーは、復元対象になることがないためスキップ
    custom_retention_rows = await db.fetch("""
        SELECT tag, battle_log_retention_months
        FROM players
        WHERE battle_log_retention_months IS NOT NULL
          AND battle_log_retention_months > $1
    """, settings.DEFAULT_BATTLE_LOG_RETENTION_MONTHS)

    for row in custom_retention_rows:
        player_tag = row['tag']
        retention_months = row['battle_log_retention_months']
        player_cutoff = await db.fetchval(
            "SELECT NOW() - make_interval(months => $1)",
            retention_months,
        )

        while True:
            try:
                # [この部分は公開用リポジトリでは非公開にされています]
            except TimeoutError:
                logger.error(
                    f"バトル復元がタイムアウトしました ({player_tag})。"
                    f"本日は {total_restored:,}件まで復元して中断します。"
                )
                return total_restored

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
        raise
    except Exception as e:
        logger.error(f"プレイヤーレベル降格処理中に予期せぬエラー: {e}", exc_info=True)
        raise


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
        raise
    except Exception as e:
        logger.error(f"期限切れプロフィール画像のクリーンアップタスクでエラーが発生しました: {e}", exc_info=True)
        raise
