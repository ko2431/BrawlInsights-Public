"""add board_notifications read_at

Revision ID: e8c1f02253ac
Revises: acad1e6154da
Create Date: 2026-10-21 09:10:49.545041

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8c1f02253ac'
down_revision: Union[str, Sequence[str], None] = 'acad1e6154da'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 通知ごとの既読。既定値なしの NULL 可の列なので、既存の行は書き換わらない
    op.add_column('board_notifications', sa.Column('read_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('board_notifications', 'read_at')
