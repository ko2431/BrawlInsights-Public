import asyncio
import signal
from typing import Set

from app.core.logging_config import setup_worker_logger
from app.core.logger import logger
from app.db.db import connect_to_db, close_db_connection
from app.core.cache import connect_redis, close_redis
from app.background_tasks.scheduler import start_scheduler, shutdown_scheduler
from app.background_tasks.task_runner import (
    PLAYER_UPDATE_START_DELAY_SEC,
    SHUTDOWN_TIMEOUT_SEC,
    interrupt_all_running_for_startup,
    start_player_update_run,
    task_dispatcher_loop,
)
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

async def _stop_running_tasks() -> None:
    """実行中タスクをキャンセルし、台帳への中断書き込みが終わるまで待つ。"""
    if not running_tasks:
        return

    logger.info(f"{len(running_tasks)}個のバックグラウンドタスクをキャンセルします。")
    tasks = list(running_tasks)
    for task in tasks:
        task.cancel()

    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=SHUTDOWN_TIMEOUT_SEC
        )
        logger.info("全てのバックグラウンドタスクは正常に終了しました。")
    except asyncio.TimeoutError:
        logger.warning(f"バックグラウンドタスクのシャットダウンが{SHUTDOWN_TIMEOUT_SEC}秒以内に完了しませんでした。")
    except asyncio.CancelledError:
        logger.warning("シャットダウン処理がキャンセルされました。")
    except Exception as e:
        logger.error(f"バックグラウンドタスクのシャットダウン中に予期せぬエラーが発生しました: {e}", exc_info=True)


async def _run_player_update_loop() -> None:
    """起動直後の負荷を避けるため、少し待ってからプレイヤー更新を開始する。"""
    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=PLAYER_UPDATE_START_DELAY_SEC)
        return
    except asyncio.TimeoutError:
        pass
    except asyncio.CancelledError:
        raise

    ctx = await start_player_update_run(shutdown_event)
    if ctx is None:
        logger.error("プレイヤーアップデートの実行台帳を作成できませんでした。台帳なしでループを開始します。")
        await player_update_task_loop(None)
        return

    heartbeat_task = asyncio.create_task(ctx.heartbeat_loop(), name="PlayerUpdateHeartbeat")
    try:
        await player_update_task_loop(ctx)
        if shutdown_event.is_set():
            await ctx.mark_interrupted("ワーカー停止により中断されました")
        else:
            await ctx.mark_failed("プレイヤーアップデートループが予期せず終了しました")
    except asyncio.CancelledError:
        await ctx.mark_interrupted("ワーカー停止により中断されました")
        raise
    except Exception as e:
        await ctx.mark_failed(str(e))
        logger.error(f"プレイヤーアップデートループで予期せぬエラー: {e}", exc_info=True)
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass


async def main():
    """
    バックグラウンドタスクを起動するメインコルーチン
    """
    setup_worker_logger()
    logger.info("バックグラウンドワーカーの起動処理を開始します...")

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_shutdown_signal, sig, None)

    await connect_to_db()
    await connect_redis()
    # [この部分は公開用リポジトリでは非公開にされています]

    await interrupt_all_running_for_startup()
    await start_scheduler()

    dispatcher_task = asyncio.create_task(task_dispatcher_loop(shutdown_event), name="TaskDispatcher")
    running_tasks.add(dispatcher_task)
    dispatcher_task.add_done_callback(_log_background_task_done)

    update_task = asyncio.create_task(_run_player_update_loop(), name="PlayerUpdateLoop")
    running_tasks.add(update_task)
    update_task.add_done_callback(_log_background_task_done)

    logger.info(
        "バックグラウンドワーカーが正常に起動しました。"
        f"プレイヤーアップデートは {PLAYER_UPDATE_START_DELAY_SEC} 秒後に開始します。"
    )

    try:
        await shutdown_event.wait()
    finally:
        logger.info("シャットダウン処理を開始します。")
        if not shutdown_event.is_set():
            shutdown_event.set()
        await _stop_running_tasks()
        await cleanup_resources()
        logger.info("バックグラウンドワーカーが正常に終了しました。")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
