"""add_trgm_alongside_bigm_for_player_name

Revision ID: 4c82ae91e372
Revises: 0069c9ebeb0a
Create Date: 2026-03-12 11:47:39.153357

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4c82ae91e372'
down_revision: Union[str, Sequence[str], None] = '0069c9ebeb0a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 現在の状態:
    #   idx_gin_players_name (bigm) が存在する
    #
    # 目標の状態:
    #   idx_gin_players_name_bigm (bigm) + idx_gin_players_name_trgm (trgm) の2本立て
    #
    # 手順:
    # 1. pg_trgm 拡張を有効化（元から存在している可能性があるが IF NOT EXISTS で安全に）
    # 2. trgm インデックスを CONCURRENTLY で新規作成（この間 bigm インデックスは生きているため検索は正常に機能する）
    # 3. 既存の bigm インデックス（旧名 idx_gin_players_name）をリネーム → idx_gin_players_name_bigm

    with op.get_context().autocommit_block():
        # 1. pg_trgm を有効化
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

        # 2. trgm インデックスを別名で並行作成（稼働中でもロックしない）
        op.execute(
            "CREATE INDEX CONCURRENTLY idx_gin_players_name_trgm "
            "ON players USING gin (name gin_trgm_ops)"
        )

    # 3. 既存の bigm インデックスをリネーム（瞬時・ロックは一瞬のみ）
    op.execute("ALTER INDEX IF EXISTS idx_gin_players_name RENAME TO idx_gin_players_name_bigm")


def downgrade() -> None:
    # upgrade の逆: trgm インデックスを削除し、bigm インデックスを旧名に戻す

    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_gin_players_name_trgm")

    op.execute("ALTER INDEX IF EXISTS idx_gin_players_name_bigm RENAME TO idx_gin_players_name")
