"""add token_gifts table and token gift notification setting

Revision ID: e1f2a3b4c5d6
Revises: d4e5f6a7b8c9
Create Date: 2026-09-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('notification_token_gift_enabled', sa.Boolean(), server_default='True', nullable=False),
    )
    op.create_index(
        'idx_posts_host_id_created_at_desc',
        'posts',
        ['host_id', sa.literal_column('created_at DESC')],
        unique=False,
        postgresql_where=sa.text('host_id IS NOT NULL'),
    )
    op.create_index(
        'idx_messages_user_id_created_at_desc',
        'messages',
        ['user_id', sa.literal_column('created_at DESC')],
        unique=False,
        postgresql_where=sa.text('user_id IS NOT NULL'),
    )
    op.create_table(
        'token_gifts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('thread_id', sa.Integer(), nullable=False),
        sa.Column('message_id', sa.Integer(), nullable=False),
        sa.Column('giver_user_id', sa.Integer(), nullable=True),
        sa.Column('recipient_user_id', sa.Integer(), nullable=True),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('fee', sa.Integer(), nullable=False),
        sa.Column('comment', sa.Text(), nullable=True),
        sa.Column('is_comment_deleted', sa.Boolean(), server_default='False', nullable=False),
        sa.Column('giver_ip', postgresql.INET(), nullable=False),
        sa.Column('recipient_ip', postgresql.INET(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('amount > 0', name='ck_token_gifts_amount_positive'),
        sa.CheckConstraint('fee >= 0', name='ck_token_gifts_fee_nonnegative'),
        sa.ForeignKeyConstraint(['giver_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['recipient_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['thread_id'], ['posts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('message_id', name='uq_token_gifts_message_id'),
    )
    op.create_index('idx_token_gifts_thread_id_created_at', 'token_gifts', ['thread_id', 'created_at'], unique=False)
    op.create_index(
        'idx_token_gifts_giver_user_id_created_at',
        'token_gifts',
        ['giver_user_id', sa.literal_column('created_at DESC')],
        unique=False,
    )
    op.create_index(
        'idx_token_gifts_recipient_user_id_created_at',
        'token_gifts',
        ['recipient_user_id', sa.literal_column('created_at DESC')],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('idx_token_gifts_recipient_user_id_created_at', table_name='token_gifts')
    op.drop_index('idx_token_gifts_giver_user_id_created_at', table_name='token_gifts')
    op.drop_index('idx_token_gifts_thread_id_created_at', table_name='token_gifts')
    op.drop_table('token_gifts')
    op.drop_index('idx_messages_user_id_created_at_desc', table_name='messages')
    op.drop_index('idx_posts_host_id_created_at_desc', table_name='posts')
    op.drop_column('users', 'notification_token_gift_enabled')
