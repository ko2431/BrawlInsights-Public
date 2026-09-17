"""add special reward link tables

Revision ID: b9c0d1e2f3a4
Revises: e2f3a4b5c6d7
Create Date: 2026-09-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'b9c0d1e2f3a4'
down_revision: Union[str, Sequence[str], None] = 'e2f3a4b5c6d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'special_reward_links',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('link_type', sa.Text(), nullable=False),
        sa.Column('name_ja', sa.Text(), nullable=False),
        sa.Column('name_en', sa.Text(), nullable=False),
        sa.Column('url', sa.Text(), nullable=True),
        sa.Column('max_uses', sa.Integer(), nullable=True),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('is_invalid', sa.Boolean(), server_default='False', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            "link_type IN ('single_unlimited', 'single_limited', 'link_set')",
            name='ck_special_reward_links_type',
        ),
        sa.CheckConstraint(
            """(
                (link_type = 'single_unlimited' AND url IS NOT NULL AND max_uses IS NULL)
                OR (link_type = 'single_limited' AND url IS NOT NULL AND max_uses >= 1)
                OR (link_type = 'link_set' AND url IS NULL AND max_uses >= 1)
            )""",
            name='ck_special_reward_links_shape',
        ),
    )
    op.create_index('ix_special_reward_links_created_by_user_id', 'special_reward_links', ['created_by_user_id'])
    op.create_index('ix_special_reward_links_is_invalid', 'special_reward_links', ['is_invalid'])
    op.create_index(
        'uq_special_reward_links_url',
        'special_reward_links',
        ['url'],
        unique=True,
        postgresql_where=sa.text('url IS NOT NULL'),
    )

    op.create_table(
        'special_reward_home_banners',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('link_id', sa.Integer(), sa.ForeignKey('special_reward_links.id', ondelete='CASCADE'), nullable=False),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('icon_path', sa.Text(), nullable=True),
        sa.Column('title_ja', sa.Text(), nullable=False),
        sa.Column('title_en', sa.Text(), nullable=False),
        sa.Column('created_by_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('click_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('ended_reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            "ended_reason IS NULL OR ended_reason IN ('scheduled', 'stock_empty', 'manual')",
            name='ck_special_reward_home_banners_ended_reason',
        ),
        sa.CheckConstraint('ends_at > starts_at', name='ck_special_reward_home_banners_period'),
    )
    op.create_index('ix_special_reward_home_banners_link_id', 'special_reward_home_banners', ['link_id'])
    op.create_index('ix_special_reward_home_banners_period', 'special_reward_home_banners', ['starts_at', 'ends_at'])

    op.create_table(
        'special_reward_link_items',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('link_id', sa.Integer(), sa.ForeignKey('special_reward_links.id', ondelete='CASCADE'), nullable=False),
        sa.Column('url', sa.Text(), nullable=False),
        sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('claimed_by_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('source', sa.Text(), nullable=True),
        sa.Column('play_id', sa.Integer(), sa.ForeignKey('minigame_plays.id', ondelete='SET NULL'), nullable=True),
        sa.Column('banner_id', sa.Integer(), sa.ForeignKey('special_reward_home_banners.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.UniqueConstraint('url', name='uq_special_reward_link_items_url'),
        sa.CheckConstraint(
            "source IS NULL OR source IN ('home_banner', 'minigame')",
            name='ck_special_reward_link_items_source',
        ),
    )
    op.create_index(
        'ix_special_reward_link_items_link_id_claimed_at',
        'special_reward_link_items',
        ['link_id', 'claimed_at'],
    )

    op.create_table(
        'special_reward_link_claims',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('link_id', sa.Integer(), sa.ForeignKey('special_reward_links.id', ondelete='CASCADE'), nullable=False),
        sa.Column('item_id', sa.Integer(), sa.ForeignKey('special_reward_link_items.id', ondelete='SET NULL'), nullable=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('play_id', sa.Integer(), sa.ForeignKey('minigame_plays.id', ondelete='SET NULL'), nullable=True),
        sa.Column('banner_id', sa.Integer(), sa.ForeignKey('special_reward_home_banners.id', ondelete='SET NULL'), nullable=True),
        sa.Column('ip', postgresql.INET(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            "source IN ('home_banner', 'minigame')",
            name='ck_special_reward_link_claims_source',
        ),
    )
    op.create_index(
        'uq_special_reward_link_claims_home_user',
        'special_reward_link_claims',
        ['link_id', 'user_id'],
        unique=True,
        postgresql_where=sa.text("source = 'home_banner' AND user_id IS NOT NULL"),
    )
    op.create_index(
        'uq_special_reward_link_claims_minigame_play',
        'special_reward_link_claims',
        ['play_id'],
        unique=True,
        postgresql_where=sa.text("source = 'minigame' AND play_id IS NOT NULL"),
    )
    op.create_index(
        'ix_special_reward_link_claims_link_id_created_at',
        'special_reward_link_claims',
        ['link_id', sa.text('created_at DESC')],
    )
    op.create_index('ix_special_reward_link_claims_user_id', 'special_reward_link_claims', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_special_reward_link_claims_user_id', table_name='special_reward_link_claims')
    op.drop_index('ix_special_reward_link_claims_link_id_created_at', table_name='special_reward_link_claims')
    op.drop_index('uq_special_reward_link_claims_minigame_play', table_name='special_reward_link_claims')
    op.drop_index('uq_special_reward_link_claims_home_user', table_name='special_reward_link_claims')
    op.drop_table('special_reward_link_claims')
    op.drop_index('ix_special_reward_link_items_link_id_claimed_at', table_name='special_reward_link_items')
    op.drop_table('special_reward_link_items')
    op.drop_index('ix_special_reward_home_banners_period', table_name='special_reward_home_banners')
    op.drop_index('ix_special_reward_home_banners_link_id', table_name='special_reward_home_banners')
    op.drop_table('special_reward_home_banners')
    op.drop_index('uq_special_reward_links_url', table_name='special_reward_links')
    op.drop_index('ix_special_reward_links_is_invalid', table_name='special_reward_links')
    op.drop_index('ix_special_reward_links_created_by_user_id', table_name='special_reward_links')
    op.drop_table('special_reward_links')
