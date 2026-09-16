"""Add player icon prices and sprays catalog

Revision ID: d0e1f2a3b4c5
Revises: b8c9d0e1f2a3
Create Date: 2026-09-17 00:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd0e1f2a3b4c5'
down_revision: Union[str, Sequence[str], None] = 'b8c9d0e1f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('player_icons', sa.Column('bling_price', sa.Integer(), nullable=True))
    op.add_column('player_icons', sa.Column('gems_price', sa.Integer(), nullable=True))
    op.create_table(
        'sprays',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('rarity', sa.Integer(), nullable=True),
        sa.Column('bling_price', sa.Integer(), nullable=True),
        sa.Column('gems_price', sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('sprays')
    op.drop_column('player_icons', 'gems_price')
    op.drop_column('player_icons', 'bling_price')
