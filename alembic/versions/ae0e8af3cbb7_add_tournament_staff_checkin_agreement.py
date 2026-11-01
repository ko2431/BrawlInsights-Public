"""add_tournament_staff_checkin_agreement

大会開催ツールの主催者の不在（notes_private/tournament_plan_2026-10-03.md の 13.11 の 1b・D69・M9〜M11）。
運営のチェックイン（tournaments.staff_checkin_at と、した人）と、確認待ちの合意での確定の中身（tournament_matches.agreement）を足す。
適用の時点でチェックインを締め切った後の大会（組み合わせの公開後・開催中）は、運営のチェックインができなかったので、
済んだものとして扱う（開催中に動きが変わらないように）。

Revision ID: ae0e8af3cbb7
Revises: 7b3c54dbeb76
Create Date: 2026-11-02 02:40:58.442088

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'ae0e8af3cbb7'
down_revision: Union[str, Sequence[str], None] = '7b3c54dbeb76'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('tournament_matches', sa.Column('agreement', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('tournaments', sa.Column('staff_checkin_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('tournaments', sa.Column('staff_checkin_user_id', sa.Integer(), nullable=True))
    # 名前は PostgreSQL の既定と同じにする（ほかの外部キーと同じく、モデルでは名前を付けていないため）
    op.create_foreign_key('tournaments_staff_checkin_user_id_fkey', 'tournaments', 'users',
                          ['staff_checkin_user_id'], ['id'], ondelete='SET NULL')
    op.execute("UPDATE tournaments SET staff_checkin_at = now() WHERE status IN ('ready', 'live')")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('tournaments_staff_checkin_user_id_fkey', 'tournaments', type_='foreignkey')
    op.drop_column('tournaments', 'staff_checkin_user_id')
    op.drop_column('tournaments', 'staff_checkin_at')
    op.drop_column('tournament_matches', 'agreement')
