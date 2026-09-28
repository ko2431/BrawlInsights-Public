from dataclasses import dataclass


@dataclass(frozen=True)
class GeneralBoardTrendingConfig:
    """なんでも掲示板「話題」タブのスコア計算パラメータ。

    全期間の投稿が対象。負荷を抑えるため、直近 recent_window_days 日の投稿は短い TTL で、
    それより古い投稿（アーカイブ）はスコアがほぼ変動しないため長い TTL で計算する。
    """

    weight_likes: float = 1.0
    weight_comments: float = 2.0
    age_offset_hours: float = 2.0
    gravity: float = 1.3
    # 直近の投稿として cache_ttl_seconds ごとにスコアを再計算する期間
    recent_window_days: int = 14
    cache_ttl_seconds: int = 45
    # アーカイブ（recent_window_days より古い投稿）のスコア再計算間隔
    archive_refresh_seconds: int = 1800
    # アーカイブのうち、直近の投稿とスコア順にマージする上位件数（以降はアーカイブのスコア順で後ろに連結）
    archive_merge_limit: int = 2000
    # アーカイブの ID リストを Redis に分割保存する1チャンクあたりの件数
    archive_chunk_size: int = 1000
    # 検索時にスコア順で並べる最大件数
    search_max_ranked: int = 5000


GENERAL_BOARD_TRENDING = GeneralBoardTrendingConfig()
