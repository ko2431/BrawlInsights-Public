#!/usr/bin/env python3
"""保存済みのバトルの勝敗（result）を、今の判定（app/services/battle_log.py の _RANK_RULES）にそろえる。

対象は、判定を直した3つのモードだけ（notes_private/battle_stats_plan_2026-11-13.md 6章 K1〜K3・K8）。
- メガバトルロイヤル（20人）: 8位までが勝ち、9・10位が引き分け、11位からが負け（以前はソロ10人の判定が当たっていた）
- メガデュオ（2人×10チーム）: 4位までが勝ち、5・6位が引き分け、7位からが負け（以前はデュオ5チームの判定が当たっていた）
- トリオ: 2位までが勝ち、3・4位が負け（以前は3位が引き分けになっていた）
勝ちの順位でもトロフィーが減っていれば引き分け、負けの順位でも増えていれば引き分け、というのは今までどおり。

何も付けずに動かすと、変わる行の数を数えるだけで書き換えない。--apply を付けると書き換える。
datetime の索引で日付ごとに区切って進めるので、途中で止めても --start で続きからやり直せる（何度動かしても結果は同じ）。

使い方:
  python3 scripts/fix_showdown_results.py                         # battles を数えるだけ
  python3 scripts/fix_showdown_results.py --apply                 # battles を書き換える
  python3 scripts/fix_showdown_results.py --apply --include-archived
  python3 scripts/fix_showdown_results.py --apply --start 2026-11-01 --batch-days 1 --sleep 0.5
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

from app.core.config import settings

ALLOWED_TABLES = ("battles", "archived_battles")

# battle_log.parse_api_battle と同じ見分け方。ソロは players で来るバトル（battle_mode が duoShowdown ではない1人チーム）。
_IS_SOLO = "(team_size = 1 AND battle_mode IS DISTINCT FROM 'duoShowdown')"
_IS_MEGA_SOLO = f"({_IS_SOLO} AND (event_mode = 'megaShowdown' OR num_of_teams > 10))"
_IS_MEGA_DUO = f"(NOT {_IS_SOLO} AND (event_mode = 'megaDuoShowdown' OR (team_size = 2 AND num_of_teams > 5)))"
_IS_TRIO = f"(NOT {_IS_SOLO} AND NOT {_IS_MEGA_DUO} AND (event_mode = 'trioShowdown' OR (team_size = 3 AND num_of_teams = 4)))"

# 変わる行と、その新しい勝敗。{table} と日付の範囲（$1 以上 $2 未満）を入れて使う
_TARGETS_SQL = f"""
    SELECT tag, datetime, rule, result AS old_result,
           CASE
               WHEN rank <= win_until THEN CASE WHEN COALESCE(trophy_change, 0) >= 0 THEN 'w' ELSE 'd' END
               WHEN rank <= draw_until THEN 'd'
               ELSE CASE WHEN COALESCE(trophy_change, 0) <= 0 THEN 'l' ELSE 'd' END
           END AS new_result
    FROM (
        SELECT tag, datetime, rank, trophy_change, result,
               CASE WHEN {_IS_MEGA_SOLO} THEN 'mega_solo' WHEN {_IS_MEGA_DUO} THEN 'mega_duo' ELSE 'trio' END AS rule,
               CASE WHEN {_IS_MEGA_SOLO} THEN 8 WHEN {_IS_MEGA_DUO} THEN 4 ELSE 2 END AS win_until,
               CASE WHEN {_IS_MEGA_SOLO} THEN 10 WHEN {_IS_MEGA_DUO} THEN 6 ELSE 2 END AS draw_until
        FROM {{table}}
        WHERE datetime >= $1 AND datetime < $2
          AND rank IS NOT NULL AND rank > 0
          AND ({_IS_MEGA_SOLO} OR {_IS_MEGA_DUO} OR {_IS_TRIO})
    ) ranked
