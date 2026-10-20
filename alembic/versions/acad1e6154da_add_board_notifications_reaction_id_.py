"""add board_notifications reaction_id index

Revision ID: acad1e6154da
Revises: 922cade77e67
Create Date: 2026-10-21 08:48:16.045291

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'acad1e6154da'
down_revision: Union[str, Sequence[str], None] = '922cade77e67'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # リアクションを外したときの CASCADE で board_notifications を全件走査しないためのインデックス
    # 通知の書き込みを止めないよう、`CREATE INDEX CONCURRENTLY` をトランザクション外で実行する
    with op.get_context().autocommit_block():
        op.create_index('idx_board_notifications_reaction_id', 'board_notifications', ['reaction_id'], unique=False,
                        postgresql_where=sa.text('reaction_id IS NOT NULL'), postgresql_concurrently=True)


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        op.drop_index('idx_board_notifications_reaction_id', table_name='board_notifications', postgresql_concurrently=True)
