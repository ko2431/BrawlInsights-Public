import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_robots_txt(client: AsyncClient):
    """
    /robots.txt エンドポイントが正常に動作するかをテストします。
    DBに依存しない最もシンプルなテストです。
    """
    response = await client.get("/robots.txt")
    
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "User-agent" in response.text
