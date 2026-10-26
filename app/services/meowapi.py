import httpx
from typing import Any
import asyncpg

from app.core.logger import logger
from app.core.cache import get_cache, set_cache
from app.exceptions.custom_exceptions import BrawlStarsAPIError
from app.utils.utils import calc_mastery_rank, calc_ranked_season

class ApiClient:
    """httpx.AsyncClientを内部で保持し、アプリケーション全体で再利用するためのクライアント。"""
    
    def __init__(self, base_url: str):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)
        logger.info(f"ApiClient: {base_url} への接続クライアントを初期化しました。")

    async def get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict | list | None:
        """GETリクエストを送信し、JSONレスポンスを返す。"""
        try:
            response = await self._client.get(endpoint, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            # 改行を削除し、最大200文字に制限
            error_text = e.response.text.replace("\n", " ").replace("\r", "")
            summary = (error_text[:200] + '...') if len(error_text) > 200 else error_text
            
            logger.warning(
                f"HTTPエラー: {e.response.status_code} - {e.request.url} 要約レスポンス: {summary}"
            )
            return None
        except httpx.RequestError as e:
            logger.warning(f"リクエストエラー: {e.request.url} - {e}")
            return None

    async def aclose(self):
        """内部のhttpxクライアントを安全に閉じる。アプリケーション終了時に呼び出す。"""
        await self._client.aclose()
        logger.info("ApiClient: 接続クライアントを正常に終了しました。")

# [この部分は公開用リポジトリでは非公開にされています]
