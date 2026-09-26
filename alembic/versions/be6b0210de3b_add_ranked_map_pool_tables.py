"""add ranked map pool tables

Revision ID: be6b0210de3b
Revises: 1c43f07fc8e0
Create Date: 2026-09-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'be6b0210de3b'
down_revision: Union[str, Sequence[str], None] = '1c43f07fc8e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ranked_map_observations',
        sa.Column('hour', sa.DateTime(timezone=True), nullable=False),
        sa.Column('map_id', sa.Integer(), nullable=False),
        sa.Column('mode_id', sa.Integer(), nullable=True),
        sa.Column('battle_count', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('hour', 'map_id', name='ranked_map_observations_pkey'),
    )

    op.create_table(
        'ranked_map_pools',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('season', sa.Integer(), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('start_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('maps', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('season', 'seq', name='uq_ranked_map_pools_season_seq'),
    )

    op.create_table(
        'ranked_seasons',
        sa.Column('season', sa.Integer(), autoincrement=False, nullable=False),
        sa.Column('max_power_brawler_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('excluded_map_ids', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('auto_locked', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('visibility', sa.Text(), server_default='auto', nullable=False),
        sa.Column('coverage', sa.Float(), nullable=True),
        sa.Column('computed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("visibility IN ('auto', 'show', 'hide')", name='ck_ranked_seasons_visibility'),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('season'),
    )


def downgrade() -> None:
    op.drop_table('ranked_seasons')
    op.drop_table('ranked_map_pools')
    op.drop_table('ranked_map_observations')
