from __future__ import annotations

import asyncio

from app.core.logger import logger
from app.db.db import get_db_connection_for_bg_task
from app.exceptions.custom_exceptions import BrawlStarsAPIError, DataBaseError
from app.services.brawl_service import get_player, get_available_brawlers, calc_num_of_available_brawlers, get_max_accessory_counts
from app.services.image_generation_service import (
    claim_next_queued_image_generation_job,
    mark_image_generation_job_completed,
    mark_image_generation_job_failed,
    requeue_stale_processing_image_generation_jobs,
    timeout_stale_queued_image_generation_jobs,
)
from app.services.profile_image_renderer import render_profile_image


async def process_image_generation_job_once() -> bool:
    async with get_db_connection_for_bg_task() as db:
        job = await claim_next_queued_image_generation_job(db)
        if not job:
            return False

    try:
        async with get_db_connection_for_bg_task() as db:
            player = await get_player(job.player_tag, db, is_bg_task=True)
            available_brawlers = await get_available_brawlers(db)
            num_of_available_brawlers = await calc_num_of_available_brawlers(db)
            max_accessory_counts = await get_max_accessory_counts(db)
            filename, public_result_path = render_profile_image(job, player, available_brawlers, num_of_available_brawlers, max_accessory_counts)
            await mark_image_generation_job_completed(
                db,
                job.id,
                result_path=public_result_path,
                result_filename=filename,
            )
        logger.debug(f"画像生成ジョブの処理が完了しました。job_id={job.id}")
        return True
    except (BrawlStarsAPIError, DataBaseError, ValueError) as e:
        logger.error(f"画像生成ジョブ処理中にエラー: job_id={job.id}, error={e}", exc_info=True)
        async with get_db_connection_for_bg_task() as db:
            await mark_image_generation_job_failed(db, job.id, error_message=str(e))
        return True
    except asyncio.CancelledError:
        # Ctrl+C などでワーカー停止中にキャンセルされたジョブを取り残さない。
        logger.info(f"画像生成ジョブがキャンセルされました。job_id={job.id}")
        try:
            async with get_db_connection_for_bg_task() as db:
                await mark_image_generation_job_failed(
                    db,
                    job.id,
                    error_message="Worker shutdown during processing",
                )
        except Exception as mark_error:
            logger.error(
                f"キャンセルされた画像生成ジョブの失敗更新に失敗しました: job_id={job.id}, error={mark_error}",
                exc_info=True,
            )
        raise
    except Exception as e:
        logger.error(f"画像生成ジョブ処理中に予期せぬエラー: job_id={job.id}, error={e}", exc_info=True)
        async with get_db_connection_for_bg_task() as db:
            await mark_image_generation_job_failed(
                db,
                job.id,
                error_message="Unexpected image generation error",
            )
        return True


async def image_generation_task_loop(poll_interval_seconds: float = 2.0) -> None:
    logger.info("画像生成ワーカーループを開始します。")
    queue_timeout_check_interval_seconds = 30.0
    stale_requeue_check_interval_seconds = 60.0
    try:
        async with get_db_connection_for_bg_task() as db:
            await timeout_stale_queued_image_generation_jobs(db)
            await requeue_stale_processing_image_generation_jobs(db)
        next_queue_timeout_check = asyncio.get_running_loop().time() + queue_timeout_check_interval_seconds
        next_stale_requeue_check = asyncio.get_running_loop().time() + stale_requeue_check_interval_seconds

        while True:
            try:
                now_mono = asyncio.get_running_loop().time()
                if now_mono >= next_queue_timeout_check:
                    async with get_db_connection_for_bg_task() as db:
                        await timeout_stale_queued_image_generation_jobs(db)
                    next_queue_timeout_check = now_mono + queue_timeout_check_interval_seconds

                if now_mono >= next_stale_requeue_check:
                    async with get_db_connection_for_bg_task() as db:
                        await requeue_stale_processing_image_generation_jobs(db)
                    next_stale_requeue_check = now_mono + stale_requeue_check_interval_seconds

                processed = await process_image_generation_job_once()
                if not processed:
                    await asyncio.sleep(poll_interval_seconds)
            except asyncio.CancelledError:
                logger.info("画像生成ワーカーループがキャンセルされました。")
                break
            except Exception as e:
                logger.error(f"画像生成ワーカーループで予期せぬエラー: {e}", exc_info=True)
                await asyncio.sleep(max(poll_interval_seconds, 5.0))
    finally:
        logger.info("画像生成ワーカーループを終了します。")
