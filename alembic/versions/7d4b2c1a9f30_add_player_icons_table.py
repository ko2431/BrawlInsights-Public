"""add_player_icons_table

Revision ID: 7d4b2c1a9f30
Revises: 4c82ae91e372
Create Date: 2026-03-15 11:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7d4b2c1a9f30'
down_revision: Union[str, Sequence[str], None] = '4c82ae91e372'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'player_icons',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('equip_rate', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('player_icons')
