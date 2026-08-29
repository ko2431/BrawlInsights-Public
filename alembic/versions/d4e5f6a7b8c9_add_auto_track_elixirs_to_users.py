"""add auto_track_elixirs to users

Revision ID: d4e5f6a7b8c9
Revises: b1c2d3e4f5a6
Create Date: 2026-08-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'b1c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('auto_track_elixirs', sa.Integer(), server_default='0', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('users', 'auto_track_elixirs')
