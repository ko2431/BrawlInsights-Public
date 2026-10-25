"""add_tournaments_hub_done_index

大会開催ツールのトップの「終了」タブで、過去のすべての大会を終了の新しい順に30件ずつ出すためのインデックス。
（公開・未削除の終了した大会だけの部分インデックス。tournaments は小さい表なので、作成は短時間で終わる。
ACCESS EXCLUSIVE ではなく SHARE ロックだが、書き込みを長く待たせないよう lock_timeout を付ける）
autogenerate が開発DBにだけある maps_legacy / modes_legacy の削除を混ぜたので取り除いた。

Revision ID: 22a51a361ee5
Revises: 5df570c6a1a7
Create Date: 2026-10-25 21:02:49.216299

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '22a51a361ee5'
down_revision: Union[str, Sequence[str], None] = '5df570c6a1a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_index('idx_tournaments_hub_done', 'tournaments', [sa.literal_column('finished_at DESC'), sa.literal_column('id DESC')],
                    unique=False, postgresql_where=sa.text("status = 'done' AND NOT is_deleted AND visibility = 'public'"))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_tournaments_hub_done', table_name='tournaments',
                  postgresql_where=sa.text("status = 'done' AND NOT is_deleted AND visibility = 'public'"))
