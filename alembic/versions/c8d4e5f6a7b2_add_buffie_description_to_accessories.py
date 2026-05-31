"""Add buffie description columns to accessories table

Revision ID: c8d4e5f6a7b2
Revises: b7f3a2c91d45
Create Date: 2026-02-26 05:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8d4e5f6a7b2'
down_revision: Union[str, Sequence[str], None] = 'b7f3a2c91d45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('accessories', sa.Column('buffie_description_en', sa.Text(), nullable=True))
    op.add_column('accessories', sa.Column('buffie_description_ja', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('accessories', 'buffie_description_ja')
    op.drop_column('accessories', 'buffie_description_en')
