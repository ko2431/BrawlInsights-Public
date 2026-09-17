"""add color to special reward home banners

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-09-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c0d1e2f3a4b5'
down_revision: Union[str, Sequence[str], None] = 'b9c0d1e2f3a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'special_reward_home_banners',
        sa.Column('color', sa.Text(), server_default='blue', nullable=False),
    )
    op.create_check_constraint(
        'ck_special_reward_home_banners_color',
        'special_reward_home_banners',
        "color IN ('red', 'orange', 'yellow', 'green', 'blue', 'purple')",
    )
    op.alter_column(
        'special_reward_home_banners',
        'color',
        existing_type=sa.Text(),
        server_default=None,
        existing_nullable=False,
    )


def downgrade() -> None:
    op.drop_constraint(
        'ck_special_reward_home_banners_color',
        'special_reward_home_banners',
        type_='check',
    )
    op.drop_column('special_reward_home_banners', 'color')
