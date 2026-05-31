"""add player_metric_thresholds table

Revision ID: c3f9a1d2e7f0
Revises: b3c4d5e6f7a8
Create Date: 2026-05-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c3f9a1d2e7f0'
down_revision: Union[str, Sequence[str], None] = 'b3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'player_metric_thresholds',
        sa.Column('metric_key', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('population', sa.Integer(), nullable=False),
        sa.Column('thresholds_json', postgresql.JSONB(), nullable=False),
        sa.Column('method', sa.Text(), nullable=False),
        sa.Column('top_k', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('metric_key')
    )


def downgrade() -> None:
    op.drop_table('player_metric_thresholds')
