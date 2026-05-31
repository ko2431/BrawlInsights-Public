"""add image generation jobs and ticket columns

Revision ID: 5c9410393f1c
Revises: a9e19646d2af
Create Date: 2026-04-01 00:08:55.921779

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5c9410393f1c'
down_revision: Union[str, Sequence[str], None] = 'a9e19646d2af'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('image_generation_jobs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('player_tag', sa.Text(), nullable=False),
    sa.Column('platform', sa.Text(), nullable=False),
    sa.Column('lang', sa.Text(), nullable=False),
    sa.Column('image_type', sa.Text(), nullable=False),
    sa.Column('orientation', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), server_default='queued', nullable=False),
    sa.Column('priority', sa.Integer(), server_default='0', nullable=False),
    sa.Column('consume_ticket', sa.Boolean(), server_default=sa.text('FALSE'), nullable=False),
    sa.Column('is_fast_lane', sa.Boolean(), server_default=sa.text('FALSE'), nullable=False),
    sa.Column('cache_key', sa.Text(), nullable=True),
    sa.Column('min_wait_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('result_path', sa.Text(), nullable=True),
    sa.Column('result_filename', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_image_generation_jobs_cache_key_expires_at', 'image_generation_jobs', ['cache_key', 'expires_at'], unique=False)
    op.create_index('ix_image_generation_jobs_player_tag_created_at', 'image_generation_jobs', ['player_tag', 'created_at'], unique=False)
    op.create_index('ix_image_generation_jobs_status_priority_created_at', 'image_generation_jobs', ['status', sa.literal_column('priority DESC'), 'created_at'], unique=False)
    op.create_index('ix_image_generation_jobs_user_id_created_at', 'image_generation_jobs', ['user_id', 'created_at'], unique=False)
    op.add_column('users', sa.Column('ad_skip_tickets', sa.Integer(), server_default='0', nullable=False))
    op.add_column('users', sa.Column('ticket_claim_count', sa.Integer(), server_default='0', nullable=False))
    op.add_column('users', sa.Column('last_ticket_claim_date', sa.Date(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'last_ticket_claim_date')
    op.drop_column('users', 'ticket_claim_count')
    op.drop_column('users', 'ad_skip_tickets')
    op.drop_index('ix_image_generation_jobs_user_id_created_at', table_name='image_generation_jobs')
    op.drop_index('ix_image_generation_jobs_status_priority_created_at', table_name='image_generation_jobs')
    op.drop_index('ix_image_generation_jobs_player_tag_created_at', table_name='image_generation_jobs')
    op.drop_index('ix_image_generation_jobs_cache_key_expires_at', table_name='image_generation_jobs')
    op.drop_table('image_generation_jobs')
