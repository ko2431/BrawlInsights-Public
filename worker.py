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

#* グローバル変数 ---
running_tasks: Set[asyncio.Task] = set()
shutdown_event = asyncio.Event()

#* シグナルハンドラ ---
def handle_shutdown_signal(sig, frame):
    logger.info(f"シグナル {sig} を受信しました。安全なシャットダウンを開始します...")
    if not shutdown_event.is_set():
        # イベントループ内で安全に shutdown を開始
        asyncio.create_task(shutdown())
    else:
        logger.warning("シャットダウン処理は既に実行中です。")

async def cleanup_resources():
    """
    リソースの解放処理を行う。
    """
    logger.info("リソースの解放処理を開始します...")
    await shutdown_scheduler()
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

    # バックグラウンドタスクのキャンセル処理（タイムアウト付き）
    if running_tasks:
        logger.info(f"{len(running_tasks)}個のバックグラウンドタスクをキャンセルします。")
        for task in running_tasks:
            task.cancel()

        try:
            # 5秒のタイムアウトを設けてタスクの終了を待つ
            # これにより、タスクが応答しない場合でも無期限に待機することを防ぐ
            shutdown_timeout = 5.0
            await asyncio.wait_for(
                asyncio.gather(*running_tasks, return_exceptions=True),
                timeout=shutdown_timeout
            )
            logger.info("全てのバックグラウンドタスクは正常に終了しました。")
        except asyncio.TimeoutError:
            # タイムアウトした場合
            logger.warning(f"バックグラウンドタスクのシャットダウンが{shutdown_timeout}秒以内に完了しませんでした。")
        except asyncio.CancelledError:
             # シャットダウン処理自体がキャンセルされた場合
            logger.warning("シャットダウン処理がキャンセルされました。")
        except Exception as e:
            # その他の予期せぬエラー
            logger.error(f"バックグラウンドタスクのシャットダウン中に予期せぬエラーが発生しました: {e}", exc_info=True)

    # イベントループを停止させるための処理


async def main():
    """
    バックグラウンドタスクを起動するメインコルーチン
    """
    setup_worker_logger()
    logger.info("バックグラウンドワーカーの起動処理を開始します...")

    # シグナルハンドラの設定
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_shutdown_signal, sig, None)

    await connect_to_db()
    await connect_redis()
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
