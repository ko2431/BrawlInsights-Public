"""Add index to battles datetime

Revision ID: fed9f681d93d
Revises: 1bb9eaf147b5
Create Date: 2026-03-08 02:53:58.530476

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fed9f681d93d'
down_revision: Union[str, Sequence[str], None] = '1bb9eaf147b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # `CREATE INDEX CONCURRENTLY` をトランザクション外で実行するための処理
    # 1.9億レコードあるため、テーブルロックを回避するために必須
    with op.get_context().autocommit_block():
        op.create_index('idx_battles_datetime', 'battles', ['datetime'], unique=False, postgresql_concurrently=True)


def downgrade() -> None:
    """Downgrade schema."""
    # ダウングレード時も同様にテーブルロックを回避
    with op.get_context().autocommit_block():
        op.drop_index('idx_battles_datetime', table_name='battles', postgresql_concurrently=True)
