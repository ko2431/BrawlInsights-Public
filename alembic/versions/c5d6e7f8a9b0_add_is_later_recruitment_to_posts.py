"""add is_later_recruitment to posts

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-08-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c5d6e7f8a9b0'
down_revision: Union[str, Sequence[str], None] = 'b4c5d6e7f8a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'posts',
        sa.Column('is_later_recruitment', sa.Boolean(), server_default='False', nullable=False),
    )
    op.create_index('ix_posts_is_later_recruitment', 'posts', ['is_later_recruitment'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_posts_is_later_recruitment', table_name='posts')
    op.drop_column('posts', 'is_later_recruitment')
