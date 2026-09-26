import random
from datetime import datetime, timedelta, timezone

from app.services.ranked_map_pool_inference import Observation, infer_ranked_pools

UTC = timezone.utc
HOUR = timedelta(hours=1)
WINDOW_START = datetime(2026, 9, 17, 7, 0, tzinfo=UTC)

BASE_POOL = list(range(100, 126))  # 26マップ


def _mode_of(map_id: int) -> int:
    return 48000000 + map_id % 6


def _simulate(schedule, hours: int, *, per_hour=None, seed: int = 1, extra=None) -> list[Observation]:
    """schedule(hour_index) -> そのときのプール。1時間ごとに per_hour 件のバトルをプールから無作為に割り振る。"""
    rng = random.Random(seed)
    observations: list[Observation] = []
    for index in range(hours):
        pool = schedule(index)
        total = per_hour(index) if per_hour else 400
        counts: dict[int, int] = {}
        for _ in range(total):
            map_id = rng.choice(pool)
            counts[map_id] = counts.get(map_id, 0) + 1
        for map_id, count in counts.items():
            observations.append(Observation(WINDOW_START + index * HOUR, map_id, _mode_of(map_id), count))
    for item in extra or []:
        observations.append(item)
    return observations


def _ids(period) -> set[int]:
    return set(period.map_ids)


def test_stable_pool_is_one_ongoing_period():
    obs = _simulate(lambda _: BASE_POOL, 24 * 5)
    result = infer_ranked_pools(obs, WINDOW_START, WINDOW_START + 24 * 5 * HOUR, ongoing=True)
    assert len(result.periods) == 1
    period = result.periods[0]
    assert _ids(period) == set(BASE_POOL)
    assert period.end_at is None
    assert result.coverage == 1.0
    assert all(item["mode_id"] == _mode_of(item["map_id"]) for item in period.maps)


def test_previous_season_tail_is_dropped():
    old_pool = BASE_POOL[:24] + [900, 901]
    obs = _simulate(lambda h: old_pool if h < 1 else BASE_POOL, 24 * 4)
    result = infer_ranked_pools(obs, WINDOW_START, WINDOW_START + 24 * 4 * HOUR, ongoing=True)
    assert len(result.periods) == 1
    assert _ids(result.periods[0]) == set(BASE_POOL)
    assert result.periods[0].start_at == WINDOW_START + HOUR


def test_mid_season_change_splits_periods_at_change_time():
    new_pool = BASE_POOL[2:] + [200, 201]
    change = 24 * 6 + 7
    obs = _simulate(lambda h: BASE_POOL if h < change else new_pool, 24 * 12)
    end = WINDOW_START + 24 * 12 * HOUR
    result = infer_ranked_pools(obs, WINDOW_START, end, ongoing=False)
    assert [len(p.maps) for p in result.periods] == [26, 26]
    first, second = result.periods
    assert _ids(first) == set(BASE_POOL)
    assert _ids(second) == set(new_pool)
    assert abs((second.start_at - (WINDOW_START + change * HOUR)) / HOUR) <= 1
    assert first.end_at == second.start_at
    assert second.end_at == end


def test_short_dip_is_absorbed():
    dip_map = BASE_POOL[5]
    reduced = [m for m in BASE_POOL if m != dip_map]
    obs = _simulate(lambda h: reduced if 50 <= h < 54 else BASE_POOL, 24 * 5)
    result = infer_ranked_pools(obs, WINDOW_START, WINDOW_START + 24 * 5 * HOUR, ongoing=True)
    assert len(result.periods) == 1
    assert dip_map in _ids(result.periods[0])


def test_noise_map_is_ignored():
    noise = [Observation(WINDOW_START + 30 * HOUR, 999, 48000002, 3)]
    obs = _simulate(lambda _: BASE_POOL, 24 * 3, extra=noise)
    result = infer_ranked_pools(obs, WINDOW_START, WINDOW_START + 24 * 3 * HOUR, ongoing=True)
    assert len(result.periods) == 1
    assert 999 not in _ids(result.periods[0])


def test_excluded_map_is_removed():
    obs = _simulate(lambda _: BASE_POOL, 24 * 3)
    result = infer_ranked_pools(
        obs, WINDOW_START, WINDOW_START + 24 * 3 * HOUR, ongoing=True, excluded_map_ids=[BASE_POOL[0]]
    )
    assert len(result.periods) == 1
    assert _ids(result.periods[0]) == set(BASE_POOL[1:])


def test_sparse_data_does_not_create_false_changes():
    # 夜間や障害で観測が極端に少ない時間帯があっても、マップが消えたとはみなさない
    obs = _simulate(
        lambda _: BASE_POOL,
        24 * 8,
        per_hour=lambda h: 5 if 48 <= h < 96 else 400,
    )
    result = infer_ranked_pools(obs, WINDOW_START, WINDOW_START + 24 * 8 * HOUR, ongoing=True)
    assert len(result.periods) == 1
    assert _ids(result.periods[0]) == set(BASE_POOL)
    assert 0.7 < result.coverage < 0.8


def test_next_season_head_is_dropped_for_finished_window():
    next_pool = BASE_POOL[3:] + [300, 301, 302]
    hours = 24 * 5
    obs = _simulate(lambda h: BASE_POOL if h < hours - 2 else next_pool, hours)
    result = infer_ranked_pools(obs, WINDOW_START, WINDOW_START + hours * HOUR, ongoing=False)
    assert len(result.periods) == 1
    assert _ids(result.periods[0]) == set(BASE_POOL)


def test_fresh_change_in_ongoing_window_is_kept():
    new_pool = BASE_POOL[1:] + [400]
    hours = 24 * 4
    obs = _simulate(lambda h: BASE_POOL if h < hours - 3 else new_pool, hours)
    result = infer_ranked_pools(obs, WINDOW_START, WINDOW_START + hours * HOUR, ongoing=True)
    assert len(result.periods) == 2
    assert _ids(result.periods[-1]) == set(new_pool)
    assert result.periods[-1].end_at is None


def test_empty_window():
    result = infer_ranked_pools([], WINDOW_START, WINDOW_START + 10 * HOUR, ongoing=True)
    assert result.periods == []
    assert result.coverage == 0.0
