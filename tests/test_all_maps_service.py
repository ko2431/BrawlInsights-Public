from app.services.all_maps_service import assemble_all_maps_data, build_rotation_hint
from app.services.map_mode_catalog import MapInfo, ModeInfo, get_mode_theme


def _mode(mode_id: int, ja: str | None, en: str | None, *, disabled: bool = False) -> ModeInfo:
    return ModeInfo(id=mode_id, ja=ja, en=en, disabled=disabled)


def _map(map_id: int, mode_id: int, name: str | None, *, disabled: bool = False) -> MapInfo:
    return MapInfo(id=map_id, ja=name, en=name, mode_id=mode_id, disabled=disabled)


MODES = [
    _mode(48000099, "テストモード", "Test Mode"),
    _mode(48000005, "ブロストライカー", "Brawl Ball"),
    _mode(48000000, "エメラルドハント", "Gem Grab"),
    _mode(48000015, "孤高のスター", "Lone Star", disabled=True),
    _mode(48000019, "ERROR : ゲーム内にデータが存在しませんでした。", "Error : Data Was Not Found In The Game."),
    _mode(48000012, None, None),
    _mode(48000088, "マップ無しモード", "No Map Mode"),
]
MAPS = [
    _map(15000003, 48000005, "スーパービーチ"),
    _map(15000001, 48000000, "ごつごつ坑道"),
    _map(15000002, 48000000, "旧マップ", disabled=True),
    _map(15000004, 48000015, "孤高のマップ"),
    _map(15000005, 48000099, "テストマップ"),
    _map(15000006, 48000000, "ERROR : Data was not found in the game."),
    _map(15000007, 48000019, "MAP 1"),
    _map(15000008, 48000012, "Tutorial"),
]


def test_excludes_error_and_nameless_entries():
    data = assemble_all_maps_data(MODES, MAPS)
    map_ids = [item["id"] for item in data["maps"]]
    assert map_ids == [15000001, 15000002, 15000003, 15000004, 15000005]
    mode_ids = {item["id"] for item in data["modes"]}
    assert 48000019 not in mode_ids
    assert 48000012 not in mode_ids
    # マップが1件も無いモードは選択肢に出さない
    assert 48000088 not in mode_ids


def test_off_when_map_or_mode_disabled():
    data = assemble_all_maps_data(MODES, MAPS)
    off = {item["id"]: item["off"] for item in data["maps"]}
    assert off[15000001] is False
    assert off[15000002] is True  # マップ無効
    assert off[15000004] is True  # モード無効


def test_mode_order_priority_then_enabled_then_disabled():
    data = assemble_all_maps_data(MODES, MAPS)
    assert [item["id"] for item in data["modes"]] == [48000000, 48000005, 48000099, 48000015]
    assert data["modes"][-1]["off"] is True


def test_mode_theme_mapping():
    assert get_mode_theme(48000000) == "gemGrab"
    assert get_mode_theme(48000033) == "gemGrab"  # 5対5エメラルドハント
    assert get_mode_theme(48000025) == "heist"  # 殲滅
    assert get_mode_theme(48000069) == "showdown"  # デュオ メガボス
    assert get_mode_theme(48000061) == "hotZone"  # メガボス
    assert get_mode_theme(48000084) == "showdown"  # メガボス Duo（20プレイヤー）
    assert get_mode_theme(48000076) == "emerald"  # スーパーボール
    assert get_mode_theme(48000047) is None  # 未定義は既定色
    assert get_mode_theme(None) is None


def test_build_rotation_hint_keeps_unknown_positions():
    payload = {
        "slots": [
            {
                "durationMinutes": 1440,
                "latestStart": "2026-09-27T08:00:00+00:00",
                "anchorStart": "2026-09-20T08:00:00+00:00",
                "latestIndex": 2,
                "currentMapId": 15000003,
                "maps": [{"map_id": 15000001, "state": "verified"}, {"map_id": None, "state": "unknown"}, {"map_id": 15000003, "state": "verified"}],
            },
            {"durationMinutes": 120, "maps": []},
            {"durationMinutes": 120, "anchorStart": "2026-09-27T08:00:00+00:00", "latestIndex": None, "maps": [{"map_id": 15000005}]},
        ]
    }
    hints = build_rotation_hint(payload)
    assert [hint["order"] for hint in hints] == [0, 2]
    assert hints[0]["maps"] == [15000001, None, 15000003]
    assert hints[0]["latestIndex"] == 2
    assert hints[1]["latestIndex"] == 0
    assert build_rotation_hint(None) == []


def test_excludes_codename_maps():
    maps = [
        _map(15000442, 48000000, "HEIST_AUGCOMP2", disabled=True),
        _map(15000401, 48000000, "JUNEMAP7_PLACEHOLDER", disabled=True),
        MapInfo(id=15001089, ja="アトラス", en="ATLAS", mode_id=48000000),
    ]
    data = assemble_all_maps_data(MODES, maps)
    assert [item["id"] for item in data["maps"]] == [15001089]


def test_dedupes_adjacent_same_name_maps_in_same_mode():
    maps = [
        _map(15001115, 48000000, "鬼の目にもワサビ", disabled=True),
        _map(15001116, 48000000, "鬼の目にもワサビ"),
        # モードが違えば別マップ
        _map(15001117, 48000005, "鬼の目にもワサビ"),
        # IDが離れていれば内容が違うことがあるので残す
        _map(15001200, 48000000, "鬼の目にもワサビ"),
    ]
    data = assemble_all_maps_data(MODES, maps)
    items = {item["id"]: item for item in data["maps"]}
    assert sorted(items) == [15001116, 15001117, 15001200]
    # 有効なほうを残し、まとめたIDは alt に入れる
    assert items[15001116]["alt"] == [15001115]
    assert "alt" not in items[15001200]


def test_links_team_showdown_maps_to_preceding_solo_map():
    modes = [
        _mode(48000006, "ソロ バトルロイヤル", "Solo Showdown"),
        _mode(48000009, "デュオ バトルロイヤル", "Duo Showdown"),
        _mode(48000038, "トリオ バトルロイヤル", "Trio Showdown"),
    ]
    maps = [
        MapInfo(id=15000032, ja="酸性湖", en="Acid Lakes", mode_id=48000006, disabled=True),
        MapInfo(id=15000956, ja="酸性湖", en="Acid Lakes", mode_id=48000006),
        MapInfo(id=15000957, ja="酸性湖", en="Acid Lakes", mode_id=48000009),
        MapInfo(id=15000958, ja="酸性湖", en="Acid Lakes", mode_id=48000038),
        MapInfo(id=15001121, ja="ゲーテッド", en="Gated Community", mode_id=48000006),
        MapInfo(id=15001140, ja="ゲーテッド", en="Gated Community", mode_id=48000009),
        # 同名のソロマップより前のIDなら紐付けない
        MapInfo(id=15000028, ja="ダブルトラブル", en="Double Trouble", mode_id=48000009, disabled=True),
        MapInfo(id=15000043, ja="ダブルトラブル", en="Double Trouble", mode_id=48000006, disabled=True),
    ]
    items = {item["id"]: item for item in assemble_all_maps_data(modes, maps)["maps"]}
    assert items[15000957]["link"] == 15000956
    assert items[15000958]["link"] == 15000956
    assert items[15001140]["link"] == 15001121
    assert "link" not in items[15000028]
    assert "link" not in items[15000956]
