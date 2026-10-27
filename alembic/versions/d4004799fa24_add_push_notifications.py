"""add push notifications

プッシュ通知（検討メモ notes_private/push_image_plan_2026-10-27.md の 3.4）のため、受け取る端末（push_devices）と、
users に種類ごとの「プッシュでも届けるか」の列・管理者通知のプッシュのレベルを足す。
users の列はどれも定数の既定値つきなので、書き直しは起きない（PostgreSQL 11 以降はカタログの変更だけ）。
管理者通知のレベルの制約は、付けるときに users を1回読む（行は既定値の 30 なので、すぐ終わる）。

Revision ID: d4004799fa24
Revises: 5d2f8a6c1e47
Create Date: 2026-10-27 15:57:24.988399

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4004799fa24'
down_revision: Union[str, Sequence[str], None] = '5d2f8a6c1e47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PUSH_COLUMNS = (
    ('push_tournament_progress_enabled', 'True'),
    ('push_tournament_chat_enabled', 'True'),
    ('push_tournament_notice_enabled', 'True'),
    ('push_post_like_enabled', 'False'),
    ('push_own_post_message_enabled', 'True'),
    ('push_participated_thread_message_enabled', 'False'),
    ('push_subscribed_thread_message_enabled', 'True'),
    ('push_message_mention_enabled', 'True'),
    ('push_message_reply_enabled', 'True'),
    ('push_message_reaction_enabled', 'False'),
    ('push_token_gift_enabled', 'True'),
)


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_table('push_devices',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('platform', sa.Text(), nullable=False),
    sa.Column('token', sa.Text(), nullable=False),
    sa.Column('p256dh', sa.Text(), nullable=True),
    sa.Column('auth', sa.Text(), nullable=True),
    sa.Column('lang', sa.Text(), server_default='ja', nullable=False),
    sa.Column('app_version', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("platform IN ('ios', 'android', 'web')", name='ck_push_devices_platform'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token')
    )
    op.create_index('idx_push_devices_user_id', 'push_devices', ['user_id'], unique=False)
    for name, default in PUSH_COLUMNS:
        op.add_column('users', sa.Column(name, sa.Boolean(), server_default=default, nullable=False))
    # [この部分は公開用リポジトリでは非公開にされています]


def downgrade() -> None:
    """Downgrade schema."""
    # [この部分は公開用リポジトリでは非公開にされています]
    for name, _ in reversed(PUSH_COLUMNS):
        op.drop_column('users', name)
    op.drop_index('idx_push_devices_user_id', table_name='push_devices')
    op.drop_table('push_devices')
