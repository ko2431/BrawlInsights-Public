# app/db/models.py
from sqlalchemy import (
    Column,
    Integer,
    SmallInteger,
    Text,
    Date,
    DateTime,
    Boolean,
    ForeignKey,
    Float,
    UniqueConstraint,
    Index,
    desc,
    PrimaryKeyConstraint,
    CheckConstraint
)
from sqlalchemy.dialects.postgresql import JSONB, INET
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func

# 全てのモデルクラスが継承する基本クラス。
# Alembicは、このBaseを継承しているクラスをテーブルとして認識します。
Base = declarative_base()


#^ テーブルのモデル定義 ---

class User(Base):
    """
    アプリケーションのユーザー情報を格納するテーブル。
    """
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False, unique=True)
    password = Column(Text, nullable=False)
    lang = Column(Text, nullable=False)
    main_account = Column(Text, ForeignKey('players.tag'), nullable=False)
    secret_questions = Column(JSONB, nullable=False)
    saved_accounts = Column(JSONB, nullable=False, server_default='[]')
    saved_clubs = Column(JSONB, nullable=False, server_default='[]')
    viewed_accounts = Column(JSONB, nullable=False, server_default='[]')
    viewed_clubs = Column(JSONB, nullable=False, server_default='[]')
    custom_settings = Column(JSONB, nullable=False, server_default='{}')
    registration_datetime = Column(DateTime(timezone=True), nullable=False)
    last_viewd_datetime = Column(DateTime(timezone=True), nullable=False)
    last_claim_date = Column(Date, nullable=True)
    last_bonus_mission_date = Column(Date, nullable=True)
    is_delete_ads = Column(Boolean, nullable=False, server_default='False')
    is_admin = Column(Boolean, nullable=False, server_default='False')
    is_invalid = Column(Boolean, nullable=False, server_default='False')
    is_prohibit_posting = Column(Boolean, nullable=False, server_default='False')
    saved_accounts_limit = Column(Integer, nullable=False, server_default='0')
    saved_clubs_limit = Column(Integer, nullable=False, server_default='3')
    viewed_accounts_limit = Column(Integer, nullable=False, server_default='0')
    viewed_clubs_limit = Column(Integer, nullable=False, server_default='4')
    pv_count = Column(Integer, nullable=False, server_default='0')
    tokens = Column(Integer, nullable=False, server_default='0')
    token_limit = Column(Integer, nullable=True)
    token_claim_count = Column(Integer, server_default='0')
    ad_skip_tickets = Column(Integer, nullable=False, server_default='0')
    ticket_claim_count = Column(Integer, nullable=False, server_default='0')
    last_ticket_claim_date = Column(Date, nullable=True)

    # リレーションシップ
    player = relationship("Player", back_populates="users", foreign_keys=[main_account])
    posts = relationship("Post", back_populates="host")
    messages = relationship("Message", back_populates="user")
    reactions = relationship("Reaction", back_populates="user")
    reports = relationship("Report", back_populates="user")
    feedbacks = relationship("Feedback", back_populates="user")


class PurchaseEvent(Base):
    """
    課金イベント履歴を格納するテーブル。
    RevenueCat Webhookを正として記録し、監査・再集計に利用する。
    """
    __tablename__ = 'purchase_events'

    id = Column(Integer, primary_key=True)
    provider = Column(Text, nullable=False)
    external_event_id = Column(Text, nullable=True, unique=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    app_user_id = Column(Text, nullable=True)
    event_type = Column(Text, nullable=False)
    product_id = Column(Text, nullable=True)
    entitlement_id = Column(Text, nullable=True)
    transaction_id = Column(Text, nullable=True)
    original_transaction_id = Column(Text, nullable=True)
    environment = Column(Text, nullable=True)
    is_sandbox = Column(Boolean, nullable=False, server_default='False')
    event_timestamp = Column(DateTime(timezone=True), nullable=True)
    raw_payload = Column(JSONB, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index('ix_purchase_events_user_id', 'user_id'),
        Index('ix_purchase_events_product_id', 'product_id'),
        Index('ix_purchase_events_created_at', 'created_at'),
        Index('ix_purchase_events_event_timestamp', 'event_timestamp'),
    )


class ImageGenerationJob(Base):
    """
    プロフィール画像生成ジョブを格納するテーブル。
    初期リリースではプロフ画像系のみを対象とし、将来バトル履歴画像にも拡張する。
    """
    __tablename__ = 'image_generation_jobs'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    player_tag = Column(Text, nullable=False)
    platform = Column(Text, nullable=False)
    lang = Column(Text, nullable=False)
    image_type = Column(Text, nullable=False)
    orientation = Column(Text, nullable=False)
    status = Column(Text, nullable=False, server_default='queued')
    priority = Column(Integer, nullable=False, server_default='0')
    consume_ticket = Column(Boolean, nullable=False, server_default='False')
    is_fast_lane = Column(Boolean, nullable=False, server_default='False')
    cache_key = Column(Text, nullable=True)
    min_wait_until = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)
    result_path = Column(Text, nullable=True)
    result_filename = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index('ix_image_generation_jobs_status_priority_created_at', 'status', desc('priority'), 'created_at'),
        Index('ix_image_generation_jobs_user_id_created_at', 'user_id', 'created_at'),
        Index('ix_image_generation_jobs_cache_key_expires_at', 'cache_key', 'expires_at'),
        Index('ix_image_generation_jobs_player_tag_created_at', 'player_tag', 'created_at'),
    )


