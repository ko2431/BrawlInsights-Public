"""プレイヤーアップデートの1人ごとの待ち時間が、処理時間を含めた間隔になることを検証する。"""

import time

from app.background_tasks import tasks


def test_remaining_interval_subtracts_processing_time():
    started_at = time.monotonic() - 0.4
    assert 0.75 < tasks._remaining_interval(1.2, started_at) <= 0.8


def test_remaining_interval_is_zero_when_processing_is_longer():
    started_at = time.monotonic() - 3.0
    assert tasks._remaining_interval(1.2, started_at) == 0.0


def test_loop_target_is_about_an_hour():
    assert tasks.PLAYER_UPDATE_LOOP_TARGET_SECONDS == 3600
