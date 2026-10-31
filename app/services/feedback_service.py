"""フィードバック（検討メモ notes_private/feedback_plan_2026-10-31.md）。

送信時の環境（3章）: 端末の種類・アプリの版・OS・ブラウザ・端末名・表示の言語・サイトの版を、リクエストからサーバーで作る。
送信画面の「一緒に送信される情報」も同じ処理で作るので、表示と保存が食い違わない。
添付の画像（5章）は media（kind = feedback。公開しない）。上げるのは送信の前（/{lang}/media/upload?kind=feedback）で、
送信のときに media の id を受け取って付ける。添付されなかった画像は1日で、チェック済みのものは180日で消す（worker）。
個別返信（7章）は feedback_replies。運営から本人への一方向で、本人には「Brawl Insights 運営」として見せる。
公開の回答（8章）は feedbacks の掲載用の本文・回答。選んだものから「フィードバックへの回答」のお知らせの下書きを作り、
お知らせが公開されたら（予約なら公開の時刻に。worker が1分ごとに見る）掲載済みにして本人に通知する。
"""
from __future__ import annotations

import datetime
import html
import re
from typing import Any

import asyncpg
from fastapi import Request
from user_agents import parse as parse_ua

from app.core.cache import get_cache, set_cache
from app.core.logger import logger
from app.core.templating import version_at_least
from app.services import media_service, push_rules, push_service
from app.utils.utils import format_display_datetime
# [この部分は公開用リポジトリでは非公開にされています]

FEEDBACK_TYPES = ("inquiry", "suggestion", "bug_report", "message")
TYPE_LABELS = {
    "inquiry": ("お問い合わせ", "Inquiry"),
    "suggestion": ("ご意見・ご要望", "Suggestion/Request"),
    "bug_report": ("不具合の報告", "Bug Report"),
    "message": ("メッセージ", "Message"),
}
COMMENT_MIN_CHARS = 5  # 「あ」などの意味のない送信を防ぐ
COMMENT_MAX_CHARS = 5000
MAX_IMAGES = 3
UA_MAX_CHARS = 500
COOLDOWN_SECONDS = 180
IMAGE_RETENTION = datetime.timedelta(days=180)  # チェック済みにしてから添付の画像を消すまで
# iOS のアプリはカメラの利用目的の文言（NSCameraUsageDescription）が入った版から、ファイルの選択を出す
# （それより前の版は、選択のメニューの「写真を撮る」でアプリが落ちる。5章）
IOS_APP_FILE_PICKER_MIN_VERSION = "15.3"

_OS_NAMES = {"Mac OS X": "macOS", "Chrome OS": "ChromeOS"}
# UA の版が固定されている OS（Chrome などは macOS を 10.15.7、Windows 11 を 10 と送る）は版を出さない
_FROZEN_OS_VERSION = {"macOS", "Windows"}
_BROWSER_NAMES = {
    "Mobile Safari": "Safari",
    "Mobile Safari UI/WKWebView": "WebView",
    "Chrome Mobile": "Chrome",
    "Chrome Mobile iOS": "Chrome",
    "Chrome Mobile WebView": "WebView",
    "Firefox Mobile": "Firefox",
    "Firefox iOS": "Firefox",
    "Edge Mobile": "Edge",
    "Opera Mobile": "Opera",
}
# iOS 26 から、Safari・アプリの WebView の UA の OS の版は 18.x に固定されている（iPadOS 27 でも「18_7」など）。
# 18.6 以上は本当の 18.x と見分けられないので「18.7以降」のように出す
_IOS_FROZEN_FROM = (18, 6)
_TWO_PART_BROWSERS = {"Safari"}  # 版を「18.1」の形で出す（ほかは「141」のように大きい番号だけ）


class FeedbackError(ValueError):
    def __init__(self, message_ja: str, message_en: str):
        super().__init__(message_ja)
        self.message_ja = message_ja
        self.message_en = message_en

    def message(self, lang: str) -> str:
        return self.message_ja if lang == "ja" else self.message_en


# --- 送信時の環境 --------------------------------------------------------------------------
def _version(version_tuple: tuple, parts: int) -> str:
    numbers = [str(v) for v in version_tuple[:parts] if isinstance(v, int)]
    return ".".join(numbers)