class Player(Base):
    """
    プレイヤー情報を格納するテーブル。
    """
    __tablename__ = 'players'

    tag = Column(Text, primary_key=True)
    name = Column(Text, nullable=False)
    club_tag = Column(Text, ForeignKey('clubs.tag'), nullable=True)
    icon_id = Column(Integer, nullable=False)
    current_trophies = Column(Integer, nullable=False)
    highest_trophies = Column(Integer, nullable=False)
    season_trophies = Column(Integer, nullable=False)
    average_highest_trophies = Column(Float, nullable=False)
    average_power = Column(Float, nullable=False)
    max_power_brawlers = Column(Integer, nullable=False)
    gadgets = Column(Integer, nullable=False)
    star_powers = Column(Integer, nullable=False)
    gears = Column(Integer, nullable=False)
    hyper_charges = Column(Integer, nullable=False, server_default='0')
    buffies = Column(Integer, nullable=False, server_default='0')
    team_victories = Column(Integer, nullable=False)
    solo_victories = Column(Integer, nullable=False)
    duo_victories = Column(Integer, nullable=False)
    unlocked_brawlers = Column(Integer, nullable=False)
    max_tier_brawlers = Column(Integer, nullable=False)
    ranked_current_rank = Column(Integer, server_default='0')
    ranked_current_score = Column(Integer, server_default='0')
    ranked_highest_rank = Column(Integer, server_default='0')
    ranked_highest_score = Column(Integer, server_default='0')
    ranked_season_highest_score = Column(Integer, server_default='0')
    solo_pl_rank = Column(Integer, server_default='0')
    team_pl_rank = Column(Integer, server_default='0')
    acc_ranked_current_rank = Column(Integer, server_default='0')
    acc_ranked_highest_rank = Column(Integer, server_default='0')
    acc_ranked_season_highest_rank = Column(Integer, server_default='0')
    highest_club_league = Column(Integer, server_default='0')
    account_creation_year = Column(Integer, server_default='0')
    max_winstreak = Column(Integer, server_default='0')
    winstreak_brawler = Column(Integer, server_default='0')
    region = Column(Text, nullable=True)
    previous_names = Column(JSONB, nullable=False)
    previous_clubs = Column(JSONB, nullable=False)
    is_invalid = Column(Boolean, nullable=False, server_default='False')
    level = Column(Integer, nullable=False, server_default='0')  # 0=未閲覧, 10=非アクティブ, 20=アクティブ, 30=自動追跡有効
    last_viewed_at = Column(DateTime(timezone=True), nullable=True)  # 最終閲覧日時(レベル格下げ判定用)
    last_updated_at = Column(DateTime(timezone=True), nullable=True)  # 最終DB更新日時(タスク取得順用)
    auto_track_expiration = Column(DateTime(timezone=True), nullable=True)  # 自動追跡有効期限
    fame_points = Column(Integer, nullable=True)
    legacy_rank_35s = Column(Integer, nullable=True)
    season_high_trophies = Column(Integer, nullable=True)
    prestige = Column(Integer, nullable=True)  # 旧トップランカー(廃止制度)
    total_mastery = Column(Integer, nullable=True)
    titles = Column(Integer, nullable=True)
    brawlpass = Column(Integer, nullable=True)
    record_points = Column(Integer, nullable=True)
    record_level = Column(Integer, nullable=True)
    favorite_skin = Column(Integer, nullable=True)
    owned_skin_count = Column(Integer, nullable=True)
    first_profile_avatar = Column(Integer, nullable=True)
    second_profile_avatar = Column(Integer, nullable=True)
    battle_card_emote = Column(Integer, nullable=True)
    battle_card_title = Column(Integer, nullable=True)
    battle_card_frame = Column(Integer, nullable=True)
    battle_card_stars = Column(JSONB, nullable=False, server_default='{}')
    milestone = Column(Integer, nullable=True)
    exp_level = Column(Integer, nullable=True)
    exp_points = Column(Integer, nullable=True)
    total_prestige_level = Column(Integer, nullable=True)  # 新トップランカー
    prestige_1_brawlers = Column(Integer, nullable=True)  # prestigeLevel>=1 のキャラ数
    prestige_2_brawlers = Column(Integer, nullable=True)  # prestigeLevel>=2 のキャラ数
    prestige_3_brawlers = Column(Integer, nullable=True)  # prestigeLevel>=3 のキャラ数
    hide_name_history = Column(Boolean, nullable=False, server_default='False')
    hide_club_history = Column(Boolean, nullable=False, server_default='False')
    battle_log_limit = Column(Integer, nullable=True, server_default=None)
    battle_log_retention_months = Column(Integer, nullable=True, server_default=None)  # NULL=システムデフォルト(4ヶ月)を適用

    # リレーションシップ
    club = relationship("Club") # club_tagに紐づくClubオブジェクトにアクセスできる
    users = relationship("User", foreign_keys="[User.main_account]") # main_accountに紐づくUserにアクセス

    # パフォーマンス改善のためのインデックスを __table_args__ で定義
    __table_args__ = (
        # pg_bigm: 1〜2文字の短いクエリに強い（ILIKE '%s%'や'%あ%'など）
        Index(
            'idx_gin_players_name_bigm',
            'name',
            postgresql_using='gin',
            postgresql_ops={'name': 'gin_bigm_ops'}
        ),
        # pg_trgm: 3文字以上の長いクエリに強い（ILIKE '%ブロスタプレイヤー%'など）
        # クエリプランナーが文字列長に応じて自動的に最適なインデックスを選択する
        Index(
            'idx_gin_players_name_trgm',
            'name',
            postgresql_using='gin',
            postgresql_ops={'name': 'gin_trgm_ops'}
        ),
        Index('idx_players_level_updated_at', 'level', 'last_updated_at'),
        Index('idx_players_lower_name', func.lower("name")),

        Index(
            'idx_gin_players_previous_names_path_ops',
            'previous_names',
            postgresql_using='gin',
            # jsonb_path_existsを高速化するための専用opsを指定
            postgresql_ops={'previous_names': 'jsonb_path_ops'}
        ),
    )


