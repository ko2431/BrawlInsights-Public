"""Player system redesign: level system, new tables, nullable normalization

Revision ID: b7f3a2c91d45
Revises: 2ec0aa4c1aa0
Create Date: 2026-02-26 03:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
import time


# revision identifiers, used by Alembic.
revision: str = 'b7f3a2c91d45'
down_revision: Union[str, Sequence[str], None] = '2ec0aa4c1aa0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# バッチUPDATEのサイズ (本番の1000万件超対策)
BATCH_SIZE = 500_000


def log(msg: str) -> None:
    """マイグレーション中に進捗をリアルタイム表示する"""
    print(f"  [migrate] {msg}", flush=True)


def batch_update(table: str, column: str, old_value, new_value_expr: str, conn) -> int:
    """大量行のUPDATEをバッチ処理で実行し、進捗をログに出す。
    
    Args:
        table: テーブル名
        column: 対象カラム名
        old_value: 変換元の値 (WHERE条件)
        new_value_expr: 変換先のSQL式 (例: "NULL")
        conn: SQLAlchemy connection
    
    Returns:
        更新された合計行数
    """
    total_updated = 0
    batch_num = 0

    # old_value が None の場合は IS NULL を使う
    if old_value is None:
        where_clause = f"{column} IS NULL"
        params = {"batch_size": BATCH_SIZE}
    else:
        where_clause = f"{column} = :old_value"
        params = {"old_value": old_value, "batch_size": BATCH_SIZE}

    while True:
        batch_num += 1
        result = conn.execute(sa.text(f"""
            UPDATE {table} SET {column} = {new_value_expr}
            WHERE ctid = ANY(
                ARRAY(
                    SELECT ctid FROM {table}
                    WHERE {where_clause}
                    LIMIT :batch_size
                )
            )
        """), params)
        rows = result.rowcount
        total_updated += rows
        if rows > 0:
            log(f"  {table}.{column}: バッチ{batch_num} {rows:,}件更新 (累計 {total_updated:,}件)")
        if rows < BATCH_SIZE:
            break
    return total_updated


def upgrade() -> None:
    """Upgrade schema."""
    start_time = time.time()
    conn = op.get_bind()

    # =========================================================================
    # 1. players テーブル: 新カラム追加
    # =========================================================================
    log("Step 1/9: players テーブルに新カラムを追加中...")
    # レベル制 (旧フラグ統合)
    op.add_column('players', sa.Column('level', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('players', sa.Column('last_viewed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('players', sa.Column('auto_track_expiration', sa.DateTime(timezone=True), nullable=True))
    
    # BrawlPlex 新データ
    op.add_column('players', sa.Column('exp_level', sa.Integer(), nullable=True))
    op.add_column('players', sa.Column('exp_points', sa.Integer(), nullable=True))
    op.add_column('players', sa.Column('total_prestige_level', sa.Integer(), nullable=True))
    op.add_column('players', sa.Column('prestige_1_brawlers', sa.Integer(), nullable=True))
    op.add_column('players', sa.Column('prestige_2_brawlers', sa.Integer(), nullable=True))
    op.add_column('players', sa.Column('prestige_3_brawlers', sa.Integer(), nullable=True))
    log("Step 1/9: 完了 (9カラム追加)")

    # =========================================================================
    # 2. players テーブル: レベル制データ移行 (旧フラグ → level)
    # =========================================================================
    log("Step 2/9: players テーブルのフラグ→level制 データ移行中...")
    t = time.time()
    
    # level のバッチ更新 (is_viewed=FALSE -> 0, etc.)
    # 複雑なCASE文を避けるため、条件ごとに個別にバッチ更新します
    
    # 1. is_viewed = FALSE -> 0 (デフォルト値が0なのでスキップ可能だが明示)
    # 2. is_viewed = TRUE AND is_acquire_automatically = TRUE -> 30
    log("  level設定 (is_acquire_automatically=TRUE -> 30)")
    batch_num = 0
    total_updated = 0
    while True:
        batch_num += 1
        result = conn.execute(sa.text(f"""
            UPDATE players SET level = 30
            WHERE ctid = ANY(
                ARRAY(
                    SELECT ctid FROM players
                    WHERE is_viewed = TRUE AND is_acquire_automatically = TRUE AND level != 30
                    LIMIT {BATCH_SIZE}
                )
            )
        """))
        rows = result.rowcount
        total_updated += rows
        if rows > 0:
            log(f"    バッチ{batch_num} {rows:,}件更新 (累計 {total_updated:,}件)")
        if rows < BATCH_SIZE:
            break

    # 3. is_viewed = TRUE AND is_inactive = TRUE -> 10
    log("  level設定 (is_inactive=TRUE -> 10)")
    batch_num = 0
    total_updated = 0
    while True:
        batch_num += 1
        result = conn.execute(sa.text(f"""
            UPDATE players SET level = 10
            WHERE ctid = ANY(
                ARRAY(
                    SELECT ctid FROM players
                    WHERE is_viewed = TRUE AND is_inactive = TRUE AND is_acquire_automatically = FALSE AND level != 10
                    LIMIT {BATCH_SIZE}
                )
            )
        """))
        rows = result.rowcount
        total_updated += rows
        if rows > 0:
            log(f"    バッチ{batch_num} {rows:,}件更新 (累計 {total_updated:,}件)")
        if rows < BATCH_SIZE:
            break

    # 4. is_viewed = TRUE AND is_inactive = FALSE -> 20
    log("  level設定 (is_viewed=TRUE -> 20)")
    batch_num = 0
    total_updated = 0
    while True:
        batch_num += 1
        result = conn.execute(sa.text(f"""
            UPDATE players SET level = 20
            WHERE ctid = ANY(
                ARRAY(
                    SELECT ctid FROM players
                    WHERE is_viewed = TRUE AND is_inactive = FALSE AND is_acquire_automatically = FALSE AND level != 20
                    LIMIT {BATCH_SIZE}
                )
            )
        """))
        rows = result.rowcount
        total_updated += rows
        if rows > 0:
            log(f"    バッチ{batch_num} {rows:,}件更新 (累計 {total_updated:,}件)")
        if rows < BATCH_SIZE:
            break
            
    log(f"  level移行完了 ({time.time() - t:.1f}秒)")
    
    t = time.time()
    log("  auto_track_expiration移行 (expiration_of_automatic_acquisition -> auto_track_expiration)")
    batch_num = 0
    total_updated = 0
    while True:
        batch_num += 1
        result = conn.execute(sa.text(f"""
            UPDATE players SET auto_track_expiration = expiration_of_automatic_acquisition
            WHERE ctid = ANY(
                ARRAY(
                    SELECT ctid FROM players
                    WHERE expiration_of_automatic_acquisition IS NOT NULL AND auto_track_expiration IS NULL
                    LIMIT {BATCH_SIZE}
                )
            )
        """))
        rows = result.rowcount
        total_updated += rows
        if rows > 0:
            log(f"    バッチ{batch_num} {rows:,}件更新 (累計 {total_updated:,}件)")
        if rows < BATCH_SIZE:
            break
    log(f"  auto_track_expiration移行完了 ({time.time() - t:.1f}秒)")
    
    t = time.time()
    log("  last_viewed_at設定 (is_viewed=TRUE -> NOW())")
    batch_num = 0
    total_updated = 0
    while True:
        batch_num += 1
        result = conn.execute(sa.text(f"""
            UPDATE players SET last_viewed_at = NOW()
            WHERE ctid = ANY(
                ARRAY(
                    SELECT ctid FROM players
                    WHERE is_viewed = TRUE AND last_viewed_at IS NULL
                    LIMIT {BATCH_SIZE}
                )
            )
        """))
        rows = result.rowcount
        total_updated += rows
        if rows > 0:
            log(f"    バッチ{batch_num} {rows:,}件更新 (累計 {total_updated:,}件)")
        if rows < BATCH_SIZE:
            break
    log(f"  last_viewed_at設定完了 ({time.time() - t:.1f}秒)")
    log("Step 2/9: 完了")

    # =========================================================================
    # 3. players テーブル: NOT NULL → nullable 変更 + -1 → NULL 正規化 (6カラム)
    # =========================================================================
    log("Step 3/9: players テーブルの nullable 変更 + -1→NULL 正規化中...")
    nullable_cols_players = ['fame_points', 'legacy_rank_35s', 'season_high_trophies', 'prestige', 'total_mastery', 'titles']
    for col in nullable_cols_players:
        op.alter_column('players', col, existing_type=sa.Integer(), nullable=True)
    log(f"  nullable変更完了: {nullable_cols_players}")
    
    t = time.time()
    for col in nullable_cols_players:
        updated = batch_update('players', col, -1, 'NULL', conn)
        if updated > 0:
            log(f"  players.{col}: 合計 {updated:,}件をNULLに変換")
    log(f"Step 3/9: 完了 ({time.time() - t:.1f}秒)")

    # =========================================================================
    # 4. players テーブル: 旧フラグカラム削除
    # =========================================================================
    log("Step 4/9: players テーブルの旧フラグカラムを削除中...")
    op.drop_column('players', 'is_viewed')
    op.drop_column('players', 'is_inactive')
    op.drop_column('players', 'is_acquire_automatically')
    op.drop_column('players', 'expiration_of_automatic_acquisition')
    log("Step 4/9: 完了 (4カラム削除)")

    # =========================================================================
    # 5. player_logs テーブル: 新カラム追加
    # =========================================================================
    log("Step 5/9: player_logs テーブルに新カラムを追加中...")
    op.add_column('player_logs', sa.Column('exp_level', sa.Integer(), nullable=True))
    op.add_column('player_logs', sa.Column('exp_points', sa.Integer(), nullable=True))
    op.add_column('player_logs', sa.Column('total_prestige_level', sa.Integer(), nullable=True))
    op.add_column('player_logs', sa.Column('prestige_1_brawlers', sa.Integer(), nullable=True))
    op.add_column('player_logs', sa.Column('prestige_2_brawlers', sa.Integer(), nullable=True))
    op.add_column('player_logs', sa.Column('prestige_3_brawlers', sa.Integer(), nullable=True))
    log("Step 5/9: 完了 (6カラム追加)")

    # =========================================================================
    # 6. player_logs テーブル: NOT NULL → nullable 変更 + -1 → NULL 正規化 (5カラム)
    # =========================================================================
    log("Step 6/9: player_logs テーブルの nullable 変更 + -1→NULL 正規化中...")
    log("  ※ player_logs は大量データの可能性があるためバッチ処理で実行します")
    nullable_cols_logs = ['fame_points', 'season_high_trophies', 'prestige', 'total_mastery', 'titles']
    for col in nullable_cols_logs:
        op.alter_column('player_logs', col, existing_type=sa.Integer(), nullable=True)
    log(f"  nullable変更完了: {nullable_cols_logs}")
    
    t = time.time()
    for col in nullable_cols_logs:
        updated = batch_update('player_logs', col, -1, 'NULL', conn)
        if updated > 0:
            log(f"  player_logs.{col}: 合計 {updated:,}件をNULLに変換")
    log(f"Step 6/9: 完了 ({time.time() - t:.1f}秒)")

    # =========================================================================
    # 7. 新テーブル: player_brawlers
    # =========================================================================
    log("Step 7/9: player_brawlers テーブルを作成中...")
    op.create_table(
        'player_brawlers',
        sa.Column('tag', sa.Text(), sa.ForeignKey('players.tag', ondelete='CASCADE'), nullable=False),
        sa.Column('brawler_id', sa.Integer(), nullable=False),
        # 基本データ
        sa.Column('power', sa.Integer(), nullable=False),
        sa.Column('rank', sa.Integer(), nullable=False),
        sa.Column('trophies', sa.Integer(), nullable=False),
        sa.Column('highest_trophies', sa.Integer(), nullable=False),
        sa.Column('prestige_level', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('current_win_streak', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('max_win_streak', sa.Integer(), nullable=False, server_default='0'),
        # バフィー
        sa.Column('buffie_star_power', sa.Boolean(), nullable=False, server_default='False'),
        sa.Column('buffie_gadget', sa.Boolean(), nullable=False, server_default='False'),
        sa.Column('buffie_hyper_charge', sa.Boolean(), nullable=False, server_default='False'),
        # 所持アクセサリーID
        sa.Column('star_power_ids', JSONB(), nullable=False, server_default='[]'),
        sa.Column('gadget_ids', JSONB(), nullable=False, server_default='[]'),
        sa.Column('gear_ids', JSONB(), nullable=False, server_default='[]'),
        sa.Column('hyper_charge_ids', JSONB(), nullable=False, server_default='[]'),
        # スキン
        sa.Column('skin_id', sa.Integer(), nullable=True),
        sa.Column('owned_skin_ids', JSONB(), nullable=False, server_default='[]'),
        # MeowAPI追加データ
        sa.Column('highest_season_trophies', sa.Integer(), nullable=True),
        sa.Column('mastery', sa.Integer(), nullable=True),
        # 複合主キー
        sa.PrimaryKeyConstraint('tag', 'brawler_id', name='player_brawlers_pkey'),
    )
    op.create_index('idx_player_brawlers_brawler', 'player_brawlers', ['brawler_id'])
    op.create_index('idx_player_brawlers_tag', 'player_brawlers', ['tag'])
    log("Step 7/9: 完了")

    # =========================================================================
    # 8. 新テーブル: accessories
    # =========================================================================
    log("Step 8/9: accessories テーブルを作成中...")
    op.create_table(
        'accessories',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('type', sa.Text(), nullable=False),
        sa.Column('brawler_id', sa.Integer(), sa.ForeignKey('brawlers.id'), nullable=True),
        sa.Column('en', sa.Text(), nullable=False),
        sa.Column('ja', sa.Text(), nullable=True),
        sa.Column('description_en', sa.Text(), nullable=True),
        sa.Column('description_ja', sa.Text(), nullable=True),
        sa.Column('cooldown', sa.Integer(), nullable=True),
    )
    log("Step 8/9: 完了")

    # =========================================================================
    # 9. 新テーブル: skins
    # =========================================================================
    log("Step 9/9: skins テーブルを作成中...")
    op.create_table(
        'skins',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('brawler_id', sa.Integer(), sa.ForeignKey('brawlers.id'), nullable=True),
        sa.Column('en', sa.Text(), nullable=True),
        sa.Column('ja', sa.Text(), nullable=True),
        sa.Column('rarity', sa.Integer(), nullable=True),
        sa.Column('is_limited', sa.Boolean(), nullable=True),
        sa.Column('description_en', sa.Text(), nullable=True),
        sa.Column('description_ja', sa.Text(), nullable=True),
    )
    log("Step 9/9: 完了")

    elapsed = time.time() - start_time
    log(f"=== マイグレーション完了 (合計 {elapsed:.1f}秒) ===")


def downgrade() -> None:
    """Downgrade schema."""
    log("ダウングレードを開始します...")
    conn = op.get_bind()

    # 9. skins テーブル削除
    log("skins テーブルを削除中...")
    op.drop_table('skins')

    # 8. accessories テーブル削除
    log("accessories テーブルを削除中...")
    op.drop_table('accessories')

    # 7. player_brawlers テーブル削除
    log("player_brawlers テーブルを削除中...")
    op.drop_index('idx_player_brawlers_tag', table_name='player_brawlers')
    op.drop_index('idx_player_brawlers_brawler', table_name='player_brawlers')
    op.drop_table('player_brawlers')

    # 6. player_logs: NULL → -1 復元 + nullable → NOT NULL 復元
    log("player_logs の NULL→-1 復元中...")
    nullable_cols_logs = ['fame_points', 'season_high_trophies', 'prestige', 'total_mastery', 'titles']
    for col in nullable_cols_logs:
        updated = batch_update('player_logs', col, None, '-1', conn)
        log(f"  player_logs.{col}: {updated:,}件を-1に復元")
    for col in nullable_cols_logs:
        op.alter_column('player_logs', col, existing_type=sa.Integer(), nullable=False)
    log("player_logs 復元完了")

    # 5. player_logs: 新カラム削除
    log("player_logs の新カラムを削除中...")
    op.drop_column('player_logs', 'prestige_3_brawlers')
    op.drop_column('player_logs', 'prestige_2_brawlers')
    op.drop_column('player_logs', 'prestige_1_brawlers')
    op.drop_column('player_logs', 'total_prestige_level')
    op.drop_column('player_logs', 'exp_points')
    op.drop_column('player_logs', 'exp_level')

    # 4. players: 旧フラグカラム復元
    log("players の旧フラグカラムを復元中...")
    op.add_column('players', sa.Column('is_viewed', sa.Boolean(), nullable=False, server_default='False'))
    op.add_column('players', sa.Column('is_inactive', sa.Boolean(), nullable=False, server_default='False'))
    op.add_column('players', sa.Column('is_acquire_automatically', sa.Boolean(), nullable=False, server_default='False'))
    op.add_column('players', sa.Column('expiration_of_automatic_acquisition', sa.DateTime(timezone=True), nullable=True))

    # 旧フラグ復元データ移行 (level → 旧フラグ)
    log("players のlevel→旧フラグ データ移行中...")
    conn.execute(sa.text("""
        UPDATE players SET
            is_viewed = CASE WHEN level >= 10 THEN TRUE ELSE FALSE END,
            is_inactive = CASE WHEN level = 10 THEN TRUE ELSE FALSE END,
            is_acquire_automatically = CASE WHEN level >= 30 THEN TRUE ELSE FALSE END,
            expiration_of_automatic_acquisition = auto_track_expiration
    """))

    # 3. players: NULL → -1 復元 + nullable → NOT NULL 復元
    log("players の NULL→-1 復元中...")
    nullable_cols_players = ['fame_points', 'legacy_rank_35s', 'season_high_trophies', 'prestige', 'total_mastery', 'titles']
    for col in nullable_cols_players:
        updated = batch_update('players', col, None, '-1', conn)
        log(f"  players.{col}: {updated:,}件を-1に復元")
    for col in nullable_cols_players:
        op.alter_column('players', col, existing_type=sa.Integer(), nullable=False)

    # 1. players: 新カラム削除
    log("players の新カラムを削除中...")
    op.drop_column('players', 'prestige_3_brawlers')
    op.drop_column('players', 'prestige_2_brawlers')
    op.drop_column('players', 'prestige_1_brawlers')
    op.drop_column('players', 'total_prestige_level')
    op.drop_column('players', 'exp_points')
    op.drop_column('players', 'exp_level')
    op.drop_column('players', 'auto_track_expiration')
    op.drop_column('players', 'last_viewed_at')
    op.drop_column('players', 'level')
    log("ダウングレード完了")
