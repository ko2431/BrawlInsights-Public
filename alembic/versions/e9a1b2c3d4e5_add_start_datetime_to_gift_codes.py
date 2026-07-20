"""add start_datetime to gift_codes

Revision ID: e9a1b2c3d4e5
Revises: d8f1a2b3c4e5
Create Date: 2026-07-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e9a1b2c3d4e5'
down_revision: Union[str, Sequence[str], None] = 'd8f1a2b3c4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'gift_codes',
        sa.Column('start_datetime', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('gift_codes', 'start_datetime')
