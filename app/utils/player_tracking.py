# /app/utils/player_tracking.py
"""プレイヤー自動追跡・バトル履歴の保存期間の延長に関する料金と上限。

サーバーの検証と、プレイヤーページの延長パネル(static/js/player_tracking.js)で同じ値を使う。
"""
import datetime
import math
from typing import Any

# --- 自動追跡 ---
TRACKING_MAX_DAYS = 365  # 残り時間をこの日数まで延長できる
TRACKING_DAY_TOKENS = 10
TRACKING_DAY_ELIXIRS = 24
TRACKING_BUNDLE_DAYS = 10  # 10日ごとにまとめ買いの料金になる
TRACKING_BUNDLE_TOKENS = 90
TRACKING_BUNDLE_ELIXIRS = 216

# --- バトル履歴の保存期間 ---
RETENTION_MAX_MONTHS = 120
RETENTION_MONTH_TOKENS = 100
RETENTION_MONTH_ELIXIRS = 240


def tracking_cost(days: int) -> tuple[int, int]:
    """自動追跡を days 日延長する料金を (トークン, エリクサー) で返す。10日ごとにまとめ買い、端数の日は1日ごとの料金。"""
    bundles, rest = divmod(days, TRACKING_BUNDLE_DAYS)
    tokens = bundles * TRACKING_BUNDLE_TOKENS + rest * TRACKING_DAY_TOKENS
    elixirs = bundles * TRACKING_BUNDLE_ELIXIRS + rest * TRACKING_DAY_ELIXIRS
    return tokens, elixirs


def tracking_max_days(remaining_seconds: float) -> int:
    """今の残り時間から、あと何日延長できるか。残り時間が上限に届くまで(端数は切り上げ)。"""
    left = TRACKING_MAX_DAYS * 86400 - max(remaining_seconds, 0)
    return max(0, math.ceil(left / 86400))


def retention_cost(months: int) -> tuple[int, int]:
    """保存期間を months ヶ月延長する料金を (トークン, エリクサー) で返す。"""
    return months * RETENTION_MONTH_TOKENS, months * RETENTION_MONTH_ELIXIRS


def tracking_remaining_seconds(player: Any) -> int:
    """表示する時点での自動追跡の残り秒数。キャッシュの残り時間は古いことがあるため、有効期限があればそこから計算する。"""
    if getattr(player, "level", 0) < 30:
        return 0
    expiration = getattr(player, "auto_track_expiration", None)
    if expiration:
        if expiration.tzinfo is None:
            expiration = expiration.replace(tzinfo=datetime.timezone.utc)
        seconds = (expiration - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
    else:
        remaining = getattr(player, "remaining_time", None)
        seconds = remaining.total_seconds() if remaining else 0
    return max(0, int(seconds))


# プレイヤーページの延長パネルに渡す料金表
PRICING_FOR_CLIENT = {
    "trackingMaxDays": TRACKING_MAX_DAYS,
    "trackingDayTokens": TRACKING_DAY_TOKENS,
    "trackingDayElixirs": TRACKING_DAY_ELIXIRS,
    "trackingBundleDays": TRACKING_BUNDLE_DAYS,
    "trackingBundleTokens": TRACKING_BUNDLE_TOKENS,
    "trackingBundleElixirs": TRACKING_BUNDLE_ELIXIRS,
    "retentionMaxMonths": RETENTION_MAX_MONTHS,
    "retentionMonthTokens": RETENTION_MONTH_TOKENS,
    "retentionMonthElixirs": RETENTION_MONTH_ELIXIRS,
}
