from datetime import datetime, timedelta, timezone

from app.services.map_rotation_inference import (
    STATE_PREDICTED,
    STATE_UNKNOWN,
    STATE_VERIFIED,
    Cycle,
    CycleMap,
    Observation,
    infer_slot,
)

UTC = timezone.utc


def _cycle(maps: list[int], *, hours: int = 24, start: datetime | None = None) -> Cycle:
    anchor = start or datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    return Cycle(
        cycle_length=len(maps),
        duration_minutes=hours * 60,
        anchor_start=anchor,
        maps=[CycleMap(map_id, STATE_VERIFIED) for map_id in maps],
        source="seed",
    )


def _obs(start: datetime, map_id: int, *, hours: int = 24) -> Observation:
    return Observation(start=start, end=start + timedelta(hours=hours), map_id=map_id)


def _daily(anchor: datetime, count: int, maps: list[int], *, hours: int = 24) -> list[Observation]:
    return [
        _obs(anchor + timedelta(hours=hours * index), maps[index % len(maps)], hours=hours)
        for index in range(count)
    ]


def test_substitution_keeps_predictions_and_sets_completion():
    baseline = _cycle([1, 2, 3, 4])
    anchor = baseline.anchor_start
    observed = _daily(anchor, 3, [1, 9, 3])
    now = observed[-1].start + timedelta(hours=1)
    result = infer_slot(baseline, observed, now=now)

    assert result.changed
    assert result.change_started_at == observed[1].start
    assert [item.map_id for item in result.cycle.maps] == [1, 9, 3, 4]
    assert result.cycle.maps[1].state == STATE_VERIFIED
    assert result.cycle.maps[3].state == STATE_PREDICTED
    assert result.confirmed is False
    assert result.expected_complete_at == observed[1].start + timedelta(days=4)


def test_full_loop_confirms_replacement():
    baseline = _cycle([1, 2, 3, 4])
    anchor = baseline.anchor_start
    observed = _daily(anchor, 6, [1, 9, 3, 4])
    now = observed[-1].start + timedelta(hours=1)
    result = infer_slot(baseline, observed, now=now)

    assert result.confirmed
    assert result.cycle.cycle_length == 4
    assert [item.map_id for item in result.cycle.maps] == [1, 9, 3, 4]


def test_slide_does_not_start_episode():
    baseline = _cycle([1, 2, 3, 4])
    anchor = baseline.anchor_start
    # 期待 1,2,3 に対して 2,3,4 が続き、位置が1日進んだだけ
    observed = _daily(anchor, 3, [2, 3, 4])
    now = observed[-1].start + timedelta(hours=1)
    result = infer_slot(baseline, observed, now=now)

    assert result.changed is False
    assert [item.map_id for item in result.cycle.maps] == [1, 2, 3, 4]
    assert result.latest_index == 3


def test_single_slid_observation_waits_for_next():
    baseline = _cycle([1, 2, 3, 4])
    anchor = baseline.anchor_start
    observed = _daily(anchor, 1, [2])
    now = observed[-1].start + timedelta(hours=1)
    result = infer_slot(baseline, observed, now=now)

    assert result.changed is False
    assert result.current_map_id == 2
    assert result.latest_index == 0


def test_slid_then_back_on_track_is_replacement():
    baseline = _cycle([1, 2, 3, 4])
    anchor = baseline.anchor_start
    # 1日目に周期内の別マップ(3)が出て、2日目は予定通り2。スライドではなく位置0の置換。
    observed = _daily(anchor, 2, [3, 2])
    now = observed[-1].start + timedelta(hours=1)
    result = infer_slot(baseline, observed, now=now)

    assert result.changed
    assert result.change_started_at == observed[0].start
    assert result.cycle.maps[0].map_id == 3
    assert result.cycle.maps[2].state == STATE_UNKNOWN


def test_half_period_shift_uses_previous_window():
    baseline = _cycle([1, 2, 3, 4])
    # メンテ明けで切替が12時間ずれた。その時刻に出ていた枠(位置1)として扱う。
    start = baseline.anchor_start + timedelta(days=1, hours=12)
    observed = [_obs(start, 2), _obs(start + timedelta(days=1), 3)]
    now = observed[-1].start + timedelta(hours=1)
    result = infer_slot(baseline, observed, now=now)

    assert result.changed is False
    assert result.latest_index == 2
    assert result.switch_time_changed
    assert result.switch_clock == 20 * 60


def test_switch_time_change_keeps_maps():
    baseline = _cycle([1, 2, 3, 4])
    anchor = baseline.anchor_start + timedelta(hours=3)
    observed = _daily(anchor, 2, [1, 2])
    now = observed[-1].start + timedelta(hours=1)
    result = infer_slot(baseline, observed, now=now)

    assert result.switch_time_changed
    assert result.changed is False
    assert [item.map_id for item in result.cycle.maps] == [1, 2, 3, 4]


def test_shortened_cycle_length():
    baseline = _cycle([1, 2, 3, 4])
    anchor = baseline.anchor_start
    observed = _daily(anchor, 5, [9, 8, 7])
    now = observed[-1].start + timedelta(hours=1)
    result = infer_slot(baseline, observed, now=now)

    assert result.changed
    assert result.cycle.cycle_length == 3
    assert [item.map_id for item in result.cycle.maps] == [9, 8, 7]


def test_length_stays_uncertain_when_repeat_distance_conflicts():
    baseline = _cycle([1, 2, 3, 4])
    anchor = baseline.anchor_start
    # 5日目に先頭と違うマップが同じ剰余へ戻る。再出現距離も旧日数と矛盾する。
    observed = _daily(anchor, 5, [9, 8, 7, 6, 5])
    now = observed[-1].start + timedelta(hours=1)
    result = infer_slot(baseline, observed, now=now)

    assert result.cycle_length_uncertain
    assert result.cycle.cycle_length is None


def test_two_hour_slot_and_gap():
    baseline = _cycle([1, 2, 3, 4], hours=2)
    anchor = baseline.anchor_start
    observed = [
        _obs(anchor, 1, hours=2),
        _obs(anchor + timedelta(hours=6), 4, hours=2),
    ]
    now = observed[-1].start + timedelta(minutes=30)
    result = infer_slot(baseline, observed, now=now)

    assert result.changed is False
    assert result.latest_index == 3
    assert result.current_map_id == 4


def test_external_episode_verifies_unchanged_slot():
    baseline = _cycle([1, 2, 3, 4])
    anchor = baseline.anchor_start
    observed = _daily(anchor, 5, [1, 2, 3, 4])
    now = observed[-1].start + timedelta(hours=1)
    result = infer_slot(baseline, observed, now=now, episode_started_at=anchor)

    assert result.changed
    assert result.confirmed
    assert all(item.state == STATE_VERIFIED for item in result.cycle.maps)
    assert result.cycle.maps[0].state != STATE_UNKNOWN
