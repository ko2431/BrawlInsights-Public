"""add_messages_mentions

チャットのメンション（大会のチャット。掲示板のチャットでも後で使う）。
messages は大きいテーブルだが、NULL 可・既定値なしの列の追加はメタデータだけの変更で、行の書き換えは起きない。
ただし ACCESS EXCLUSIVE ロックを取るので、ほかの処理を長く待たせないよう lock_timeout を付ける（取れなければ失敗するので、やり直す）。

Revision ID: bfa2177ddcf9
Revises: ccdbaec2886d
Create Date: 2026-10-23 21:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'bfa2177ddcf9'
down_revision: Union[str, Sequence[str], None] = 'ccdbaec2886d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column('messages', sa.Column('mentions', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_column('messages', 'mentions')