class Club(Base):
    """
    クラブの情報を格納するテーブル。
    """
    __tablename__ = 'clubs'
    tag = Column(Text, primary_key=True)
    name = Column(Text, nullable=False)
    type = Column(Text, nullable=False)
    trophies = Column(Integer, nullable=False)
    badge_id = Column(Integer, nullable=False)
    region = Column(Text, nullable=True)
    is_invalid = Column(Boolean, nullable=False, server_default='False')

    # リレーションシップを追加
    players = relationship("Player", back_populates="club")
    posts = relationship("Post", back_populates="host_club")


class Battle(Base):
    """
    バトルを格納するテーブル。
    """
    __tablename__ = 'battles'

    tag = Column(Text, nullable=False)
    datetime = Column(DateTime(timezone=True), nullable=False)
    event_id = Column(Integer, nullable=True)
    event_mode = Column(Text, nullable=True)
    event_map = Column(Text, nullable=True)
    battle_mode = Column(Text, nullable=True)
    battle_type = Column(Text, nullable=True)
    result = Column(Text, nullable=False)
    rank = Column(Integer, nullable=True)
    brawler = Column(Integer, nullable=True)
    power = Column(Integer, nullable=True)
    trophies = Column(Integer, nullable=True)
    trophy_change = Column(Integer, nullable=True)
    team_size = Column(Integer, nullable=False)
    num_of_teams = Column(Integer, nullable=False)
    ranked_score_after = Column(Integer, nullable=True)
    is_starplayer = Column(Boolean, nullable=True)
    brawlers = Column(JSONB, nullable=True)
    teammate_tags = Column(JSONB, nullable=False, server_default='[]')
    opponent_tags = Column(JSONB, nullable=False, server_default='[]')
    teammate_names = Column(JSONB, nullable=False, server_default='[]')
    opponent_names = Column(JSONB, nullable=False, server_default='[]')
    teammate_brawlers = Column(JSONB, nullable=False, server_default='[]')
    opponent_brawlers = Column(JSONB, nullable=False, server_default='[]')

    __table_args__ = (
        PrimaryKeyConstraint('tag', 'datetime', name='battles_pkey'),
        Index('idx_battles_tag_time', 'tag', desc('datetime')),
        Index('idx_battles_datetime', 'datetime'),
        CheckConstraint("result IN ('w', 'd', 'l')", name='ck_battles_result')
    )


class ArchivedBattle(Base):
    """
    保存期限切れのバトルを移管するアーカイブテーブル。
    battlesテーブルと同一カラム構成 + アーカイブ日時。
    VPSのバックアップ対象外とする。
    """
    __tablename__ = 'archived_battles'

    tag = Column(Text, nullable=False)
    datetime = Column(DateTime(timezone=True), nullable=False)
    event_id = Column(Integer, nullable=True)
    event_mode = Column(Text, nullable=True)
    event_map = Column(Text, nullable=True)
    battle_mode = Column(Text, nullable=True)
    battle_type = Column(Text, nullable=True)
    result = Column(Text, nullable=False)
    rank = Column(Integer, nullable=True)
    brawler = Column(Integer, nullable=True)
    power = Column(Integer, nullable=True)
    trophies = Column(Integer, nullable=True)
    trophy_change = Column(Integer, nullable=True)
    team_size = Column(Integer, nullable=False)
    num_of_teams = Column(Integer, nullable=False)
    ranked_score_after = Column(Integer, nullable=True)
    is_starplayer = Column(Boolean, nullable=True)
    brawlers = Column(JSONB, nullable=True)
    teammate_tags = Column(JSONB, nullable=False, server_default='[]')
    opponent_tags = Column(JSONB, nullable=False, server_default='[]')
    teammate_names = Column(JSONB, nullable=False, server_default='[]')
    opponent_names = Column(JSONB, nullable=False, server_default='[]')
    teammate_brawlers = Column(JSONB, nullable=False, server_default='[]')
    opponent_brawlers = Column(JSONB, nullable=False, server_default='[]')
    archived_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        PrimaryKeyConstraint('tag', 'datetime', name='archived_battles_pkey'),
        Index('idx_archived_battles_datetime', 'datetime'),
    )


