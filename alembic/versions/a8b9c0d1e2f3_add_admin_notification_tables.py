"""add admin notification tables

Revision ID: a8b9c0d1e2f3
Revises: f6a7b8c9d0e1
Create Date: 2026-08-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'a8b9c0d1e2f3'
down_revision: Union[str, Sequence[str], None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('admin_notifications_dashboard_read_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        'admin_notification_event_settings',
        sa.Column('event_key', sa.Text(), nullable=False),
        sa.Column('level', sa.SmallInteger(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('level IN (0, 10, 20, 30)', name='ck_admin_notification_event_settings_level'),
        sa.PrimaryKeyConstraint('event_key'),
    )
    op.create_table(
        'admin_notifications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('category', sa.Text(), nullable=False),
        sa.Column('event_key', sa.Text(), nullable=False),
        sa.Column('level', sa.SmallInteger(), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('summary', sa.Text(), server_default='', nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('target_path', sa.Text(), nullable=False),
        sa.Column('actor_user_id', sa.Integer(), nullable=True),
        sa.Column('dedupe_key', sa.Text(), nullable=True),
        sa.CheckConstraint('level IN (10, 20, 30)', name='ck_admin_notifications_level'),
        sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'idx_admin_notifications_created_at',
        'admin_notifications',
        [sa.literal_column('created_at DESC')],
        unique=False,
    )
    op.create_index(
        'idx_admin_notifications_category_created_at',
        'admin_notifications',
        ['category', sa.literal_column('created_at DESC')],
        unique=False,
    )
    op.create_index(
        'idx_admin_notifications_level_created_at',
        'admin_notifications',
        ['level', sa.literal_column('created_at DESC')],
        unique=False,
    )
    op.create_index(
        'uq_admin_notifications_event_dedupe',
        'admin_notifications',
        ['event_key', 'dedupe_key'],
        unique=True,
        postgresql_where=sa.text('dedupe_key IS NOT NULL'),
    )
    op.create_table(
        'admin_notification_category_reads',
        sa.Column('admin_user_id', sa.Integer(), nullable=False),
        sa.Column('category', sa.Text(), nullable=False),
        sa.Column('visited_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['admin_user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('admin_user_id', 'category'),
    )


def downgrade() -> None:
    op.drop_table('admin_notification_category_reads')
    op.drop_index('uq_admin_notifications_event_dedupe', table_name='admin_notifications')
    op.drop_index('idx_admin_notifications_level_created_at', table_name='admin_notifications')
    op.drop_index('idx_admin_notifications_category_created_at', table_name='admin_notifications')
    op.drop_index('idx_admin_notifications_created_at', table_name='admin_notifications')
    op.drop_table('admin_notifications')
    op.drop_table('admin_notification_event_settings')
    op.drop_column('users', 'admin_notifications_dashboard_read_at')
