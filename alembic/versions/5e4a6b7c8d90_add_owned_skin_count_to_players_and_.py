"""add owned_skin_count to players and player_logs

Revision ID: 5e4a6b7c8d90
Revises: 0b9e7f2d1c3a
Create Date: 2026-04-26 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5e4a6b7c8d90'
down_revision: Union[str, Sequence[str], None] = '0b9e7f2d1c3a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('players', sa.Column('owned_skin_count', sa.Integer(), nullable=True))
    op.add_column('player_logs', sa.Column('owned_skin_count', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('player_logs', 'owned_skin_count')
    op.drop_column('players', 'owned_skin_count')
