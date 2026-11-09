"""ミニゲーム型企画の管理（minigame_campaign_service・minigame_assets）の計算のテスト。"""
import unittest
from datetime import datetime, timedelta, timezone

from app.services import minigame_assets as ma
from app.services import minigame_campaign_service as mcs
from app.services import minigame_service as ms


def _campaign(**kwargs) -> mcs.Campaign:
    base = {
        "id": 1, "round_no": 100, "name_ja": "第100回プレゼント企画", "name_en": "Giveaway #100", "game_type": "card_flip_multi1",
        "starts_at": datetime(2026, 11, 10, 0, 0, tzinfo=timezone.utc), "ends_at": datetime(2026, 11, 17, 0, 0, tzinfo=timezone.utc),
        "prizes": mcs.default_prizes(), "price_ad_tokens": 10, "price_token_tokens": 25, "ad_daily_limit": 5,
        "expected_total_plays": 1000, "is_invalid": False, "is_published": True, "terms_extra_ja": None, "terms_extra_en": None,
        "settings": mcs.merged_settings(mcs.new_settings()),
    }
    base.update(kwargs)
    return mcs.Campaign(**base)


class BonusTest(unittest.TestCase):
    def test_milestones_repeat_after_last_step(self):
        s = mcs.new_settings()
        self.assertEqual(mcs.bonus_milestones(s, 20), [(5, 5), (10, 20), (20, 10)])
        self.assertEqual(mcs.bonus_milestones(s, 125)[-3:], [(100, 30), (110, 10), (120, 10)])

    def test_totals(self):
        s = mcs.new_settings()
        self.assertEqual([mcs.bonus_total(s, n) for n in (4, 10, 30, 50, 100, 120)], [0, 25, 55, 85, 155, 175])
        self.assertEqual(mcs.bonus_total(mcs.merged_settings({}), 100), 0)  # 古い企画はボーナスなし

    def test_bonus_text(self):
        text = mcs.bonus_text(mcs.new_settings(), "ja")
        self.assertTrue(text.startswith("5回で+5、10回で+20"))
        self.assertTrue(text.endswith("以後10回ごとに+10"))
        self.assertEqual(mcs.bonus_text(mcs.merged_settings({}), "ja"), "")


class RateTest(unittest.TestCase):
    def test_probabilities_stock_and_weight(self):
        probs = mcs.tier_probabilities(mcs.default_prizes(), 1000)
        self.assertAlmostEqual(probs[1], 0.003)
        self.assertAlmostEqual(probs[2] + probs[3], 0.997)
        self.assertAlmostEqual(probs[2] / probs[3], 10 / 90)

    def test_stock_capped_at_95_percent(self):
        prizes = {"tiers": [
            {"rank": 1, "allocation": "stock", "quantity": 2000, "items": [{"type": "token", "amount": 1}]},
            {"rank": 2, "allocation": "weight", "quantity": 1, "items": [{"type": "none"}]},
        ]}
        probs = mcs.tier_probabilities(prizes, 1000)
        self.assertAlmostEqual(probs[1], 0.95)
        self.assertAlmostEqual(probs[2], 0.05)

    def test_return_rates_with_values_and_bonus(self):
        prizes = {"tiers": [
            {"rank": 1, "allocation": "stock", "quantity": 1, "items": [{"type": "gift", "name_ja": "ギフト", "name_en": "Gift"}], "value_tokens": 5000},
            {"rank": 2, "allocation": "weight", "quantity": 1, "items": [{"type": "ad_skip_ticket", "amount": 1}]},
            {"rank": 3, "allocation": "weight", "quantity": 1, "items": [{"type": "auto_track_elixir", "amount": 30}]},
        ]}
        r = mcs.return_rates(prizes, 25, 10, 1000, mcs.merged_settings({}))
        # アプリ内: (1-0.001) × (10 + 10) / 2 ＝ 9.99、全体はギフト 5000 × 0.001 を足す
        self.assertAlmostEqual(r["ev_app"], 9.99)
        self.assertAlmostEqual(r["ev_all"], 9.99 + 5)
        self.assertAlmostEqual(r["table"][0]["token_app"], 9.99 / 25)
        self.assertEqual(r["unvalued"], [])
        r2 = mcs.return_rates(prizes, 25, 10, 1000, mcs.new_settings())
        ten = next(row for row in r2["table"] if row["plays"] == 10)
        self.assertAlmostEqual(ten["token_app"], (9.99 + 2.5) / 25)  # 10回で25トークンのボーナス

    def test_unvalued_tiers_are_reported(self):
        prizes = {"tiers": [
            {"rank": 1, "allocation": "stock", "quantity": 1, "items": [{"type": "battle_log_retention", "months": 1}]},
            {"rank": 2, "allocation": "weight", "quantity": 1, "items": [{"type": "token", "amount": 5}]},
        ]}
        self.assertEqual(mcs.return_rates(prizes, 25, 10, 100, {})["unvalued"], [1])


