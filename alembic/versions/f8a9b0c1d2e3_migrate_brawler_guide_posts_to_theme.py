"""migrate brawler_guide posts to theme board

Revision ID: f8a9b0c1d2e3
Revises: e1f2a3b4c5d6
Create Date: 2026-09-05

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'f8a9b0c1d2e3'
down_revision: Union[str, Sequence[str], None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 同一 brawler_id の重複は、メッセージが多い（同数なら古い）投稿を残して削除扱いにする
    op.execute(
        """
        WITH ranked AS (
            SELECT
                p.id,
                ROW_NUMBER() OVER (
                    PARTITION BY p.custom_settings->>'brawler_id'
                    ORDER BY COALESCE(mc.cnt, 0) DESC, p.created_at ASC, p.id ASC
                ) AS rn
            FROM posts p
            LEFT JOIN (
                SELECT thread_id, COUNT(*)::int AS cnt
                FROM messages
                WHERE is_deleted = FALSE
                GROUP BY thread_id
            ) mc ON mc.thread_id = p.id
            WHERE p.type IN ('brawler_guide', 'theme')
              AND COALESCE(p.custom_settings->>'brawler_id', '') <> ''
              AND p.is_deleted = FALSE
        )
        UPDATE posts
        SET is_deleted = TRUE
        WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
        """
    )
    op.execute(
        """
        UPDATE posts
        SET type = 'theme',
            category = 'brawler'
        WHERE type = 'brawler_guide'
        """
    )
    op.execute(
        """
        INSERT INTO posts (
            type, host_id, host_ip, host_anonymous_id, region,
            host_player_tag, host_club_tag, link, comment,
            conditions_application_type, chat_permission_level, category, mode,
            hashtags, custom_settings, permitted_ids, prohibited_ids,
            required_highest_trophies, required_current_trophies,
            required_ranked_highest_rank, required_ranked_current_rank,
            required_ranked_highest_score, required_ranked_current_score,
            required_solo_pl_rank, required_max_power_brawlers, required_prestige, other_conditions
        )
        SELECT
            'theme', NULL, '127.0.0.1'::inet, NULL, NULL,
            NULL, NULL, NULL, NULL,
            'and', 30, 'brawler', NULL,
            '[]'::jsonb, jsonb_build_object('brawler_id', b.id), '[]'::jsonb, '[]'::jsonb,
            NULL, NULL,
            NULL, NULL,
            NULL, NULL,
            NULL, NULL, NULL, '{}'::jsonb
        FROM brawlers b
        WHERE NOT EXISTS (
            SELECT 1
            FROM posts p
            WHERE p.is_deleted = FALSE
              AND p.type = 'theme'
              AND p.category = 'brawler'
              AND p.custom_settings->>'brawler_id' = b.id::text
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_posts_theme_brawler_id
        ON posts ((custom_settings->>'brawler_id'))
        WHERE type = 'theme'
          AND category = 'brawler'
          AND is_deleted = FALSE
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_posts_type_category
        ON posts (type, category)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_posts_type_category")
    op.execute("DROP INDEX IF EXISTS uq_posts_theme_brawler_id")
    op.execute(
        """
        UPDATE posts
        SET type = 'brawler_guide',
            category = NULL
        WHERE type = 'theme'
          AND category = 'brawler'
        """
    )
