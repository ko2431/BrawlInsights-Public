"""add_post_reposts

なんでも掲示板の投稿のリポスト。
posts に最後にリポストした日時（reposted_at）を足し、「最新」「話題」の並び順 COALESCE(reposted_at, created_at) の式インデックスを作る。
NULL 可・既定値なしの列の追加はメタデータだけの変更で、行の書き換えは起きない。posts は数万件規模のため、インデックスの作成も短時間で終わる。
ただし ACCESS EXCLUSIVE ロックを取るので、ほかの処理を長く待たせないよう lock_timeout を付ける（取れなければ失敗するので、やり直す）。
リポストの履歴（post_reposts）は、ユーザー単位の間隔の判定と運用状況の確認に使う。

Revision ID: faefe646d117
Revises: cbd00528a7b9
Create Date: 2026-10-24 18:26:09.733769

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'faefe646d117'
down_revision: Union[str, Sequence[str], None] = 'cbd00528a7b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_table('post_reposts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('post_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('tokens_spent', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('tokens_spent >= 0', name='ck_post_reposts_tokens_spent_nonnegative'),
    sa.ForeignKeyConstraint(['post_id'], ['posts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_post_reposts_post_id_created_at_desc', 'post_reposts', ['post_id', sa.literal_column('created_at DESC')], unique=False)
    op.create_index('idx_post_reposts_user_id_created_at_desc', 'post_reposts', ['user_id', sa.literal_column('created_at DESC')], unique=False)
    op.add_column('posts', sa.Column('reposted_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('idx_posts_type_activity_at_desc', 'posts', ['type', sa.literal_column('COALESCE(reposted_at, created_at) DESC')], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_posts_type_activity_at_desc', table_name='posts')
    op.drop_column('posts', 'reposted_at')
    op.drop_index('idx_post_reposts_user_id_created_at_desc', table_name='post_reposts')
    op.drop_index('idx_post_reposts_post_id_created_at_desc', table_name='post_reposts')
    op.drop_table('post_reposts')
