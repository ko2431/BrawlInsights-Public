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
            # [この部分は公開用リポジトリでは非公開にされています]
