"""add user_blocks source_post_id

アカウントタブのブロック一覧で、ブロックしたゲストを表示するための user_blocks.source_post_id。
ゲストをブロックしたときに、そのきっかけになった投稿を記録する。
既存のゲストのブロックは、そのゲストの最新の投稿で埋める。

Revision ID: 922cade77e67
Revises: 39e9e060f360
Create Date: 2026-10-20 13:54:06.961131

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '922cade77e67'
down_revision: Union[str, Sequence[str], None] = '39e9e060f360'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 自動生成に含まれていた maps_legacy・modes_legacy の削除は、今回の変更と関係がないので外した
    op.add_column('user_blocks', sa.Column('source_post_id', sa.Integer(), nullable=True))
    op.create_foreign_key('user_blocks_source_post_id_fkey', 'user_blocks', 'posts', ['source_post_id'], ['id'], ondelete='SET NULL')

    # 既存データの埋め込みは posts を一通り読むため、user_blocks の排他ロックを持ったまま行わないよう
    # DDL をコミットしてから別トランザクションで行う
    with op.get_context().autocommit_block():
        op.execute("""
            UPDATE user_blocks ub
            SET source_post_id = latest.id
            FROM (
                SELECT DISTINCT ON (p.host_anonymous_id) p.host_anonymous_id, p.id
                FROM posts p
                WHERE p.host_anonymous_id IN (
                    SELECT blocked_anonymous_id FROM user_blocks WHERE blocked_anonymous_id IS NOT NULL
                )
                ORDER BY p.host_anonymous_id, p.created_at DESC
            ) latest
            WHERE ub.blocked_anonymous_id = latest.host_anonymous_id
              AND ub.source_post_id IS NULL
        """)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('user_blocks_source_post_id_fkey', 'user_blocks', type_='foreignkey')
    op.drop_column('user_blocks', 'source_post_id')
