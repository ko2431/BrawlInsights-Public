"""tournament_double_elim

大会のダブルエリミネーション（検討メモ 14章・D64）のため、試合（tournament_matches）に表の区分（bracket_side）と
敗者の行き先（loser_next_match_id・loser_next_slot）を足す。既存の試合はすべて main（通常の表）。
同じステージの勝者側と敗者側で round・position が重なるので、一意の制約に bracket_side を入れて付け直す。
表は小さいが、書き込みを長く待たせないよう lock_timeout を付ける（NOT NULL の列は既定値つきなので書き直しは起きない）。

Revision ID: 3b7e9c1d5a24
Revises: 8c41d2e7a9b3
Create Date: 2026-10-26 18:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '3b7e9c1d5a24'
down_revision: Union[str, Sequence[str], None] = '8c41d2e7a9b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column('tournament_matches', sa.Column('bracket_side', sa.Text(), server_default='main', nullable=False))
    op.add_column('tournament_matches', sa.Column('loser_next_match_id', sa.Integer(), nullable=True))
    op.add_column('tournament_matches', sa.Column('loser_next_slot', sa.SmallInteger(), nullable=True))
    op.create_foreign_key(
        'tournament_matches_loser_next_match_id_fkey', 'tournament_matches', 'tournament_matches',
        ['loser_next_match_id'], ['id'], ondelete='SET NULL',
    )
    op.create_check_constraint(
        'ck_tournament_matches_bracket_side', 'tournament_matches', "bracket_side IN ('main', 'losers', 'final')",
    )
    op.drop_constraint('uq_tournament_matches_position', 'tournament_matches', type_='unique')
    op.create_unique_constraint(
        'uq_tournament_matches_position', 'tournament_matches', ['stage_id', 'bracket_side', 'round', 'position'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("DELETE FROM tournament_matches WHERE bracket_side <> 'main'")
    op.drop_constraint('uq_tournament_matches_position', 'tournament_matches', type_='unique')
    op.create_unique_constraint('uq_tournament_matches_position', 'tournament_matches', ['stage_id', 'round', 'position'])
    op.drop_constraint('ck_tournament_matches_bracket_side', 'tournament_matches', type_='check')
    op.drop_constraint('tournament_matches_loser_next_match_id_fkey', 'tournament_matches', type_='foreignkey')
    op.drop_column('tournament_matches', 'loser_next_slot')
    op.drop_column('tournament_matches', 'loser_next_match_id')
    op.drop_column('tournament_matches', 'bracket_side')
