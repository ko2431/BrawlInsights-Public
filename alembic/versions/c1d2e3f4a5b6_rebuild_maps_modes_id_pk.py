"""rebuild maps/modes with integer ID PK and add battles.mode_id

Revision ID: c1d2e3f4a5b6
Revises: a8b9c0d1e2f3
Create Date: 2026-08-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, Sequence[str], None] = 'a8b9c0d1e2f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.rename_table('maps', 'maps_legacy')
    op.rename_table('modes', 'modes_legacy')

    op.create_table(
        'modes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('slug', sa.Text(), nullable=True),
        sa.Column('en', sa.Text(), nullable=True),
        sa.Column('ja', sa.Text(), nullable=True),
        sa.Column('en_is_manual', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('ja_is_manual', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('desc_ja', sa.Text(), nullable=True),
        sa.Column('desc_en', sa.Text(), nullable=True),
        sa.Column('desc2_ja', sa.Text(), nullable=True),
        sa.Column('desc2_en', sa.Text(), nullable=True),
        sa.Column('overtime', sa.Boolean(), nullable=True),
        sa.Column('overtime_text_ja', sa.Text(), nullable=True),
        sa.Column('overtime_text_en', sa.Text(), nullable=True),
        sa.Column('format_ja', sa.Text(), nullable=True),
        sa.Column('format_en', sa.Text(), nullable=True),
        sa.Column('color', sa.Text(), nullable=True),
        sa.Column('bg_color', sa.Text(), nullable=True),
        sa.Column('battle_time', sa.Integer(), nullable=True),
        sa.Column('respawn_time', sa.Integer(), nullable=True),
        sa.Column('disabled', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('is_boss_fight', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('is_special_event', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('is_not_rewarding_trophies', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('is_trophy_mode', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('rounds', sa.Integer(), nullable=True),
        sa.Column('team_size', sa.Integer(), nullable=True),
        sa.Column('team_count', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'uq_modes_slug',
        'modes',
        ['slug'],
        unique=True,
        postgresql_where=sa.text('slug IS NOT NULL'),
    )
    op.create_index('idx_modes_en', 'modes', ['en'], unique=False)

    op.create_table(
        'maps',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('en', sa.Text(), nullable=True),
        sa.Column('ja', sa.Text(), nullable=True),
        sa.Column('en_is_manual', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('ja_is_manual', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('codename', sa.Text(), nullable=True),
        sa.Column('theme', sa.Integer(), nullable=True),
        sa.Column('mode_id', sa.Integer(), nullable=True),
        sa.Column('disabled', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['mode_id'], ['modes.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_maps_en', 'maps', ['en'], unique=False)
    op.create_index('idx_maps_mode_id', 'maps', ['mode_id'], unique=False)

    op.add_column('battles', sa.Column('mode_id', sa.Integer(), nullable=True))
    op.add_column('archived_battles', sa.Column('mode_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('archived_battles', 'mode_id')
    op.drop_column('battles', 'mode_id')

    op.drop_index('idx_maps_mode_id', table_name='maps')
    op.drop_index('idx_maps_en', table_name='maps')
    op.drop_table('maps')

    op.drop_index('idx_modes_en', table_name='modes')
    op.drop_index('uq_modes_slug', table_name='modes')
    op.drop_table('modes')

    op.rename_table('maps_legacy', 'maps')
    op.rename_table('modes_legacy', 'modes')