class AssetTest(unittest.TestCase):
    def test_standard_assets_pass(self):
        for game, n in ma.CARD_ASSETS:
            self.assertEqual(ma.validate_assets(game, n, ma.standard_assets(game, n)), [], (game, n))
            self.assertEqual(ms.verify_assets(game, n, None, tries=10), [], (game, n))

    def test_minimum_counts(self):
        self.assertEqual(ma.asset_spec("card_flip_multi1", 5)["decoys"], (1, 6))
        self.assertEqual(ma.asset_spec("card_flip_multi2", 3), {"ranks": [], "decoys": None, "pool": (3, 8)})
        self.assertEqual(ma.asset_spec("scratch1", 8)["decoys"][0], 1)
        self.assertEqual(ma.asset_spec("scratch1", 6)["decoys"][0], 0)
        self.assertEqual(ma.asset_spec("bingo1", 5)["pool"][0], 3)

    def test_too_few_and_duplicates_are_stopped(self):
        a = ma.standard_assets("card_flip_multi1", 3)
        a["decoys"] = []
        self.assertTrue(any("はずれ" in e for e in ma.validate_assets("card_flip_multi1", 3, a)))
        b = ma.standard_assets("card_flip_single", 2)
        b["ranks"]["2"] = b["ranks"]["1"]
        self.assertTrue(any("同じ絵柄" in e for e in ma.validate_assets("card_flip_single", 2, b)))
        c = ma.standard_assets("bingo1", 4)
        c["pool"][0] = "spray/1.webp"
        self.assertTrue(any("見つかりません" in e for e in ma.validate_assets("bingo1", 4, c)))

    def test_verify_catches_combination_without_decoy(self):
        a = ma.standard_assets("card_flip_multi2", 8)
        a["decoys"] = []
        self.assertTrue(ms.verify_assets("card_flip_multi2", 8, a, tries=40))

    def test_normalize_keeps_only_used_slots(self):
        raw = {"ranks": {"1": "spray/68000004.webp", "9": "x"}, "decoys": ["spray/68000098.webp"], "pool": ["spray/68000095.webp"]}
        self.assertEqual(ma.normalize_assets("card_flip_single", 2, raw), {"ranks": {"1": "spray/68000004.webp", "2": ""}, "decoys": [], "pool": []})


class CampaignTest(unittest.TestCase):
    def test_state(self):
        now = datetime(2026, 11, 12, tzinfo=timezone.utc)
        self.assertEqual(_campaign().state(now), "live")
        self.assertEqual(_campaign(is_published=False).state(now), "draft")
        self.assertEqual(_campaign(is_invalid=True).state(now), "canceled")
        self.assertEqual(_campaign().state(now - timedelta(days=5)), "scheduled")
        self.assertEqual(_campaign().state(now + timedelta(days=10)), "ended")

    def test_terms_and_start_content(self):
        c = _campaign()
        terms = mcs.terms_html(c, "ja")
        self.assertIn("参加ボーナス: 5回で+5", terms)
        self.assertIn("1回25トークンで参加できます", terms)
        self.assertIn("72時間", terms)
        self.assertNotIn("ミニゲーム", terms)
        content = mcs.start_content_html(c, "ja")
        self.assertIn("■ 参加ボーナス", content)
        self.assertNotIn("ミニゲーム", content)
        self.assertIn("Each play costs 25 tokens", mcs.start_content_html(c, "en"))

    def test_howto_limits(self):
        c = _campaign(settings=mcs.merged_settings({**mcs.new_settings(), "daily_limit": 20, "total_limit": 100}))
        self.assertIn("参加は1日20回まで・期間中100回までです。", mcs.howto_text(c, "ja"))


