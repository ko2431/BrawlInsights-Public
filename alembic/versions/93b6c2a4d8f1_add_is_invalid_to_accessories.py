"""Add is_invalid flag to accessories

Revision ID: 93b6c2a4d8f1
Revises: 5c9410393f1c
Create Date: 2026-04-25 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '93b6c2a4d8f1'
down_revision: Union[str, Sequence[str], None] = '5c9410393f1c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('accessories', sa.Column('is_invalid', sa.Boolean(), nullable=False, server_default='False'))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('accessories', 'is_invalid')
