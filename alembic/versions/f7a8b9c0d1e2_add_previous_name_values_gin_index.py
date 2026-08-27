"""add gin index on previous_names values for player search

Revision ID: f7a8b9c0d1e2
Revises: c1d2e3f4a5b6
Create Date: 2026-08-27

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f7a8b9c0d1e2'
down_revision: Union[str, Sequence[str], None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """改名前検索用。値配列への @> が GIN を使えるようにする。

    既存の idx_gin_players_previous_names_path_ops は $.* の値検索では使えない。
    約1800万行のため CONCURRENTLY で作成する（テーブルロックしない）。
    """
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_gin_players_previous_name_values "
            "ON players USING gin "
            "(jsonb_path_query_array(previous_names, '$.*'::jsonpath) jsonb_path_ops)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_gin_players_previous_name_values")
