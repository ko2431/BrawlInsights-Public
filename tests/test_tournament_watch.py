import datetime

from app.services.tournament_watch import HINT_HOST_MINUTES, HINT_IDLE_MINUTES, HINT_WAIT_MINUTES, stall_hint

NOW = datetime.datetime(2026, 10, 26, 12, 0, tzinfo=datetime.timezone.utc)


def ago(minutes: float) -> datetime.datetime:
    return NOW - datetime.timedelta(minutes=minutes)


def hint(m: dict, *, player=True, staff=False, live=True, forfeit=15, last_game_at=None):
    return stall_hint({"review_at": None, "review_reason": None, "forfeit_deadline_at": None, "started_at": None,
                       "room_registered_at": None} | m,
                      live=live, player=player, staff=staff, forfeit_minutes=forfeit, last_game_at=last_game_at, now=NOW)


def test_only_players_and_staff_while_live():
    m = {"state": "ready", "room_registered_at": ago(30)}
    assert hint(m) == "player"
    assert hint(m, player=False, staff=True) == "player"
    assert hint(m, player=False, staff=False) is None  # 観戦者には出さない
    assert hint(m, live=False) is None


def test_waiting_room_uses_forfeit_timer_start():
    # 不戦敗タイマー15分の締切が12分後 = 3分前に始まった → まだ出さない
    m = {"state": "waiting_room", "forfeit_deadline_at": NOW + datetime.timedelta(minutes=12), "started_at": ago(60)}
    assert hint(m) is None
    m["forfeit_deadline_at"] = NOW + datetime.timedelta(minutes=15 - HINT_WAIT_MINUTES)
    assert hint(m) == "player"
    # タイマーのない大会（止めたあと）は試合の開始から
    assert hint({"state": "waiting_room", "started_at": ago(HINT_WAIT_MINUTES - 1)}) is None


def test_ready_and_playing_idle():
    assert hint({"state": "ready", "room_registered_at": ago(HINT_IDLE_MINUTES - 1)}) is None
    assert hint({"state": "ready", "room_registered_at": ago(HINT_IDLE_MINUTES)}) == "player"
    # 対戦中は最後のゲームから数える
    assert hint({"state": "playing", "room_registered_at": ago(60)}, last_game_at=ago(3)) is None
    assert hint({"state": "playing", "room_registered_at": ago(60)}, last_game_at=ago(HINT_IDLE_MINUTES)) == "player"


def test_review_host_hint_for_players_only():
    m = {"state": "review", "review_at": ago(HINT_HOST_MINUTES), "review_reason": "outsider"}
    assert hint(m) == "host"
    assert hint(m, player=False, staff=True) is None
    assert hint({"state": "review", "review_at": ago(HINT_HOST_MINUTES - 1)}) is None
    # 部屋が登録されないまま確認待ち → 主催者には参加者の不在のヒント（失格の案内）
    assert hint({"state": "review", "review_at": ago(1), "review_reason": "no_room"}, player=False, staff=True) == "player"


def test_no_hint_in_other_states():
    for state in ("pending", "confirming", "done", "forfeit", "bye", "cancelled"):
        assert hint({"state": state, "room_registered_at": ago(60), "started_at": ago(60)}) is None
