"""add trophy_stats_brawler table

Revision ID: e7b8c9d0e1f2
Revises: d9f05b04d957
Create Date: 2026-09-12 02:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e7b8c9d0e1f2'
down_revision: Union[str, Sequence[str], None] = 'd9f05b04d957'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'trophy_stats_brawler',
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('mode_id', sa.Integer(), nullable=False),
        sa.Column('map_id', sa.Integer(), nullable=False),
        sa.Column('trophy_band', sa.SmallInteger(), nullable=False),
        sa.Column('brawler_id', sa.Integer(), nullable=False),
        sa.Column('games_played', sa.Integer(), nullable=False),
        sa.Column('wins', sa.Integer(), nullable=False),
        sa.Column('draws', sa.Integer(), nullable=False),
        sa.Column('star_player_count', sa.Integer(), nullable=False),
        sa.Column('star_player_opportunities', sa.Integer(), nullable=False),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            'date',
            'mode_id',
            'map_id',
            'trophy_band',
            'brawler_id',
            name='trophy_stats_brawler_pkey',
        ),
    )
    op.create_index(
        'idx_trophy_stats_brawler_mode_map_date',
        'trophy_stats_brawler',
        ['mode_id', 'map_id', 'date'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('idx_trophy_stats_brawler_mode_map_date', table_name='trophy_stats_brawler')
    op.drop_table('trophy_stats_brawler')
