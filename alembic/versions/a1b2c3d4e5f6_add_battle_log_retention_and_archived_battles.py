"""Add battle_log_retention_months and archived_battles table

Revision ID: a1b2c3d4e5f6
Revises: 4132e8f5ea03
Create Date: 2026-02-21 00:06:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '4132e8f5ea03'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. playersテーブルに保存期間カラムを追加
    op.add_column('players', sa.Column(
        'battle_log_retention_months',
        sa.Integer(),
        nullable=True,
        server_default=None
    ))

    # 2. archived_battlesテーブルを作成
    op.create_table(
        'archived_battles',
        sa.Column('tag', sa.Text(), nullable=False),
        sa.Column('datetime', sa.DateTime(timezone=True), nullable=False),
        sa.Column('event_id', sa.Integer(), nullable=True),
        sa.Column('event_mode', sa.Text(), nullable=True),
        sa.Column('event_map', sa.Text(), nullable=True),
        sa.Column('battle_mode', sa.Text(), nullable=True),
        sa.Column('battle_type', sa.Text(), nullable=True),
        sa.Column('result', sa.Text(), nullable=False),
        sa.Column('rank', sa.Integer(), nullable=True),
        sa.Column('brawler', sa.Integer(), nullable=True),
        sa.Column('power', sa.Integer(), nullable=True),
        sa.Column('trophies', sa.Integer(), nullable=True),
        sa.Column('trophy_change', sa.Integer(), nullable=True),
        sa.Column('team_size', sa.Integer(), nullable=False),
        sa.Column('num_of_teams', sa.Integer(), nullable=False),
        sa.Column('ranked_score_after', sa.Integer(), nullable=True),
        sa.Column('is_starplayer', sa.Boolean(), nullable=True),
        sa.Column('brawlers', JSONB(), nullable=True),
        sa.Column('teammate_tags', JSONB(), nullable=False, server_default='[]'),
        sa.Column('opponent_tags', JSONB(), nullable=False, server_default='[]'),
        sa.Column('teammate_names', JSONB(), nullable=False, server_default='[]'),
        sa.Column('opponent_names', JSONB(), nullable=False, server_default='[]'),
        sa.Column('teammate_brawlers', JSONB(), nullable=False, server_default='[]'),
        sa.Column('opponent_brawlers', JSONB(), nullable=False, server_default='[]'),
        sa.Column('archived_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('tag', 'datetime', name='archived_battles_pkey'),
    )

    # 3. archived_battlesにインデックスを作成
    op.create_index(
        'idx_archived_battles_datetime',
        'archived_battles',
        ['datetime']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_archived_battles_datetime', table_name='archived_battles')
    op.drop_table('archived_battles')
    op.drop_column('players', 'battle_log_retention_months')
