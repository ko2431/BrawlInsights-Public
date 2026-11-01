"""add_tournament_match_checkin

大会開催ツールの試合のチェックイン（notes_private/tournament_plan_2026-10-03.md の 13.11・D69）。
試合（SD はロビー）の期限（tournament_matches.checkin_deadline_at と worker 用の部分インデックス）、
枠ごとのチェックインしたメンバーと、必要な人数がそろった時刻（tournament_match_slots）、
続けてチェックインしなかった回数（tournament_entries.checkin_misses。2回で失格）を足す。
どれも既定値つきで、既存の大会（設定なし＝チェックインなし）の動きは変わらない。

Revision ID: fd45312e2516
Revises: 1583f3c04f80
Create Date: 2026-11-01 22:33:24.583587

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'fd45312e2516'
down_revision: Union[str, Sequence[str], None] = '1583f3c04f80'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('tournament_entries', sa.Column('checkin_misses', sa.SmallInteger(), server_default='0', nullable=False))
    op.add_column('tournament_match_slots', sa.Column('checkin_user_ids', postgresql.ARRAY(sa.Integer()), server_default='{}', nullable=False))
    op.add_column('tournament_match_slots', sa.Column('checked_in_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('tournament_matches', sa.Column('checkin_deadline_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('idx_tournament_matches_checkin', 'tournament_matches', ['checkin_deadline_at'], unique=False, postgresql_where=sa.text('checkin_deadline_at IS NOT NULL'))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_tournament_matches_checkin', table_name='tournament_matches', postgresql_where=sa.text('checkin_deadline_at IS NOT NULL'))
    op.drop_column('tournament_matches', 'checkin_deadline_at')
    op.drop_column('tournament_match_slots', 'checked_in_at')
    op.drop_column('tournament_match_slots', 'checkin_user_ids')
    op.drop_column('tournament_entries', 'checkin_misses')