class PlayerLog(Base):
    """
    プレイヤーの日ごとの状態を記録するテーブル。
    """
    __tablename__ = 'player_logs'

    tag = Column(Text, ForeignKey('players.tag'), nullable=False)
    date = Column(Date, nullable=False)
    current_trophies = Column(Integer, nullable=False)
    highest_trophies = Column(Integer, nullable=False)
    season_trophies = Column(Integer, nullable=False)
    average_highest_trophies = Column(Float, nullable=False)
    average_power = Column(Float, nullable=False)
    max_power_brawlers = Column(Integer, nullable=False)
    gadgets = Column(Integer, nullable=False)
    star_powers = Column(Integer, nullable=False)
    gears = Column(Integer, nullable=False)
    hyper_charges = Column(Integer, nullable=False, server_default='0')
    buffies = Column(Integer, nullable=False, server_default='0')
    team_victories = Column(Integer, nullable=False)
    solo_victories = Column(Integer, nullable=False)
    duo_victories = Column(Integer, nullable=False)
    unlocked_brawlers = Column(Integer, nullable=False)
    max_tier_brawlers = Column(Integer, nullable=False)
    ranked_current_rank = Column(Integer, server_default='0')
    ranked_highest_rank = Column(Integer, server_default='0')
    ranked_current_score = Column(Integer, server_default='0')
    ranked_highest_score = Column(Integer, server_default='0')
    ranked_season_highest_score = Column(Integer, server_default='0')
    acc_ranked_current_rank = Column(Integer, server_default='0')
    acc_ranked_highest_rank = Column(Integer, server_default='0')
    acc_ranked_season_highest_rank = Column(Integer, server_default='0')
    max_winstreak = Column(Integer, server_default='0')
    winstreak_brawler = Column(Integer, server_default='0')
    fame_points = Column(Integer, nullable=True)
    season_high_trophies = Column(Integer, nullable=True)
    prestige = Column(Integer, nullable=True)  # 旧トップランカー(廃止制度)
    total_mastery = Column(Integer, nullable=True)
    titles = Column(Integer, nullable=True)
    brawlpass = Column(Integer, nullable=True)
    record_points = Column(Integer, nullable=True)
    record_level = Column(Integer, nullable=True)
    owned_skin_count = Column(Integer, nullable=True)
    exp_level = Column(Integer, nullable=True)
    exp_points = Column(Integer, nullable=True)
    total_prestige_level = Column(Integer, nullable=True)  # 新トップランカー
    prestige_1_brawlers = Column(Integer, nullable=True)
    prestige_2_brawlers = Column(Integer, nullable=True)
    prestige_3_brawlers = Column(Integer, nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint('tag', 'date', name='player_logs_pkey'),
    )


