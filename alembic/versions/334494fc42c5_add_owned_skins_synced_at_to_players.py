"""add owned_skins_synced_at to players

Revision ID: 334494fc42c5
Revises: e002caa096c9
Create Date: 2026-10-01 00:48:42.702655

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '334494fc42c5'
down_revision: Union[str, Sequence[str], None] = 'e002caa096c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # NULL許容・デフォルトなしのため、大きなplayersテーブルでもメタデータ変更のみで即時完了する
    op.add_column('players', sa.Column('owned_skins_synced_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('players', 'owned_skins_synced_at')
