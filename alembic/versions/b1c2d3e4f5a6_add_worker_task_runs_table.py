"""add worker_task_runs table

Revision ID: b1c2d3e4f5a6
Revises: a3b4c5d6e7f8
Create Date: 2026-08-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, Sequence[str], None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'worker_task_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('task_key', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('trigger', sa.Text(), nullable=False),
        sa.Column('scheduled_for', sa.DateTime(timezone=True), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('progress', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('worker_id', sa.Text(), nullable=True),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'success', 'failed', 'interrupted', 'skipped')",
            name='ck_worker_task_runs_status',
        ),
        sa.CheckConstraint(
            "trigger IN ('cron', 'manual', 'catchup', 'resume', 'startup')",
            name='ck_worker_task_runs_trigger',
        ),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_worker_task_runs_task_key_id',
        'worker_task_runs',
        ['task_key', sa.literal_column('id DESC')],
        unique=False,
    )
    op.create_index('ix_worker_task_runs_status_id', 'worker_task_runs', ['status', 'id'], unique=False)
    op.create_index(
        'uq_worker_task_runs_inflight',
        'worker_task_runs',
        ['task_key'],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index('uq_worker_task_runs_inflight', table_name='worker_task_runs')
    op.drop_index('ix_worker_task_runs_status_id', table_name='worker_task_runs')
    op.drop_index('ix_worker_task_runs_task_key_id', table_name='worker_task_runs')
    op.drop_table('worker_task_runs')