class PlayerMetricThreshold(Base):
    """
    プレイヤー評価指標の閾値を格納するテーブル。
    """
    __tablename__ = 'player_metric_thresholds'

    metric_key = Column(Text, primary_key=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    population = Column(Integer, nullable=False)
    thresholds_json = Column(JSONB, nullable=False)
    method = Column(Text, nullable=False)
    top_k = Column(Integer, nullable=False)


class Brawler(Base):
    """
    キャラクターの情報を格納するテーブル。
    """
    __tablename__ = 'brawlers'

    id = Column(Integer, primary_key=True)
    en = Column(Text, nullable=False)
    ja = Column(JSONB, nullable=False, server_default='[]')
    is_temporary = Column(Boolean, nullable=False, server_default='False')
    rarity = Column(Integer, nullable=True)
    
    prestige_borders = relationship("PrestigeBorder", back_populates="brawler")



class BrawlerAccessoryStats(Base):
    """
    キャラクターのアクセサリー（ガジェット・スタパ・ギア）の所持率などの統計データを日次で記録するテーブル。
    """
    __tablename__ = 'brawler_accessory_stats'

    date = Column(Date, nullable=False)
    brawler_id = Column(Integer, ForeignKey('brawlers.id', ondelete='CASCADE'), nullable=False)

    # 統計データ (日次スナップショット)
    gadget_stats = Column(JSONB, nullable=False, server_default='{}')
    star_power_stats = Column(JSONB, nullable=False, server_default='{}')
    gear_stats = Column(JSONB, nullable=False, server_default='{}')
    hypercharge_stats = Column(JSONB, nullable=False, server_default='{}')
    buffie_stats = Column(JSONB, nullable=False, server_default='{}')

    # リレーションシップ
    brawler = relationship("Brawler")

    __table_args__ = (
        # 日付とキャラIDで複合主キー
        PrimaryKeyConstraint('date', 'brawler_id', name='brawler_accessory_stats_pkey'),
        # 特定キャラの時系列データを引くためのインデックス
        Index('idx_brawler_accessory_stats_brawler_date', 'brawler_id', desc('date')),
    )


class PlayerBrawlerDB(Base):
    """
    プレイヤーの所持キャラクター情報を格納するテーブル。
    level 10以上のプレイヤーのみデータを保存する。
    """
    __tablename__ = 'player_brawlers'

    tag = Column(Text, ForeignKey('players.tag', ondelete='CASCADE'), nullable=False)
    brawler_id = Column(Integer, nullable=False)

    # 基本データ (BrawlPlex API)
    power = Column(Integer, nullable=False)
    rank = Column(Integer, nullable=False)          # ゲーム内ランク
    trophies = Column(Integer, nullable=False)
    highest_trophies = Column(Integer, nullable=False)
    prestige_level = Column(Integer, nullable=False, server_default='0')
    current_win_streak = Column(Integer, nullable=False, server_default='0')
    max_win_streak = Column(Integer, nullable=False, server_default='0')

    # バフィー
    buffie_star_power = Column(Boolean, nullable=False, server_default='False')
    buffie_gadget = Column(Boolean, nullable=False, server_default='False')
    buffie_hyper_charge = Column(Boolean, nullable=False, server_default='False')

    # 所持アクセサリーID (JSONB)
    star_power_ids = Column(JSONB, nullable=False, server_default='[]')
    gadget_ids = Column(JSONB, nullable=False, server_default='[]')
    gear_ids = Column(JSONB, nullable=False, server_default='[]')
    hyper_charge_ids = Column(JSONB, nullable=False, server_default='[]')

    # スキン
    skin_id = Column(Integer, nullable=True)              # 現在装備中のスキンID
    owned_skin_ids = Column(JSONB, nullable=False, server_default='[]')  # 所持スキンIDリスト (BSInfo APIベース)

    # MeowAPI追加データ
    highest_season_trophies = Column(Integer, nullable=True)
    mastery = Column(Integer, nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint('tag', 'brawler_id', name='player_brawlers_pkey'),
        Index('idx_player_brawlers_brawler', 'brawler_id'),  # キャラ別分析用
        Index('idx_player_brawlers_tag', 'tag'),              # プレイヤー別検索用
    )


class Accessory(Base):
    """
    アクセサリー（ガジェット・スターパワー・ギア・ハイパーチャージ）のマスターデータ。
    PlayerBrawlerデータから自動で追加され、日本語名や説明文は管理者が後から入力する。
    """
    __tablename__ = 'accessories'

    id = Column(Integer, primary_key=True)           # アクセサリーID
    brawler_id = Column(Integer, ForeignKey('brawlers.id', ondelete='CASCADE'), primary_key=True, nullable=False)
    type = Column(Text, nullable=False)              # "gadget" / "starPower" / "gear" / "hyperCharge"
    rarity = Column(Integer, nullable=True)          # ギア用レア度
    en = Column(Text, nullable=False)                # 英語名 (Title Case: "Spark Plug")
    ja = Column(Text, nullable=True)                 # 日本語名 (管理者が後から入力)
    description_en = Column(Text, nullable=True)     # 英語説明文 (管理者が手動入力)
    description_ja = Column(Text, nullable=True)     # 日本語説明文 (管理者が手動入力)
    buffie_description_en = Column(Text, nullable=True)  # バフィー有効時の英語説明文
    buffie_description_ja = Column(Text, nullable=True)  # バフィー有効時の日本語説明文
    cooldown = Column(Integer, nullable=True)        # クールダウン秒数 (ガジェット専用、管理者が入力)
    is_invalid = Column(Boolean, nullable=False, server_default='False')  # ゲーム内削除などで表示対象外にするフラグ


class Skin(Base):
    """
    スキン情報。PlayerBrawlerデータから自動で新規追加される。
    デフォルトスキン（name=null）も保存してスキン装着率分析に使用する。
    """
    __tablename__ = 'skins'

    id = Column(Integer, primary_key=True)            # スキンID
    brawler_id = Column(Integer, ForeignKey('brawlers.id'), nullable=True)
    en = Column(Text, nullable=True)                  # 英語名 (Title Case化、デフォルトスキンはNull)
    ja = Column(Text, nullable=True)                  # 日本語名 (管理者が後から入力)
    rarity = Column(Integer, nullable=True)           # レアリティ (管理者が入力)
    is_limited = Column(Boolean, nullable=True)       # 限定スキンか (管理者が入力)
    description_en = Column(Text, nullable=True)      # 英語説明文 (管理者が入力)
    description_ja = Column(Text, nullable=True)      # 日本語説明文 (管理者が入力)
    ownership_rate = Column(Float, nullable=True)     # スキン所持率（日次自動集計）
    equip_rate = Column(Float, nullable=True)         # スキン装備率（日次自動集計）


class Pin(Base):
    """
    プレイヤーのバトルカード用ピンズ（エモート）情報を格納するテーブル。
    """
    __tablename__ = 'pins'

    id = Column(Integer, primary_key=True)            # ピンズID
    brawler_id = Column(Integer, ForeignKey('brawlers.id', ondelete='CASCADE'), nullable=True)
    rarity = Column(Integer, nullable=True)
    description_en = Column(Text, nullable=True)
    description_ja = Column(Text, nullable=True)
    equip_rate = Column(Float, nullable=True)         # 使用率（日次自動集計）


class Title(Base):
    """
    プレイヤーのバトルカード用タイトル情報を格納するテーブル。
    """
    __tablename__ = 'titles'

    id = Column(Integer, primary_key=True)            # タイトルID
    brawler_id = Column(Integer, ForeignKey('brawlers.id', ondelete='CASCADE'), nullable=True)
    rarity = Column(Integer, nullable=True)
    en = Column(Text, nullable=True)                  # 英語名
    ja = Column(Text, nullable=True)                  # 日本語名
    equip_rate = Column(Float, nullable=True)         # 使用率（日次自動集計）


class Frame(Base):
    """
    プレイヤーのバトルカード用フレーム情報を格納するテーブル。
    """
    __tablename__ = 'frames'

    id = Column(Integer, primary_key=True)            # フレームID
    type = Column(Text, nullable=True)                # フレームのタイプ
    en = Column(Text, nullable=True)                  # 英語名
    ja = Column(Text, nullable=True)                  # 日本語名
    equip_rate = Column(Float, nullable=True)         # 使用率（日次自動集計）


class PlayerIcon(Base):
    """
    プレイヤーアイコンの使用率ランキング用マスターデータ。
    """
    __tablename__ = 'player_icons'

    id = Column(Integer, primary_key=True)            # アイコンID
    equip_rate = Column(Float, nullable=True)         # 使用率（日次自動集計）


class Map(Base):
    """
    マップの静的情報を格納するテーブル。
    """
    __tablename__ = 'maps'

    en = Column(Text, primary_key=True)
    ja = Column(Text, nullable=True)


class Mode(Base):
    """
    ゲームモードの静的情報を格納するテーブル。
    """
    __tablename__ = 'modes'

    en = Column(Text, primary_key=True)
    ja = Column(Text, nullable=True)


class Announcement(Base):
    """
    お知らせを格納するテーブル。
    """
    __tablename__ = 'announcements'

    id = Column(Text, primary_key=True)
    datetime = Column(DateTime(timezone=True), nullable=False)
    en_title = Column(Text, nullable=False)
    ja_title = Column(Text, nullable=True)
    en_text = Column(Text, nullable=False)
    ja_text = Column(Text, nullable=True)
    category = Column(Integer, nullable=True)


class SecretQuestion(Base):
    """
    パスワードリセット用の秘密の質問を格納するテーブル。
    """
    __tablename__ = 'secret_questions'

    id = Column(Integer, primary_key=True) # SERIAL PRIMARY KEY
    en = Column(Text, nullable=False, unique=True)
    ja = Column(Text, nullable=True, unique=True)


class GiftCode(Base):
    """
    ギフトコードの情報を格納するテーブル。
    """
    __tablename__ = 'gift_codes'

    code = Column(Text, primary_key=True)
    reward = Column(JSONB, nullable=False)
    is_admin_only = Column(Boolean, nullable=False, server_default='False')
    num_of_uses = Column(Integer, nullable=False, server_default='0')
    usage_log = Column(JSONB, nullable=False, server_default='[]')
    usage_limit_per_user = Column(Integer, nullable=True)
    usage_limit_total = Column(Integer, nullable=True)
    is_invalid = Column(Boolean, nullable=False, server_default='False')
    expiration_datetime = Column(DateTime(timezone=True), nullable=True)


class PrestigeBorder(Base):
    """
    キャラクターごとのトップランカーボーダー情報を格納するテーブル。
    """
    __tablename__ = 'prestige_borders'

    date = Column(Date, nullable=False)
    brawler_id = Column(Integer, ForeignKey('brawlers.id'), nullable=False)
    border = Column(Integer, nullable=False)
    
    # リレーションシップ
    brawler = relationship("Brawler", back_populates="prestige_borders")

    __table_args__ = (
        PrimaryKeyConstraint('date', 'brawler_id', name='prestige_borders_pkey'),
    )


class Feedback(Base):
    """
    ユーザーからのフィードバックを格納するテーブル。
    """
    __tablename__ = 'feedbacks'

    id = Column(Integer, primary_key=True) # SERIAL PRIMARY KEY
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    datetime = Column(DateTime(timezone=True), nullable=False)
    feedback_type = Column(Text, nullable=False)
    comment = Column(Text, nullable=False, server_default='')
    is_checked = Column(Boolean, nullable=False, server_default='False')

    # リレーションシップ
    user = relationship("User", back_populates="feedbacks")


class Region(Base):
    """
    地域の静的情報を格納するテーブル。
    """
    __tablename__ = 'regions'

    id = Column(Integer, primary_key=True) # SERIAL PRIMARY KEY
    code = Column(Text, nullable=False, unique=True)
    en = Column(Text, nullable=False)
    ja = Column(Text, nullable=False)


class UsageStats(Base):
    """
    日々の使用統計を格納するテーブル。
    """
    __tablename__ = 'usage_stats'

    date = Column(Date, primary_key=True)
    users = Column(Integer, nullable=False)
    ja_users = Column(Integer, nullable=False)
    en_users = Column(Integer, nullable=False)
    invalid_users = Column(Integer, nullable=False)
    delete_ads_users = Column(Integer, nullable=False)
    total_user_pv_count = Column(Integer, nullable=False)
    players = Column(Integer, nullable=False)
    viewed_players = Column(Integer, nullable=False)
    acquire_automatically_players = Column(Integer, nullable=False)
    invalid_players = Column(Integer, nullable=False)
    inactive_players = Column(Integer, nullable=False)
    player_logs = Column(Integer, nullable=False)
    battles = Column(Integer, nullable=False)
    clubs = Column(Integer, nullable=False)
    # 新しい6つのカラムを追加
    posts = Column(Integer, nullable=False, server_default='0')
    team_posts = Column(Integer, nullable=False, server_default='0')
    friend_posts = Column(Integer, nullable=False, server_default='0')
    club_posts = Column(Integer, nullable=False, server_default='0')
    general_posts = Column(Integer, nullable=False, server_default='0')
    messages = Column(Integer, nullable=False, server_default='0')
    reactions = Column(Integer, nullable=False, server_default='0')


class Post(Base):
    """
    募集掲示板の投稿を格納するテーブル。
    """
    __tablename__ = 'posts'

    id = Column(Integer, primary_key=True)
    type = Column(Text, nullable=False) # "team", "friend", "club" など
    host_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    host_ip = Column(INET, nullable=False)
    host_anonymous_id = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    region = Column(Text, nullable=True)
    host_player_tag = Column(Text, ForeignKey('players.tag', ondelete='SET NULL'), nullable=True)
    host_club_tag = Column(Text, ForeignKey('clubs.tag', ondelete='SET NULL'), nullable=True)
    link = Column(Text, nullable=True)
    comment = Column(Text, nullable=True)
    conditions_application_type = Column(Text, nullable=False, server_default='and') # "and" または "or"
    chat_permission_level = Column(Integer, nullable=False, server_default='30')
    is_deleted = Column(Boolean, nullable=False, server_default='False', index=True)
    category = Column(Text, nullable=True)
    mode = Column(Text, nullable=True)
    hashtags = Column(JSONB, nullable=False, server_default='[]')
    custom_settings = Column(JSONB, nullable=False, server_default='{}')
    permitted_ids = Column(JSONB, nullable=False, server_default='[]')
    prohibited_ids = Column(JSONB, nullable=False, server_default='[]')
    
    # 参加条件
    required_highest_trophies = Column(Integer, nullable=True)
    required_current_trophies = Column(Integer, nullable=True)
    required_ranked_highest_rank = Column(Integer, nullable=True)
    required_ranked_current_rank = Column(Integer, nullable=True)
    required_ranked_highest_score = Column(Integer, nullable=True)
    required_ranked_current_score = Column(Integer, nullable=True)
    required_solo_pl_rank = Column(Integer, nullable=True)
    required_max_power_brawlers = Column(Integer, nullable=True)
    required_prestige = Column(Integer, nullable=True)
    other_conditions = Column(JSONB, nullable=False, server_default='{}')

    # SQLAlchemyのリレーションシップ (任意ですが、アプリ内で使う際に非常に便利です)
    messages = relationship("Message", back_populates="post")
    reports = relationship("Report", back_populates="post")
    
    __table_args__ = (
        # CREATE INDEX ON posts (type, created_at DESC); を正しく定義
        Index('idx_posts_type_created_at_desc', 'type', desc('created_at')),
    )


class PostVote(Base):
    """
    投稿への投票(👍/将来的な👎)を格納するテーブル。
    """
    __tablename__ = 'post_votes'

    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey('posts.id', ondelete='CASCADE'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    # 1=👍, -1=👎(将来用)
    vote_type = Column(SmallInteger, nullable=False, server_default='1')
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint('vote_type IN (1, -1)', name='ck_post_votes_vote_type'),
        UniqueConstraint('post_id', 'user_id', name='uq_post_votes_post_user'),
        Index('idx_post_votes_post_vote_type', 'post_id', 'vote_type'),
        Index('idx_post_votes_user_id', 'user_id'),
    )


class Message(Base):
    """
    投稿に付属するチャットスレッドのメッセージを格納するテーブル。
    """
    __tablename__ = 'messages'

    id = Column(Integer, primary_key=True)
    thread_id = Column(Integer, ForeignKey('posts.id', ondelete='CASCADE'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    user_ip = Column(INET, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    message_type = Column(Text, nullable=False, server_default='message')
    message = Column(Text, nullable=False)
    is_deleted = Column(Boolean, nullable=False, server_default='False')

    # リレーションシップ
    post = relationship("Post", back_populates="messages")
    reactions = relationship("Reaction", back_populates="message", cascade="all, delete-orphan")
    
    __table_args__ = (
        # CREATE INDEX ON messages (thread_id, created_at ASC); を正しく定義
        Index('idx_messages_thread_id_created_at_asc', 'thread_id', 'created_at'),
    )


class Reaction(Base):
    """
    チャットメッセージへのリアクションを格納するテーブル。
    """
    __tablename__ = 'reactions'

    id = Column(Integer, primary_key=True)
    message_id = Column(Integer, ForeignKey('messages.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    emoji = Column(Text, nullable=False)

    # リレーションシップ
    message = relationship("Message", back_populates="reactions")
    
    # 複合ユニーク制約: 1ユーザーは1メッセージに同じ絵文字で1回しかリアクションできない
    __table_args__ = (UniqueConstraint('message_id', 'user_id', 'emoji', name='_message_user_emoji_uc'),)


class Report(Base):
    """
    投稿やメッセージへの通報を格納するテーブル。
    """
    __tablename__ = 'reports'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    user_ip = Column(INET, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    target_type = Column(Text, nullable=False) # "post" or "message"
    target_id = Column(Integer, nullable=False) # 通報対象のID
    category = Column(Text, nullable=False)
    text = Column(Text, nullable=True)
    is_checked = Column(Boolean, nullable=False, server_default='False')
    
    # Postへのリレーションシップ (target_typeが'post'の場合)
    # primaryjoinで条件を指定することで、target_typeとtarget_idが一致するものを引ける
    post = relationship(
        "Post",
        primaryjoin="and_(Report.target_type=='post', foreign(Report.target_id)==Post.id)",
        back_populates="reports",
        uselist=False
    )
    
    __table_args__ = (
        # CREATE INDEX ON reports (is_checked, created_at DESC); を正しく定義
        Index('idx_reports_is_checked_created_at_desc', 'is_checked', desc('created_at')),
    )


class RankedStatsBrawler(Base):
    """
    ガチバトルのキャラクターごとの日次集計データを格納するテーブル。
    """
    __tablename__ = 'ranked_stats_brawler'

    #^ カラム定義 ---
    date = Column(Date, nullable=False)
    mode = Column(Text, nullable=False)
    map = Column(Text, nullable=False)
    rank_tier = Column(Integer, nullable=False)
    brawler_id = Column(Integer, nullable=False)

    #^ 集計データ ---
    total_games_in_condition = Column(Integer, nullable=False)
    games_played = Column(Integer, nullable=False)
    wins = Column(Integer, nullable=False)
    use_rate = Column(Float, nullable=False)
    win_rate = Column(Float, nullable=False)
    star_player_count = Column(Integer, nullable=False)
    star_player_rate = Column(Float, nullable=False)

    #^ 管理用 ---
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    #^ 複合主キーを定義
    __table_args__ = (
        PrimaryKeyConstraint('date', 'mode', 'map', 'rank_tier', 'brawler_id', name='ranked_stats_brawler_pkey'),
        Index('idx_brawler_mode_rank_date', 'mode', 'rank_tier', 'date'),
    )


class RankedStatsSynergy(Base):
    """
    キャラクター2体間の相性（シナジー/カウンター）に関する日次集計データを格納するテーブル。
    """
    __tablename__ = 'ranked_stats_synergy'

    #^ カラム定義 ---
    date = Column(Date, nullable=False)
    mode = Column(Text, nullable=False)
    map = Column(Text, nullable=False)
    rank_tier = Column(Integer, nullable=False)
    brawler_id_1 = Column(Integer, nullable=False)
    brawler_id_2 = Column(Integer, nullable=False)

    #^ 集計データ ---
    synergy_games_played = Column(Integer, nullable=False)
    synergy_wins = Column(Integer, nullable=False)
    synergy_win_rate = Column(Float, nullable=False)
    counter_games_played = Column(Integer, nullable=False)
    counter_wins = Column(Integer, nullable=False)
    counter_win_rate = Column(Float, nullable=False)

    #^ 管理用 ---
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    #^ 複合主キーとチェック制約
    __table_args__ = (
        PrimaryKeyConstraint('date', 'mode', 'map', 'rank_tier', 'brawler_id_1', 'brawler_id_2', name='ranked_stats_synergy_pkey'),
        CheckConstraint('brawler_id_1 < brawler_id_2', name='ck_synergy_brawler_order'),
        Index('idx_synergy_mode_rank_date', 'mode', 'rank_tier', 'date'),
    )


class RankedStatsComposition(Base):
    """
    3キャラクターのチーム編成ごとの日次統計データを格納するテーブル。
    """
    __tablename__ = 'ranked_stats_composition'

    #^ カラム定義 ---
    date = Column(Date, nullable=False)
    mode = Column(Text, nullable=False)
    map = Column(Text, nullable=False)
    rank_tier = Column(Integer, nullable=False)
    brawler_id_1 = Column(Integer, nullable=False)
    brawler_id_2 = Column(Integer, nullable=False)
    brawler_id_3 = Column(Integer, nullable=False)
    
    #^ 集計データ ---
    games_played = Column(Integer, nullable=False)
    wins = Column(Integer, nullable=False)
    win_rate = Column(Float, nullable=False)

    #^ 管理用 ---
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    #^ 複合主キーとチェック制約
    __table_args__ = (
        PrimaryKeyConstraint('date', 'mode', 'map', 'rank_tier', 'brawler_id_1', 'brawler_id_2', 'brawler_id_3', name='ranked_stats_composition_pkey'),
        CheckConstraint('brawler_id_1 < brawler_id_2 AND brawler_id_2 < brawler_id_3', name='ck_composition_brawler_order'),
        Index('idx_composition_mode_rank_date', 'mode', 'rank_tier', 'date'),
    )


class UserBlock(Base):
    """
    ユーザー間のブロック関係を格納するテーブル。
    """
    __tablename__ = 'user_blocks'

    id = Column(Integer, primary_key=True)
    # ブロックした側
    blocker_user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=True)
    blocker_anonymous_id = Column(Text, nullable=True)
    # ブロックされた側
    blocked_user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=True)
    blocked_anonymous_id = Column(Text, nullable=True)
    # 作成日時
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        #* 制約① ---
        # blocker_user_id と blocker_anonymous_id のうち、片方のみがNULLであることを保証
        CheckConstraint(
            'num_nonnulls(blocker_user_id, blocker_anonymous_id) = 1',
            name='ck_user_blocks_blocker_one_not_null'
        ),
        # blocked_user_id と blocked_anonymous_id のうち、片方のみがNULLであることを保証
        CheckConstraint(
            'num_nonnulls(blocked_user_id, blocked_anonymous_id) = 1',
            name='ck_user_blocks_blocked_one_not_null'
        ),

        #* 制約② ---
        # ブロックする側とブロックされる側の組み合わせの重複を禁止
        UniqueConstraint(
            'blocker_user_id', 'blocked_user_id',
            name='uq_user_blocks_user_to_user'
        ),
        UniqueConstraint(
            'blocker_user_id', 'blocked_anonymous_id',
            name='uq_user_blocks_user_to_anonymous'
        ),
        UniqueConstraint(
            'blocker_anonymous_id', 'blocked_user_id',
            name='uq_user_blocks_anonymous_to_user'
        ),
        UniqueConstraint(
            'blocker_anonymous_id', 'blocked_anonymous_id',
            name='uq_user_blocks_anonymous_to_anonymous'
        ),

        #* パフォーマンス向上のためのインデックス ---
        Index('idx_user_blocks_blocker', 'blocker_user_id', 'blocker_anonymous_id'),
    )


class Faq(Base):
    """
    よくある質問を格納するテーブル。
    """
    __tablename__ = 'faqs'

    id = Column(Integer, primary_key=True)                                      # 自動採番
    priority = Column(Integer, nullable=False, server_default='0')              # 低いほど優先
    category_ja = Column(Text, nullable=False)                                  # カテゴリ (日本語)
    category_en = Column(Text, nullable=False)                                  # カテゴリ (英語)
    title_ja = Column(Text, nullable=False)                                     # 質問タイトル (日本語)
    title_en = Column(Text, nullable=False)                                     # 質問タイトル (英語)
    body_ja = Column(Text, nullable=False)                                      # 回答本文 (日本語)
    body_en = Column(Text, nullable=True)                                       # 回答本文 (英語, 任意)
    is_deleted = Column(Boolean, nullable=False, server_default='False')        # 論理削除フラグ


class BrawlVideo(Base):
    """
    ブロスタ動画ページに表示する動画を格納するテーブル。
    スポンサー動画と非スポンサー動画をDBで管理し、管理画面から編集する。
    """
    __tablename__ = 'brawl_videos'

    id = Column(Integer, primary_key=True)
    title_ja = Column(Text, nullable=False)                                     # 日本語タイトル
    title_en = Column(Text, nullable=True)                                      # 英語タイトル（任意。空の場合はtitle_jaで代替）
    platform = Column(Text, nullable=False, server_default='youtube')           # 'youtube' / 将来: 'tiktok' 等
    video_id = Column(Text, nullable=False)                                     # YouTubeのVideo ID等
    thumbnail_url = Column(Text, nullable=True)                                 # カスタムサムネイルURL（空の場合はYouTubeから自動取得）
    is_sponsored = Column(Boolean, nullable=False, server_default='False')      # スポンサー動画フラグ
    sponsor_name = Column(Text, nullable=True)                                  # スポンサー名（ラベル表示用）
    display_order = Column(Integer, nullable=False, server_default='0')         # 表示順（小さいほど先）
    is_active = Column(Boolean, nullable=False, server_default='True')          # 表示有効フラグ
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index('ix_brawl_videos_display_order', 'display_order'),
        Index('ix_brawl_videos_is_active', 'is_active'),
    )
