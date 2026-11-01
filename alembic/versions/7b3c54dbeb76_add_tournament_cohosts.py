"""add_tournament_cohosts

大会開催ツールの副主催者（notes_private/tournament_plan_2026-10-03.md の 13.16・D69）。
副主催者の表（tournament_cohosts。大会・ユーザー・招待した人・日時）と、
主催者が作る招待リンク（tournaments.cohost_invite_token と、リンクを作った人）を足す。
既存の大会は副主催者なし・リンクなしで、動きは変わらない。

Revision ID: 7b3c54dbeb76
Revises: fd45312e2516
Create Date: 2026-11-02 02:03:50.626975

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7b3c54dbeb76'
down_revision: Union[str, Sequence[str], None] = 'fd45312e2516'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('tournament_cohosts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tournament_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('invited_by_user_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['invited_by_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tournament_id'], ['tournaments.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tournament_id', 'user_id', name='uq_tournament_cohosts')
    )
    op.create_index('idx_tournament_cohosts_user', 'tournament_cohosts', ['user_id'], unique=False)
    op.add_column('tournaments', sa.Column('cohost_invite_token', sa.Text(), nullable=True))
    op.add_column('tournaments', sa.Column('cohost_invite_by_user_id', sa.Integer(), nullable=True))
    # 名前は PostgreSQL の既定と同じにする（ほかの外部キーと同じく、モデルでは名前を付けていないため）
    op.create_foreign_key('tournaments_cohost_invite_by_user_id_fkey', 'tournaments', 'users',
                          ['cohost_invite_by_user_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('tournaments_cohost_invite_by_user_id_fkey', 'tournaments', type_='foreignkey')
    op.drop_column('tournaments', 'cohost_invite_by_user_id')
    op.drop_column('tournaments', 'cohost_invite_token')
    op.drop_index('idx_tournament_cohosts_user', table_name='tournament_cohosts')
    op.drop_table('tournament_cohosts')
