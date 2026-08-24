import asyncio
import signal
from typing import Set

from app.background_tasks.image_generation_tasks import image_generation_task_loop
from app.core.cache import close_redis, connect_redis
from app.core.logger import logger
from app.core.logging_config import setup_worker_logger
from app.db.db import close_db_connection, connect_to_db

running_tasks: Set[asyncio.Task] = set()
shutdown_event = asyncio.Event()
shutdown_task: asyncio.Task | None = None


def handle_shutdown_signal(sig, frame):
    global shutdown_task
    logger.info(f"シグナル {sig} を受信しました。画像生成ワーカーの安全なシャットダウンを開始します...")
    if shutdown_task is None or shutdown_task.done():
        # [この部分は公開用リポジトリでは非公開にされています]

    image_task = asyncio.create_task(image_generation_task_loop(), name="ImageGenerationLoop")
    running_tasks.add(image_task)
    image_task.add_done_callback(running_tasks.discard)

    logger.info("画像生成ワーカーが正常に起動しました。シャットダウンシグナルを待機します...")

    try:
        # シャットダウンイベントがセットされるまで待機
        await shutdown_event.wait()
    finally:
        # まれにイベント先行で shutdown_task が残っている場合に備えて待機。
        if shutdown_task is not None and not shutdown_task.done():
            await shutdown_task

        # 明示的なリソース解放
        await cleanup_resources()
        logger.info("画像生成ワーカーが正常に終了しました。")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