def client_env(request: Request, lang: str, *, site_version: str) -> dict[str, Any]:
    """リクエストから環境を作る。platform・ui_lang は feedbacks の列に、それ以外は client_env に入れる。"""
    ua_text = (request.headers.get("user-agent") or "")[:UA_MAX_CHARS]
    ua = parse_ua(ua_text)
    platform = getattr(request.state, "platform", "web") or "web"
    os_name = _OS_NAMES.get(ua.os.family, ua.os.family)
    os_version = "" if os_name in _FROZEN_OS_VERSION else _version(ua.os.version, 2)
    if platform == "app" and os_name == "macOS":
        os_name = "iPadOS"  # iPad のアプリは Mac の UA を名乗る（版は分からない）
    os_version_frozen = False
    device = ""
    if os_name == "iOS":
        device = ua.device.family if ua.device.family in ("iPhone", "iPad", "iPod") else ""
        if device == "iPad":
            os_name = "iPadOS"
        numbers = tuple(v for v in ua.os.version[:2] if isinstance(v, int))
        os_version_frozen = len(numbers) == 2 and numbers >= _IOS_FROZEN_FROM
    elif os_name == "Android":
        family = ua.device.family or ""
        if family == "K":
            os_version = ""  # 版と機種を伏せた UA（Chrome の「Android 10; K」）
        elif family != "Other" and not family.startswith("Generic"):
            device = family
    browser = _BROWSER_NAMES.get(ua.browser.family, ua.browser.family)
    browser_version = _version(ua.browser.version, 2 if browser in _TWO_PART_BROWSERS else 1)
    return {
        "platform": platform,
        "ui_lang": "en" if lang == "en" else "ja",
        "app_version": getattr(request.state, "app_client_version", None) or "",
        "os": "" if os_name == "Other" else os_name,
        "os_version": os_version,
        "os_version_frozen": os_version_frozen,  # UA の版は下限（本当の版はそれ以降のどれか）
        "browser": "" if browser == "Other" else browser,
        "browser_version": browser_version if browser != "Other" else "",
        "device": device,
        "site_version": site_version,
        "ua": ua_text,
    }


def env_parts(env: dict[str, Any] | None, lang: str, *, with_site: bool = False) -> list[str]:
    """[「iOSアプリ 15.0」, 「iOS 18.1」, 「iPhone」, 「日本語」] の形。記録のない古いフィードバックは空。"""
    if not env or not env.get("platform"):
        return []
    ja = lang == "ja"
    platform = env["platform"]
    app_version = env.get("app_version") or ""
    if platform == "web":
        head = "Web版" if ja else "Web"
    else:
        name = {"ios": ("iOSアプリ", "iOS app"), "android": ("Androidアプリ", "Android app")}.get(platform, ("アプリ", "App"))
        head = (name[0] if ja else name[1]) + (f" {app_version}" if app_version else "")
    parts = [head]
    if env.get("os"):
        os_version = env.get("os_version") or ""
        if os_version and env.get("os_version_frozen"):
            os_version += "以降" if ja else "+"
        parts.append(" ".join(filter(None, [env["os"], os_version])))
    if platform == "web" and env.get("browser"):
        parts.append(" ".join(filter(None, [env["browser"], env.get("browser_version")])))
    if env.get("device"):
        parts.append(env["device"])
    ui_lang = env.get("ui_lang")
    if ui_lang:
        parts.append({"ja": ("日本語", "Japanese"), "en": ("英語", "English")}.get(ui_lang, (ui_lang, ui_lang))[0 if ja else 1])
    if with_site and env.get("site_version"):
        parts.append(("サイト " if ja else "Site ") + env["site_version"])
    return parts


def env_label(env: dict[str, Any] | None, lang: str, *, with_site: bool = False) -> str:
    """「iOSアプリ 15.0・iOS 18.1・iPhone・日本語」の形。"""
    return ("・" if lang == "ja" else " · ").join(env_parts(env, lang, with_site=with_site))


def row_env(row: asyncpg.Record | dict[str, Any]) -> dict[str, Any] | None:
    """feedbacks の行から env_label に渡す形にする。"""
    if not row["platform"]:
        return None
    return {**(row["client_env"] or {}), "platform": row["platform"], "ui_lang": row["ui_lang"]}


