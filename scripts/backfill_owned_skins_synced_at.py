#!/usr/bin/env python3
"""players.owned_skins_synced_at のバックフィルと、旧仕様の所持スキンRedisキャッシュの削除を行う。

Alembic 適用後、新コードのデプロイ(再起動)前に VPS で backfill を実行する。
新コードは owned_skins_synced_at が NULL のプレイヤーの所持スキン数を「不明」扱いにするため。

backfill:
    BSInfo所持スキン対応後に更新され、所持スキン数が解放キャラ数を上回るプレイヤーを「取得済み」とみなす。
    (取得失敗時は各キャラ装備中の1件のみとなり、所持スキン数は解放キャラ数以下になる)
    スキンを1つも持たない(デフォルトのみの)プレイヤーは次回更新時に取得済みになる。

cleanup-redis:
    有効期限なしで保存されていた bsinfo:skins:#TAG を削除する。有効期限付きのキー(新仕様)は残す。

使い方:
  python3 scripts/backfill_owned_skins_synced_at.py backfill
  python3 scripts/backfill_owned_skins_synced_at.py backfill --batch-size 20000 --sleep 0.2
  python3 scripts/backfill_owned_skins_synced_at.py backfill --start-after-tag '#2ABC'
  python3 scripts/backfill_owned_skins_synced_at.py cleanup-redis
  python3 scripts/backfill_owned_skins_synced_at.py cleanup-redis --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncpg

from app.core.cache import close_redis, connect_redis, get_redis
from app.core.config import settings
from app.core.logger import logger
from app.services.brawl_service import BSINFO_SKIN_OWNERSHIP_STATS_START_AT

REDIS_SKINS_KEY_PATTERN = "bsinfo:skins:#*"


def _progress(message: str) -> None:
    """tmux でもすぐ見えるよう stdout に出す。アプリの logger はスクリプト単体では出ないことが多い。"""
    print(message, flush=True)
    logger.info(message)


async def _backfill(args: argparse.Namespace) -> int:
    db = await asyncpg.connect(
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        database=settings.DB_NAME,
        timeout=60.0,
        command_timeout=args.command_timeout,
    )
    # PKのtag順にバッチを区切り、1回の更新でロックする行数を小さく保つ
    query = """
        WITH batch AS (
            SELECT tag FROM players
            WHERE tag > $1
            ORDER BY tag
            LIMIT $2
        ),
        updated AS (
            UPDATE players p
            SET owned_skins_synced_at = p.last_updated_at
            FROM batch b
            WHERE p.tag = b.tag
              AND p.owned_skins_synced_at IS NULL
              AND p.last_updated_at >= $3
              AND p.owned_skin_count > p.unlocked_brawlers
            RETURNING 1
        )
        SELECT (SELECT MAX(tag) FROM batch) AS last_tag, (SELECT COUNT(*) FROM updated) AS updated_count
    """
    last_tag = args.start_after_tag
    total_updated = 0
    batches = 0
    started = time.monotonic()
    _progress(f"backfill開始: start_after_tag={last_tag!r}, batch_size={args.batch_size}, since={BSINFO_SKIN_OWNERSHIP_STATS_START_AT}")
    try:
        while True:
            row = await db.fetchrow(query, last_tag, args.batch_size, BSINFO_SKIN_OWNERSHIP_STATS_START_AT)
            if row["last_tag"] is None:
                break
            last_tag = row["last_tag"]
            total_updated += row["updated_count"]
            batches += 1
            if batches % 50 == 0:
                _progress(f"  {batches}バッチ処理 / 更新 {total_updated:,}件 / last_tag={last_tag} ({time.monotonic() - started:.0f}s)")
            if args.sleep:
                await asyncio.sleep(args.sleep)
    except (asyncpg.PostgresError, OSError) as e:
        _progress(f"backfill中断: {e} (再開する場合は --start-after-tag '{last_tag}' を指定)")
        return 1
    finally:
        await db.close()
    _progress(f"backfill完了: {batches}バッチ / 更新 {total_updated:,}件 ({time.monotonic() - started:.0f}s)")
    return 0


async def _cleanup_redis(args: argparse.Namespace) -> int:
    await connect_redis()
    r = get_redis()
    if not r:
        _progress("Redisに接続できませんでした")
        return 1
    scanned = 0
    deleted = 0
    started = time.monotonic()
    _progress(f"cleanup-redis開始: pattern={REDIS_SKINS_KEY_PATTERN}, dry_run={args.dry_run}")
    try:
        cursor = 0
        iterations = 0
        while True:
            cursor, keys = await r.scan(cursor=cursor, match=REDIS_SKINS_KEY_PATTERN, count=args.scan_count)
            if keys:
                scanned += len(keys)
                pipe = r.pipeline(transaction=False)
                for key in keys:
                    pipe.ttl(key)
                ttls = await pipe.execute()
                # TTL -1(有効期限なし)が旧仕様のキー。-2(既に消えた)や有効期限付きは対象外
                persistent_keys = [key for key, ttl in zip(keys, ttls) if ttl == -1]
                if persistent_keys and not args.dry_run:
                    await r.unlink(*persistent_keys)
                deleted += len(persistent_keys)
                if args.sleep:
                    await asyncio.sleep(args.sleep)
            iterations += 1
            if iterations % 100 == 0:
                _progress(f"  走査 {scanned:,}件 / 削除{'対象' if args.dry_run else ''} {deleted:,}件 ({time.monotonic() - started:.0f}s)")
            if cursor == 0:
                break
    finally:
        await close_redis()
    _progress(f"cleanup-redis完了: 走査 {scanned:,}件 / 削除{'対象' if args.dry_run else ''} {deleted:,}件 ({time.monotonic() - started:.0f}s)")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="owned_skins_synced_at のバックフィルと旧所持スキンキャッシュの削除")
    sub = parser.add_subparsers(dest="command", required=True)

    p_backfill = sub.add_parser("backfill", help="players.owned_skins_synced_at を埋める")
    p_backfill.add_argument("--batch-size", type=int, default=20000, help="1バッチで走査するプレイヤー数。既定 20000")
    p_backfill.add_argument("--sleep", type=float, default=0.2, help="バッチ間スリープ秒。既定 0.2")
    p_backfill.add_argument("--start-after-tag", default="", help="このタグより後から再開する")
    p_backfill.add_argument("--command-timeout", type=float, default=120.0, help="1ステートメントの秒タイムアウト。既定 120")

    p_cleanup = sub.add_parser("cleanup-redis", help="有効期限なしの bsinfo:skins:#TAG を削除する")
    p_cleanup.add_argument("--scan-count", type=int, default=1000, help="SCANのCOUNT。既定 1000")
    p_cleanup.add_argument("--sleep", type=float, default=0.02, help="SCAN間スリープ秒。既定 0.02")
    p_cleanup.add_argument("--dry-run", action="store_true", help="削除せず件数のみ数える")

    args = parser.parse_args()
    if args.command == "backfill":
        if args.batch_size < 1:
            parser.error("--batch-size は 1 以上")
        raise SystemExit(asyncio.run(_backfill(args)))
    if args.scan_count < 1:
        parser.error("--scan-count は 1 以上")
    raise SystemExit(asyncio.run(_cleanup_redis(args)))


if __name__ == "__main__":
    main()
