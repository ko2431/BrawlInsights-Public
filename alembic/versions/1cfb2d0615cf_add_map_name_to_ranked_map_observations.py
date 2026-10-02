"""add map_name to ranked_map_observations

Revision ID: 1cfb2d0615cf
Revises: 334494fc42c5
Create Date: 2026-10-02 22:17:02.652858

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1cfb2d0615cf'
down_revision: Union[str, Sequence[str], None] = '334494fc42c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # NULL許容・デフォルトなしのため、メタデータ変更のみで即時完了する
    op.add_column('ranked_map_observations', sa.Column('map_name', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('ranked_map_observations', 'map_name')
