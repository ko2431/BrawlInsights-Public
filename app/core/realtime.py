"""WebSocket の配信（Redis の pub/sub）。

プロセスごとに Redis の購読（pubsub の接続）を1本だけ持ち、必要なチャネルを subscribe / unsubscribe して、
このプロセスにつながっている WebSocket に配る。チャネルの最後の接続が切れたら unsubscribe する。
以前は WebSocket の接続1本ごとに pubsub（Redis の接続）を1本ずつ作っていたため、接続数がそのまま Redis の接続数になっていた。

- 受信は1つのタスク（_read_loop）がまとめて行い、接続ごとの送信キューに入れる。
  送信は接続ごとのタスクが順に行うので、遅い接続がほかの接続への配信を止めない。
- Redis の接続が切れたときは、redis-py が再接続して購読し直す。それでも読めないときは pubsub を作り直す。
- 配信（publish）は今までどおり、どのプロセス・worker からでも Redis に流せばよい（チャネル名も変えていない）。
"""
from __future__ import annotations

import asyncio
import time

from starlette.websockets import WebSocket

from app.core import cache as cache_module
from app.core.logger import logger

SEND_QUEUE_MAX = 200  # 送れずにたまったメッセージの上限（超えたら、その接続は閉じる）
SEND_TIMEOUT_SECONDS = 10
READ_TIMEOUT_SECONDS = 5.0
PING_INTERVAL_SECONDS = 30  # 購読の接続が生きているかを確かめる間隔（何も流れないチャネルでも切断に気づけるように）


class _Client:
    """1本の WebSocket への送信（キューと送信タスク）。"""

    __slots__ = ("ws", "queue", "task", "channels")

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=SEND_QUEUE_MAX)
        self.channels: set[str] = set()
        self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            while True:
                data = await self.queue.get()
                await asyncio.wait_for(self.ws.send_text(data), SEND_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            # 閉じた・応答しない接続。受信側のループが終わって後始末される
            logger.debug(f"WebSocketへの送信を終了しました: {type(e).__name__}: {e}")
            await self._close()

    def push(self, data: str) -> None:
        try:
            self.queue.put_nowait(data)
        except asyncio.QueueFull:
            logger.warning("WebSocketの送信がたまりすぎたため、接続を閉じます。")
            self.task.cancel()
            asyncio.create_task(self._close())

    async def _close(self) -> None:
        try:
            await self.ws.close()
        except Exception:
            pass


class RealtimeHub:
    """プロセスに1つ。チャネル → このプロセスの接続の集合。"""

    def __init__(self) -> None:
        self._channels: dict[str, set[_Client]] = {}
        self._clients: dict[WebSocket, _Client] = {}
        self._pubsub = None
        self._reader: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def join(self, ws: WebSocket, channel: str) -> None:
        """接続をチャネルに加える（このプロセスでそのチャネルの最初の接続なら subscribe する）。"""
        async with self._lock:
            client = self._clients.get(ws)
            if client is None:
                client = self._clients[ws] = _Client(ws)
            subscribers = self._channels.get(channel)
            if subscribers is None:
                subscribers = self._channels[channel] = set()
                try:
                    await self._ensure_pubsub()
                    await self._pubsub.subscribe(channel)
                except Exception as e:
                    # Redis が使えないときも接続は残す（読み込みループが作り直したときに、まとめて購読し直す）
                    self._log_error(f"Redisチャネル '{channel}' の購読に失敗しました", e)
                    await self._drop_pubsub()
            subscribers.add(client)
            client.channels.add(channel)
            if self._reader is None or self._reader.done():
                self._reader = asyncio.create_task(self._read_loop())

    async def leave(self, ws: WebSocket) -> None:
        """接続を外す（チャネルの最後の接続なら unsubscribe する）。"""
        async with self._lock:
            client = self._clients.pop(ws, None)
            if client is None:
                return
            client.task.cancel()
            for channel in client.channels:
                subscribers = self._channels.get(channel)
                if subscribers is None:
                    continue
                subscribers.discard(client)
                if subscribers:
                    continue
                del self._channels[channel]
                if self._pubsub is not None:
                    try:
                        await self._pubsub.unsubscribe(channel)
                    except Exception as e:
                        self._log_error(f"Redisチャネル '{channel}' の購読の解除に失敗しました", e)

    def send(self, ws: WebSocket, data: str) -> None:
        """この接続だけに送る（配信と同じ送信キューを通すので、配信と送信が重ならない）。join の後に使う。"""
        client = self._clients.get(ws)
        if client is not None:
            client.push(data)

    def stats(self) -> dict:
        return {"channels": len(self._channels), "connections": len(self._clients)}

    # --- 内部 -------------------------------------------------------------------------------

    async def _ensure_pubsub(self) -> None:
        if self._pubsub is not None:
            return
        redis = cache_module.redis_pool
        if redis is None:
            raise RuntimeError("Redis接続が利用できません")
        self._pubsub = redis.pubsub()

    async def _drop_pubsub(self) -> None:
        pubsub, self._pubsub = self._pubsub, None
        if pubsub is not None:
            try:
                await pubsub.aclose()
            except Exception:
                pass

    async def _resubscribe(self) -> None:
        """pubsub を作り直し、いまのチャネルをまとめて購読し直す。"""
        async with self._lock:
            await self._drop_pubsub()
            if not self._channels:
                return
            await self._ensure_pubsub()
            await self._pubsub.subscribe(*self._channels.keys())
            logger.info(f"Redisの購読を作り直しました（チャネル {len(self._channels)}）")

    async def _read_loop(self) -> None:
        backoff = 1.0
        last_ping = time.monotonic()
        while True:
            try:
                if not self._channels and not self._clients:
                    return  # 接続がなくなったら止める（次の join で起動する）
                if self._pubsub is None or not self._pubsub.subscribed:
                    if self._channels:
                        await self._resubscribe()
                    if self._pubsub is None:
                        await asyncio.sleep(0.5)
                        continue
                message = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=READ_TIMEOUT_SECONDS)
                backoff = 1.0
                if time.monotonic() - last_ping > PING_INTERVAL_SECONDS:
                    last_ping = time.monotonic()
                    await self._pubsub.ping()
                if not message or message.get("type") != "message":
                    continue
                channel = message["channel"]
                channel = channel.decode() if isinstance(channel, bytes) else channel
                data = message["data"]
                data = data.decode() if isinstance(data, bytes) else data
                for client in tuple(self._channels.get(channel, ())):
                    client.push(data)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._log_error("Redisの購読の読み込みでエラーが発生しました", e)
                async with self._lock:
                    await self._drop_pubsub()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    @staticmethod
    def _log_error(message: str, e: Exception) -> None:
        if cache_module.is_transient_redis_error(e):
            cache_module.log_transient_redis_warning(f"{message}: {e}")
        else:
            logger.error(f"{message}: {e}", exc_info=True)


hub = RealtimeHub()
