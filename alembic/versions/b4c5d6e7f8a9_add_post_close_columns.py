"""add is_closed and auto_close_exempt to posts

Revision ID: b4c5d6e7f8a9
Revises: a1b2c3d4e5f7
Create Date: 2026-08-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b4c5d6e7f8a9'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'posts',
        sa.Column('is_closed', sa.Boolean(), server_default='False', nullable=False),
    )
    op.add_column(
        'posts',
        sa.Column('auto_close_exempt', sa.Boolean(), server_default='False', nullable=False),
    )
    op.create_index('ix_posts_is_closed', 'posts', ['is_closed'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_posts_is_closed', table_name='posts')
    op.drop_column('posts', 'auto_close_exempt')
    op.drop_column('posts', 'is_closed')
