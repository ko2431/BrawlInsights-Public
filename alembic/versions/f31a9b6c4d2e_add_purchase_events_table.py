"""add purchase_events table

Revision ID: f31a9b6c4d2e
Revises: 7d4b2c1a9f30
Create Date: 2026-03-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f31a9b6c4d2e'
down_revision: Union[str, Sequence[str], None] = '7d4b2c1a9f30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'purchase_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('provider', sa.Text(), nullable=False),
        sa.Column('external_event_id', sa.Text(), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('app_user_id', sa.Text(), nullable=True),
        sa.Column('event_type', sa.Text(), nullable=False),
        sa.Column('product_id', sa.Text(), nullable=True),
        sa.Column('entitlement_id', sa.Text(), nullable=True),
        sa.Column('transaction_id', sa.Text(), nullable=True),
        sa.Column('original_transaction_id', sa.Text(), nullable=True),
        sa.Column('environment', sa.Text(), nullable=True),
        sa.Column('is_sandbox', sa.Boolean(), nullable=False, server_default=sa.text('FALSE')),
        sa.Column('event_timestamp', sa.DateTime(timezone=True), nullable=True),
        sa.Column('raw_payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('external_event_id', name='uq_purchase_events_external_event_id'),
    )

    op.create_index('ix_purchase_events_user_id', 'purchase_events', ['user_id'])
    op.create_index('ix_purchase_events_product_id', 'purchase_events', ['product_id'])
    op.create_index('ix_purchase_events_created_at', 'purchase_events', ['created_at'])
    op.create_index('ix_purchase_events_event_timestamp', 'purchase_events', ['event_timestamp'])


def downgrade() -> None:
    op.drop_index('ix_purchase_events_event_timestamp', table_name='purchase_events')
    op.drop_index('ix_purchase_events_created_at', table_name='purchase_events')
    op.drop_index('ix_purchase_events_product_id', table_name='purchase_events')
    op.drop_index('ix_purchase_events_user_id', table_name='purchase_events')
    op.drop_table('purchase_events')