"""
_COUNT_SQL = f"""
    SELECT rule, old_result, new_result, count(*) AS n
    FROM ({_TARGETS_SQL}) targets
    WHERE old_result IS DISTINCT FROM new_result
    GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
"""
_UPDATE_SQL = f"""
    UPDATE {{table}} b
    SET result = targets.new_result
    FROM ({_TARGETS_SQL}) targets
    WHERE b.tag = targets.tag AND b.datetime = targets.datetime
      AND b.result IS DISTINCT FROM targets.new_result
"""


def _progress(message: str) -> None:
    print(message, flush=True)


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def _fix_table(db: asyncpg.Connection, table: str, *, apply: bool, batch_days: int, sleep_s: float,
                     start_at: datetime | None, end_at: datetime | None) -> int:
    if table not in ALLOWED_TABLES:
        raise ValueError(table)
    _progress(f"{table}: MIN/MAX(datetime) を取得しています")
    row = await db.fetchrow(f"SELECT MIN(datetime) AS mn, MAX(datetime) AS mx FROM {table}")
    if row["mn"] is None:
        _progress(f"{table}: 行がないためスキップします")
        return 0
    start = max(_utc(row["mn"]), _utc(start_at)) if start_at else _utc(row["mn"])
    end = min(_utc(row["mx"]), _utc(end_at)) if end_at else _utc(row["mx"])
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    window = timedelta(days=batch_days)
    total = 0
    summary: dict[tuple[str, str, str], int] = {}
    cursor = start
    while cursor <= end:
        nxt = cursor + window
        started = time.monotonic()
        if apply:
            status = await db.execute(_UPDATE_SQL.format(table=table), cursor, nxt)
            changed = int(status.split()[-1])
        else:
            rows = await db.fetch(_COUNT_SQL.format(table=table), cursor, nxt)
            changed = sum(r["n"] for r in rows)
            for r in rows:
                key = (r["rule"], r["old_result"], r["new_result"])
                summary[key] = summary.get(key, 0) + r["n"]
        total += changed
        _progress(f"{table} {cursor.date()}〜{nxt.date()}: {'書き換え' if apply else '対象'} {changed} 行 ({time.monotonic() - started:.1f}s)")
        cursor = nxt
        if sleep_s > 0:
            await asyncio.sleep(sleep_s)
    for (rule, old, new), n in sorted(summary.items()):
        _progress(f"{table}  {rule}: {old} → {new}  {n} 行")
    _progress(f"{table}: 合計 {total} 行{'を書き換えました' if apply else 'が対象です（--apply で書き換えます）'}")
    return total


async def main() -> None:
    parser = argparse.ArgumentParser(description="メガバトルロイヤル・メガデュオ・トリオの保存済みの勝敗を今の判定にそろえる")
    parser.add_argument("--apply", action="store_true", help="書き換える（付けなければ数えるだけ）")
    parser.add_argument("--include-archived", action="store_true", help="archived_battles も対象にする")
    parser.add_argument("--batch-days", type=int, default=1, help="1回に進める日数")
    parser.add_argument("--sleep", type=float, default=0.3, help="バッチの間に待つ秒数")
    parser.add_argument("--start", type=str, default=None, help="この日（UTC, YYYY-MM-DD）から")
    parser.add_argument("--end", type=str, default=None, help="この日（UTC, YYYY-MM-DD）まで")
    parser.add_argument("--command-timeout", type=float, default=600.0, help="1バッチのタイムアウト（秒）")
    args = parser.parse_args()
    start_at = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc) if args.start else None
    end_at = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1) if args.end else None

    db = await asyncpg.connect(
        host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER, password=settings.DB_PASSWORD,
        database=settings.DB_NAME, timeout=60.0, command_timeout=args.command_timeout,
    )
    try:
        tables = ("battles", "archived_battles") if args.include_archived else ("battles",)
        for table in tables:
            await _fix_table(db, table, apply=args.apply, batch_days=max(1, args.batch_days), sleep_s=args.sleep,
                             start_at=start_at, end_at=end_at)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
