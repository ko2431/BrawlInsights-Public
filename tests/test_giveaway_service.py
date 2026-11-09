"""事前応募型企画（giveaway_service・giveaway_prizes）の計算のテスト。"""
import unittest
from datetime import datetime, timedelta, timezone

from app.services import giveaway_prizes as gp
from app.services import giveaway_service as gs


def _campaign(**kwargs) -> gs.Campaign:
    settings = gs.default_settings()
    settings.update(kwargs.pop("settings", {}))
    base = {
        "id": 1, "round_no": 97, "name_ja": "第97回プレゼント企画", "name_en": "Giveaway #97",
        "starts_at": datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc),
        "ends_at": datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc),
        "is_published": True, "settings": gs.merged_settings(settings), "stats": {},
    }
    base.update(kwargs)
    return gs.Campaign(**base)


class ParticipationTest(unittest.TestCase):
    def test_bonus_is_the_largest_reached_step(self):
        s = gs.default_settings()
        self.assertEqual(gs.participation_tokens(s, 0), 0)
        self.assertEqual(gs.participation_tokens(s, 49), 49)
        self.assertEqual(gs.participation_tokens(s, 50), 70)
        self.assertEqual(gs.participation_tokens(s, 99), 119)
        self.assertEqual(gs.participation_tokens(s, 100), 150)

    def test_next_bonus_shows_the_difference(self):
        s = gs.default_settings()
        self.assertEqual(gs.next_bonus(s, 37), {"min": 50, "remaining": 13, "delta": 20})
        self.assertEqual(gs.next_bonus(s, 61), {"min": 100, "remaining": 39, "delta": 30})
        self.assertIsNone(gs.next_bonus(s, 100))

    def test_participation_text(self):
        c = _campaign()
        self.assertEqual(gs.participation_text(c, "ja"), "応募口数 × 1トークン（50口以上で+20、100口で+50）")
        self.assertEqual(gs.participation_text(_campaign(settings={"participation_per_entry": 0, "participation_bonuses": []}), "ja"), "")


class ReachTest(unittest.TestCase):
    def test_reachable_with_daily_limit(self):
        c = _campaign()
        # 11/17（UTC）の時点で61口・今日0口。残りは今日・18・19日の3日 × 8口
        now = datetime(2026, 11, 17, 3, 0, tzinfo=timezone.utc)
        self.assertEqual(gs.reachable_total(c, 61, 0, now), 85)
        self.assertEqual(gs.reachable_total(c, 61, 8, now), 77)
        # 初日はまだ届く
        self.assertEqual(gs.reachable_total(c, 0, 0, datetime(2026, 11, 6, 1, 0, tzinfo=timezone.utc)), 100)

    def test_next_texts_from_long_to_short(self):
        c = _campaign()
        user = type("U", (), {"tokens": 1000, "is_prohibit_giveaway": False, "is_invalid": False})()
        now = datetime(2026, 11, 7, 3, 0, tzinfo=timezone.utc)
        counts = {"total": 37, "today": 3, "limit_total": 37, "limit_today": 3, "shared": False}
        box = gs.build_entry_box(c, "live", "ja", now, user=user, counts=counts, stats={"users": 1, "entries": 37})
        self.assertEqual(box["next_texts"], ["あと13口で参加賞ボーナス +20トークン", "あと13口でボーナス +20トークン", "あと13口でボーナス +20"])

    def test_reachable_without_daily_limit(self):
        c = _campaign(settings={"daily_limit": 0, "total_limit": 20})
        self.assertEqual(gs.reachable_total(c, 3, 3, datetime(2026, 11, 19, 23, 0, tzinfo=timezone.utc)), 20)

    def test_entry_box_marks_unreachable_steps(self):
        c = _campaign()
        user = type("U", (), {"tokens": 1000, "is_prohibit_giveaway": False, "is_invalid": False})()
        now = datetime(2026, 11, 17, 3, 0, tzinfo=timezone.utc)
        counts = {"total": 61, "today": 0, "limit_total": 61, "limit_today": 0, "shared": False}
        box = gs.build_entry_box(c, "live", "ja", now, user=user, counts=counts, stats={"users": 1, "entries": 61})
        self.assertEqual(box["reach"], 85)
        self.assertEqual([m["state"] for m in box["marks"]], ["done", "lost"])
        self.assertEqual(box["next_texts"], [])  # 100口には届かないので出さない
        self.assertEqual(box["marks"][0]["label_long"], "50口 ボーナス<b>+20トークン</b>")
        # 100口は前の段（+20）からの増える分
        self.assertEqual([m["label"] for m in box["marks"]], ["50口 <b>+20</b>", "100口 <b>+30</b>"])
        self.assertEqual(box["participation"], 81)
        self.assertEqual(box["bulk"], 8)

    def test_counting_box_keeps_entries_without_next_or_lost(self):
        c = _campaign()
        user = type("U", (), {"tokens": 0, "is_prohibit_giveaway": False, "is_invalid": False})()
        now = datetime(2026, 11, 21, 3, 0, tzinfo=timezone.utc)
        counts = {"total": 61, "today": 0, "limit_total": 61, "limit_today": 0, "shared": False}
        box = gs.build_entry_box(c, "counting", "ja", now, user=user, counts=counts, stats={"users": 10, "entries": 300})
        self.assertEqual(box["badge"], "集計中")
        self.assertEqual(box["left"], "")
        self.assertIsNone(box["lost_pct"])
        self.assertIsNone(box["reach"])
        self.assertEqual(box["next_texts"], [])
        self.assertEqual([m["state"] for m in box["marks"]], ["done", ""])
        self.assertEqual(box["participation"], 81)


