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


def test_loop_stats_split_new_battle_counts():
    stats = tasks._UpdateLoopStats()
    stats.add("auto", 0.5, fetched=25, new=0)       # 新しいバトルなし（空振り）
    stats.add("auto", 0.7, fetched=25, new=12)      # 一部が新しい
    stats.add("auto", 1.5, fetched=25, new=25)      # すべて新しい（取りこぼしの可能性）
    stats.add("auto", 0.1)                           # キャッシュを使った
    stats.add("active", 2.0, error=True)
    stats.add("active", 0.4, fetched=3, new=3)       # 届いた数が25未満なら、すべて新しくても取りこぼしではない
    auto, active = stats.lines()
    assert auto.startswith("自動追跡 4人: 処理 平均0.70秒・最大1.5秒")
    assert "0件 1人・1件以上 1人・25件すべて新しい(取りこぼしの可能性) 1人（計37件）" in auto and "取得なし 1人・エラー 0人" in auto
    assert "1件以上 1人・25件すべて新しい(取りこぼしの可能性) 0人（計3件）" in active and "エラー 1人" in active


def test_save_battles_records_how_many_were_new(monkeypatch):
    import asyncio
    import datetime
    from types import SimpleNamespace
    from app.services import brawl_service

    saved = {"t3"}  # 新しい順に t1, t2, t3, t4。t3 から先は保存済み

    async def fake_insert(db, tag, battle):
        return battle.key in saved

    async def fake_fill(db, tag, battles):
        return None

    monkeypatch.setattr(brawl_service, "insert_api_battle", fake_insert)
    monkeypatch.setattr(brawl_service, "fill_missing_result_data", fake_fill)
    now = datetime.datetime.now(datetime.timezone.utc)
    battles = [SimpleNamespace(key=f"t{i}", battle_type="ranked", datetime=now) for i in range(1, 5)]
    player = brawl_service.Player("#TEST", None)
    player.level = 30  # アクティブ判定の更新（DB への書き込み）を起こさない
    assert player.fetched_battle_count is None and player.new_battle_count is None
    asyncio.run(player._save_battles(battles))
    assert (player.fetched_battle_count, player.new_battle_count) == (4, 2)
    saved.clear()
    asyncio.run(player._save_battles(battles))
    assert (player.fetched_battle_count, player.new_battle_count) == (4, 4)
