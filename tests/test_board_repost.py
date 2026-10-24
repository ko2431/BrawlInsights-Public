import datetime

from app.services.board_repost_service import format_repost_wait_text, next_post_repost_at


CREATED = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)


def _h(hours: float) -> datetime.timedelta:
    return datetime.timedelta(hours=hours)


def test_first_repost_waits_30_minutes_after_posting():
    assert next_post_repost_at(CREATED, None) == CREATED + datetime.timedelta(minutes=30)


def test_tier_is_decided_by_age_at_the_time_of_repost():
    # 5時間30分にリポスト → 6時間ちょうどは「6時間以内」の段（30分）
    assert next_post_repost_at(CREATED, CREATED + _h(5.5)) == CREATED + _h(6)
    # 5時間50分にリポスト → 30分後は6時間を超えるので「12時間以内」の段（60分）
    assert next_post_repost_at(CREATED, CREATED + _h(5) + datetime.timedelta(minutes=50)) == CREATED + _h(6) + datetime.timedelta(minutes=50)
    # 23時間54分にリポスト → 2時間後は24時間を超えるので「2日以内」の段（3時間）
    reposted = CREATED + _h(23.9)
    assert next_post_repost_at(CREATED, reposted) == reposted + _h(3)


def test_old_posts_wait_longer():
    reposted = CREATED + _h(100)
    assert next_post_repost_at(CREATED, reposted) == reposted + _h(12)
    reposted = CREATED + _h(400)
    assert next_post_repost_at(CREATED, reposted) == reposted + _h(48)


def test_wait_text():
    assert format_repost_wait_text(30, "ja") == "1分"
    assert format_repost_wait_text(80 * 60, "ja") == "1時間20分"
    assert format_repost_wait_text(2 * 3600, "ja") == "2時間"
    assert format_repost_wait_text(61 * 60, "en") == "1 hour 1 minute"
