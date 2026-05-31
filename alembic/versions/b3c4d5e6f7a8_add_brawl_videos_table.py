"""Add brawl_videos table

Revision ID: b3c4d5e6f7a8
Revises: 9ac0db547f26
Create Date: 2026-05-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3c4d5e6f7a8'
down_revision: Union[str, Sequence[str], None] = '5e4a6b7c8d90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'brawl_videos',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('title_ja', sa.Text(), nullable=False),
        sa.Column('title_en', sa.Text(), nullable=True),
        sa.Column('platform', sa.Text(), server_default='youtube', nullable=False),
        sa.Column('video_id', sa.Text(), nullable=False),
        sa.Column('thumbnail_url', sa.Text(), nullable=True),
        sa.Column('is_sponsored', sa.Boolean(), server_default='False', nullable=False),
        sa.Column('sponsor_name', sa.Text(), nullable=True),
        sa.Column('display_order', sa.Integer(), server_default='0', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='True', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_brawl_videos_display_order', 'brawl_videos', ['display_order'])
    op.create_index('ix_brawl_videos_is_active', 'brawl_videos', ['is_active'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_brawl_videos_is_active', table_name='brawl_videos')
    op.drop_index('ix_brawl_videos_display_order', table_name='brawl_videos')
    op.drop_table('brawl_videos')
