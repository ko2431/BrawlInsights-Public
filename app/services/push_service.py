"""プッシュ通知の積み込み・端末の登録・設定（検討メモ notes_private/push_image_plan_2026-10-27.md の 3.2・3.4）。

- 通知を作る所（掲示板・大会・管理者）は enqueue で Redis のキューに積むだけにする（リクエストの中では送らない）。
  送信は worker の push_sender。鍵（VAPID・FCM）がどちらも未設定なら積まない。
- 端末は push_devices。token で upsert し、同じ端末で別のアカウントにログインし直したら user_id を付け替える。
- 送信の失敗などのログは管理者通知にしない（管理者通知 → プッシュ → 失敗のログ → 管理者通知、の循環を作らない）。
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict

import asyncpg

from app.core import cache as cache_module
from app.core.config import settings
from app.core.logger import logger
from app.services import push_rules as rules

QUEUE_KEY = "push:queue"
QUEUE_MAX = 100_000  # worker が止まっている間に積み上がりすぎないように（古いものから捨てる）
MAX_DEVICES_PER_USER = 10
NATIVE_TOKEN_MAX_CHARS = 4096
WEB_KEY_MAX_CHARS = 200
# アプリ版のプッシュ（15.3 のアプリ）がストアに出たら True にする（設定画面の案内が「最新版に更新」に変わる）
APP_PUSH_RELEASED = False
APP_PUSH_MIN_VERSION = "15.3"
NO_ADMIN_NOTICE = {"skip_admin_notification": True}


def warn(message: str) -> None:
    logger.warning(message, extra=NO_ADMIN_NOTICE)


def is_enabled() -> bool:
    return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY) or bool(settings.FCM_SERVICE_ACCOUNT_FILE)


def web_push_public_key() -> str | None:
    return settings.VAPID_PUBLIC_KEY if settings.VAPID_PRIVATE_KEY else None


def group_key(user_id: int, group: str, target) -> str:
    """まとめて送る単位ごとの状態（push_sender が使う。スレッドは開いたら消す）。"""
    return f"push:g:{int(user_id)}:{group}:{target}"


# --- 積み込み ---------------------------------------------------------------------------------

async def enqueue(jobs: list[dict]) -> None:
    if not jobs or not is_enabled():
        return
    redis = cache_module.redis_pool
    if redis is None:
        return
    now = time.time()
    try:
        async with redis.pipeline(transaction=False) as pipe:
            for job in jobs:
                pipe.rpush(QUEUE_KEY, json.dumps(job | {"at": now}, ensure_ascii=False))
            pipe.ltrim(QUEUE_KEY, -QUEUE_MAX, -1)
            await pipe.execute()
    except Exception as e:
        if cache_module.is_transient_redis_error(e):
            cache_module.log_transient_redis_warning(f"プッシュ通知を積めませんでした: {e}")
        else:
            warn(f"プッシュ通知を積めませんでした: {e}")


async def enqueue_board(*, notification_id: int | None, recipient_user_id: int, notification_type: str,
                        actor_user_id: int | None, post_id: int | None, message_id: int | None) -> None:
    """掲示板の通知（board_notifications に書いた後で）。"""
    await enqueue([{
        "src": "board", "nid": notification_id, "uid": int(recipient_user_id), "type": notification_type,
        "actor": actor_user_id, "post": post_id, "msg": message_id,
    }])


async def enqueue_tournament(notices: list[tuple[int, dict]]) -> None:
    """大会の通知（tournament_notify.deliver から。コミット後）。notice は画面内の通知と同じもの。"""
    await enqueue([{"src": "tn", "uid": int(uid), "notice": notice} for uid, notice in notices])


async def enqueue_direct(user_id: int, pushes: dict[str, rules.Push], *, setting: str | None = None) -> None:
    """作ったものをそのまま1人に送る（お知らせのプッシュの試し送り・フィードバックへの返信）。pushes は端末の言語 → 通知。
    setting（受け取りの設定のキー）を渡すと、worker がその設定を確かめる。渡さなければ設定は見ない。"""
    await enqueue_direct_many([(user_id, pushes)], setting=setting)


async def enqueue_direct_many(items: list[tuple[int, dict[str, rules.Push]]], *, setting: str | None = None) -> None:
    jobs = []
    for user_id, pushes in items:
        job = {"src": "direct", "uid": int(user_id), "push": {lang: asdict(push) for lang, push in pushes.items()}}
        if setting:
            job["setting"] = setting
        jobs.append(job)
    if jobs:
        await enqueue(jobs)


# [この部分は公開用リポジトリでは非公開にされています]


async def reset_thread(user_id: int, thread_id: int) -> None:
    """スレッドを開いたら、そのスレッドの一般メッセージのまとめを最初からにする（次の新着でまた1回送る）。"""
    if not is_enabled():
        return
    redis = cache_module.redis_pool
    if redis is None:
        return
    try:
        await redis.delete(group_key(user_id, rules.GROUP_THREAD, int(thread_id)))
    except Exception as e:
        if cache_module.is_transient_redis_error(e):
            cache_module.log_transient_redis_warning(f"プッシュ通知のまとめを消せませんでした: {e}")
        else:
            warn(f"プッシュ通知のまとめを消せませんでした: {e}")


# --- 端末 -------------------------------------------------------------------------------------

def validate_device(platform: str, token: str, p256dh: str | None, auth: str | None) -> bool:
    if platform == "web":
        return (rules.is_allowed_web_endpoint(token) and bool(p256dh) and bool(auth)
                and len(p256dh) <= WEB_KEY_MAX_CHARS and len(auth) <= WEB_KEY_MAX_CHARS)
    if platform in ("ios", "android"):
        return 0 < len(token) <= NATIVE_TOKEN_MAX_CHARS
    return False


async def register_device(db: asyncpg.Connection, *, user_id: int, platform: str, token: str,
                          p256dh: str | None, auth: str | None, lang: str, app_version: str | None) -> None:
    """端末を登録する（同じ token なら付け替え）。1人あたりの端末は、新しいものから MAX_DEVICES_PER_USER 台まで。"""
    async with db.transaction():
        await db.execute(
            """
            INSERT INTO push_devices (user_id, platform, token, p256dh, auth, lang, app_version)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (token) DO UPDATE SET
                user_id = EXCLUDED.user_id, platform = EXCLUDED.platform, p256dh = EXCLUDED.p256dh,
                auth = EXCLUDED.auth, lang = EXCLUDED.lang, app_version = EXCLUDED.app_version, last_seen_at = NOW()
            """,
            user_id, platform, token, p256dh, auth, "en" if lang == "en" else "ja", app_version,
        )
        await db.execute(
            """
            DELETE FROM push_devices
            WHERE user_id = $1 AND id NOT IN (
                SELECT id FROM push_devices WHERE user_id = $1 ORDER BY last_seen_at DESC LIMIT $2
            )
            """,
            user_id, MAX_DEVICES_PER_USER,
        )


async def remove_device(db: asyncpg.Connection, token: str) -> bool:
    status = await db.execute("DELETE FROM push_devices WHERE token = $1", token)
    return status.split()[-1] != "0"


async def remove_devices(db: asyncpg.Connection, tokens: list[str]) -> int:
    if not tokens:
        return 0
    status = await db.execute("DELETE FROM push_devices WHERE token = ANY($1::text[])", list(tokens))
    return int(status.split()[-1])


# --- 設定 -------------------------------------------------------------------------------------

async def get_push_settings(db: asyncpg.Connection, user_id: int) -> dict:
    row = await db.fetchrow(f"SELECT {', '.join(rules.PUSH_SETTING_KEYS)} FROM users WHERE id = $1", user_id)
    if not row:
        return dict(rules.PUSH_SETTING_DEFAULTS)
    return {key: bool(row[key]) for key in rules.PUSH_SETTING_KEYS}


async def update_push_setting(db: asyncpg.Connection, user_id: int, key: str, enabled: bool) -> None:
    if key not in rules.PUSH_SETTING_KEYS:
        raise ValueError("Invalid push setting key")
    await db.execute(f"UPDATE users SET {key} = $1 WHERE id = $2", bool(enabled), user_id)


# [この部分は公開用リポジトリでは非公開にされています]
