"""止まった大会の検知と自動中止（検討メモ 13.5・13.6）。worker（tournament_loop）から1分ごとに呼ぶ。

- 確認待ち（review）の試合・ロビーが放置されたら、30分で主催者にもう一度通知し、2時間で管理者にも知らせる（review_alert で1回ずつ）。
- 開催中のまま、最後の進行（試合の開始・部屋の登録・結果の確定・主催者やシステムの操作の記録）から18時間動きがなければ、
  主催者と参加者に自動中止を予告し、予告から6時間（最後の進行から24時間以上）たっても動きがなければ中止する。
  予告のあとに進行があれば、予告は無かったものとして扱う（また18時間止まったら予告し直す）。

開催中の大会・確認待ちの試合は少ないので、どちらも部分インデックスと小さい集計で済む。
"""
from __future__ import annotations

import datetime

import asyncpg

from app.core.logger import logger
from app.services import tournament_chat as chat
from app.services import tournament_notify
from app.services.tournament_service import TournamentError, _now

REVIEW_REMIND_MINUTES = 30  # 確認待ちのまま、主催者にもう一度通知するまで
REVIEW_ADMIN_MINUTES = 120  # 確認待ちのまま、管理者に知らせるまで
STALL_HOURS = 24  # 進行がないまま、自動で中止するまで
STALL_WARN_HOURS = 6  # 自動中止の何時間前に予告するか
STALLED_LIST_HOURS = 6  # 管理画面の「止まっている大会」に、進行のない大会を出すまで（確認待ちが REVIEW_ADMIN_MINUTES を超えた大会も出す）
DUE_LIMIT = 50

# 大会の最後の進行の時刻（t は tournaments の別名）。GREATEST は NULL を無視する
LAST_PROGRESS_SQL = """GREATEST(
    t.start_at,
    (SELECT MAX(a.created_at) FROM tournament_audit_logs a WHERE a.tournament_id = t.id),
    (SELECT MAX(GREATEST(m.started_at, m.room_registered_at, m.finished_at)) FROM tournament_matches m WHERE m.tournament_id = t.id)
)"""