def can_pick_files(platform: str, app_version: str | None) -> bool:
    """ファイルの選択（写真ライブラリ・カメラ）を出せるか。貼り付け・ドロップはどこでもできる。"""
    if platform in ("ios", "app"):  # iPad のアプリは UA に iPad が入らず app になることがある
        return version_at_least(app_version, IOS_APP_FILE_PICKER_MIN_VERSION)
    return True


def form_context(request: Request, lang: str, *, site_version: str) -> dict[str, Any]:
    """送信画面に渡すもの。"""
    env = client_env(request, lang, site_version=site_version)
    return {
        "env_label": env_label(env, lang),
        "can_pick_files": can_pick_files(env["platform"], env["app_version"]),
        "max_images": MAX_IMAGES,
        "min_chars": COMMENT_MIN_CHARS,
        "max_chars": COMMENT_MAX_CHARS,
    }


def type_label(feedback_type: str, lang: str) -> str:
    labels = TYPE_LABELS.get(feedback_type)
    return (labels[0] if lang == "ja" else labels[1]) if labels else feedback_type


# --- 送信 ----------------------------------------------------------------------------------
async def create_feedback(db: asyncpg.Connection, *, user_id: int, feedback_type: str, comment: str,
                          env: dict[str, Any], media_ids: list[int], reply_to_id: int | None = None) -> int:
    """フィードバックを保存し、添付の画像を付ける。投稿禁止中のユーザーも送れる。失敗したら FeedbackError。
    reply_to_id は履歴の「このフィードバックについて追加で送る」の元（本人のものでなければ付けない）。"""
    comment = (comment or "").strip()
    if feedback_type not in FEEDBACK_TYPES or not comment:
        raise FeedbackError("フィードバックの種類を選び、内容を入力してください。", "Please select a feedback type and enter a comment.")
    if len(comment) < COMMENT_MIN_CHARS:
        raise FeedbackError("フィードバックの内容が短すぎるため、送信できません。", "Your feedback is too short to send.")
    if len(comment) > COMMENT_MAX_CHARS:
        raise FeedbackError(
            f"内容は {COMMENT_MAX_CHARS:,} 文字までです。", f"Comments can be up to {COMMENT_MAX_CHARS:,} characters.",
        )
    media_ids = list(dict.fromkeys(int(i) for i in media_ids))
    if len(media_ids) > MAX_IMAGES:
        raise FeedbackError(f"画像は {MAX_IMAGES} 枚までです。", f"You can attach up to {MAX_IMAGES} images.")

    # [この部分は公開用リポジトリでは非公開にされています]

    client = {key: value for key, value in env.items() if key not in ("platform", "ui_lang")}
    async with db.transaction():
        if reply_to_id is not None:
            reply_to_id = await db.fetchval("SELECT id FROM feedbacks WHERE id = $1 AND user_id = $2", reply_to_id, user_id)
        feedback_id = await db.fetchval(
            """
            INSERT INTO feedbacks (user_id, datetime, feedback_type, comment, platform, ui_lang, client_env, reply_to_id)
            VALUES ($1, now(), $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            user_id, feedback_type, comment, env.get("platform"), env.get("ui_lang"), client, reply_to_id,
        )
        attached = await media_service.attach(
            db, "feedback", media_ids, owner_user_id=user_id, ref_type="feedback", ref_id=str(feedback_id),
        )
        if len(attached) != len(media_ids):
            # 1日たって消えた・別の人の画像など。本文を失わないよう、送信はやめて付け直してもらう
            raise FeedbackError(
                "添付の画像の一部が見つかりませんでした。画像を付け直してから、もう一度送信してください。",
                "Some attached images could not be found. Please attach them again and resend.",
            )

    # [この部分は公開用リポジトリでは非公開にされています]
    logger.info(
        f"新しいフィードバック (ID: {feedback_id}) ユーザーID: {user_id} タイプ: {feedback_type} "
        f"画像: {len(attached)}枚 環境: {env_label(env, 'ja')} 本文: {comment}"
    )
    # [この部分は公開用リポジトリでは非公開にされています]
    return feedback_id


# --- 管理画面 --------------------------------------------------------------------------------
async def images_for(db: asyncpg.Connection, feedback_ids: list[int], *, lang: str = "ja") -> dict[int, list[dict[str, Any]]]:
    """フィードバックごとの添付の画像（消したものは removed: True。並びは添付した順）。"""
    if not feedback_ids:
        return {}
    rows = await db.fetch(
        """
        SELECT id, key, kind, status, variants, width, height, bytes, name, created_at, ref_id
        FROM media
        WHERE ref_type = 'feedback' AND ref_id = ANY($1::text[])
        ORDER BY id
        """,
        [str(i) for i in feedback_ids],
    )
    result: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        item = {"id": row["id"], "removed": True} if row["status"] != "active" else {
            **media_service.to_item(row, lang=lang), "removed": False,
        }
        result.setdefault(int(row["ref_id"]), []).append(item)
    return result


async def set_checked(db: asyncpg.Connection, feedback_id: int, checked: bool, *, by_user_id: int) -> bool:
    """チェックを付ける・外す。変えたら True。チェックした日時は添付の画像の保持の起点になる。"""
    result = await db.execute(
        """
        UPDATE feedbacks
        SET is_checked = $2,
            checked_at = CASE WHEN $2 THEN now() ELSE NULL END,
            checked_by = CASE WHEN $2 THEN $3::int ELSE NULL END
        WHERE id = $1 AND is_checked IS DISTINCT FROM $2
        """,
        feedback_id, checked, by_user_id,
    )
    return result.endswith(" 1")


# --- 片付け（worker） --------------------------------------------------------------------------
async def cleanup_images(db: asyncpg.Connection, *, limit: int = 500) -> dict[str, int]:
    """添付されなかった画像（1日）と、チェック済みにしてから180日たったフィードバックの画像を消す。"""
    unattached = await media_service.cleanup_unattached(db, "feedback", limit=limit)
    rows = await db.fetch(
        """
        SELECT m.id, m.kind, m.key
        FROM media m
        JOIN feedbacks f ON f.id::text = m.ref_id
        WHERE m.kind = 'feedback' AND m.ref_type = 'feedback' AND m.status = 'active'
          AND f.is_checked AND f.checked_at < now() - $1::interval
        ORDER BY m.id
        LIMIT $2
        """,
        IMAGE_RETENTION, limit,
    )
    expired = await media_service.expire_files(db, rows)
    return {"unattached": unattached, "expired": expired}


# --- 文字だけの入力 -------------------------------------------------------------------------
REPLY_MAX_CHARS = 2000
PUBLIC_TEXT_MAX_CHARS = 1000
ANSWER_MAX_CHARS = 3000
ANSWER_STATES = ("none", "ready", "draft", "published")
ANSWER_STATE_LABELS = {"none": "なし", "ready": "掲載待ち", "draft": "お知らせの下書き", "published": "掲載済み"}
_URL_RE = re.compile(r"https?://[^\s<>\"'）」』】]+")


def clean_text(value: Any, max_chars: int) -> str:
    """改行をそろえ、前後と行末の空白を除き、3行以上の空行を詰める。"""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n")).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:max_chars]


def linkify(text: str) -> str:
    """文字を HTML に直し、URL をリンクにする（本人の履歴・お知らせの回答）。"""
    out = []
    last = 0
    for match in _URL_RE.finditer(text):
        out.append(html.escape(text[last:match.start()], quote=False))
        url = html.escape(match.group(0))
        out.append(f'<a href="{url}">{url}</a>')
        last = match.end()
    out.append(html.escape(text[last:], quote=False))
    return "".join(out)


# --- 個別返信 --------------------------------------------------------------------------------
async def _push_langs_payload(make) -> dict[str, push_rules.Push]:
    """端末の言語ごとの通知（worker が端末の言語で選ぶ）。"""
    return {lang: make(lang) for lang in ("ja", "en")}


async def add_reply(db: asyncpg.Connection, feedback_id: int, body: str, *, admin_user_id: int) -> dict[str, Any]:
    """本人に個別返信を送る。チェック済みにして、アプリ内の通知とプッシュ（設定がオンなら）を届ける。"""
    from app.services.notification_service import NOTIFICATION_TYPE_FEEDBACK_REPLY, invalidate_notification_cache

    body = clean_text(body, REPLY_MAX_CHARS)
    if not body:
        raise FeedbackError("返信の内容を入力してください。", "Please enter a reply.")
    async with db.transaction():
        feedback = await db.fetchrow("SELECT id, user_id, is_checked FROM feedbacks WHERE id = $1 FOR UPDATE", feedback_id)
        if feedback is None:
            raise FeedbackError("フィードバックが見つかりません。", "Feedback not found.")
        reply = await db.fetchrow(
            "INSERT INTO feedback_replies (feedback_id, body, created_by) VALUES ($1, $2, $3) RETURNING *",
            feedback_id, body, admin_user_id,
        )
        checked = await set_checked(db, feedback_id, True, by_user_id=admin_user_id)
        # actor は本人（送った管理者の名前は出さず、ブロックの対象にもしない）
        await db.execute(
            """
            INSERT INTO board_notifications (recipient_user_id, notification_type, actor_user_id, feedback_id)
            VALUES ($1, $2, $1, $3)
            """,
            feedback["user_id"], NOTIFICATION_TYPE_FEEDBACK_REPLY, feedback_id,
        )
    await invalidate_notification_cache(feedback["user_id"])
    await push_service.enqueue_direct(
        feedback["user_id"],
        await _push_langs_payload(lambda lang: push_rules.feedback_reply_push(lang, feedback_id=feedback_id, text=body)),
        setting=push_rules.FEEDBACK_SETTING_KEY,
    )
    logger.info(f"フィードバック (ID: {feedback_id}) に個別返信 (ID: {reply['id']}) を送りました（管理者 ID: {admin_user_id}）")
    return {"reply": dict(reply), "checked": checked}


async def withdraw_reply(db: asyncpg.Connection, feedback_id: int, reply_id: int, *, admin_user_id: int) -> bool:
    """取り消す（本人から見えなくする）。届いた通知は残る（通知には返信の文面を出していない）。"""
    result = await db.execute(
        """
        UPDATE feedback_replies SET withdrawn_at = now(), withdrawn_by = $3
        WHERE id = $1 AND feedback_id = $2 AND withdrawn_at IS NULL
        """,
        reply_id, feedback_id, admin_user_id,
    )
    changed = result.endswith(" 1")
    if changed:
        logger.info(f"フィードバック (ID: {feedback_id}) の個別返信 (ID: {reply_id}) を取り消しました（管理者 ID: {admin_user_id}）")
    return changed


# --- 公開の回答 ------------------------------------------------------------------------------
def clean_answer_input(data: dict[str, Any]) -> dict[str, str]:
    out = {
        "public_text_ja": clean_text(data.get("public_text_ja"), PUBLIC_TEXT_MAX_CHARS),
        "public_text_en": clean_text(data.get("public_text_en"), PUBLIC_TEXT_MAX_CHARS),
        "answer_ja": clean_text(data.get("answer_ja"), ANSWER_MAX_CHARS),
        "answer_en": clean_text(data.get("answer_en"), ANSWER_MAX_CHARS),
    }
    for lang, label in (("ja", "日本語"), ("en", "英語")):
        if bool(out[f"public_text_{lang}"]) != bool(out[f"answer_{lang}"]):
            raise FeedbackError(f"{label}は、掲載用の本文と回答の両方を入力してください（載せないなら両方を空に）。", "")
    if out["public_text_en"] and not out["public_text_ja"]:
        raise FeedbackError("日本語の掲載用の本文と回答を入力してください（英語だけでは載せられません）。", "")
    return out


async def save_answer(db: asyncpg.Connection, feedback_id: int, data: dict[str, Any], *, admin_user_id: int) -> dict[str, Any]:
    """掲載用の本文・回答を保存する。書けば「掲載待ち」、空にすれば「なし」。お知らせに入れた後は、空にはできない。"""
    values = clean_answer_input(data)
    has_answer = bool(values["answer_ja"])
    async with db.transaction():
        current = await db.fetchrow("SELECT id, answer_state FROM feedbacks WHERE id = $1 FOR UPDATE", feedback_id)
        if current is None:
            raise FeedbackError("フィードバックが見つかりません。", "Feedback not found.")
        state = current["answer_state"]
        if state in ("draft", "published") and not has_answer:
            raise FeedbackError("お知らせに入れた回答は空にできません。お知らせを削除すると、掲載待ちに戻ります。", "")
        if state in ("none", "ready"):
            state = "ready" if has_answer else "none"
        row = await db.fetchrow(
            """
            UPDATE feedbacks SET public_text_ja = $2, public_text_en = $3, answer_ja = $4, answer_en = $5,
                answer_state = $6, answer_updated_at = now(), answer_updated_by = $7
            WHERE id = $1 RETURNING *
            """,
            feedback_id, values["public_text_ja"], values["public_text_en"], values["answer_ja"], values["answer_en"],
            state, admin_user_id,
        )
    logger.info(f"フィードバック (ID: {feedback_id}) の公開の回答を保存しました（{state}、管理者 ID: {admin_user_id}）")
    return dict(row)


FEEDBACK_TEMPLATE_ROLE = "feedback_answers"
ANNOUNCEMENT_SOURCE_TYPE = "feedback_answers"
TEMPLATE_MARKER_TEXT = "［ここに回答の一覧が入ります］"
_MARKER_RE = re.compile(r"<p>\s*" + re.escape(TEMPLATE_MARKER_TEXT) + r"\s*</p>")
MAX_ITEMS_PER_ANNOUNCEMENT = 100


def has_marker(body: str) -> bool:
    return bool(_MARKER_RE.search(body or ""))


def answer_items_html(feedbacks: list[dict[str, Any]], lang: str) -> tuple[str, int]:
    """#269 の形の番号付きの項目。その言語の掲載用の本文と回答がないものは入れない。(HTML, 件数)。"""
    parts = []
    number = 0
    for feedback in feedbacks:
        public = feedback.get(f"public_text_{lang}") or ""
        answer = feedback.get(f"answer_{lang}") or ""
        if not public or not answer:
            continue
        number += 1
        ol = "<ol>" if number == 1 else f'<ol start="{number}">'
        quote = "<br>".join(linkify(line) for line in public.split("\n") if line.strip())
        parts.append(f'{ol}<li><p><b><c9>"{quote}"</c9></b></p></li></ol>')
        parts.extend(f"<p>{linkify(line)}</p>" if line.strip() else "<p></p>" for line in answer.split("\n"))
        parts.append("<p></p>")
    return "".join(parts), number


def fill_template(body: str, items_html: str) -> str:
    return _MARKER_RE.sub(lambda _: items_html, body, count=1)


async def get_answer_template(db: asyncpg.Connection) -> dict[str, Any] | None:
    row = await db.fetchrow(
        "SELECT * FROM announcement_templates WHERE kind = 'template' AND settings ->> 'role' = $1 ORDER BY id LIMIT 1",
        FEEDBACK_TEMPLATE_ROLE,
    )
    return dict(row) if row else None


async def create_announcement(db: asyncpg.Connection, feedback_ids: list[int], *, admin_user_id: int) -> dict[str, Any]:
    """選んだ「掲載待ち」のフィードバックから、回答のお知らせの下書きを作る。並びは選んだ順（一覧の並び）。"""
    from app.services import announcement_service as ann

    feedback_ids = list(dict.fromkeys(int(i) for i in feedback_ids))
    if not feedback_ids:
        raise FeedbackError("お知らせに入れるフィードバックを選んでください。", "")
    if len(feedback_ids) > MAX_ITEMS_PER_ANNOUNCEMENT:
        raise FeedbackError(f"1つのお知らせに入れられるのは {MAX_ITEMS_PER_ANNOUNCEMENT} 件までです。", "")
    template = await get_answer_template(db)
    if template is None:
        raise FeedbackError("「フィードバックへの回答」のテンプレートが見つかりません。", "")
    if not has_marker(template["body_ja"]):
        raise FeedbackError(f"テンプレートの日本語の本文に「{TEMPLATE_MARKER_TEXT}」の段落がありません。", "")
    async with db.transaction():
        rows = await db.fetch(
            "SELECT * FROM feedbacks WHERE id = ANY($1::int[]) AND answer_state = 'ready' FOR UPDATE",
            feedback_ids,
        )
        if len(rows) != len(feedback_ids):
            raise FeedbackError("選んだ中に、掲載待ちではないものがあります。一覧を読み込み直してください。", "")
        order = {fid: index for index, fid in enumerate(feedback_ids)}
        items = sorted((dict(r) for r in rows), key=lambda r: order[r["id"]])
        html_ja, count_ja = answer_items_html(items, "ja")
        html_en, count_en = answer_items_html(items, "en")
        settings = template["settings"] or {}
        with_en = count_en > 0 and has_marker(template["body_en"]) and settings.get("show_en", True)
        post_input = {
            "status": "draft",
            "category": template["category"],
            **{key: settings.get(key, True) for key in ("show_web", "show_ios", "show_android", "on_home", "listed")},
            "show_ja": True,
            "show_en": with_en,
            "title_ja": template["title_ja"],
            "title_en": template["title_en"] if with_en else "",
            "body_ja": fill_template(template["body_ja"], html_ja),
            "body_en": fill_template(template["body_en"], html_en) if with_en else "",
            "published_at": None,
            "source_type": ANNOUNCEMENT_SOURCE_TYPE,
        }
        values = ann.clean_post_input(post_input)
        saved = await ann.save_post(db, None, values, user_id=admin_user_id, expected_version=None)
        post_id = saved["row"]["id"]
        await db.execute(
            "UPDATE feedbacks SET answer_state = 'draft', announcement_id = $2 WHERE id = ANY($1::int[])",
            feedback_ids, post_id,
        )
        for fid in feedback_ids:
            await set_checked(db, fid, True, by_user_id=admin_user_id)
    logger.info(
        f"フィードバック {len(feedback_ids)} 件から回答のお知らせ #{post_id} の下書きを作りました"
        f"（日本語 {count_ja} 件・英語 {count_en if with_en else 0} 件、管理者 ID: {admin_user_id}）"
    )
    return {"post_id": post_id, "count_ja": count_ja, "count_en": count_en if with_en else 0}


async def unlink_announcement(db: asyncpg.Connection, post_id: int) -> int:
    """お知らせを削除したら、入れていたフィードバックを掲載待ちに戻す（復元しても戻さない）。"""
    result = await db.execute(
        "UPDATE feedbacks SET answer_state = 'ready', announcement_id = NULL, answer_published_at = NULL "
        "WHERE announcement_id = $1",
        post_id,
    )
    count = int(result.split()[-1]) if result.startswith("UPDATE") else 0
    if count:
        logger.info(f"お知らせ #{post_id} の削除で、フィードバック {count} 件を掲載待ちに戻しました")
    return count


async def publish_due_answers(db: asyncpg.Connection) -> int:
    """公開の時刻が来た回答のお知らせのフィードバックを掲載済みにして、本人に通知する（worker が1分ごと）。"""
    from app.services.announcement_html import html_to_text
    from app.services.notification_service import NOTIFICATION_TYPE_FEEDBACK_ANSWER, invalidate_notification_cache

    async with db.transaction():
        rows = await db.fetch(
            """
            UPDATE feedbacks f SET answer_state = 'published', answer_published_at = now()
            FROM announcement_posts p
            WHERE f.answer_state = 'draft' AND p.id = f.announcement_id
              AND p.status = 'published' AND p.published_at <= now() AND p.deleted_at IS NULL
            RETURNING f.id, f.user_id, p.id AS post_id, p.title_ja, p.title_en, p.show_ja, p.show_en
            """
        )
        if not rows:
            return 0
        await db.executemany(
            """
            INSERT INTO board_notifications (recipient_user_id, notification_type, actor_user_id, feedback_id)
            VALUES ($1, $2, $1, $3)
            """,
            [(row["user_id"], NOTIFICATION_TYPE_FEEDBACK_ANSWER, row["id"]) for row in rows],
        )
    pushes = []
    for row in rows:
        await invalidate_notification_cache(row["user_id"])

        def make(lang: str, row=row) -> push_rules.Push:
            # 英語に載っていなければ日本語のページ（とタイトル）を開く
            page_lang = lang if row[f"show_{lang}"] else ("ja" if lang == "en" else "en")
            push = push_rules.feedback_answer_push(lang, post_id=row["post_id"], title=html_to_text(row[f"title_{page_lang}"]))
            push.url = f"/{page_lang}/help/announcements/{int(row['post_id'])}"
            return push

        pushes.append((row["user_id"], await _push_langs_payload(make)))
    await push_service.enqueue_direct_many(pushes, setting=push_rules.FEEDBACK_SETTING_KEY)
    logger.info(f"回答のお知らせの公開で、フィードバック {len(rows)} 件を掲載済みにし、送った人に通知しました")
    return len(rows)


# --- 本人の履歴 ------------------------------------------------------------------------------
HISTORY_PAGE_SIZE = 10


async def unread_count(db: asyncpg.Connection, user_id: int) -> int:
    """まだ見ていない返信と掲載済みの回答の数（アカウントタブの印）。"""
    value = await db.fetchval(
        """
        SELECT
            (SELECT count(*) FROM feedback_replies r JOIN feedbacks f ON f.id = r.feedback_id
             WHERE f.user_id = $1 AND r.read_at IS NULL AND r.withdrawn_at IS NULL)
          + (SELECT count(*) FROM feedbacks WHERE user_id = $1 AND answer_state = 'published' AND answer_read_at IS NULL)
        """,
        user_id,
    )
    return int(value or 0)


async def list_for_user(db: asyncpg.Connection, user_id: int, lang: str, *, before_id: int | None = None,
                        limit: int = HISTORY_PAGE_SIZE) -> dict[str, Any]:
    """本人の「フィードバック履歴」。新しい順。見たものの返信・回答は既読にする（チェックの状態は出さない）。"""
    rows = await db.fetch(
        """
        SELECT f.id, f.datetime, f.feedback_type, f.comment, f.platform, f.ui_lang, f.client_env, f.reply_to_id,
               f.answer_state, f.answer_ja, f.answer_en, f.public_text_ja, f.public_text_en, f.answer_read_at,
               f.announcement_id, p.show_ja, p.show_en
        FROM feedbacks f LEFT JOIN announcement_posts p ON p.id = f.announcement_id
        WHERE f.user_id = $1 AND ($2::int IS NULL OR f.id < $2)
        ORDER BY f.id DESC
        LIMIT $3
        """,
        user_id, before_id, limit + 1,
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    ids = [row["id"] for row in rows]
    replies: dict[int, list[asyncpg.Record]] = {}
    if ids:
        for reply in await db.fetch(
            """
            SELECT id, feedback_id, body, created_at, read_at FROM feedback_replies
            WHERE feedback_id = ANY($1::int[]) AND withdrawn_at IS NULL ORDER BY id
            """,
            ids,
        ):
            replies.setdefault(reply["feedback_id"], []).append(reply)
    images = await images_for(db, ids, lang=lang)

    items = []
    unread_reply_ids: list[int] = []
    unread_answer_ids: list[int] = []
    for row in rows:
        item_replies = []
        for reply in replies.get(row["id"], []):
            if reply["read_at"] is None:
                unread_reply_ids.append(reply["id"])
            item_replies.append({
                "id": reply["id"],
                "html": linkify(reply["body"]),
                "at": format_display_datetime(reply["created_at"], lang),
                "new": reply["read_at"] is None,
            })
        answer = None
        if row["answer_state"] == "published":
            text = row[f"answer_{lang}"] or row["answer_ja"] or row["answer_en"]
            page_lang = lang if row[f"show_{lang}"] else ("ja" if lang == "en" else "en")
            answer = {
                "html": linkify(text),
                "url": f"/{page_lang}/help/announcements/{row['announcement_id']}" if row["announcement_id"] else "",
                "new": row["answer_read_at"] is None,
            }
            if row["answer_read_at"] is None:
                unread_answer_ids.append(row["id"])
        items.append({
            "id": row["id"],
            "at": format_display_datetime(row["datetime"], lang),
            "type": row["feedback_type"],
            "type_label": type_label(row["feedback_type"], lang),
            "comment": row["comment"],
            "env": env_label(row_env(row), lang),
            "reply_to_id": row["reply_to_id"],
            "images": [image for image in images.get(row["id"], []) if not image.get("removed")],
            "removed_images": sum(1 for image in images.get(row["id"], []) if image.get("removed")),
            "replies": item_replies,
            "answer": answer,
        })
    if unread_reply_ids:
        await db.execute("UPDATE feedback_replies SET read_at = now() WHERE id = ANY($1::int[])", unread_reply_ids)
    if unread_answer_ids:
        await db.execute("UPDATE feedbacks SET answer_read_at = now() WHERE id = ANY($1::int[])", unread_answer_ids)
    return {"items": items, "has_more": has_more}
