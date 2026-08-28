from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor
from datetime import datetime, timedelta, timezone

from app.core.logger import logger
from app.background_tasks.task_runner import run_recorded_interval_task
# [この部分は公開用リポジトリでは非公開にされています]

# [この部分は公開用リポジトリでは非公開にされています]


# [この部分は公開用リポジトリでは非公開にされています]

    scheduler.add_job(
        run_recorded_interval_task,
        'interval',
        minutes=15,
        start_date=now_utc + timedelta(minutes=5),
        args=['cleanup_expired_profile_images'],
        id='cleanup_expired_profile_images',
        name='Cleanup Expired Profile Images',
        misfire_grace_time=60,
        coalesce=True
    )
    logger.info("cleanup_expired_profile_images をスケジュールしました (15分ごと)。")

    # [この部分は公開用リポジトリでは非公開にされています]
    
    logger.info("スケジューラーのセットアップが完了しました。")


async def start_scheduler():
    """スケジューラーを開始する"""
    try:
        await setup_scheduler()
        if not scheduler.running:
            scheduler.start()
            logger.info("スケジューラーを開始しました。")
        else:
            logger.info("スケジューラーは既に実行中です。")
    except Exception as e:
        logger.error(f"スケジューラーの開始に失敗しました: {e}", exc_info=True)

async def shutdown_scheduler():
    """スケジューラーを停止する"""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("スケジューラーをシャットダウンしました。")
