"""掲示板企画の「大会を主催・参加」: 12月の企画から一度も試合に出なかったエントリーを除く（大会の検討メモ 13.20・D71）。"""
from datetime import datetime, timezone

from app.services import board_campaign_service as bcs
from app.services import board_campaign_start as start
from app.services.tournament_prizes import PLAYED_SQL


def _campaign(starts_at: datetime, ends_at: datetime) -> bcs.Campaign:
    return bcs.Campaign(
        id=1, name_ja="企画", name_en="Campaign", starts_at=starts_at, ends_at=ends_at, is_published=True,
        settings=bcs.default_settings(), start_announcement_id=None, stats={}, counted_at=None, counted_day=None,
        locked_through=None, snapshot_at=None,
    )


NOV = _campaign(datetime(2026, 11, 1, tzinfo=timezone.utc), datetime(2026, 11, 29, tzinfo=timezone.utc))
DEC = _campaign(datetime(2026, 12, 1, tzinfo=timezone.utc), datetime(2026, 12, 30, tzinfo=timezone.utc))


def _sql(c: bcs.Campaign) -> str:
    return bcs._method_sql("tournament", bcs._Params(), c.starts_at, c.ends_at, c, None)


def test_requires_play_from_december():
    assert not bcs.tournament_requires_play(NOV)
    assert bcs.tournament_requires_play(DEC)


def test_method_sql_adds_played_only_from_december():
    played = PLAYED_SQL.format(entry="e.id")
    assert played not in _sql(NOV)
    # 人数（チェックイン済みのエントリー）とメンバーの口の両方に入る
    assert _sql(DEC).count(played) == 2


def test_notes_follow_campaign():
    tournament_group = next(g for g in bcs.CHIP_GROUPS if g["methods"] == ("tournament",))
    assert "一度も試合に出なかった" not in bcs._group_note(tournament_group, NOV, "ja")
    assert "チェックインしなかった・一度も試合に出なかった・失格・棄権" in bcs._group_note(tournament_group, DEC, "ja")
    assert "never playing a match" in bcs._group_note(tournament_group, DEC, "en")
    # ほかのまとまりは今までどおり
    chat_group = next(g for g in bcs.CHIP_GROUPS if "chat" in g["methods"])
    assert bcs._group_note(chat_group, DEC, "ja") == chat_group["note_ja"]


def test_start_announcement_notes_follow_campaign():
    nov, dec = start.content_html(NOV, "ja"), start.content_html(DEC, "ja")
    assert "チェックインしなかった・失格・棄権となった場合は対象外" in nov
    assert "一度も試合に出なかった" not in nov
    assert "チェックインしなかった・一度も試合に出なかった・失格・棄権となった場合は対象外" in dec
    assert "試合に出た参加者が合計4人以上" in dec
    assert "never played a match" in start.content_html(DEC, "en")
