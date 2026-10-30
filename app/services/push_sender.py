"""プッシュ通知の送信（worker.py の常駐ループ。検討メモ notes_private/push_image_plan_2026-10-27.md の 3.4・3.6）。

- push_service.enqueue が積んだものを、まとめて（最大 BATCH_SIZE 件）取り出して送る。
- 宛先の端末・設定は、まとめた分を1回ずつで引く。端末のない人の分はここで捨てる。
- 送りすぎの対策（3.6）: 大会の進行と警告は間引かない。返信・メンションは同じ人から10分に3回まで（4回目はまとめの1回）、
  受け取りは1時間に30件まで。スレッドの一般メッセージ・いいね・リアクションは、まとめる単位ごとに1回送り、
  その後は1時間たってから件数を入れて出し直す（スレッドは開いたら最初から。push_service.reset_thread）。
  管理者通知は同じ種類を10分に1回にまとめる。
- 送り先は FCM HTTP v1（アプリ）と Web Push（ブラウザ）。失効した端末は消す。429・5xx は間をあけて再送する。
- ログは管理者通知にしない（push_service.warn）。
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from types import SimpleNamespace
from urllib.parse import urlparse

import asyncpg
import httpx

from app.core import cache as cache_module
from app.core.config import settings
from app.core.logger import logger
from app.core.templating import censor_filter
from app.db.db import get_db_connection_for_bg_task
from app.services import push_rules as rules
from app.services import push_service
from app.services.push_service import warn

BATCH_SIZE = 200
POP_TIMEOUT_SECONDS = 2
CONCURRENCY = 20
RETRY_DELAYS = (2, 6)
GROUP_STATE_TTL_SECONDS = 24 * 3600
FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
FCM_URL = "https://fcm.googleapis.com/v1/projects/{}/messages:send"
VAPID_EXPIRE_SECONDS = 12 * 3600
WEB_TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

OK, GONE, RETRY, ERROR = "ok", "gone", "retry", "error"

USER_COLUMNS = (
    "id", "notification_badge_enabled",
    "notification_post_like_enabled", "notification_own_post_message_enabled",
    "notification_participated_thread_message_enabled", "notification_message_mention_enabled",
    "notification_message_reply_enabled", "notification_message_reaction_enabled", "notification_token_gift_enabled",
    *rules.PUSH_SETTING_KEYS,
    # [この部分は公開用リポジトリでは非公開にされています]
)


# --- ループ -----------------------------------------------------------------------------------

async def push_loop(shutdown_event: asyncio.Event) -> None:
    sender = Sender()
    logger.info("プッシュ通知の送信ループを開始しました。")
    try:
        while not shutdown_event.is_set():
            jobs = await _pop_batch()
            if not jobs:
                continue
            try:
                await process(jobs, sender)
            except Exception as e:
                warn(f"プッシュ通知の送信中にエラー（{len(jobs)}件を捨てました）: {e}")
    finally:
        await sender.close()


async def _pop_batch() -> list[dict]:
    redis = cache_module.redis_pool
    if redis is None:
        await asyncio.sleep(POP_TIMEOUT_SECONDS)
        return []
    try:
        first = await redis.blpop([push_service.QUEUE_KEY], timeout=POP_TIMEOUT_SECONDS)
        if not first:
            return []
        rest = await redis.lpop(push_service.QUEUE_KEY, BATCH_SIZE - 1) or []
    except Exception as e:
        if cache_module.is_transient_redis_error(e):
            cache_module.log_transient_redis_warning(f"プッシュ通知のキューを読めませんでした: {e}")
        else:
            warn(f"プッシュ通知のキューを読めませんでした: {e}")
        await asyncio.sleep(POP_TIMEOUT_SECONDS)
        return []
    jobs = []
    for raw in [first[1], *rest]:
        try:
            jobs.append(json.loads(raw))
        except (TypeError, ValueError):
            continue
    return jobs


# --- まとめて処理 -----------------------------------------------------------------------------

async def process(jobs: list[dict], sender: "Sender") -> None:
    now = time.time()
    jobs = [j for j in jobs if rules.is_fresh(j, now)]
    if not jobs:
        return
    async with get_db_connection_for_bg_task() as db:
        user_jobs = [(int(j["uid"]), j) for j in jobs if j.get("src") in ("board", "tn", "direct") and j.get("uid")]
        # [この部分は公開用リポジトリでは非公開にされています]
        if not user_jobs:
            return
        devices = await _devices(db, {uid for uid, _ in user_jobs})
        user_jobs = [(uid, j) for uid, j in user_jobs if uid in devices]
        if not user_jobs:
            return
        users = await _users(db, set(devices))
        board = await BoardContext.load(db, [j for _, j in user_jobs if j["src"] == "board"])

        sends: list[tuple[int, object]] = []  # (uid, lang → Push)
        for uid, job in user_jobs:
            user = users.get(uid)
            if user is None:
                continue
            make = await _plan(job, user, board, now)
            if make is not None:
                sends.append((uid, make))
        if not sends:
            return

        badges: dict[int, int | None] = {}
        for uid in {uid for uid, _ in sends}:
            if any(d["platform"] != "web" for d in devices[uid]):
                badges[uid] = await _badge(db, users[uid])

        tasks = []
        for uid, make in sends:
            for device in devices[uid]:
                push = make(device["lang"])
                tasks.append(sender.deliver(device, push, badges.get(uid)))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        gone = []
        index = 0
        for uid, _ in sends:
            for device in devices[uid]:
                result = results[index]
                index += 1
                if result == GONE:
                    gone.append(device["token"])
                elif isinstance(result, Exception):
                    warn(f"プッシュ通知を送れませんでした (user={uid}, platform={device['platform']}): {result}")
        if gone:
            await push_service.remove_devices(db, gone)
        logger.debug(f"プッシュ通知: {len(tasks)}件を送信（失効した端末 {len(gone)}台を削除）")


async def _devices(db: asyncpg.Connection, uids: set[int]) -> dict[int, list[asyncpg.Record]]:
    rows = await db.fetch(
        "SELECT user_id, platform, token, p256dh, auth, lang FROM push_devices WHERE user_id = ANY($1::int[])",
        list(uids),
    )
    out: dict[int, list] = {}
    for r in rows:
        out.setdefault(r["user_id"], []).append(r)
    return out


async def _users(db: asyncpg.Connection, uids: set[int]) -> dict[int, dict]:
    rows = await db.fetch(
        f"SELECT {', '.join(USER_COLUMNS)} FROM users WHERE id = ANY($1::int[]) AND NOT is_invalid", list(uids),
    )
    return {r["id"]: dict(r) for r in rows}


class BoardContext:
    """掲示板の通知の文面に要るもの（送る人の名前・投稿の種類・本文・トークンの額）を、まとめて1回ずつ引く。"""

    def __init__(self) -> None:
        self.alive: set[int] = set()
        self.names: dict[int, str] = {}
        self.post_types: dict[int, str] = {}
        self.texts: dict[int, str] = {}
        self.amounts: dict[int, int] = {}

    @classmethod
    async def load(cls, db: asyncpg.Connection, jobs: list[dict]) -> "BoardContext":
        ctx = cls()
        if not jobs:
            return ctx
        nids = [int(j["nid"]) for j in jobs if j.get("nid")]
        actors = [int(j["actor"]) for j in jobs if j.get("actor")]
        posts = [int(j["post"]) for j in jobs if j.get("post")]
        messages = [int(j["msg"]) for j in jobs if j.get("msg")]
        if nids:
            # 送るまでに消えた・読まれた通知（いいねの取り消し・スレッドを開いた）は送らない
            rows = await db.fetch("SELECT id FROM board_notifications WHERE id = ANY($1::int[]) AND read_at IS NULL", nids)
            ctx.alive = {r["id"] for r in rows}
        if actors:
            rows = await db.fetch("SELECT id, name FROM users WHERE id = ANY($1::int[])", actors)
            ctx.names = {r["id"]: r["name"] for r in rows}
        if posts:
            rows = await db.fetch("SELECT id, type FROM posts WHERE id = ANY($1::int[])", posts)
            ctx.post_types = {r["id"]: r["type"] for r in rows}
        if messages:
            rows = await db.fetch(
                "SELECT id, message FROM messages WHERE id = ANY($1::int[]) AND NOT is_deleted AND message_type = 'message'",
                messages,
            )
            ctx.texts = {r["id"]: censor_filter(r["message"] or "") for r in rows}
            rows = await db.fetch(
                "SELECT message_id, amount, comment FROM token_gifts WHERE message_id = ANY($1::int[])", messages,
            )
            for r in rows:
                ctx.amounts[r["message_id"]] = int(r["amount"])
                if r["comment"]:
                    ctx.texts[r["message_id"]] = censor_filter(r["comment"])
        return ctx


async def _plan(job: dict, user: dict, board: BoardContext, now: float):
    """送るなら「端末の言語 → Push」の関数、送らないなら None。間引きの状態はここで進める。"""
    uid = user["id"]
    src = job.get("src")
    if src == "direct":
        pushes = {lang: rules.Push(**data) for lang, data in (job.get("push") or {}).items()}
        if not pushes:
            return None
        return lambda lang: pushes.get(lang) or next(iter(pushes.values()))
    if src == "tn":
        notice = job.get("notice") or {}
        category = notice.get("cat") or rules.tournament_category(notice.get("kind") or "")
        if not rules.tournament_enabled(category, user):
            return None
        if category == rules.CAT_TN_CHAT:
            decision = await _pair_decision(uid, notice.get("by") or "", now)
            if decision is None:
                return None
            if decision == "summary":
                by = notice.get("by") or ""
                return lambda lang: rules.pair_summary_push(
                    lang, actor=by, url=f"/{lang}/{notice.get('path') or 'tournaments'}", tag=f"tp{by}"[:64],
                    channel="tournament")
        return lambda lang: rules.tournament_push(notice, lang)

    if src == "board":
        ntype = job.get("type") or ""
        nid = job.get("nid")
        if nid and int(nid) not in board.alive:
            return None
        if not rules.board_enabled(ntype, user):
            return None
        post_id, message_id, actor_id = job.get("post"), job.get("msg"), job.get("actor")
        actor = board.names.get(int(actor_id), "") if actor_id else ""
        post_type = board.post_types.get(int(post_id)) if post_id else None
        text = board.texts.get(int(message_id), "") if message_id else ""
        amount = board.amounts.get(int(message_id)) if message_id else None
        group = rules.board_group(ntype)
        count = 1
        if group in (rules.GROUP_THREAD, rules.GROUP_LIKE, rules.GROUP_REACTION):
            target = message_id if group == rules.GROUP_REACTION else post_id
            send, count = await _group_decision(uid, group, target, now)
            if not send:
                return None
        elif group == rules.GROUP_PAIR:
            decision = await _pair_decision(uid, f"u{actor_id}", now)
            if decision is None:
                return None
            if decision == "summary":
                return lambda lang: rules.pair_summary_push(
                    lang, actor=actor, url=rules.board_url(lang, ntype, post_id, message_id), tag=f"bp{actor_id}",
                    channel="board")
        return lambda lang: rules.board_push(ntype, lang, actor=actor, post_type=post_type, post_id=post_id,
                                             message_id=message_id, text=text, count=count, amount=amount)

    # [この部分は公開用リポジトリでは非公開にされています]
    return None


# --- 間引き（Redis） --------------------------------------------------------------------------
# worker は1プロセスで順に処理するので、読んでから書くまでの間に他から変わることはない

async def _group_decision(uid: int, group: str, target, now: float) -> tuple[bool, int]:
    redis = cache_module.redis_pool
    if redis is None or target is None:
        return True, 1
    key = push_service.group_key(uid, group, target)
    try:
        async with redis.pipeline(transaction=False) as pipe:
            pipe.hincrby(key, "n", 1)
            pipe.hget(key, "at")
            pending, at = await pipe.execute()
        send, count = rules.group_decision(float(at) if at else None, int(pending), now)
        if send:
            async with redis.pipeline(transaction=False) as pipe:
                pipe.hset(key, mapping={"at": now, "n": 0})
                pipe.expire(key, GROUP_STATE_TTL_SECONDS)
                await pipe.execute()
        return send, count
    except Exception as e:
        warn(f"プッシュ通知のまとめの状態を読めませんでした: {e}")
        return True, 1


async def _pair_decision(uid: int, actor_key: str, now: float) -> str | None:
    """返信・メンション: "send"（送る）・"summary"（まとめの1回）・None（送らない）。"""
    redis = cache_module.redis_pool
    if redis is None:
        return "send"
    try:
        decision = "send"
        if actor_key:
            pair_key = f"push:pair:{uid}:{actor_key}"[:200]
            async with redis.pipeline(transaction=False) as pipe:
                pipe.incr(pair_key)
                pipe.expire(pair_key, rules.PAIR_WINDOW_SECONDS, nx=True)
                count, _ = await pipe.execute()
            if count > rules.PAIR_LIMIT + 1:
                return None
            if count == rules.PAIR_LIMIT + 1:
                decision = "summary"
        cap_key = f"push:recv:{uid}"
        async with redis.pipeline(transaction=False) as pipe:
            pipe.incr(cap_key)
            pipe.expire(cap_key, rules.RECIPIENT_WINDOW_SECONDS, nx=True)
            received, _ = await pipe.execute()
        return decision if received <= rules.RECIPIENT_LIMIT else None
    except Exception as e:
        warn(f"プッシュ通知の上限を読めませんでした: {e}")
        return "send"


# [この部分は公開用リポジトリでは非公開にされています]


async def _badge(db: asyncpg.Connection, user: dict) -> int | None:
    """アプリのアイコンのバッジの数（掲示板の未読＋大会の未読、管理者・副管理者は管理者通知の未読も）。"""
    from app.services import notification_service, tournament_notify
    try:
        total = await tournament_notify.unread_count(user["id"])
        if user.get("notification_badge_enabled", True):
            total += await notification_service.get_unread_badge_count(db, user["id"])
        # [この部分は公開用リポジトリでは非公開にされています]
        return total
    except Exception as e:
        warn(f"アイコンのバッジの数を数えられませんでした (user={user['id']}): {e}")
        return None


# --- お知らせのプッシュ（AD26・AD30） ----------------------------------------------------------------
# 一度に多くの端末に送るので、通知のキュー（push_loop）とは別のループ・別の同時数で少しずつ送り、
# 大会の進行などの通知を遅らせない。送り終えた端末を cursor に残し、worker が止まったら次から続ける

ANNOUNCEMENT_POLL_SECONDS = 15
ANNOUNCEMENT_CHUNK = 500
ANNOUNCEMENT_CONCURRENCY = 8
ANNOUNCEMENT_LATE_SECONDS = 6 * 3600  # 送る時刻（または送信の途中）からこれ以上たっていたら送らない

_DUE_SQL = """
    SELECT ap.id FROM announcement_pushes ap JOIN announcement_posts p ON p.id = ap.post_id
    WHERE ap.status IN ('pending', 'sending') AND p.status = 'published' AND p.deleted_at IS NULL
      AND p.published_at <= now()
    ORDER BY p.published_at, ap.id
    LIMIT 10