async def remind_reviews(db: asyncpg.Connection) -> int:
    """放置された確認待ちを、主催者（30分）と管理者（2時間）に知らせる。知らせた試合の数を返す。"""
    from app.services import tournament_match, tournament_sd  # 循環 import を避ける
    rows = await db.fetch(
        """
        SELECT m.id, m.tournament_id, m.review_alert, m.review_reason,
               EXTRACT(EPOCH FROM now() - m.review_at) / 60 AS minutes, s.kind
        FROM tournament_matches m
        JOIN tournaments t ON t.id = m.tournament_id
        JOIN tournament_stages s ON s.id = m.stage_id
        WHERE m.state = 'review' AND m.review_at <= now() - make_interval(mins => $1)
          AND m.review_alert < 2 AND t.status = 'live' AND NOT t.is_deleted
        ORDER BY m.review_at
        LIMIT $2
        """,
        REVIEW_REMIND_MINUTES, DUE_LIMIT,
    )
    count = 0
    admin_notified: set[int] = set()
    admin_targets: list[dict] = []
    for r in rows:
        level = 2 if r["minutes"] >= REVIEW_ADMIN_MINUTES else 1
        if level <= r["review_alert"]:
            continue
        outbox = chat.Outbox()
        async with db.transaction():
            # 他の操作で確認待ちが解かれていたら何もしない
            claimed = await db.fetchval(
                "UPDATE tournament_matches SET review_alert = $2 WHERE id = $1 AND state = 'review' AND review_alert = $3 RETURNING id",
                r["id"], level, r["review_alert"],
            )
            if claimed is None:
                continue
            t = dict(await db.fetchrow("SELECT * FROM tournaments WHERE id = $1", r["tournament_id"]))
            if r["review_alert"] == 0:
                minutes = REVIEW_REMIND_MINUTES * (r["minutes"] // REVIEW_REMIND_MINUTES)
                if r["kind"] == "sd_points":
                    m = await tournament_sd.load_lobby(db, t["id"], r["id"])
                    await tournament_sd._notify_lobby_review(db, outbox, t, m, r["review_reason"], reminder=True, minutes=int(minutes))
                else:
                    m = await tournament_match.load_match(db, t["id"], r["id"])
                    await tournament_match.notify_review(db, outbox, t, m, r["review_reason"], reminder=True, minutes=int(minutes),
                                                         round_count=await tournament_match._round_count(db, m["stage_id"]))
            if level == 2 and t["id"] not in admin_notified:
                admin_notified.add(t["id"])
                admin_targets.append(t)
        await outbox.flush()
        count += 1
    # 管理者通知は失敗しても落とさない作りだが、トランザクションの外で出す（失敗で上の処理を巻き込まないように）
    from app.services.admin_notification_service import emit_admin_notification
    for t in admin_targets:
        await emit_admin_notification(
            db, "tournament_review_stalled", title="大会の確認待ちが放置されています",
            summary=f"「{t['title']}」（{t['public_id']}）で、確認待ちの試合が{REVIEW_ADMIN_MINUTES // 60}時間以上そのままです。",
            payload={"tournament_id": t["id"], "public_id": t["public_id"]},
            target_path="/admin/tournaments?tab=stalled",
            dedupe_key=f"tournament_review_stalled:{t['id']}",
        )
    return count


async def watch_stalled(db: asyncpg.Connection) -> int:
    """進行の止まった開催中の大会に、自動中止を予告し、予告から時間がたったら中止する。予告・中止した数を返す。"""
    rows = await db.fetch(
        f"""
        SELECT t.id, t.stall_warned_at, {LAST_PROGRESS_SQL} AS last_progress
        FROM tournaments t
        WHERE t.status = 'live' AND NOT t.is_deleted
        """
    )
    now = _now()
    count = 0
    for r in rows:
        last = r["last_progress"]
        if last is None:
            continue
        warned = r["stall_warned_at"] if r["stall_warned_at"] and r["stall_warned_at"] >= last else None
        idle = now - last
        try:
            if warned and idle >= datetime.timedelta(hours=STALL_HOURS) and now - warned >= datetime.timedelta(hours=STALL_WARN_HOURS):
                if await _cancel_stalled(db, r["id"]):
                    logger.info(f"大会を進行が止まったため自動で中止しました: tournament_id={r['id']}")
                    await _notify_admin_cancelled(db, r["id"])
                    count += 1
            elif not warned and idle >= datetime.timedelta(hours=STALL_HOURS - STALL_WARN_HOURS):
                await _warn(db, r["id"], idle)
                count += 1
        except TournamentError:
            continue  # 同時に主催者が中止・終了したなど
    return count


async def _still_stalled(db: asyncpg.Connection, t: dict) -> bool:
    """行をロックしたあとに、まだ予告のあとに進行がなく、24時間以上止まっているかを確かめる。"""
    row = await db.fetchrow(
        f"SELECT t.status, t.stall_warned_at, {LAST_PROGRESS_SQL} AS last_progress FROM tournaments t WHERE t.id = $1", t["id"],
    )
    now = _now()
    return bool(
        row and row["status"] == "live" and row["stall_warned_at"] and row["last_progress"]
        and row["stall_warned_at"] >= row["last_progress"]
        and now - row["last_progress"] >= datetime.timedelta(hours=STALL_HOURS)
        and now - row["stall_warned_at"] >= datetime.timedelta(hours=STALL_WARN_HOURS)
    )


async def _cancel_stalled(db: asyncpg.Connection, tournament_id: int) -> bool:
    from app.services import tournament_progress  # 循環 import を避ける
    return await tournament_progress.cancel_tournament(db, tournament_id, actor_user_id=None, reason="stalled",
                                                       still_due=_still_stalled, detail={"hours": STALL_HOURS})


async def _notify_admin_cancelled(db: asyncpg.Connection, tournament_id: int) -> None:
    from app.services.admin_notification_service import emit_admin_notification
    t = await db.fetchrow("SELECT id, public_id, title FROM tournaments WHERE id = $1", tournament_id)
    await emit_admin_notification(
        db, "tournament_stalled_cancelled", title="進行が止まった大会を自動で中止しました",
        summary=f"「{t['title']}」（{t['public_id']}）は{STALL_HOURS}時間進行がなかったため、自動で中止しました。",
        payload={"tournament_id": t["id"], "public_id": t["public_id"]},
        target_path=f"/admin/tournaments?q={t['public_id']}",
    )


async def _warn(db: asyncpg.Connection, tournament_id: int, idle: datetime.timedelta) -> None:
    """主催者と参加者に、自動中止を予告する（大会のチャットにも出す）。"""
    outbox = chat.Outbox()
    async with db.transaction():
        row = await db.fetchrow(
            "UPDATE tournaments SET stall_warned_at = now() WHERE id = $1 AND status = 'live' RETURNING *", tournament_id,
        )
        if row is None:
            return
        t = dict(row)
        hours = int(idle.total_seconds() // 3600)
        params = {"idle": hours, "left": max(STALL_WARN_HOURS, STALL_HOURS - hours)}
        await chat.post_system(db, outbox, await chat.ensure_lobby_post(db, t), "stall_warning", **params)
        entries = await tournament_notify.participant_entry_ids(db, t["id"], ("checked_in",))
        await tournament_notify.notify_entries(db, outbox, t, entries, "tournament_stall_warning", **params)
        await tournament_notify.notify_host(db, outbox, t, "tournament_stall_warning", host=True, **params)
    await outbox.flush()
    logger.info(f"進行が止まった大会に自動中止を予告しました: tournament_id={tournament_id}, idle={hours}h")


async def stalled_list(db: asyncpg.Connection) -> list[dict]:
    """管理画面の「止まっている大会」: 確認待ちが2時間以上放置された大会と、6時間以上進行のない大会。"""
    rows = await db.fetch(
        f"""
        SELECT * FROM (
            SELECT t.id, t.public_id, t.title, t.format, t.host_user_id, u.name AS host_name, t.stall_warned_at,
                   {LAST_PROGRESS_SQL} AS last_progress,
                   (SELECT MIN(m.review_at) FROM tournament_matches m WHERE m.tournament_id = t.id AND m.state = 'review') AS oldest_review,
                   (SELECT COUNT(*) FROM tournament_matches m WHERE m.tournament_id = t.id AND m.state = 'review') AS reviews
            FROM tournaments t
            LEFT JOIN users u ON u.id = t.host_user_id
            WHERE t.status = 'live' AND NOT t.is_deleted
        ) x
        WHERE x.oldest_review <= now() - make_interval(mins => $1) OR x.last_progress <= now() - make_interval(hours => $2)
        ORDER BY x.last_progress
        """,
        REVIEW_ADMIN_MINUTES, STALLED_LIST_HOURS,
    )
    result = []
    for r in rows:
        warned = r["stall_warned_at"] if r["stall_warned_at"] and r["last_progress"] and r["stall_warned_at"] >= r["last_progress"] else None
        result.append(dict(r) | {
            "warned_at": warned,
            "cancel_at": max(r["last_progress"] + datetime.timedelta(hours=STALL_HOURS),
                             warned + datetime.timedelta(hours=STALL_WARN_HOURS)) if warned else None,
        })
    return result


# --- 試合・ロビーの「音信不通ですか？」のヒント -----------------------------------------------------

HINT_WAIT_MINUTES = 5  # 部屋の登録待ちのまま、相手の不在を疑うまで
HINT_IDLE_MINUTES = 10  # 部屋の登録・最後のゲームから、次のゲームを検知できないまま相手の不在を疑うまで
HINT_HOST_MINUTES = 10  # 確認待ちのまま、主催者の不在を疑うまで


def stall_hint(m: dict, *, live: bool, player: bool, staff: bool, forfeit_minutes: int, last_game_at: datetime.datetime | None,
               now: datetime.datetime | None = None) -> str | None:
    """試合・ロビーに出すヒントの種類。player: 相手（参加者）が来ていないかも / host: 主催者が対応していないかも / None。

    出場者（player）と運営（staff。主催者・管理者）だけに出す。主催者の不在は出場者にだけ出す。
    m は tournament_matches の行（state・review_at・forfeit_deadline_at・started_at・room_registered_at を使う）。
    """
    if not live or not (player or staff):
        return None
    now = now or _now()
    state = m["state"]

    def passed(at: datetime.datetime | None, minutes: int) -> bool:
        return at is not None and now - at >= datetime.timedelta(minutes=minutes)

    if state == "review":
        if player and passed(m.get("review_at"), HINT_HOST_MINUTES):
            return "host"
        # 部屋が登録されないまま確認待ちになった試合は、主催者が来ていない参加者を失格にするなどで進める
        return "player" if staff and not player and m.get("review_reason") == "no_room" else None
    if state == "waiting_room":
        # 不戦敗タイマーの始まり（延長した分は後ろにずれる）
        deadline = m.get("forfeit_deadline_at")
        since = deadline - datetime.timedelta(minutes=forfeit_minutes) if deadline else m.get("started_at")
        return "player" if passed(since, HINT_WAIT_MINUTES) else None
    if state == "ready":
        return "player" if passed(m.get("room_registered_at"), HINT_IDLE_MINUTES) else None
    if state == "playing":
        return "player" if passed(last_game_at or m.get("room_registered_at"), HINT_IDLE_MINUTES) else None
    return None


async def hint_context(db: asyncpg.Connection, t: dict, kind: str, *, mention_entries: list[dict], player: bool,
                       lang: str) -> dict:
    """ヒントに出すメンションの相手（エントリーごとのユーザー・主催者）とリンク。ヒントを出すときだけ呼ぶ。

    mention_entries: ボタンで呼びかけるエントリー（{id, name}。トーナメントの相手チーム、主催者には両チーム。SDは出さない）。
    """
    ids = [e["id"] for e in mention_entries]
    users: dict[int, list[dict]] = {i: [] for i in ids}
    if ids:
        for r in await db.fetch(
            """
            SELECT tm.entry_id, u.id, u.name FROM tournament_entry_members tm JOIN users u ON u.id = tm.user_id
            WHERE tm.entry_id = ANY($1::int[]) AND tm.role <> 'sub' AND NOT u.is_invalid
            ORDER BY tm.entry_id, tm.role = 'captain' DESC, tm.id
            """,
            ids,
        ):
            users[r["entry_id"]].append({"id": r["id"], "name": r["name"]})
    host = None
    if player:
        row = await db.fetchrow("SELECT id, name FROM users WHERE id = $1 AND NOT is_invalid", t["host_user_id"])
        host = {"id": row["id"], "name": row["name"]} if row else None
    base = f"/{lang}/tournaments/{t['public_id']}"
    return {
        "kind": kind,
        "player": player,
        "groups": [{"name": e["name"], "users": users[e["id"]]} for e in mention_entries if users[e["id"]]],
        "host": host,
        "report_url": f"{base}?report=tournament_host_absent",
        "manage_url": f"{base}/manage?tab=entries",
        "stall_hours": STALL_HOURS,
    }
