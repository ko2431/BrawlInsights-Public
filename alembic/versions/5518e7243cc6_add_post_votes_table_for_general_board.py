"""add post votes table for general board

Revision ID: 5518e7243cc6
Revises: 66e42e62affd
Create Date: 2026-03-28 20:12:05.554198

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5518e7243cc6'
down_revision: Union[str, Sequence[str], None] = '66e42e62affd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'post_votes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('post_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('vote_type', sa.SmallInteger(), server_default='1', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('vote_type IN (1, -1)', name='ck_post_votes_vote_type'),
        sa.ForeignKeyConstraint(['post_id'], ['posts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('post_id', 'user_id', name='uq_post_votes_post_user'),
    )
    op.create_index('idx_post_votes_post_vote_type', 'post_votes', ['post_id', 'vote_type'], unique=False)
    op.create_index('idx_post_votes_user_id', 'post_votes', ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_post_votes_user_id', table_name='post_votes')
    op.drop_index('idx_post_votes_post_vote_type', table_name='post_votes')
    op.drop_table('post_votes')
