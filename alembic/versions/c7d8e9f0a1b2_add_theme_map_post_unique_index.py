"""add unique index for theme map board posts

Revision ID: c7d8e9f0a1b2
Revises: be6b0210de3b
Create Date: 2026-09-28

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'c7d8e9f0a1b2'
down_revision: Union[str, Sequence[str], None] = 'be6b0210de3b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # マップ掲示板（テーマ掲示板のマップカテゴリ）は map_id ごとに1件だけにする
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_posts_theme_map_id
        ON posts ((custom_settings->>'map_id'))
        WHERE type = 'theme'
          AND category = 'map'
          AND is_deleted = FALSE
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_posts_theme_map_id")
