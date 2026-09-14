"""Add penalty management tables and user columns

Revision ID: b8c9d0e1f2a3
Revises: a6db98ea2af2
Create Date: 2026-09-14 18:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, Sequence[str], None] = 'a6db98ea2af2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('is_prohibit_giveaway', sa.Boolean(), server_default='False', nullable=False),
    )
    op.add_column(
        'users',
        sa.Column('penalty_level', sa.Integer(), server_default='0', nullable=False),
    )
    op.add_column(
        'users',
        sa.Column('penalty_level_changed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'users',
        sa.Column('unacked_penalty_count', sa.Integer(), server_default='0', nullable=False),
    )
    op.create_check_constraint(
        'ck_users_penalty_level_step',
        'users',
        'penalty_level >= 0 AND penalty_level <= 80 AND penalty_level % 10 = 0',
    )
    op.create_check_constraint(
        'ck_users_unacked_penalty_count_nonnegative',
        'users',
        'unacked_penalty_count >= 0',
    )
    op.create_index(
        'idx_users_penalty_decay',
        'users',
        ['penalty_level_changed_at'],
        unique=False,
        postgresql_where=sa.text('penalty_level BETWEEN 10 AND 60 AND NOT is_invalid'),
    )

    op.create_table(
        'user_penalties',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('actor_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('action_kind', sa.Text(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('other_text', sa.Text(), nullable=True),
        sa.Column('level_delta', sa.Integer(), server_default='0', nullable=False),
        sa.Column('level_before', sa.Integer(), nullable=False),
        sa.Column('level_after', sa.Integer(), nullable=False),
        sa.Column('token_delta', sa.Integer(), server_default='0', nullable=False),
        sa.Column('tokens_after', sa.Integer(), nullable=True),
        sa.Column('prohibit_posting', sa.Boolean(), server_default='False', nullable=False),
        sa.Column('prohibit_giveaway', sa.Boolean(), server_default='False', nullable=False),
        sa.Column('invalidated', sa.Boolean(), server_default='False', nullable=False),
        sa.Column('blacklisted', sa.Boolean(), server_default='False', nullable=False),
        sa.Column('synced_user_ids', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('target_type', sa.Text(), nullable=True),
        sa.Column('target_id', sa.Integer(), nullable=True),
        sa.Column('report_id', sa.Integer(), sa.ForeignKey('reports.id', ondelete='SET NULL'), nullable=True),
        sa.Column('warning_text', sa.Text(), nullable=True),
        sa.Column('warning_message_id', sa.Integer(), sa.ForeignKey('messages.id', ondelete='SET NULL'), nullable=True),
        sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "action_kind IN ('warning', 'penalty', 'decay', 'level80_sync')",
            name='ck_user_penalties_action_kind',
        ),
        sa.CheckConstraint(
            "target_type IS NULL OR target_type IN ('post', 'message')",
            name='ck_user_penalties_target_type',
        ),
    )
    op.create_index('idx_user_penalties_user_created_at', 'user_penalties', ['user_id', sa.text('created_at DESC')])
    op.create_index('idx_user_penalties_actor_created_at', 'user_penalties', ['actor_user_id', sa.text('created_at DESC')])
    op.create_index('idx_user_penalties_target', 'user_penalties', ['target_type', 'target_id'])
    op.create_index(
        'idx_user_penalties_unacked',
        'user_penalties',
        ['user_id'],
        unique=False,
        postgresql_where=sa.text("acknowledged_at IS NULL AND action_kind IN ('warning', 'penalty')"),
    )

    op.create_table(
        'penalty_blacklisted_players',
        sa.Column('tag', sa.Text(), sa.ForeignKey('players.tag', ondelete='RESTRICT'), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('source_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('penalty_id', sa.Integer(), sa.ForeignKey('user_penalties.id', ondelete='SET NULL'), nullable=True),
        sa.Column('actor_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('penalty_blacklisted_players')
    op.drop_index('idx_user_penalties_unacked', table_name='user_penalties')
    op.drop_index('idx_user_penalties_target', table_name='user_penalties')
    op.drop_index('idx_user_penalties_actor_created_at', table_name='user_penalties')
    op.drop_index('idx_user_penalties_user_created_at', table_name='user_penalties')
    op.drop_table('user_penalties')
    op.drop_index('idx_users_penalty_decay', table_name='users')
    op.drop_constraint('ck_users_unacked_penalty_count_nonnegative', 'users', type_='check')
    op.drop_constraint('ck_users_penalty_level_step', 'users', type_='check')
    op.drop_column('users', 'unacked_penalty_count')
    op.drop_column('users', 'penalty_level_changed_at')
    op.drop_column('users', 'penalty_level')
    op.drop_column('users', 'is_prohibit_giveaway')
