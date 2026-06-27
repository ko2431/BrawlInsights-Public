from dataclasses import dataclass


@dataclass(frozen=True)
class GeneralBoardTrendingConfig:
    """なんでも掲示板「話題の投稿」フィルターのスコア計算パラメータ。"""

    weight_likes: float = 1.0
    weight_comments: float = 2.0
    age_offset_hours: float = 2.0
    gravity: float = 1.3
    candidate_max_age_days: int = 14
    cache_ttl_seconds: int = 45


GENERAL_BOARD_TRENDING = GeneralBoardTrendingConfig()
