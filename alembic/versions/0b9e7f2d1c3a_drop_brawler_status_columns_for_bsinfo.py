"""drop brawler status columns for bsinfo

Revision ID: 0b9e7f2d1c3a
Revises: 93b6c2a4d8f1
Create Date: 2026-04-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '0b9e7f2d1c3a'
down_revision: Union[str, Sequence[str], None] = '93b6c2a4d8f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


STATUS_COLUMNS: tuple[str, ...] = (
    'class_info',
    'hp',
    'hp_special_statuses',
    'speed',
    'speed_special_statuses',
    'trait_settings',
    'attack_names',
    'attack_range',
    'attack_range_special_statuses',
    'attack_reload',
    'attack_reload_special_statuses',
    'attack_projectiles_per_attack',
    'attack_projectiles_per_attack_special_statuses',
    'attack_super_charge_per_hit',
    'attack_super_charge_per_hit_special_statuses',
    'attack_hypercharge_charge_per_hit',
    'attack_hypercharge_charge_per_hit_special_statuses',
    'attack_spread',
    'attack_spread_special_statuses',
    'attack_width',
    'attack_width_special_statuses',
    'attack_projectile_speed',
    'attack_projectile_speed_special_statuses',
    'attack_damage',
    'attack_damage_special_statuses',
    'attack_special_statuses',
    'super_names',
    'super_range',
    'super_range_special_statuses',
    'super_reload',
    'super_reload_special_statuses',
    'super_projectiles_per_attack',
    'super_projectiles_per_attack_special_statuses',
    'super_super_charge_per_hit',
    'super_super_charge_per_hit_special_statuses',
    'super_hypercharge_charge_per_hit',
    'super_hypercharge_charge_per_hit_special_statuses',
    'super_spread',
    'super_spread_special_statuses',
    'super_width',
    'super_width_special_statuses',
    'super_projectile_speed',
    'super_projectile_speed_special_statuses',
    'super_damage',
    'super_damage_special_statuses',
    'super_special_statuses',
    'pet_names',
    'pet_hp',
    'pet_hp_special_statuses',
    'pet_speed',
    'pet_speed_special_statuses',
    'pet_range',
    'pet_range_special_statuses',
    'pet_reload',
    'pet_reload_special_statuses',
    'pet_projectiles_per_attack',
    'pet_projectiles_per_attack_special_statuses',
    'pet_super_charge_per_hit',
    'pet_super_charge_per_hit_special_statuses',
    'pet_hypercharge_charge_per_hit',
    'pet_hypercharge_charge_per_hit_special_statuses',
    'pet_spread',
    'pet_spread_special_statuses',
    'pet_width',
    'pet_width_special_statuses',
    'pet_projectile_speed',
    'pet_projectile_speed_special_statuses',
    'pet_damage',
    'pet_damage_special_statuses',
    'pet_special_statuses',
)


def upgrade() -> None:
    """Upgrade schema."""
    for column_name in STATUS_COLUMNS:
        op.drop_column('brawlers', column_name)


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('brawlers', sa.Column('class_info', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False))
    op.add_column('brawlers', sa.Column('hp', sa.Float(), nullable=True))
    op.add_column('brawlers', sa.Column('hp_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('speed', sa.Float(), nullable=True))
    op.add_column('brawlers', sa.Column('speed_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('trait_settings', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False))
    op.add_column('brawlers', sa.Column('attack_names', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False))
    op.add_column('brawlers', sa.Column('attack_range', sa.Float(), nullable=True))
    op.add_column('brawlers', sa.Column('attack_range_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('attack_reload', sa.Float(), nullable=True))
    op.add_column('brawlers', sa.Column('attack_reload_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('attack_projectiles_per_attack', sa.Integer(), nullable=True))
    op.add_column('brawlers', sa.Column('attack_projectiles_per_attack_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('attack_super_charge_per_hit', sa.Float(), nullable=True))
    op.add_column('brawlers', sa.Column('attack_super_charge_per_hit_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('attack_hypercharge_charge_per_hit', sa.Float(), nullable=True))
    op.add_column('brawlers', sa.Column('attack_hypercharge_charge_per_hit_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('attack_spread', sa.Float(), nullable=True))
    op.add_column('brawlers', sa.Column('attack_spread_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('attack_width', sa.Float(), nullable=True))
    op.add_column('brawlers', sa.Column('attack_width_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('attack_projectile_speed', sa.Float(), nullable=True))
    op.add_column('brawlers', sa.Column('attack_projectile_speed_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('attack_damage', sa.Float(), nullable=True))
    op.add_column('brawlers', sa.Column('attack_damage_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('attack_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('super_names', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False))
    op.add_column('brawlers', sa.Column('super_range', sa.Float(), nullable=True))
    op.add_column('brawlers', sa.Column('super_range_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('super_reload', sa.Float(), nullable=True))
    op.add_column('brawlers', sa.Column('super_reload_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('super_projectiles_per_attack', sa.Integer(), nullable=True))
    op.add_column('brawlers', sa.Column('super_projectiles_per_attack_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('super_super_charge_per_hit', sa.Float(), nullable=True))
    op.add_column('brawlers', sa.Column('super_super_charge_per_hit_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('super_hypercharge_charge_per_hit', sa.Float(), nullable=True))
    op.add_column('brawlers', sa.Column('super_hypercharge_charge_per_hit_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('super_spread', sa.Float(), nullable=True))
    op.add_column('brawlers', sa.Column('super_spread_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('super_width', sa.Float(), nullable=True))
    op.add_column('brawlers', sa.Column('super_width_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('super_projectile_speed', sa.Float(), nullable=True))
    op.add_column('brawlers', sa.Column('super_projectile_speed_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('super_damage', sa.Float(), nullable=True))
    op.add_column('brawlers', sa.Column('super_damage_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('super_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('pet_names', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False))
    op.add_column('brawlers', sa.Column('pet_hp', sa.Float(), nullable=True))
    op.add_column('brawlers', sa.Column('pet_hp_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('pet_speed', sa.Float(), nullable=True))
    op.add_column('brawlers', sa.Column('pet_speed_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('pet_range', sa.Float(), nullable=True))
    op.add_column('brawlers', sa.Column('pet_range_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('pet_reload', sa.Float(), nullable=True))
    op.add_column('brawlers', sa.Column('pet_reload_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('pet_projectiles_per_attack', sa.Integer(), nullable=True))
    op.add_column('brawlers', sa.Column('pet_projectiles_per_attack_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('pet_super_charge_per_hit', sa.Float(), nullable=True))
    op.add_column('brawlers', sa.Column('pet_super_charge_per_hit_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('pet_hypercharge_charge_per_hit', sa.Float(), nullable=True))
    op.add_column('brawlers', sa.Column('pet_hypercharge_charge_per_hit_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('pet_spread', sa.Float(), nullable=True))
    op.add_column('brawlers', sa.Column('pet_spread_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('pet_width', sa.Float(), nullable=True))
    op.add_column('brawlers', sa.Column('pet_width_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('pet_projectile_speed', sa.Float(), nullable=True))
    op.add_column('brawlers', sa.Column('pet_projectile_speed_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('pet_damage', sa.Float(), nullable=True))
    op.add_column('brawlers', sa.Column('pet_damage_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('brawlers', sa.Column('pet_special_statuses', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
