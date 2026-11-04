"""drop duplicate battles tag time index

Revision ID: 3b670c17d222
Revises: ae0e8af3cbb7
Create Date: 2026-11-04 19:53:28.249692

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '3b670c17d222'
down_revision: Union[str, Sequence[str], None] = 'ae0e8af3cbb7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """(tag, datetime DESC) の索引は、主キー battles_pkey (tag, datetime) を逆向きにたどれば足りるため削除する。"""
    # 書き込みの多い大きなテーブルのため、テーブルをロックしない CONCURRENTLY をトランザクション外で実行する
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_battles_tag_time")


def downgrade() -> None:
    # 作り直しは全件を読むため、時間がかかる
    with op.get_context().autocommit_block():
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_battles_tag_time ON battles (tag, datetime DESC)")
