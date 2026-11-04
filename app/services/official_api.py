"""Brawl Stars 公式APIの非同期クライアント。

以前の BrawlPlex・brawlstats は同期の requests を使っていて、呼んでいる間はワーカーのイベントループ全体が止まり、
BrawlPlex はタイムアウトもなかった。httpx.AsyncClient を使い回して接続を再利用し、タイムアウトを設ける。
"""
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import settings

BASE_URL = "https://api.brawlstars.com/v1/"
TIMEOUT_SECONDS = 10.0


class OfficialAPIError(Exception):
    """公式APIから結果を受け取れなかった。status は HTTP ステータス（通信エラー・タイムアウトでは None）。"""

    def __init__(self, status: int | None, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"HTTP {status} {detail}" if status is not None else detail)

    @property
    def label(self) -> str:
        """ログに出す短い表記（ステータスがない場合は失敗の種類）"""
        return str(self.status) if self.status is not None else self.detail


class ApiClient:
    """httpx.AsyncClientを内部で保持し、アプリケーション全体で再利用するためのクライアント。"""

    def __init__(self, base_url: str, token: str | None) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.AsyncClient(base_url=base_url, headers=headers, timeout=TIMEOUT_SECONDS)

    async def get(self, endpoint: str) -> dict[str, Any]:
        """GETリクエストを送信し、JSONオブジェクトを返す。200以外や通信エラーは OfficialAPIError。"""
        try:
            response = await self._client.get(endpoint)
        except httpx.TimeoutException as e:
            raise OfficialAPIError(None, "timeout") from e
        except httpx.RequestError as e:
            raise OfficialAPIError(None, type(e).__name__) from e
        if response.status_code != 200:
            raise OfficialAPIError(response.status_code, response.reason_phrase)
        try:
            data = response.json()
        except ValueError as e:
            raise OfficialAPIError(response.status_code, "invalid json") from e
        if not isinstance(data, dict):
            raise OfficialAPIError(response.status_code, "unexpected json")
        return data

    async def aclose(self) -> None:
        """内部のhttpxクライアントを安全に閉じる。アプリケーション終了時に呼び出す。"""
        await self._client.aclose()


_api_client = ApiClient(BASE_URL, settings.BRAWL_TOKEN)


def _tag_path(tag: str) -> str:
    """'#XXXX' 形式のタグを、URLのパスに使える '%23XXXX' にする。"""
    return quote(tag if tag.startswith("#") else "#" + tag, safe="")


async def get_player(tag: str) -> dict[str, Any]:
    """プレイヤー情報を取得する。"""
    return await _api_client.get(f"players/{_tag_path(tag)}")


async def get_battlelog(tag: str) -> dict[str, Any]:
    """プレイヤーのバトル履歴を取得する。"""
    return await _api_client.get(f"players/{_tag_path(tag)}/battlelog")


async def get_club(tag: str) -> dict[str, Any]:
    """クラブ情報を取得する。"""
    return await _api_client.get(f"clubs/{_tag_path(tag)}")


async def get_brawlers() -> dict[str, Any]:
    """キャラクター一覧を取得する（APIが応答するかの確認にも使う）。"""
    return await _api_client.get("brawlers")
