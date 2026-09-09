"""add app login mission flags to users

Revision ID: d9f05b04d957
Revises: f8a9b0c1d2e3
Create Date: 2026-09-09 15:27:15.576268

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd9f05b04d957'
down_revision: Union[str, Sequence[str], None] = 'f8a9b0c1d2e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('is_ios_app_login_cleared', sa.Boolean(), server_default='False', nullable=False),
    )
    op.add_column(
        'users',
        sa.Column('is_android_app_login_cleared', sa.Boolean(), server_default='False', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('users', 'is_android_app_login_cleared')
    op.drop_column('users', 'is_ios_app_login_cleared')