"""


async def announcement_loop(shutdown_event: asyncio.Event) -> None:
    if not push_service.is_enabled():
        logger.info("プッシュ通知の鍵がないため、お知らせのプッシュ通知の送信ループは動かしません。")
        return
    sender = Sender(concurrency=ANNOUNCEMENT_CONCURRENCY)
    logger.info("お知らせのプッシュ通知の送信ループを開始しました。")
    try:
        while not shutdown_event.is_set():
            try:
                await send_due_announcements(sender, shutdown_event)
            except Exception as e:
                warn(f"お知らせのプッシュ通知の送信中にエラー: {e}")
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=ANNOUNCEMENT_POLL_SECONDS)
            except asyncio.TimeoutError:
                pass
    finally:
        await sender.close()


async def send_due_announcements(sender: "Sender", shutdown_event: asyncio.Event | None = None) -> None:
    async with get_db_connection_for_bg_task() as db:
        # 送信の途中で長く止まったまま（お知らせを下書きに戻した・消したなど）のものは終わりにする
        await db.execute(
            "UPDATE announcement_pushes SET status = 'interrupted', finished_at = now(), updated_at = now() "
            "WHERE status = 'sending' AND updated_at < now() - make_interval(secs => $1)",
            ANNOUNCEMENT_LATE_SECONDS,
        )
        ids = [r["id"] for r in await db.fetch(_DUE_SQL)]
        for push_id in ids:
            if shutdown_event is not None and shutdown_event.is_set():
                return
            await _send_announcement(db, push_id, sender, shutdown_event)


async def _send_announcement(db: asyncpg.Connection, push_id: int, sender: "Sender",
                             shutdown_event: asyncio.Event | None) -> None:
    from app.services import announcement_push_service as aps

    row = await db.fetchrow("SELECT * FROM announcement_pushes WHERE id = $1", push_id)
    post = await db.fetchrow("SELECT * FROM announcement_posts WHERE id = $1", row["post_id"]) if row else None
    if row is None or post is None or row["status"] not in ("pending", "sending"):
        return
    now = time.time()
    if row["status"] == "pending":
        due = max(post["published_at"], row["armed_at"] or post["published_at"]).timestamp()
        if now - due > ANNOUNCEMENT_LATE_SECONDS:
            await db.execute(
                "UPDATE announcement_pushes SET status = 'expired', finished_at = now(), updated_at = now() WHERE id = $1",
                push_id,
            )
            warn(f"お知らせ (ID: {post['id']})のプッシュ通知は、送る時刻から時間がたちすぎたので送りませんでした。")
            return
    platforms, langs = aps.targets(post)
    pushes = aps.build_pushes(post, row, platforms, langs)
    claimed = await db.fetchrow(
        """
        UPDATE announcement_pushes SET status = 'sending', started_at = COALESCE(started_at, now()), updated_at = now(),
            target_count = CASE WHEN status = 'pending' THEN (
                SELECT COUNT(*) FROM push_devices d JOIN users u ON u.id = d.user_id WHERE """ + aps.RECIPIENT_WHERE + """
            ) ELSE target_count END
        WHERE id = $3 AND status = $4
        RETURNING *
        """,
        platforms, langs, push_id, row["status"],
    )
    if claimed is None:
        return
    if row["status"] == "pending":
        logger.info(f"お知らせ (ID: {post['id']})のプッシュ通知を送り始めます（{claimed['target_count']}台）。")
    cursor, sent, failed, gone = claimed["cursor"], claimed["sent_count"], claimed["failed_count"], claimed["gone_count"]
    while True:
        if shutdown_event is not None and shutdown_event.is_set():
            return  # sending のまま。次に worker が動いたら cursor の次から続ける
        devices = await db.fetch(
            f"""
            SELECT d.id, d.user_id, d.platform, d.token, d.p256dh, d.auth, d.lang
            FROM push_devices d JOIN users u ON u.id = d.user_id
            WHERE {aps.RECIPIENT_WHERE} AND d.id > $3
            ORDER BY d.id
            LIMIT $4
            """,
            platforms, langs, cursor, ANNOUNCEMENT_CHUNK,
        )
        if not devices:
            break
        results = await asyncio.gather(
            *(sender.deliver(d, pushes[(d["platform"], d["lang"])], None) for d in devices), return_exceptions=True,
        )
        gone_tokens = []
        for device, result in zip(devices, results):
            if result == OK:
                sent += 1
            elif result == GONE:
                gone += 1
                gone_tokens.append(device["token"])
            else:
                failed += 1
                if isinstance(result, Exception):
                    warn(f"お知らせのプッシュ通知を送れませんでした (user={device['user_id']}): {result}")
        if gone_tokens:
            await push_service.remove_devices(db, gone_tokens)
        cursor = devices[-1]["id"]
        await db.execute(
            "UPDATE announcement_pushes SET cursor = $2, sent_count = $3, failed_count = $4, gone_count = $5, "
            "updated_at = now() WHERE id = $1",
            push_id, cursor, sent, failed, gone,
        )
        if len(devices) < ANNOUNCEMENT_CHUNK:
            break
    await db.execute(
        "UPDATE announcement_pushes SET status = 'sent', finished_at = now(), updated_at = now() WHERE id = $1", push_id,
    )
    logger.info(
        f"お知らせ (ID: {post['id']})のプッシュ通知を送りました（{sent}台に送信、失敗 {failed}台、失効して削除 {gone}台）。"
    )


# --- 送り先 -----------------------------------------------------------------------------------

class Sender:
    def __init__(self, concurrency: int = CONCURRENCY) -> None:
        self.client = httpx.AsyncClient(http2=True, timeout=10.0)
        self.sem = asyncio.Semaphore(concurrency)
        self.fcm = FcmClient(settings.FCM_SERVICE_ACCOUNT_FILE) if settings.FCM_SERVICE_ACCOUNT_FILE else None
        self.web = (WebPushClient(settings.VAPID_PRIVATE_KEY, settings.VAPID_SUBJECT)
                    if settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY else None)

    async def close(self) -> None:
        await self.client.aclose()

    async def deliver(self, device, push: rules.Push, badge: int | None) -> str:
        if device["platform"] == "web":
            if self.web is None:
                return ERROR
            send = lambda: self.web.send(self.client, device, push)  # noqa: E731
        else:
            if self.fcm is None:
                return ERROR
            send = lambda: self.fcm.send(self.client, device["token"], push, badge)  # noqa: E731
        for delay in (0, *RETRY_DELAYS):
            if delay:
                await asyncio.sleep(delay)
            async with self.sem:
                result = await send()
            if result != RETRY:
                return result
        return ERROR


class FcmClient:
    """FCM HTTP v1。アクセストークンはサービスアカウントの鍵で取り、切れる前に取り直す。"""

    def __init__(self, path: str) -> None:
        self.path = path
        self.credentials = None
        self.project_id: str | None = None
        self._lock = asyncio.Lock()

    async def _token(self, force: bool = False) -> str | None:
        async with self._lock:
            try:
                if self.credentials is None:
                    from google.oauth2 import service_account
                    self.credentials = await asyncio.to_thread(
                        service_account.Credentials.from_service_account_file, self.path, scopes=[FCM_SCOPE])
                    self.project_id = self.credentials.project_id
                if force or not self.credentials.valid:
                    from google.auth.transport.requests import Request
                    await asyncio.to_thread(self.credentials.refresh, Request())
                return self.credentials.token
            except Exception as e:
                warn(f"FCM のアクセストークンを取れませんでした: {e}")
                return None

    async def send(self, client: httpx.AsyncClient, token: str, push: rules.Push, badge: int | None) -> str:
        access = await self._token()
        if not access or not self.project_id:
            return ERROR
        body = {"message": rules.fcm_message(token, push, badge, now=time.time())}
        try:
            r = await client.post(FCM_URL.format(self.project_id), json=body,
                                  headers={"Authorization": f"Bearer {access}"})
        except httpx.HTTPError as e:
            logger.info(f"FCM への送信で通信エラー（再送します）: {e}")
            return RETRY
        if r.status_code == 200:
            return OK
        if r.status_code == 401:
            await self._token(force=True)
            return RETRY
        if r.status_code in (429, 500, 502, 503, 504):
            return RETRY
        detail = r.text[:300]
        if r.status_code in (403, 404) or (r.status_code == 400 and "registration token" in detail.lower()):
            return GONE  # UNREGISTERED・SENDER_ID_MISMATCH・不正なトークン
        warn(f"FCM への送信に失敗しました ({r.status_code}): {detail}")
        return ERROR


class WebPushClient:
    """Web Push（RFC 8291 の暗号化は pywebpush、VAPID の署名は py_vapid）。送信は httpx。"""

    def __init__(self, private_key: str, subject: str) -> None:
        from py_vapid import Vapid
        self.vapid = Vapid.from_string(private_key=private_key)
        self.subject = subject if subject.startswith(("mailto:", "https://")) else f"https://{subject}"
        self._auth: dict[str, tuple[dict, float]] = {}

    def _vapid_headers(self, endpoint: str) -> dict:
        parsed = urlparse(endpoint)
        aud = f"{parsed.scheme}://{parsed.netloc}"
        now = time.time()
        cached = self._auth.get(aud)
        if cached and cached[1] - now > 3600:
            return cached[0]
        exp = int(now) + VAPID_EXPIRE_SECONDS
        headers = self.vapid.sign({"sub": self.subject, "aud": aud, "exp": exp})
        self._auth[aud] = (headers, exp)
        return headers

    async def send(self, client: httpx.AsyncClient, device, push: rules.Push) -> str:
        from pywebpush import WebPusher
        endpoint = device["token"]
        try:
            pusher = WebPusher({"endpoint": endpoint, "keys": {"p256dh": device["p256dh"], "auth": device["auth"]}})
            data = json.dumps(rules.web_payload(push), ensure_ascii=False).encode()
            body = pusher.encode(data, "aes128gcm")["body"]
        except Exception as e:
            logger.info(f"Web Push の購読の鍵が不正なため削除します: {e}")
            return GONE
        headers = {
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": str(push.ttl),
            "Urgency": "high" if push.high else "normal",
            **self._vapid_headers(endpoint),
        }
        if WEB_TOPIC_RE.match(push.tag):
            headers["Topic"] = push.tag  # 届く前の同じ tag の通知は、新しいもので置き換えられる
        try:
            r = await client.post(endpoint, content=body, headers=headers)
        except httpx.HTTPError as e:
            logger.info(f"Web Push の送信で通信エラー（再送します）: {e}")
            return RETRY
        if r.status_code in (200, 201, 202):
            return OK
        if r.status_code in (404, 410, 403):
            return GONE  # 購読の解除・VAPID の鍵の不一致
        if r.status_code == 429 or r.status_code >= 500:
            return RETRY
        warn(f"Web Push の送信に失敗しました ({r.status_code}): {r.text[:300]}")
        return ERROR
