"""add_notification_message_mention_enabled

掲示板の通知設定「自分へのメンション」。
users は行数の多いテーブルだが、定数の既定値を持つ列の追加はメタデータだけの変更（PostgreSQL 11 以降）で、行の書き換えは起きない。
ただし ACCESS EXCLUSIVE ロックを取るので、ほかの処理を長く待たせないよう lock_timeout を付ける（取れなければ失敗するので、やり直す）。

Revision ID: cbd00528a7b9
Revises: bfa2177ddcf9
Create Date: 2026-10-23 22:16:27.609348

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cbd00528a7b9'
down_revision: Union[str, Sequence[str], None] = 'bfa2177ddcf9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column('users', sa.Column('notification_message_mention_enabled', sa.Boolean(), server_default='True', nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_column('users', 'notification_message_mention_enabled')
