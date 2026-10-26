"""tournament_stage_kinds

大会のステージの種類（tournament_stages.kind）に、総当たり（round_robin）を足す（検討メモ 14章・D64）。
あとで足すスイスドロー（swiss）・ダブルエリミネーション（double_elim）も、マイグレーションを重ねないよう先に入れる。
CHECK 制約の付け直しだけ。表は小さいが、書き込みを長く待たせないよう lock_timeout を付ける。

Revision ID: 8c41d2e7a9b3
Revises: 605121e3f67a
Create Date: 2026-10-26 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '8c41d2e7a9b3'
down_revision: Union[str, Sequence[str], None] = '605121e3f67a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_constraint('ck_tournament_stages_kind', 'tournament_stages', type_='check')
    op.create_check_constraint(
        'ck_tournament_stages_kind', 'tournament_stages',
        "kind IN ('single_elim', 'sd_points', 'round_robin', 'swiss', 'double_elim')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_constraint('ck_tournament_stages_kind', 'tournament_stages', type_='check')
    op.create_check_constraint('ck_tournament_stages_kind', 'tournament_stages', "kind IN ('single_elim', 'sd_points')")
