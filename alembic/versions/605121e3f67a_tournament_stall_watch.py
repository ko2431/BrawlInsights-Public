"""tournament_stall_watch

大会の止まった進行の検知（検討メモ 13.6）。試合・ロビーが確認待ちになった時刻と放置のお知らせの段階
（tournament_matches.review_at / review_alert、確認待ちだけの部分インデックス）、自動中止の予告の時刻（tournaments.stall_warned_at）。
今すでに確認待ちの試合は、適用した時刻を確認待ちになった時刻とする（適用直後にまとめてお知らせが出ないように）。
どちらの表も小さく、列の追加は既定値つきでもメタデータの変更だけで終わるが、書き込みを長く待たせないよう lock_timeout を付ける。

Revision ID: 605121e3f67a
Revises: 22a51a361ee5
Create Date: 2026-10-26 01:04:00.763596

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '605121e3f67a'
down_revision: Union[str, Sequence[str], None] = '22a51a361ee5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column('tournament_matches', sa.Column('review_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('tournament_matches', sa.Column('review_alert', sa.SmallInteger(), server_default='0', nullable=False))
    op.create_index('idx_tournament_matches_review', 'tournament_matches', ['review_at'], unique=False, postgresql_where=sa.text("state = 'review'"))
    op.add_column('tournaments', sa.Column('stall_warned_at', sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE tournament_matches SET review_at = now() WHERE state = 'review'")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('tournaments', 'stall_warned_at')
    op.drop_index('idx_tournament_matches_review', table_name='tournament_matches', postgresql_where=sa.text("state = 'review'"))
    op.drop_column('tournament_matches', 'review_alert')
    op.drop_column('tournament_matches', 'review_at')
