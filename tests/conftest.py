import pytest
import pytest_asyncio
import asyncpg
import json
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.main import app
from app.db.db import get_shared_db, setup_jsonb_codec
from app.db.models import Base
from app.core.config import settings

# テスト用DBの接続URL構築
DB_USER = settings.DB_USER
DB_PASSWORD = settings.DB_PASSWORD
DB_HOST = settings.DB_HOST
DB_PORT = settings.DB_PORT
TEST_DB_NAME = "brawl_insights_test"

if DB_PASSWORD:
    TEST_DB_URL = f"postgresql+asyncpg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{TEST_DB_NAME}"
    ASYNC_PG_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{TEST_DB_NAME}"
else:
    TEST_DB_URL = f"postgresql+asyncpg://{DB_USER}@{DB_HOST}:{DB_PORT}/{TEST_DB_NAME}"
    ASYNC_PG_URL = f"postgresql://{DB_USER}@{DB_HOST}:{DB_PORT}/{TEST_DB_NAME}"

@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_test_db():
    """
    テストセッション開始時に一度だけ呼ばれるFixture。
    テスト用DBのテーブルを全て初期化し、ダミーデータを投入する。
    """
    # SQLAlchemyを使ってテーブルの作成とダミーデータ投入
    engine = create_async_engine(TEST_DB_URL, echo=False)
    
    async with engine.begin() as conn:
        # 必要な拡張機能の有効化 (pg_bigm, pg_trgm 等)
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_bigm;"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm;"))

        # テーブルをリセットして新規作成
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        
        # ダミーデータの挿入 (生SQL)
        # Brawler: Shelly (ID: 16000000)
        await conn.execute(
            text("""
            INSERT INTO brawlers (id, en, ja, is_temporary, rarity)
            VALUES (:id, :en, CAST(:ja AS JSONB), :is_temporary, :rarity)
            """),
            {"id": 16000000, "en": "Shelly", "ja": '["シェリー", "しえりー"]', "is_temporary": False, "rarity": 1}
        )
        
        # Accessory: Shelly
        await conn.execute(
            text("""
            INSERT INTO accessories (id, brawler_id, type, en, ja, is_invalid)
            VALUES (:id, :brawler_id, :type, :en, :ja, :is_invalid)
            """),
            [{"id": 23000076, "brawler_id": 16000000, "type": "starPower", "en": "Shell Shock", "ja": "シェルショック", "is_invalid": False},
             {"id": 23000135, "brawler_id": 16000000, "type": "starPower", "en": "Band-Aid", "ja": "ばんそうこう", "is_invalid": False}]
        )
        
        # Skin: Shelly's Default Skin (ID: 29000000)
        await conn.execute(
            text("""
            INSERT INTO skins (id, brawler_id, en, ja)
            VALUES (:id, :brawler_id, :en, :ja)
            """),
            {"id": 29000000, "brawler_id": 16000000, "en": None, "ja": "デフォルトスキン"}
        )
    
    await engine.dispose()
    yield

# テスト全体で共有するテスト用DBのコネクションプール
_test_pool = None

@pytest_asyncio.fixture(scope="session")
async def test_db_pool():
    global _test_pool
    if _test_pool is None:
        _test_pool = await asyncpg.create_pool(
            dsn=ASYNC_PG_URL,
            min_size=1,
            max_size=5,
            init=setup_jsonb_codec
        )
    yield _test_pool
    if _test_pool:
        await _test_pool.close()

@pytest_asyncio.fixture(scope="session")
async def client(test_db_pool):
    """
    FastAPIの dependency_overrides を設定して、
    本番DB接続の代わりにテスト用DBに繋がるようにするテストクライアント。
    """
    async def override_get_shared_db():
        async with test_db_pool.acquire() as connection:
            yield connection

    # DB接続関数をテスト用に上書き
    app.dependency_overrides[get_shared_db] = override_get_shared_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
