import asyncio
import signal
from typing import Set

from app.core.logging_config import setup_worker_logger
from app.core.logger import logger
from app.db.db import connect_to_db, close_db_connection
from app.core.cache import connect_redis, close_redis
from app.background_tasks.scheduler import start_scheduler, shutdown_scheduler
from app.background_tasks.tasks import player_update_task_loop
from app.services.meowapi import _api_client as _meow_api_client
from app.services.bsinfoapi import _api_client as _bsinfo_api_client

# [この部分は公開用リポジトリでは非公開にされています]
    await close_redis()
    await close_db_connection()
    try:
        await _meow_api_client.aclose()
        logger.info("MeowAPI クライアント接続を閉じました。")
    except Exception as e:
        logger.error(f"MeowAPI クライアントのクローズ中にエラーが発生しました: {e}", exc_info=True)
    try:
        await _bsinfo_api_client.aclose()
        logger.info("BSInfo API クライアント接続を閉じました。")
    except Exception as e:
        logger.error(f"BSInfo API クライアントのクローズ中にエラーが発生しました: {e}", exc_info=True)

    logger.info("すべてのリソースが正常に解放されました。")

async def shutdown():
    """
    シャットダウン処理の本体。タスクのキャンセルを行う。
    """
    if shutdown_event.is_set():
        return

    logger.info("シャットダウン処理を開始します。")
    shutdown_event.set()

    # [この部分は公開用リポジトリでは非公開にされています]
    await start_scheduler()

    update_task = asyncio.create_task(player_update_task_loop(), name="PlayerUpdateLoop")
    running_tasks.add(update_task)
    # タスクが完了（正常/異常問わず）したら、running_tasksから自身を削除
    update_task.add_done_callback(running_tasks.discard)

    logger.info("バックグラウンドワーカーが正常に起動しました。シャットダウンシグナルを待機します...")

    try:
        # シャットダウンイベントがセットされるまで待機
        await shutdown_event.wait()
    finally:
        # 明示的なリソース解放
        await cleanup_resources()
        logger.info("バックグラウンドワーカーが正常に終了しました。")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
