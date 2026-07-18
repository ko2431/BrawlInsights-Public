"""add minigame tables and user ad play columns

Revision ID: c7e8f9a0b1d2
Revises: 4aa6c2581dbe
Create Date: 2026-07-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'c7e8f9a0b1d2'
down_revision: Union[str, Sequence[str], None] = '4aa6c2581dbe'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('minigame_ad_play_count', sa.Integer(), server_default='0', nullable=False),
    )
    op.add_column(
        'users',
        sa.Column('last_minigame_ad_play_date', sa.Date(), nullable=True),
    )

    op.create_table(
        'minigame_campaigns',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name_ja', sa.Text(), nullable=False),
        sa.Column('name_en', sa.Text(), nullable=False),
        sa.Column('game_type', sa.Text(), nullable=False),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('prizes', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('price_ad_tokens', sa.Integer(), nullable=True),
        sa.Column('price_token_tokens', sa.Integer(), nullable=True),
        sa.Column('ad_daily_limit', sa.Integer(), nullable=True),
        sa.Column('expected_total_plays', sa.Integer(), nullable=True),
        sa.Column('is_invalid', sa.Boolean(), server_default='False', nullable=False),
        sa.Column('terms_extra_ja', sa.Text(), nullable=True),
        sa.Column('terms_extra_en', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_minigame_campaigns_starts_at', 'minigame_campaigns', ['starts_at'])
    op.create_index('ix_minigame_campaigns_ends_at', 'minigame_campaigns', ['ends_at'])
    op.create_index('ix_minigame_campaigns_is_invalid', 'minigame_campaigns', ['is_invalid'])

    op.create_table(
        'minigame_prize_stocks',
        sa.Column('campaign_id', sa.Integer(), sa.ForeignKey('minigame_campaigns.id', ondelete='CASCADE'), nullable=False),
        sa.Column('rank', sa.SmallInteger(), nullable=False),
        sa.Column('remaining', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('campaign_id', 'rank', name='minigame_prize_stocks_pkey'),
    )

    op.create_table(
        'minigame_plays',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('campaign_id', sa.Integer(), sa.ForeignKey('minigame_campaigns.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('play_method', sa.Text(), nullable=False),
        sa.Column('tokens_spent', sa.Integer(), server_default='0', nullable=False),
        sa.Column('result_rank', sa.SmallInteger(), nullable=False),
        sa.Column('result_prizes', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('animation_payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('status', sa.Text(), server_default='pending_reveal', nullable=False),
        sa.Column('grant_log', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('granted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('gift_fulfillment_status', sa.Text(), nullable=True),
        sa.Column('is_admin_play', sa.Boolean(), server_default='False', nullable=False),
    )
    op.create_index('ix_minigame_plays_user_id_created_at', 'minigame_plays', ['user_id', sa.text('created_at DESC')])
    op.create_index('ix_minigame_plays_campaign_id_created_at', 'minigame_plays', ['campaign_id', sa.text('created_at DESC')])
    op.create_index('ix_minigame_plays_user_id_status', 'minigame_plays', ['user_id', 'status'])
    op.create_index('ix_minigame_plays_gift_fulfillment_status', 'minigame_plays', ['gift_fulfillment_status'])


def downgrade() -> None:
    op.drop_index('ix_minigame_plays_gift_fulfillment_status', table_name='minigame_plays')
    op.drop_index('ix_minigame_plays_user_id_status', table_name='minigame_plays')
    op.drop_index('ix_minigame_plays_campaign_id_created_at', table_name='minigame_plays')
    op.drop_index('ix_minigame_plays_user_id_created_at', table_name='minigame_plays')
    op.drop_table('minigame_plays')
    op.drop_table('minigame_prize_stocks')
    op.drop_index('ix_minigame_campaigns_is_invalid', table_name='minigame_campaigns')
    op.drop_index('ix_minigame_campaigns_ends_at', table_name='minigame_campaigns')
    op.drop_index('ix_minigame_campaigns_starts_at', table_name='minigame_campaigns')
    op.drop_table('minigame_campaigns')
    op.drop_column('users', 'last_minigame_ad_play_date')
    op.drop_column('users', 'minigame_ad_play_count')
