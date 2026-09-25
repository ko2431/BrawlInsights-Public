"""add map rotation tables

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
Create Date: 2026-09-25

"""
from typing import Sequence, Union

import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, Sequence[str], None] = 'c0d1e2f3a4b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SEED_SLOTS = [
    {
        "display_order": 1,
        "name_ja": "バトロイ",
        "name_en": "Showdown",
        "icons": ["soloShowdown"],
        "icon_path": None,
        "api_slot_ids": [2, 5, 39],
        "primary_api_slot_id": 2,
        "duration_minutes": 1440,
        "anchor_start": "2026-06-30T05:00:00+09:00",
        "maps": [15001121, 15001012, 15001216, 15001087, 15000950, 15000959, 15000990, 15001278, 15001281, 15000947, 15000953, 15001284, 15001119, 15000032],
    },
    {
        "display_order": 2,
        "name_ja": "サッカー",
        "name_en": "Brawl Ball",
        "icons": ["brawlBall"],
        "icon_path": None,
        "api_slot_ids": [1],
        "primary_api_slot_id": 1,
        "duration_minutes": 1440,
        "anchor_start": "2026-06-30T17:00:00+09:00",
        "maps": [15000143, 15000118, 15000026, 15001270, 15001330, 15001020, 15001054, 15000144, 15000051, 15001331, 15000025, 15000050, 15000024, 15001021],
    },
    {
        "display_order": 3,
        "name_ja": "エメハン",
        "name_en": "Gem Grab",
        "icons": ["gemGrab"],
        "icon_path": None,
        "api_slot_ids": [3],
        "primary_api_slot_id": 3,
        "duration_minutes": 1440,
        "anchor_start": "2026-07-01T09:00:00+09:00",
        "maps": [15000009, 15001332, 15000010, 15000007, 15000931, 15001274, 15001208, 15000499, 15001275, 15000011, 15001051, 15001026, 15000932, 15000115],
    },
    {
        "display_order": 4,
        "name_ja": "ノック",
        "name_en": "Knockout",
        "icons": ["knockout"],
        "icon_path": None,
        "api_slot_ids": [6],
        "primary_api_slot_id": 6,
        "duration_minutes": 1440,
        "anchor_start": "2026-07-12T11:00:00+09:00",
        "maps": [15001173, 15000367, 15000368, 15001058, 15001276, 15001210, 15000548, 15001059, 15001333, 15001127, 15001019, 15001247, 15001018, 15000528],
    },
    {
        "display_order": 5,
        "name_ja": "強奪／ホッゾ",
        "name_en": "Heist/HZ",
        "icons": ["heist", "hotZone"],
        "icon_path": None,
        "api_slot_ids": [4],
        "primary_api_slot_id": 4,
        "duration_minutes": 1440,
        "anchor_start": "2026-07-08T03:00:00+09:00",
        "maps": [15001273, 15001023, 15000053, 15001268, 15001170, 15001269, 15000018, 15000293, 15001099, 15001202, 15001272, 15001329],
    },
    {
        "display_order": 6,
        "name_ja": "ホッケー",
        "name_en": "Hockey",
        "icons": ["airHockey"],
        "icon_path": None,
        "api_slot_ids": [33],
        "primary_api_slot_id": 33,
        "duration_minutes": 1440,
        "anchor_start": "2026-06-30T19:00:00+09:00",
        "maps": [15001265, 15001266, 15001222, 15001338, 15001248, 15001249],
    },
    {
        "display_order": 7,
        "name_ja": "殲滅／バスケ",
        "name_en": "WO/Basket",
        "icons": ["wipeout", "basketBrawl"],
        "icon_path": None,
        "api_slot_ids": [42],
        "primary_api_slot_id": 42,
        "duration_minutes": 1440,
        "anchor_start": "2026-07-03T13:00:00+09:00",
        "maps": [15001163, 15000386, 15001244, 15000387, 15001056, 15000384, 15001201, 15000390],
    },
    {
        "display_order": 8,
        "name_ja": "5対5",
        "name_en": "5v5",
        "icons": ["5v5"],
        "icon_path": None,
        "api_slot_ids": [43],
        "primary_api_slot_id": 43,
        "duration_minutes": 1440,
        "anchor_start": "2026-07-01T21:00:00+09:00",
        "maps": [15000971, 15001049, 15001047, 15000919, 15000920, 15001048, 15001043, 15001050, 15001101],
    },
    {
        "display_order": 9,
        "name_ja": "デュエル／賞金",
        "name_en": "Duels/Bounty",
        "icons": ["duels", "bounty"],
        "icon_path": None,
        "api_slot_ids": [15],
        "primary_api_slot_id": 15,
        "duration_minutes": 1440,
        "anchor_start": "2026-06-30T01:00:00+09:00",
        "maps": [15001334, 15000022, 15001212, 15001336, 15000463, 15000082, 15001335, 15001176, 15001061, 15000005, 15000460, 15001288, 15001097, 15001287],
    },
    {
        "display_order": 10,
        "name_ja": "アリーナ",
        "name_en": "Arena",
        "icons": ["brawlArena"],
        "icon_path": None,
        "api_slot_ids": [41],
        "primary_api_slot_id": 41,
        "duration_minutes": 1440,
        "anchor_start": "2026-06-30T23:00:00+09:00",
        "maps": [15000997, 15001324, 15001241, 15001242, 15001064, 15001065, 15001323],
    },
    {
        "display_order": 11,
        "name_ja": "フリープレイ",
        "name_en": "Free Play",
        "icons": ["friends_old"],
        "icon_path": "/images/ui/friends_old.png",
        "api_slot_ids": [55],
        "primary_api_slot_id": 55,
        "duration_minutes": 120,
        "anchor_start": "2026-03-04T01:00:00+09:00",
        "maps": [15000528, 15000581, 15001101, 15001102, 15000852, 15000853, 15000292, 15000306, 15000959, 15001122, 15000024, 15000143],
    },
]


