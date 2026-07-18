"""add minigame ticket skip option and tickets_spent

Revision ID: d8f1a2b3c4e5
Revises: c7e8f9a0b1d2
Create Date: 2026-07-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd8f1a2b3c4e5'
down_revision: Union[str, Sequence[str], None] = 'c7e8f9a0b1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('minigame_use_ad_skip_ticket', sa.Boolean(), server_default='True', nullable=False),
    )
    op.add_column(
        'minigame_plays',
        sa.Column('tickets_spent', sa.Integer(), server_default='0', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('minigame_plays', 'tickets_spent')
    op.drop_column('users', 'minigame_use_ad_skip_ticket')
