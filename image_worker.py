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
        # イベントループ内で安全に shutdown を開始
        shutdown_task = asyncio.create_task(shutdown())
    else:
        logger.warning("画像生成ワーカーのシャットダウン処理は既に実行中です。")


async def cleanup_resources():
    logger.info("画像生成ワーカーのリソース解放処理を開始します...")
    await close_redis()
    await close_db_connection()
    logger.info("画像生成ワーカーのリソースを正常に解放しました。")


async def shutdown():
    if shutdown_event.is_set():
        return

    logger.info("画像生成ワーカーのシャットダウン処理を開始します。")
    try:
        if running_tasks:
            logger.info(f"{len(running_tasks)}個の画像生成タスクをキャンセルします。")
            for task in running_tasks:
                task.cancel()

            try:
                await asyncio.wait_for(
                    asyncio.gather(*running_tasks, return_exceptions=True),
                    timeout=5.0,
                )
                logger.info("全ての画像生成タスクは正常に終了しました。")
            except asyncio.TimeoutError:
                logger.warning("画像生成タスクのシャットダウンが5秒以内に完了しませんでした。")
            except asyncio.CancelledError:
                logger.warning("画像生成ワーカーのシャットダウン処理自体がキャンセルされました。")
            except Exception as e:
                logger.error(f"画像生成ワーカーのシャットダウン中に予期せぬエラーが発生しました: {e}", exc_info=True)
    finally:
        # 画像生成タスクの終了待ち後に main 側の finally を進める。
        shutdown_event.set()



async def main():
    setup_worker_logger()
    logger.info("画像生成ワーカーの起動処理を開始します...")

    # シグナルハンドラの設定
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_shutdown_signal, sig, None)

    await connect_to_db()
    await connect_redis()

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
