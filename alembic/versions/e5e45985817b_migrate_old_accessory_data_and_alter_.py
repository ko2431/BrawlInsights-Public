"""migrate old accessory data and alter tables

Revision ID: e5e45985817b
Revises: c20ba600cc45
Create Date: 2026-02-26 18:53:42.867814

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e5e45985817b'
down_revision: Union[str, Sequence[str], None] = 'c20ba600cc45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 接続の取得
    bind = op.get_bind()

    #^ 1. accessoriesテーブルの複合主キー化とrarity追加
    # 既存データのbrawler_idがNULLの場合に備え、必要に応じて対応が必要だが今回は既存データは削除＆再投入・移行の前提とする
    op.drop_constraint('accessories_pkey', 'accessories', type_='primary')
    op.alter_column('accessories', 'brawler_id', existing_type=sa.Integer(), nullable=False)
    op.create_primary_key('accessories_pkey', 'accessories', ['id', 'brawler_id'])
    op.add_column('accessories', sa.Column('rarity', sa.Integer(), nullable=True))

    #^ 2. データ移行: brawlers テーブルの既存JSONBから accessories テーブルへ挿入
    # - gadgets
    bind.execute(sa.text("""
        INSERT INTO accessories (id, type, brawler_id, en, ja, description_en, description_ja, buffie_description_en, buffie_description_ja, cooldown)
        SELECT 
            (item->>'id')::int,
            'gadget',
            b.id,
            INITCAP(COALESCE(item->'name'->>'en', '')),
            item->'name'->>'ja',
            item->'description'->>'en',
            item->'description'->>'ja',
            item->'description_with_buffie'->>'en',
            item->'description_with_buffie'->>'ja',
            (item->>'cooldown')::numeric::int
        FROM brawlers b,
        jsonb_array_elements(b.gadgets) AS item
        ON CONFLICT (id, brawler_id) DO UPDATE SET
            en = EXCLUDED.en,
            ja = COALESCE(EXCLUDED.ja, accessories.ja),
            description_en = COALESCE(EXCLUDED.description_en, accessories.description_en),
            description_ja = COALESCE(EXCLUDED.description_ja, accessories.description_ja),
            buffie_description_en = COALESCE(EXCLUDED.buffie_description_en, accessories.buffie_description_en),
            buffie_description_ja = COALESCE(EXCLUDED.buffie_description_ja, accessories.buffie_description_ja),
            cooldown = COALESCE(EXCLUDED.cooldown, accessories.cooldown);
    """))
    # - star_powers
    bind.execute(sa.text("""
        INSERT INTO accessories (id, type, brawler_id, en, ja, description_en, description_ja, buffie_description_en, buffie_description_ja)
        SELECT 
            (item->>'id')::int,
            'starPower',
            b.id,
            INITCAP(COALESCE(item->'name'->>'en', '')),
            item->'name'->>'ja',
            item->'description'->>'en',
            item->'description'->>'ja',
            item->'description_with_buffie'->>'en',
            item->'description_with_buffie'->>'ja'
        FROM brawlers b,
        jsonb_array_elements(b.star_powers) AS item
        ON CONFLICT (id, brawler_id) DO UPDATE SET
            en = EXCLUDED.en,
            ja = COALESCE(EXCLUDED.ja, accessories.ja),
            description_en = COALESCE(EXCLUDED.description_en, accessories.description_en),
            description_ja = COALESCE(EXCLUDED.description_ja, accessories.description_ja),
            buffie_description_en = COALESCE(EXCLUDED.buffie_description_en, accessories.buffie_description_en),
            buffie_description_ja = COALESCE(EXCLUDED.buffie_description_ja, accessories.buffie_description_ja);
    """))
    # - special_gears
    bind.execute(sa.text("""
        INSERT INTO accessories (id, type, brawler_id, en, ja, description_en, description_ja, rarity)
        SELECT 
            (item->>'id')::int,
            'gear',
            b.id,
            INITCAP(COALESCE(item->'name'->>'en', '')),
            item->'name'->>'ja',
            item->'description'->>'en',
            item->'description'->>'ja',
            (item->>'rarity')::int
        FROM brawlers b,
        jsonb_array_elements(b.special_gears) AS item
        ON CONFLICT (id, brawler_id) DO UPDATE SET
            en = EXCLUDED.en,
            ja = COALESCE(EXCLUDED.ja, accessories.ja),
            description_en = COALESCE(EXCLUDED.description_en, accessories.description_en),
            description_ja = COALESCE(EXCLUDED.description_ja, accessories.description_ja),
            rarity = COALESCE(EXCLUDED.rarity, accessories.rarity);
    """))
    # - hypercharge (JSON Object扱いの場合を考慮。配列の場合は要素、オブジェクトなら単体)
    bind.execute(sa.text("""
        INSERT INTO accessories (id, type, brawler_id, en, ja, description_en, description_ja, buffie_description_en, buffie_description_ja)
        SELECT 
            (b.hypercharge->>'id')::int,
            'hyperCharge',
            b.id,
            INITCAP(COALESCE(b.hypercharge->'name'->>'en', '')),
            b.hypercharge->'name'->>'ja',
            b.hypercharge->'description'->>'en',
            b.hypercharge->'description'->>'ja',
            b.hypercharge->'description_with_buffie'->>'en',
            b.hypercharge->'description_with_buffie'->>'ja'
        FROM brawlers b
        WHERE b.hypercharge IS NOT NULL 
          AND b.hypercharge != '{}'::jsonb
          AND b.hypercharge->>'id' IS NOT NULL
          AND (b.hypercharge->>'id')::int != 0
        ON CONFLICT (id, brawler_id) DO UPDATE SET
            en = EXCLUDED.en,
            ja = COALESCE(EXCLUDED.ja, accessories.ja),
            description_en = COALESCE(EXCLUDED.description_en, accessories.description_en),
            description_ja = COALESCE(EXCLUDED.description_ja, accessories.description_ja),
            buffie_description_en = COALESCE(EXCLUDED.buffie_description_en, accessories.buffie_description_en),
            buffie_description_ja = COALESCE(EXCLUDED.buffie_description_ja, accessories.buffie_description_ja);
    """))

    #^ 3. brawler_accessory_stats への新規カラム追加
    op.add_column('brawler_accessory_stats', sa.Column('hypercharge_stats', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.add_column('brawler_accessory_stats', sa.Column('buffie_stats', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False))

    #^ 4. brawlers テーブルからの不要JSONカラム削除
    op.drop_column('brawlers', 'gadgets')
    op.drop_column('brawlers', 'star_powers')
    op.drop_column('brawlers', 'special_gears')
    op.drop_column('brawlers', 'hypercharge')


def downgrade() -> None:
    """Downgrade schema."""
    # 復元用: ただし完全なデータの復元は困難なのでカラムの作り直しのみ
    op.add_column('brawlers', sa.Column('hypercharge', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), autoincrement=False, nullable=False))
    op.add_column('brawlers', sa.Column('special_gears', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), autoincrement=False, nullable=False))
    op.add_column('brawlers', sa.Column('star_powers', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), autoincrement=False, nullable=False))
    op.add_column('brawlers', sa.Column('gadgets', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), autoincrement=False, nullable=False))

    op.drop_column('brawler_accessory_stats', 'buffie_stats')
    op.drop_column('brawler_accessory_stats', 'hypercharge_stats')

    op.drop_column('accessories', 'rarity')
    op.drop_constraint('accessories_pkey', 'accessories', type_='primary')
    op.alter_column('accessories', 'brawler_id', existing_type=sa.Integer(), nullable=True)
    # 重複IDのレコードを削除してIDの一意性を確保（brawler_idが最大のものを残す）
    bind = op.get_bind()
    bind.execute(sa.text("""
        DELETE FROM accessories a1
        USING accessories a2
        WHERE a1.id = a2.id AND a1.brawler_id < a2.brawler_id;
    """))
    op.create_primary_key('accessories_pkey', 'accessories', ['id'])