if __name__ == "__main__":
    unittest.main()


class BoxTest(unittest.TestCase):
    """アカウントタブの箱（minigame_box_service）。"""

    def setUp(self):
        from app.services import minigame_box_service as mbs
        self.mbs = mbs
        self.s = mcs.merged_settings(mcs.new_settings())

    def test_gauge_starts_at_zero_and_keeps_claimed_in_one_cell(self):
        g = self.mbs.gauge(self.s, 37, {5, 10, 20, 30}, "ja")
        labels = [m["short"] for m in g["marks"]]
        self.assertEqual(labels, ["0回", "30回", "40回 <b>+10</b>", "50回 <b>+20</b>", "60回 <b>+10</b>"])
        self.assertEqual(g["next"], (40, 10))
        self.assertTrue(next(m for m in g["marks"] if m["priority"])["long"].startswith("40回 ボーナス"))
        self.assertGreater(g["fill"], g["marks"][1]["pct"])  # 30回のマスは塗り切っている

    def test_gauge_ready_milestones_stay_visible(self):
        g = self.mbs.gauge(self.s, 52, {5, 10, 20, 30}, "ja")
        self.assertEqual([m["cls"].strip() for m in g["marks"][2:4]], ["mga__mark--ready", "mga__mark--ready"])
        self.assertEqual(g["next"], (60, 10))

    def test_gauge_without_claims_has_three_cells(self):
        g = self.mbs.gauge(self.s, 3, set(), "ja")
        self.assertEqual([m["short"] for m in g["marks"]], ["0回", "5回 <b>+5</b>", "10回 <b>+20</b>", "20回 <b>+10</b>"])

    def test_gauge_with_cap_and_lost(self):
        settings = mcs.merged_settings({**mcs.new_settings(), "daily_limit": 10, "total_limit": 30})
        g = self.mbs.gauge(settings, 12, {5, 10}, "ja", cap=30, reach=25)
        self.assertEqual(g["marks"][-1]["short"], "30回 <b>+20</b>")
        self.assertIn("mga__mark--lost", g["marks"][-1]["cls"])
        self.assertIsNotNone(g["lost_pct"])
        maxed = self.mbs.gauge(settings, 30, {5, 10, 20, 30}, "ja", cap=30)
        self.assertTrue(maxed["maxed"])

    def test_no_gauge_without_bonus_or_cap(self):
        self.assertIsNone(self.mbs.gauge(mcs.merged_settings({}), 12, set(), "ja"))

    def test_box_mode(self):
        now = datetime(2026, 11, 12, tzinfo=timezone.utc)
        c = _campaign()
        self.assertEqual(self.mbs.box_mode(c, False, now), "live")
        early = _campaign(starts_at=now + timedelta(hours=10), ends_at=now + timedelta(days=7))
        self.assertEqual(self.mbs.box_mode(early, False, now), "upcoming")  # 予告は24時間前から
        self.assertEqual(self.mbs.box_mode(early, True, now), "preview")  # 管理者は先行プレイ
        draft = _campaign(starts_at=now + timedelta(hours=10), ends_at=now + timedelta(days=7), is_published=False)
        self.assertIsNone(self.mbs.box_mode(draft, False, now))
        self.assertEqual(self.mbs.box_mode(draft, True, now), "preview")
        far = _campaign(starts_at=now + timedelta(days=3), ends_at=now + timedelta(days=7))
        self.assertIsNone(self.mbs.box_mode(far, True, now))
        self.assertEqual(self.mbs.box_mode(_campaign(), False, now + timedelta(days=6)), "ended")

    def test_reachable_total(self):
        settings = mcs.merged_settings({**mcs.new_settings(), "daily_limit": 10, "total_limit": 100})
        c = _campaign(settings=settings)  # 11/10 0:00〜11/17 0:00（UTC）
        now = datetime(2026, 11, 15, 3, 0, tzinfo=timezone.utc)
        self.assertEqual(self.mbs.reachable_total(c, 40, 4, now), 40 + 6 + 10)  # 今日の残り6＋16日の10
        self.assertIsNone(self.mbs.reachable_total(_campaign(), 40, 4, now))
