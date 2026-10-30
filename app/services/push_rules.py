"""プッシュ通知の決まりごと（検討メモ notes_private/push_image_plan_2026-10-27.md の 3.5・3.6）。

DB・Redis に触れない部分だけを置く: 通知の種類と設定の対応、送り方（優先度・緊急・まとめる単位）、文面、
送り先（FCM・Web Push）ごとのメッセージの形。積み込みと端末の登録は push_service、送信は push_sender。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

# --- 種類と設定 -------------------------------------------------------------------------------

# 大会の通知の分け方（PD19）
CAT_TN_PROGRESS = "tn_progress"
CAT_TN_CHAT = "tn_chat"
CAT_TN_NOTICE = "tn_notice"
CAT_BOARD = "board"
CAT_ADMIN = "admin"
CAT_NEWS = "news"  # お知らせ（Android の通知チャンネルの id もこれ）
ANNOUNCEMENT_SETTING_KEY = "push_announcement_enabled"

TOURNAMENT_SETTING_KEYS = {
    CAT_TN_PROGRESS: "push_tournament_progress_enabled",
    CAT_TN_CHAT: "push_tournament_chat_enabled",
    CAT_TN_NOTICE: "push_tournament_notice_enabled",
}

# 掲示板の通知の種類 → (プッシュの設定, アプリ内の通知の設定)。アプリ内の設定がないものは None（いつも作られる）
BOARD_SETTING_KEYS = {
    "post_like": ("push_post_like_enabled", "notification_post_like_enabled"),
    "own_post_message": ("push_own_post_message_enabled", "notification_own_post_message_enabled"),
    "participated_thread_message": ("push_participated_thread_message_enabled", "notification_participated_thread_message_enabled"),
    "subscribed_thread_message": ("push_subscribed_thread_message_enabled", None),
    "message_mention": ("push_message_mention_enabled", "notification_message_mention_enabled"),
    "message_reply": ("push_message_reply_enabled", "notification_message_reply_enabled"),
    "message_reaction": ("push_message_reaction_enabled", "notification_message_reaction_enabled"),
    "token_gift": ("push_token_gift_enabled", "notification_token_gift_enabled"),
}
BOARD_WARNING = "admin_warning"  # 管理者からの警告。設定はなく、いつも届ける

PUSH_SETTING_KEYS = (tuple(TOURNAMENT_SETTING_KEYS.values()) + tuple(push for push, _ in BOARD_SETTING_KEYS.values())
                     + (ANNOUNCEMENT_SETTING_KEY,))
PUSH_SETTING_DEFAULTS = {
    **{key: True for key in TOURNAMENT_SETTING_KEYS.values()},
    "push_post_like_enabled": False,
    "push_own_post_message_enabled": True,
    "push_participated_thread_message_enabled": False,
    "push_subscribed_thread_message_enabled": True,
    "push_message_mention_enabled": True,
    "push_message_reply_enabled": True,
    "push_message_reaction_enabled": False,
    "push_token_gift_enabled": True,
    ANNOUNCEMENT_SETTING_KEY: False,  # プレゼント企画などの宣伝を含むので、本人がオンにしたときだけ（AD25）
}
ADMIN_PUSH_LEVELS = (0, 20, 30)

# 遅れると不戦敗・失格・自動確定につながる大会の通知（iOS の Time Sensitive。PD12・3.6）
TIME_SENSITIVE_KINDS = frozenset({
    "match_ready", "room_ready", "checkin_open", "result_reported", "proposal", "result_confirming",
    "match_review", "qualifier_tie",
})
# 時間がたつと意味のない大会の通知は、届かないまま15分たったら捨てる（端末がオフラインのときなど）
SHORT_TTL_KINDS = frozenset({"match_ready", "room_ready", "result_reported", "proposal", "result_confirming"})
SHORT_TTL_SECONDS = 15 * 60
DEFAULT_TTL_SECONDS = 24 * 3600

# まとめて送る単位（3.6）
GROUP_THREAD = "thread"  # スレッドの一般メッセージ: 開くまで1回、1時間ごとに件数で出し直す
GROUP_LIKE = "like"
GROUP_REACTION = "reaction"
GROUP_PAIR = "pair"  # 返信・メンション: 同じ人から10分に3回まで、受け取りは1時間に30件まで
THREAD_MESSAGE_TYPES = frozenset({"own_post_message", "participated_thread_message", "subscribed_thread_message"})
GROUP_REPEAT_SECONDS = 3600
PAIR_WINDOW_SECONDS = 600
PAIR_LIMIT = 3
RECIPIENT_WINDOW_SECONDS = 3600
RECIPIENT_LIMIT = 30
ADMIN_WINDOW_SECONDS = 600

# 積んでから送るまでに時間がたちすぎたものは送らない（worker が止まっていたときなど）
JOB_MAX_AGE_SECONDS = 6 * 3600


def is_fresh(job: dict, now: float) -> bool:
    """積んでから時間がたちすぎていないか。試合の開始などは15分、それ以外は6時間を過ぎたら送らない。"""
    age = now - float(job.get("at") or now)
    if job.get("src") == "tn" and (job.get("notice") or {}).get("kind") in SHORT_TTL_KINDS:
        return age <= SHORT_TTL_SECONDS
    return age <= JOB_MAX_AGE_SECONDS


def tournament_category(kind: str, everyone: bool = False) -> str:
    if kind == "notice_posted" or (kind == "chat_mention" and everyone):
        return CAT_TN_NOTICE
    if kind in ("chat_reply", "chat_mention"):
        return CAT_TN_CHAT
    return CAT_TN_PROGRESS


def board_group(notification_type: str) -> str | None:
    if notification_type in THREAD_MESSAGE_TYPES:
        return GROUP_THREAD
    if notification_type == "post_like":
        return GROUP_LIKE
    if notification_type == "message_reaction":
        return GROUP_REACTION
    if notification_type in ("message_reply", "message_mention"):
        return GROUP_PAIR
    return None


def board_enabled(notification_type: str, settings: dict) -> bool:
    """掲示板の通知をプッシュでも届けるか。アプリ内の通知がオフの種類は届けない。"""
    if notification_type == BOARD_WARNING:
        return True
    keys = BOARD_SETTING_KEYS.get(notification_type)
    if not keys:
        return False
    push_key, in_app_key = keys
    if in_app_key and not settings.get(in_app_key, True):
        return False
    return bool(settings.get(push_key, PUSH_SETTING_DEFAULTS[push_key]))


def tournament_enabled(category: str, settings: dict) -> bool:
    key = TOURNAMENT_SETTING_KEYS[category]
    return bool(settings.get(key, PUSH_SETTING_DEFAULTS[key]))


def group_decision(at: float | None, pending: int, now: float, repeat: int = GROUP_REPEAT_SECONDS) -> tuple[bool, int]:
    """まとめる単位ごとの送るかどうか。at: 前に送った時刻（なければ None）、pending: その後に来て送っていない件数（今回の分を含む）。

    (送るか, 文面に出す件数) を返す。件数が 1 なら、まとめずにそのまま送る。
    """
    if at is None:
        return True, 1
    if now - at >= repeat and pending > 0:
        return True, pending
    return False, 0


# --- 文面 -------------------------------------------------------------------------------------

POST_TYPE_LABELS = {
    "ja": {"team": "チーム募集", "friend": "フレンド募集", "club": "クラブ募集", "general": "なんでも掲示板",
           "theme": "テーマ掲示板", "brawler_guide": "テーマ掲示板"},
    "en": {"team": "team recruitment", "friend": "friend recruitment", "club": "club recruitment",
           "general": "general board", "theme": "theme board", "brawler_guide": "theme board"},
}
THEME_POST_TYPES = frozenset({"theme", "brawler_guide"})
BODY_MAX_CHARS = 120
ICON_PATH = "/static/images/ui/logo.png"
SITE_ORIGIN = "https://brawlinsights.com"  # FCM の画像は絶対 URL（アプリが読む）


@dataclass
class Push:
    """1件のプッシュ通知（端末の言語で作ったもの）。"""
    title: str
    body: str
    url: str  # 言語つきのパス（例 /ja/boards/chat/1）
    tag: str  # 端末で置き換える単位（同じ tag の通知は新しいもので置き換える）
    channel: str  # Android の通知チャンネル（アプリが作る）
    high: bool = False  # すぐ届ける（大会の進行・警告）
    time_sensitive: bool = False
    ttl: int = DEFAULT_TTL_SECONDS
    kind: str = ""
    image: str = ""  # 大きい画像（サイト内のパス。Chrome・Android などで出る）


def clip(text: str | None, limit: int = BODY_MAX_CHARS) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _label(post_type: str | None, lang: str) -> str:
    labels = POST_TYPE_LABELS["ja" if lang == "ja" else "en"]
    return labels.get(post_type or "", labels["general"])


def board_title(notification_type: str, lang: str, *, actor: str, post_type: str | None,
                count: int = 1, amount: int | None = None) -> str:
    """アプリ内の通知の見出しと同じ言い回し（太字なし）。count が 2 以上ならまとめた文面。"""
    ja = lang == "ja"
    label = _label(post_type, lang)
    if count > 1:
        if notification_type == "post_like":
            return f"あなたの投稿に新しいいいねが{count}件あります" if ja else f"Your post got {count} new likes"
        if notification_type == "message_reaction":
            return (f"あなたのメッセージに新しいリアクションが{count}件あります" if ja
                    else f"Your message got {count} new reactions")
        return (f"{label}のスレッドに新しいメッセージが{count}件あります" if ja
                else f"{count} new messages in a {label} thread")
    if notification_type == "message_reply":
        return f"{actor}さんがあなたのメッセージにリプライしました" if ja else f"{actor} replied to your message"
    if notification_type == "message_mention":
        return (f"{actor}さんが{label}のスレッドであなたをメンションしました" if ja
                else f"{actor} mentioned you in a {label} thread")
    if notification_type == "own_post_message":
        return f"{actor}さんがあなたの{label}の投稿に返信しました" if ja else f"{actor} replied to your {label} post"
    if notification_type == "subscribed_thread_message":
        return (f"{actor}さんが通知オンにした{label}のスレッドにメッセージを送りました" if ja
                else f"{actor} messaged in a {label} thread you turned notifications on for")
    if notification_type == "participated_thread_message":
        if post_type in THEME_POST_TYPES:
            return (f"{actor}さんがあなたがメッセージを送ったテーマ掲示板に返信しました" if ja
                    else f"{actor} replied to the theme board you messaged in")
        return (f"{actor}さんがあなたがメッセージを送った{label}の投稿に返信しました" if ja
                else f"{actor} replied to a {label} post you messaged in")
    if notification_type == "post_like":
        return f"{actor}さんがあなたの投稿をいいねしました" if ja else f"{actor} liked your post"
    if notification_type == "message_reaction":
        return f"{actor}さんがあなたのメッセージにリアクションしました" if ja else f"{actor} reacted to your message"
    if notification_type == "token_gift":
        amount_text = f"{int(amount or 0):,}"
        return f"{actor}さんがあなたに{amount_text}トークンを贈りました" if ja else f"{actor} gifted you {amount_text} tokens"
    if notification_type == BOARD_WARNING:
        return ("管理者から警告が届いています。内容を確認してください" if ja
                else "You received a warning from an administrator. Please review it")
    return ""


def board_url(lang: str, notification_type: str, post_id: int | None, message_id: int | None) -> str:
    if notification_type == "post_like":
        return f"/{lang}/boards/general?tab=own"
    if not post_id:
        return f"/{lang}/boards/notifications"
    url = f"/{lang}/boards/chat/{int(post_id)}?from=notifications"
    return f"{url}&message_id={int(message_id)}" if message_id else url


def board_push(notification_type: str, lang: str, *, actor: str, post_type: str | None, post_id: int | None,
               message_id: int | None, text: str = "", count: int = 1, amount: int | None = None) -> Push:
    """掲示板の通知。text はメッセージの本文（NG ワードを伏せたもの）。警告は本文を出さない。"""
    group = board_group(notification_type)
    if group == GROUP_THREAD:
        tag = f"bt{post_id}"
    elif group == GROUP_LIKE:
        tag = f"bl{post_id}"
    elif group == GROUP_REACTION:
        tag = f"br{message_id}"
    else:
        tag = f"bm{message_id or post_id or 0}"
    warning = notification_type == BOARD_WARNING
    return Push(
        title=board_title(notification_type, lang, actor=actor, post_type=post_type, count=count, amount=amount),
        body="" if warning else clip(text),
        url=board_url(lang, notification_type, post_id, message_id),
        tag=tag,
        channel="board",
        high=warning,
        kind=notification_type,
    )


def pair_summary_push(lang: str, *, actor: str, url: str, tag: str, channel: str) -> Push:
    """同じ人からの返信・メンションが続いたときの、まとめの1回。"""
    title = (f"{actor}さんから、ほかにもリプライ・メンションが届いています" if lang == "ja"
             else f"{actor} sent you more replies and mentions")
    return Push(title=title, body="", url=url, tag=tag, channel=channel, kind="pair_summary")


def tournament_push(notice: dict, lang: str) -> Push:
    """大会の通知（tournament_notify.notify が作るもの）。本文は通知の文面、見出しは大会名。"""
    kind = notice.get("kind") or ""
    text = notice.get("text") or {}
    category = notice.get("cat") or tournament_category(kind)
    return Push(
        title=clip(notice.get("t") or "", 60),
        body=clip(text.get("en" if lang == "en" else "ja") or text.get("ja") or "", 200),
        url=f"/{lang}/{notice.get('path') or 'tournaments'}",
        tag=f"tn{notice.get('id') or ''}",
        channel="tn_progress" if category == CAT_TN_PROGRESS else "tournament",
        high=category == CAT_TN_PROGRESS,
        time_sensitive=kind in TIME_SENSITIVE_KINDS,
        ttl=SHORT_TTL_SECONDS if kind in SHORT_TTL_KINDS else DEFAULT_TTL_SECONDS,
        kind=kind,
    )


def admin_push(lang: str, *, event_key: str, title: str, summary: str, target_path: str, more: int = 0) -> Push:
    """管理者通知。more: 前回からまとめて送らなかった件数。"""
    path = target_path or "/admin"
    url = path if path.startswith("http") else f"/{lang}{path}"
    if more:
        title = f"{title}（ほか{more}件）" if lang == "ja" else f"{title} (+{more} more)"
    return Push(title=clip(title, 80), body=clip(summary, 160), url=url, tag=f"ad{event_key}",
                channel="admin", kind=event_key)


ANNOUNCEMENT_TITLE_MAX = 60
ANNOUNCEMENT_BODY_MAX = 120


def announcement_push(lang: str, *, post_id: int, title: str, body: str, image: str = "") -> Push:
    """お知らせ。タップで個別ページ。同じお知らせの通知は置き換える。"""
    return Push(
        title=clip(title, ANNOUNCEMENT_TITLE_MAX) or ("お知らせ" if lang == "ja" else "News"),
        body=clip(body, ANNOUNCEMENT_BODY_MAX),
        url=f"/{lang}/help/announcements/{int(post_id)}",
        tag=f"news{int(post_id)}",
        channel=CAT_NEWS,
        kind="announcement",
        image=image,
    )


# --- 送り先ごとの形 ---------------------------------------------------------------------------

# Web Push の送り先として受け付けるサービス（登録された URL へサーバーから送るので、ほかのホストは受け付けない）
WEB_PUSH_HOST_SUFFIXES = (
    "fcm.googleapis.com",
    "android.googleapis.com",
    "push.services.mozilla.com",
    "push.apple.com",
    "notify.windows.com",
)


def is_allowed_web_endpoint(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or len(url) > 1024:
        return False
    return any(host == suffix or host.endswith("." + suffix) for suffix in WEB_PUSH_HOST_SUFFIXES)


def web_payload(push: Push) -> dict:
    """Service Worker（sw.js）が受け取る形。"""
    payload = {"title": push.title, "body": push.body, "url": push.url, "tag": push.tag, "icon": ICON_PATH}
    if push.image:
        payload["image"] = push.image
    return payload


def fcm_message(token: str, push: Push, badge: int | None = None, *, now: float) -> dict:
    """FCM HTTP v1 の message。iOS は APNs の設定、Android はチャンネル・tag で、同じ単位の通知を置き換える。"""
    android_notification = {"channel_id": push.channel, "tag": push.tag[:64]}
    aps: dict = {
        "alert": {"title": push.title, "body": push.body},
        "sound": "default",
        "thread-id": push.channel,
        "interruption-level": "time-sensitive" if push.time_sensitive else "active",
    }
    if badge is not None:
        android_notification["notification_count"] = badge
        aps["badge"] = badge
    notification = {"title": push.title, "body": push.body}
    if push.image:
        notification["image"] = SITE_ORIGIN + push.image  # iOS で出すにはアプリの Notification Service Extension が要る
    return {
        "token": token,
        "notification": notification,
        "data": {"url": push.url, "kind": push.kind, "tag": push.tag},
        "android": {
            "priority": "high" if push.high else "normal",
            "ttl": f"{push.ttl}s",
            "notification": android_notification,
        },
        "apns": {
            "headers": {
                "apns-priority": "10" if push.high else "5",
                "apns-collapse-id": push.tag[:64],
                "apns-expiration": str(int(now) + push.ttl),
            },
            "payload": {"aps": aps},
        },
    }
