"""add tournament notice post

大会ページの「お知らせ」タブの器（posts の種別 tournament_notice）を指す tournaments.notice_post_id。
器は最初のお知らせの投稿のときに作るので、既存の大会は NULL のまま。

Revision ID: 39e9e060f360
Revises: 0e2fa42f0058
Create Date: 2026-10-19 19:48:38.305189

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '39e9e060f360'
down_revision: Union[str, Sequence[str], None] = '0e2fa42f0058'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 自動生成に含まれていた maps_legacy・modes_legacy の削除は、今回の変更と関係がないので外した
    op.add_column('tournaments', sa.Column('notice_post_id', sa.Integer(), nullable=True))
    op.create_foreign_key('tournaments_notice_post_id_fkey', 'tournaments', 'posts', ['notice_post_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('tournaments_notice_post_id_fkey', 'tournaments', type_='foreignkey')
    op.drop_column('tournaments', 'notice_post_id')
