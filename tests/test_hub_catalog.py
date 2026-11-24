"""統計データ・ツールタブ（ハブ）の並びの扱い（services/hub_catalog.py）。"""
import datetime

from app.services import hub_catalog as hc


def test_merge_order_keeps_saved_and_inserts_new_after_default_neighbor():
    # 増えた c は、デフォルトで直前にある b のすぐ後ろへ。もう無い x は読み飛ばす
    assert hc.merge_order(["b", "x", "a"], ["a", "b", "c"]) == ["b", "c", "a"]


def test_merge_order_new_item_without_neighbor_goes_first():
    assert hc.merge_order(["b"], ["a", "b"]) == ["a", "b"]


def test_merge_order_empty_returns_defaults():
    assert hc.merge_order(None, ["a", "b"]) == ["a", "b"]
    assert hc.merge_order([], ["a", "b"]) == ["a", "b"]


def test_merge_order_drops_duplicates():
    assert hc.merge_order(["b", "b", "a"], ["a", "b"]) == ["b", "a"]


def test_clean_orders_drops_unknown_and_default_orders():
    stats_all = hc.default_order("stats", "all")
    custom = [stats_all[1], stats_all[0]] + stats_all[2:]
    raw = {
        "stats": {"all": custom, "ranked": hc.default_order("stats", "ranked"), "nope": ["a"]},
        "unknown": {"all": ["a"]},
        "tools": "broken",
    }
    assert hc.clean_orders(raw) == {"stats": {"all": custom}}


def test_clean_orders_keeps_saved_hub_even_if_empty():
    # 「デフォルトに戻す」で空になっても、保存したことは残す（端末の写しを引き継いで元に戻さないため）
    assert hc.clean_orders({"tools": {"ranked": hc.default_order("tools", "ranked")}}) == {"tools": {}}
    assert hc.clean_orders({"tools": {}}) == {"tools": {}}


def test_clean_orders_limits_ids():
    ids = ["x" * (hc.ORDER_MAX_ID_LEN + 1)] + hc.default_order("tools", "ranked")[::-1]
    assert hc.clean_orders({"tools": {"ranked": ids}}) == {"tools": {"ranked": hc.default_order("tools", "ranked")[::-1]}}


def test_every_subtab_item_is_defined():
    for hub in hc.HUBS.values():
        for sub in hub["subtabs"]:
            for item_id in (*sub["order"], *sub["suggest"], *sub["external"]):
                assert item_id in hc.ITEMS, item_id


def test_badge_until_is_inclusive_utc_date():
    now = datetime.datetime(2026, 12, 5, 23, 59, tzinfo=datetime.timezone.utc)
    assert hc._active(datetime.date(2026, 12, 5), now)
    assert not hc._active(datetime.date(2026, 12, 4), now)
    assert hc._active(None, now)


def test_cluster_icons_use_visible_area():
    html = hc._icon_html((("ui/starrdrop.png", 50, 50, 44, 1),), "/static/images/", 1.0)
    # 不透明な部分が 44% の正方形になる幅（starrdrop は 0.44 なので 44 / sqrt(0.44) ≒ 66.3）
    assert "width:66.3%" in html
    assert "left:16.8%" in html
