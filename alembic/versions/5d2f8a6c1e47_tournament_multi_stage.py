"""tournament_multi_stage

大会の多段（予選 → 決勝トーナメント。検討メモ 14.13・D65）のため、試合（tournament_matches）に予選のグループ（grp）を、
ステージ（tournament_stages）に決勝トーナメントを始める時刻（start_at）を足す。どちらも NULL を許す列なので書き直しは起きない。

Revision ID: 5d2f8a6c1e47
Revises: 3b7e9c1d5a24
Create Date: 2026-10-26 21:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '5d2f8a6c1e47'
down_revision: Union[str, Sequence[str], None] = '3b7e9c1d5a24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column('tournament_matches', sa.Column('grp', sa.SmallInteger(), nullable=True))
    op.add_column('tournament_stages', sa.Column('start_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_column('tournament_stages', 'start_at')
    op.drop_column('tournament_matches', 'grp')
