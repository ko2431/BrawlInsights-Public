"""add users hub_orders

統計データ・ツールタブのコンテンツの並び（サブタブごと）をアカウントに保存する列を users に足す
（notes_private/hub_renewal_plan_2026-11-25.md 2.4）。未ログインは端末、ログイン中はアカウントに保存する。
既定値が定数（'{}'）の列の追加なので、表は書き直されずすぐ終わる。ただし一瞬だけ強いロックを取るので、
長いクエリの後ろで待って通常のクエリを詰まらせないよう、5秒で取れなければ失敗させる（やり直せばよい）。

Revision ID: 3dab75401b11
Revises: ef51bf33c458
Create Date: 2026-11-25 03:41:40.018271

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '3dab75401b11'
down_revision: Union[str, Sequence[str], None] = 'ef51bf33c458'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column('users', sa.Column('hub_orders', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'hub_orders')
