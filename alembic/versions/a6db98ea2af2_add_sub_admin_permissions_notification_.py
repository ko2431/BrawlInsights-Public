"""add sub-admin permissions notification settings and audit logs

Revision ID: a6db98ea2af2
Revises: e7b8c9d0e1f2
Create Date: 2026-09-12 21:33:03.821521

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a6db98ea2af2'
down_revision: Union[str, Sequence[str], None] = 'e7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'admin_audit_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('actor_user_id', sa.Integer(), nullable=True),
        sa.Column('actor_name', sa.Text(), server_default='', nullable=False),
        sa.Column('actor_is_admin', sa.Boolean(), server_default='False', nullable=False),
        sa.Column('action_key', sa.Text(), nullable=False),
        sa.Column('target_type', sa.Text(), nullable=True),
        sa.Column('target_id', sa.Text(), nullable=True),
        sa.Column('summary', sa.Text(), server_default='', nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('ip', postgresql.INET(), nullable=True),
        sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'idx_admin_audit_logs_action_created_at',
        'admin_audit_logs',
        ['action_key', sa.literal_column('created_at DESC')],
        unique=False,
    )
    op.create_index(
        'idx_admin_audit_logs_actor_created_at',
        'admin_audit_logs',
        ['actor_user_id', sa.literal_column('created_at DESC')],
        unique=False,
    )
    op.create_index(
        'idx_admin_audit_logs_created_at',
        'admin_audit_logs',
        [sa.literal_column('created_at DESC')],
        unique=False,
    )
    op.create_table(
        'admin_notification_user_settings',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('event_key', sa.Text(), nullable=False),
        sa.Column('level', sa.SmallInteger(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('level IN (0, 10, 20, 30)', name='ck_admin_notification_user_settings_level'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'event_key'),
    )
    op.create_index(
        'idx_admin_notification_user_settings_user_id',
        'admin_notification_user_settings',
        ['user_id'],
        unique=False,
    )
    op.add_column(
        'users',
        sa.Column('is_sub_admin', sa.Boolean(), server_default='False', nullable=False),
    )
    op.add_column(
        'users',
        sa.Column('admin_permissions', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    )
    op.create_check_constraint(
        'ck_users_not_admin_and_sub_admin',
        'users',
        'NOT (is_admin AND is_sub_admin)',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('ck_users_not_admin_and_sub_admin', 'users', type_='check')
    op.drop_column('users', 'admin_permissions')
    op.drop_column('users', 'is_sub_admin')
    op.drop_index('idx_admin_notification_user_settings_user_id', table_name='admin_notification_user_settings')
    op.drop_table('admin_notification_user_settings')
    op.drop_index('idx_admin_audit_logs_created_at', table_name='admin_audit_logs')
    op.drop_index('idx_admin_audit_logs_actor_created_at', table_name='admin_audit_logs')
    op.drop_index('idx_admin_audit_logs_action_created_at', table_name='admin_audit_logs')
    op.drop_table('admin_audit_logs')
