#!/usr/bin/env python3
"""maps/modes を BSInfo でシードし、battles / archived_battles の mode_id を日付バッチで埋める。

Alembic 適用後に VPS で実行する。upgrade 自体はこのスクリプトでは行わない。
旧管理画面の日本語名は転写せず、BSInfo の名前を正とする。

使い方:
  python3 scripts/backfill_map_mode_ids.py
  python3 scripts/backfill_map_mode_ids.py --skip-seed
  python3 scripts/backfill_map_mode_ids.py --report-only
  python3 scripts/backfill_map_mode_ids.py --batch-days 7 --sleep 0.5
  python3 scripts/backfill_map_mode_ids.py --skip-seed --batch-days 1 --start YYYY-MM-DD --command-timeout 900
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncpg

from app.core.cache import close_redis, connect_redis
from app.core.config import settings
from app.core.logger import logger
from app.db.db import setup_jsonb_codec
from app.services.bsinfoapi import _api_client as bsinfo_client
from app.services.map_mode_catalog import (
    collect_unresolved_report,
    fill_slugs_from_legacy_modes,
    sync_maps_and_modes_from_bsinfo,
)

ALLOWED_TABLES = frozenset({"battles", "archived_battles"})


def _progress(message: str) -> None:
    """tmux でもすぐ見えるよう stdout に出す。アプリの logger はスクリプト単体では出ないことが多い。"""
    print(message, flush=True)
    logger.info(message)


def _parse_update_count(result: str) -> int:
    try:
        return int(str(result).split()[-1])
    except (IndexError, ValueError):
        return 0


async def _connect(*, command_timeout: float) -> asyncpg.Connection:
    conn = await asyncpg.connect(
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        database=settings.DB_NAME,
        timeout=60.0,
        command_timeout=command_timeout,
    )
    await setup_jsonb_codec(conn)
    return conn


async def _table_bounds(db: asyncpg.Connection, table: str) -> tuple[datetime | None, datetime | None]:
    if table not in ALLOWED_TABLES:
        raise ValueError(table)
    _progress(f"{table}: MIN/MAX(datetime) を取得しています（大きいテーブルでは数分かかることがあります）")
    started = time.monotonic()
    row = await db.fetchrow(f"SELECT MIN(datetime) AS mn, MAX(datetime) AS mx FROM {table}")
    _progress(f"{table}: 範囲取得完了 {row['mn']} 〜 {row['mx']} ({time.monotonic() - started:.1f}s)")
    return row["mn"], row["mx"]


async def _backfill_table(
    db: asyncpg.Connection,
    table: str,
    *,
    batch_days: int,
    sleep_s: float,
    start_at: datetime | None = None,
) -> dict[str, int]:
    if table not in ALLOWED_TABLES:
        raise ValueError(table)
    mn, mx = await _table_bounds(db, table)
    totals = {"map_path": 0, "event_mode": 0, "battle_mode": 0}
    if mn is None or mx is None:
        _progress(f"{table}: 行がないためスキップします")
        return totals

    start = mn.astimezone(timezone.utc) if mn.tzinfo else mn.replace(tzinfo=timezone.utc)
    end = mx.astimezone(timezone.utc) if mx.tzinfo else mx.replace(tzinfo=timezone.utc)
    if start_at is not None:
        start_at_utc = start_at.astimezone(timezone.utc) if start_at.tzinfo else start_at.replace(tzinfo=timezone.utc)
        if start_at_utc > start:
            start = start_at_utc
            _progress(f"{table}: --start により {start.date()} から再開します")
    if start > end:
        _progress(f"{table}: 開始日が最終日時より後のためスキップします")
        return totals
    window = timedelta(days=batch_days)
    cursor = start
    batch_index = 0
    total_batches = int((end - start) / window) + 1
    _progress(
        f"{table}: {start.isoformat()} 〜 {end.isoformat()} を {batch_days}日窓 "
        f"（約 {total_batches} バッチ、command_timeout 内に終わるまで1バッチ待ち）で更新します"
    )

    while cursor <= end:
        nxt = cursor + window
        batch_index += 1
        _progress(f"{table} [{batch_index}/{total_batches}] 開始 {cursor.date()}〜{nxt.date()}")
        batch_started = time.monotonic()
        map_updated = _parse_update_count(
            await db.execute(
                f"""
                UPDATE {table} AS b
                SET mode_id = maps.mode_id
                FROM maps
                WHERE b.event_id = maps.id
                  AND b.mode_id IS NULL
                  AND maps.mode_id IS NOT NULL
                  AND b.datetime >= $1 AND b.datetime < $2
                """,
                cursor,
                nxt,
            )
        )
        event_mode_updated = _parse_update_count(
            await db.execute(
                f"""
                UPDATE {table} AS b
                SET mode_id = modes.id
                FROM modes
                WHERE b.mode_id IS NULL
                  AND modes.slug IS NOT NULL
                  AND b.event_mode = modes.slug
                  AND b.datetime >= $1 AND b.datetime < $2
                """,
                cursor,
                nxt,
            )
        )
        battle_mode_updated = _parse_update_count(
            await db.execute(
                f"""
                UPDATE {table} AS b
                SET mode_id = modes.id
                FROM modes
                WHERE b.mode_id IS NULL
                  AND modes.slug IS NOT NULL
                  AND b.battle_mode = modes.slug
                  AND b.datetime >= $1 AND b.datetime < $2
                """,
                cursor,
                nxt,
            )
        )
        totals["map_path"] += map_updated
        totals["event_mode"] += event_mode_updated
        totals["battle_mode"] += battle_mode_updated
        elapsed = time.monotonic() - batch_started
        _progress(
            f"{table} [{batch_index}/{total_batches}] 完了 {cursor.date()}〜{nxt.date()}: "
            f"map={map_updated} event_mode={event_mode_updated} battle_mode={battle_mode_updated} "
            f"({elapsed:.1f}s) 累計 map={totals['map_path']} event_mode={totals['event_mode']} "
            f"battle_mode={totals['battle_mode']}"
        )
        cursor = nxt
        if sleep_s > 0:
            await asyncio.sleep(sleep_s)

    _progress(f"{table} 完了: {totals}")
    return totals


def _print_report(report: dict) -> None:
    print("=== 未解決レポート ===")
    print(f"slug未設定 modes: {len(report.get('modes_without_slug') or [])}")
    for row in report.get("modes_without_slug") or []:
        print(f"  - {row.get('id')} / {row.get('en')} / {row.get('ja')}")
    print(f"名前なし modes: {report.get('nameless_mode_ids')}")
    print(f"名前なし maps: {report.get('maps_without_name_ids')}")
    print(f"legacy未突合 maps: {len(report.get('legacy_maps_unmatched') or [])}")
    unmatched_maps = report.get("legacy_maps_unmatched") or []
    if unmatched_maps:
        preview = unmatched_maps[:30]
        print(f"  例: {preview}{' ...' if len(unmatched_maps) > 30 else ''}")
    print(f"legacy未突合 slugs: {len(report.get('legacy_modes_unmatched') or [])}")
    unmatched_modes = report.get("legacy_modes_unmatched") or []
    if unmatched_modes:
        print(f"  {unmatched_modes}")
    if report.get("missing_map_ids") is not None:
        print(f"battles の maps未登録 event_id 種類数: {report['missing_map_ids']}")
        print(f"battles.mode_id NULL: {report['null_mode_id_battles']}")
        print(f"archived_battles.mode_id NULL: {report['null_mode_id_archived']}")


async def _run(args: argparse.Namespace) -> int:
    _progress(
        f"開始 skip_seed={args.skip_seed} skip_archived={args.skip_archived} "
        f"report_only={args.report_only} batch_days={args.batch_days} sleep={args.sleep} "
        f"start={args.start.date() if args.start else '(最古から)'} "
        f"command_timeout={args.command_timeout}s"
    )
    _progress("Redis に接続しています")
    await connect_redis()
    _progress("PostgreSQL に接続しています")
    db = await _connect(command_timeout=args.command_timeout)
    _progress("DB 接続完了")
    try:
        if not args.report_only and not args.skip_seed:
            _progress("BSInfo から maps/modes を同期します（既存の手動日本語名は上書きします）")
            sync_result = await sync_maps_and_modes_from_bsinfo(db, force=True)
            _progress(f"同期結果: {sync_result}")
            slug_filled = await fill_slugs_from_legacy_modes(db)
            _progress(f"legacy slug 補完: {slug_filled} 件")
        elif args.skip_seed and not args.report_only:
            _progress("BSInfo 同期はスキップします (--skip-seed)")

        if not args.report_only:
            await _backfill_table(
                db, "battles",
                batch_days=args.batch_days, sleep_s=args.sleep, start_at=args.start,
            )
            if not args.skip_archived:
                await _backfill_table(
                    db, "archived_battles",
                    batch_days=args.batch_days, sleep_s=args.sleep, start_at=args.start,
                )
            else:
                _progress("archived_battles はスキップします (--skip-archived)")

        _progress("未解決レポートを集計しています（battles 全表の COUNT は時間がかかることがあります）")
        report = await collect_unresolved_report(db, include_battle_scans=True)
        _print_report(report)
        return 0
    finally:
        await db.close()
        await close_redis()
        await bsinfo_client.aclose()
        _progress("接続を閉じました")


def main() -> None:
    parser = argparse.ArgumentParser(description="maps/modes シードと battles.mode_id バックフィル")
    parser.add_argument("--skip-seed", action="store_true", help="BSInfo同期とlegacy slug補完をスキップ")
    parser.add_argument("--report-only", action="store_true", help="更新せず未解決レポートのみ")
    parser.add_argument("--skip-archived", action="store_true", help="archived_battles を対象外にする")
    parser.add_argument("--batch-days", type=int, default=7, help="日付バッチ幅（日）。既定 7")
    parser.add_argument("--sleep", type=float, default=0.5, help="バッチ間スリープ秒。既定 0.5")
    parser.add_argument(
        "--start",
        type=lambda value: datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc),
        default=None,
        metavar="YYYY-MM-DD",
        help="この日付（UTC 0:00）から再開する。省略時はテーブルの最古日時",
    )
    parser.add_argument(
        "--command-timeout",
        type=float,
        default=300.0,
        help="1ステートメントの秒タイムアウト。既定 300。本番の大きい窓では 900 など",
    )
    args = parser.parse_args()
    if args.batch_days < 1:
        parser.error("--batch-days は 1 以上")
    if args.sleep < 0:
        parser.error("--sleep は 0 以上")
    if args.command_timeout < 30:
        parser.error("--command-timeout は 30 以上")
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
