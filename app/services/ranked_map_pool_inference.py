"""ガチバトルのマッププール推論。1時間ごとのマップ出現数から、期間ごとのマップ一覧を求める。

DBやRedisには触れない純粋関数だけを置く(テストしやすくするため)。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Iterable

HOUR = timedelta(hours=1)

# [この部分は公開用リポジトリでは非公開にされています]


@dataclass(frozen=True)
class Observation:
    hour: datetime
    map_id: int
    mode_id: int | None
    battle_count: int


@dataclass
class InferredPeriod:
    start_at: datetime
    end_at: datetime | None
    maps: list[dict[str, Any]] = field(default_factory=list)

    @property
    def map_ids(self) -> frozenset[int]:
        return frozenset(item["map_id"] for item in self.maps)


@dataclass
class InferenceResult:
    periods: list[InferredPeriod]
    coverage: float
    total_battles: int


@dataclass
class _Piece:
    map_id: int
    start: int
    end: int
    count: int
    share: float


# [この部分は公開用リポジトリでは非公開にされています]


def infer_ranked_pools(
    observations: Iterable[Observation],
    window_start: datetime,
    window_end: datetime,
    *,
    ongoing: bool,
    excluded_map_ids: Iterable[int] = (),
) -> InferenceResult:
    """窓内の観測から期間ごとのマッププールを求める。

    Args:
        observations: 1時間単位の観測。窓の外のものは無視する。
        window_start: 窓の開始(時間単位に切り捨てる)。
        window_end: 窓の終了(含まない)。進行中のシーズンではデータの最終時刻。
        ongoing: 窓の終端がシーズン終了ではなくデータの終端かどうか。Trueなら最後の期間の end_at は None。
        excluded_map_ids: 管理者が除外したマップ。
    """
    # [この部分は公開用リポジトリでは非公開にされています]