def upgrade() -> None:
    op.create_table(
        'event_rotation_observations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('api_slot_id', sa.Integer(), nullable=False),
        sa.Column('start_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('map_id', sa.Integer(), nullable=False),
        sa.Column('mode_slug', sa.Text(), nullable=True),
        sa.Column('api_mode_id', sa.Integer(), nullable=True),
        sa.Column('map_name_en', sa.Text(), nullable=True),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('api_slot_id', 'start_time', name='uq_event_rotation_observations_slot_start'),
    )
    op.create_index(
        'idx_event_rotation_observations_slot_start',
        'event_rotation_observations',
        ['api_slot_id', 'start_time'],
    )

    op.create_table(
        'map_rotation_slots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('display_order', sa.Integer(), server_default='0', nullable=False),
        sa.Column('name_ja', sa.Text(), nullable=False),
        sa.Column('name_en', sa.Text(), nullable=False),
        sa.Column('icons', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('icon_path', sa.Text(), nullable=True),
        sa.Column('api_slot_ids', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('primary_api_slot_id', sa.Integer(), nullable=False),
        sa.Column('duration_minutes', sa.Integer(), server_default='1440', nullable=False),
        sa.Column('is_visible', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('needs_review', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('auto_created', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('last_observed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('inference_state', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_map_rotation_slots_display_order', 'map_rotation_slots', ['display_order'])
    op.create_index('idx_map_rotation_slots_primary_api_slot', 'map_rotation_slots', ['primary_api_slot_id'])

    op.create_table(
        'map_rotation_cycles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('slot_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('cycle_length', sa.Integer(), nullable=True),
        sa.Column('duration_minutes', sa.Integer(), nullable=False),
        sa.Column('anchor_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('maps', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('source', sa.Text(), server_default='auto', nullable=False),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("status IN ('confirmed', 'working', 'archived')", name='ck_map_rotation_cycles_status'),
        sa.CheckConstraint("source IN ('seed', 'auto', 'manual')", name='ck_map_rotation_cycles_source'),
        sa.ForeignKeyConstraint(['slot_id'], ['map_rotation_slots.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_map_rotation_cycles_slot_status', 'map_rotation_cycles', ['slot_id', 'status'])
    op.create_index(
        'uq_map_rotation_cycles_confirmed',
        'map_rotation_cycles',
        ['slot_id'],
        unique=True,
        postgresql_where=sa.text("status = 'confirmed'"),
    )
    op.create_index(
        'uq_map_rotation_cycles_working',
        'map_rotation_cycles',
        ['slot_id'],
        unique=True,
        postgresql_where=sa.text("status = 'working'"),
    )

    op.create_table(
        'map_rotation_episodes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('started_at_override', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expected_complete_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expected_complete_override', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.Text(), server_default='active', nullable=False),
        sa.Column('trigger_slot_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("status IN ('active', 'completed', 'cancelled')", name='ck_map_rotation_episodes_status'),
        sa.ForeignKeyConstraint(['trigger_slot_id'], ['map_rotation_slots.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'uq_map_rotation_episodes_active',
        'map_rotation_episodes',
        ['status'],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    conn = op.get_bind()
    for slot in _SEED_SLOTS:
        slot_id = conn.execute(
            sa.text(
                """
                INSERT INTO map_rotation_slots (
                    display_order, name_ja, name_en, icons, icon_path,
                    api_slot_ids, primary_api_slot_id, duration_minutes,
                    is_visible, needs_review, auto_created
                ) VALUES (
                    :display_order, :name_ja, :name_en, CAST(:icons AS jsonb), :icon_path,
                    CAST(:api_slot_ids AS jsonb), :primary_api_slot_id, :duration_minutes,
                    TRUE, FALSE, FALSE
                ) RETURNING id
                """
            ),
            {
                "display_order": slot["display_order"],
                "name_ja": slot["name_ja"],
                "name_en": slot["name_en"],
                "icons": json.dumps(slot["icons"]),
                "icon_path": slot["icon_path"],
                "api_slot_ids": json.dumps(slot["api_slot_ids"]),
                "primary_api_slot_id": slot["primary_api_slot_id"],
                "duration_minutes": slot["duration_minutes"],
            },
        ).scalar_one()
        maps = [{"map_id": map_id, "state": "verified"} for map_id in slot["maps"]]
        conn.execute(
            sa.text(
                """
                INSERT INTO map_rotation_cycles (
                    slot_id, status, cycle_length, duration_minutes, anchor_start, maps, source, confirmed_at
                ) VALUES (
                    :slot_id, 'confirmed', :cycle_length, :duration_minutes, :anchor_start, CAST(:maps AS jsonb), 'seed', NOW()
                )
                """
            ),
            {
                "slot_id": slot_id,
                "cycle_length": len(slot["maps"]),
                "duration_minutes": slot["duration_minutes"],
                "anchor_start": slot["anchor_start"],
                "maps": json.dumps(maps),
            },
        )


def downgrade() -> None:
    op.drop_index('uq_map_rotation_episodes_active', table_name='map_rotation_episodes')
    op.drop_table('map_rotation_episodes')
    op.drop_index('uq_map_rotation_cycles_working', table_name='map_rotation_cycles')
    op.drop_index('uq_map_rotation_cycles_confirmed', table_name='map_rotation_cycles')
    op.drop_index('idx_map_rotation_cycles_slot_status', table_name='map_rotation_cycles')
    op.drop_table('map_rotation_cycles')
    op.drop_index('idx_map_rotation_slots_primary_api_slot', table_name='map_rotation_slots')
    op.drop_index('idx_map_rotation_slots_display_order', table_name='map_rotation_slots')
    op.drop_table('map_rotation_slots')
    op.drop_index('idx_event_rotation_observations_slot_start', table_name='event_rotation_observations')
    op.drop_table('event_rotation_observations')
