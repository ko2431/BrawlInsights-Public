"""Add pin jewel-chip and emerald prices

Revision ID: e2f3a4b5c6d7
Revises: d0e1f2a3b4c5
Create Date: 2026-09-17 04:59:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e2f3a4b5c6d7'
down_revision: Union[str, Sequence[str], None] = 'd0e1f2a3b4c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('pins', sa.Column('bling_price', sa.Integer(), nullable=True))
    op.add_column('pins', sa.Column('gems_price', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('pins', 'gems_price')
    op.drop_column('pins', 'bling_price')
