"""allow_tournament_best_of_7

大会の試合の BO（外部）に BO7 を足す（検討メモ D60。パワーマッチを複数本で戦う形式と、外部 BO7）。
tournament_matches の CHECK 制約 ck_tournament_matches_best_of を (1, 3, 5) から (1, 3, 5, 7) に広げるだけ。
autogenerate は CHECK 制約の変更を検出しないので手で書いた。
制約の付け替えは ACCESS EXCLUSIVE ロックを取るので、NOT VALID で付けてから VALIDATE する
（VALIDATE は行の読み取りだけで、書き込みを止めない）。広げる変更なので既存の行はすべて通る。

Revision ID: 5df570c6a1a7
Revises: faefe646d117
Create Date: 2026-10-25 17:24:44.630515

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '5df570c6a1a7'
down_revision: Union[str, Sequence[str], None] = 'faefe646d117'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_constraint('ck_tournament_matches_best_of', 'tournament_matches', type_='check')
    op.execute("ALTER TABLE tournament_matches ADD CONSTRAINT ck_tournament_matches_best_of "
               "CHECK (best_of IN (1, 3, 5, 7)) NOT VALID")
    op.execute("ALTER TABLE tournament_matches VALIDATE CONSTRAINT ck_tournament_matches_best_of")


def downgrade() -> None:
    """Downgrade schema."""
    # BO7 の試合が残っていると元の制約を検証できないので、NOT VALID のまま戻す（新しく書く行だけを確かめる）
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_constraint('ck_tournament_matches_best_of', 'tournament_matches', type_='check')
    op.execute("ALTER TABLE tournament_matches ADD CONSTRAINT ck_tournament_matches_best_of "
               "CHECK (best_of IN (1, 3, 5)) NOT VALID")
