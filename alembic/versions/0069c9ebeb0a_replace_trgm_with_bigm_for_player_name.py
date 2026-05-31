"""replace_trgm_with_bigm_for_player_name

Revision ID: 0069c9ebeb0a
Revises: f0131ce40c0f
Create Date: 2026-03-11 21:19:14.341870

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0069c9ebeb0a'
down_revision: Union[str, Sequence[str], None] = 'f0131ce40c0f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    with op.get_context().autocommit_block():
        # 1. pg_bigm を有効化
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_bigm")

        # 2. Alembicのメソッドでインデックス作成
        op.create_index(
            'idx_gin_players_name_new',
            'players',
            ['name'],
            postgresql_using='gin',
            postgresql_ops={'name': 'gin_bigm_ops'},
            postgresql_concurrently=True
        )

        # 3. 古いインデックスを削除
        op.drop_index('idx_gin_players_name', table_name='players', postgresql_concurrently=True)

    # 4. リネーム
    op.execute("ALTER INDEX idx_gin_players_name_new RENAME TO idx_gin_players_name")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        # 1. 元に戻すための trgm 拡張を確認
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        
        # 2. trgm を使って「別名」でインデックスを並行作成
        # 1,200万件だとここでも数時間かかる可能性があるため concurrently=True
        op.create_index(
            'idx_gin_players_name_old',
            'players',
            ['name'],
            postgresql_using='gin',
            postgresql_ops={'name': 'gin_trgm_ops'}, # trgmに戻す
            postgresql_concurrently=True
        )
        
        # 3. 現在の bigm インデックスを削除
        op.drop_index(
            'idx_gin_players_name',
            table_name='players',
            postgresql_concurrently=True
        )
    
    # 4. 復元したインデックスを元の名前に戻す
    op.execute("ALTER INDEX idx_gin_players_name_old RENAME TO idx_gin_players_name")
