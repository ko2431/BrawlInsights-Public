"""add message reply and reply notification setting

Revision ID: a1b2c3d4e5f7
Revises: e9a1b2c3d4e5
Create Date: 2026-08-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f7'
down_revision: Union[str, Sequence[str], None] = 'e9a1b2c3d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'messages',
        sa.Column('reply_to_message_id', sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        'fk_messages_reply_to_message_id',
        'messages',
        'messages',
        ['reply_to_message_id'],
        ['id'],
        ondelete='SET NULL',
    )
    op.create_index(
        'idx_messages_reply_to_message_id',
        'messages',
        ['reply_to_message_id'],
    )
    op.add_column(
        'users',
        sa.Column(
            'notification_message_reply_enabled',
            sa.Boolean(),
            nullable=False,
            server_default='True',
        ),
    )


def downgrade() -> None:
    op.drop_column('users', 'notification_message_reply_enabled')
    op.drop_index('idx_messages_reply_to_message_id', table_name='messages')
    op.drop_constraint('fk_messages_reply_to_message_id', 'messages', type_='foreignkey')
    op.drop_column('messages', 'reply_to_message_id')