class RoundTest(unittest.TestCase):
    def test_with_round(self):
        self.assertEqual(gs._with_round("第96回プレゼント企画【トークンチャンス！】", 98, "ja"), "第98回プレゼント企画【トークンチャンス！】")
        self.assertEqual(gs._with_round("Giveaway #96", 98, "en"), "Giveaway #98")
        self.assertEqual(gs._with_round("プレゼント企画", 98, "ja"), "第98回プレゼント企画")

    def test_next_jst_9(self):
        self.assertEqual(gs._next_jst_9(datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)), datetime(2026, 11, 8, tzinfo=timezone.utc))
        self.assertEqual(gs._next_jst_9(datetime(2026, 11, 8, 0, 0, tzinfo=timezone.utc)), datetime(2026, 11, 8, tzinfo=timezone.utc))


class ListTest(unittest.TestCase):
    def test_applicants_list_text_repeats_ids_and_skips_invalid(self):
        rows = [
            {"user_id": 5, "entries": 2, "invalid_reason": None},
            {"user_id": 3, "entries": 1, "invalid_reason": None},
            {"user_id": 9, "entries": 4, "invalid_reason": "staff"},
        ]
        self.assertEqual(gs.applicants_list_text(rows), "[3, 5, 5]")

    def test_distribution_buckets(self):
        dist = gs.distribution([1, 3, 50, 100, 100], 100)
        self.assertEqual([d["label"] for d in dist], ["1口", "2〜4口", "5〜9口", "10〜19口", "20〜49口", "50〜99口", "100口"])
        self.assertEqual([d["users"] for d in dist], [1, 1, 0, 0, 0, 1, 2])
        self.assertEqual([d["label"] for d in gs.distribution([1], 3)], ["1口", "2口", "3口"])

    def test_parse_ids(self):
        self.assertEqual(gp.parse_ids("12, 34\n56 [78]"), [12, 34, 56, 78])
        with self.assertRaises(gs.GiveawayError):
            gp.parse_ids("12, abc")


class TextTest(unittest.TestCase):
    def test_terms_have_no_x_and_mention_email_only_with_gifts(self):
        c = _campaign()
        ja = gs.terms_html(c, "ja")
        self.assertNotIn("X Corp", ja)
        self.assertIn(gs.CONTACT_EMAIL, ja)
        no_gift = _campaign(settings={"tiers": [{"kind": "app", "tokens": 100, "count": 3}]})
        self.assertNotIn(gs.CONTACT_EMAIL, gs.terms_html(no_gift, "ja"))

    def test_end_text_for_title(self):
        c = _campaign(ends_at=datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(gp._end_text(c, "ja"), "11/20(金)9時")
        self.assertEqual(gp._end_text(c, "en"), "Nov 19")
        odd = _campaign(ends_at=datetime(2026, 11, 7, 12, 30, tzinfo=timezone.utc))
        self.assertEqual(gp._end_text(odd, "ja"), "11/7(土)21:30")

    def test_fmt_left(self):
        self.assertEqual(gs.fmt_left(timedelta(days=9, hours=15, minutes=5), "ja"), "あと9日15時間")
        self.assertEqual(gs.fmt_left(timedelta(hours=3, minutes=20), "ja"), "あと3時間20分")


if __name__ == "__main__":
    unittest.main()
