from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor
from datetime import datetime, time, timedelta, timezone
from typing import Callable, Awaitable, Any
import asyncpg

from app.core.logger import logger
from app.db.db import get_db_connection_for_bg_task
from app.background_tasks.tasks import (
    player_update_task_loop,
    update_usage_stats_task,
    check_new_maps_and_modes_task,
    update_prestige_borders_task,
    sync_user_view_data_from_redis,
    update_accessory_stats_task,
    archive_expired_battles_task,
    demote_inactive_players,
    cleanup_expired_profile_images_task,
    update_player_metric_thresholds_task
)
from app.services.admin_notification_service import poll_admin_notification_schedule_events

# [この部分は公開用リポジトリでは非公開にされています]

    # [この部分は公開用リポジトリでは非公開にされています]

    # [この部分は公開用リポジトリでは非公開にされています]
    
    logger.info("スケジューラーのセットアップが完了しました。")


async def start_scheduler():
    """スケジューラーを開始する"""
    try:
        await setup_scheduler() # ジョブを追加
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
        # wait=False に変更。AsyncIOExecutorの場合、wait=Trueは正常に機能しないことが多く、
        # またイベントループをブロックする可能性があるため。
        # タスクの終了待機は worker.py 側のロジックで一括して行う。
        scheduler.shutdown(wait=False)
        logger.info("スケジューラーをシャットダウンしました。")
        
