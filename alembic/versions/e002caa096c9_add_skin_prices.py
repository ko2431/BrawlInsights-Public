"""add skin prices

Revision ID: e002caa096c9
Revises: 3d8065a5bbc0
Create Date: 2026-09-30 23:31:09.840979

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e002caa096c9'
down_revision: Union[str, Sequence[str], None] = '3d8065a5bbc0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('skins', sa.Column('bling_price', sa.Integer(), nullable=True))
    op.add_column('skins', sa.Column('coins_price', sa.Integer(), nullable=True))
    op.add_column('skins', sa.Column('gems_price', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('skins', 'gems_price')
    op.drop_column('skins', 'coins_price')
    op.drop_column('skins', 'bling_price')
