"""フィードバック（検討メモ notes_private/feedback_plan_2026-10-31.md）。

送信時の環境（3章）: 端末の種類・アプリの版・OS・ブラウザ・端末名・表示の言語・サイトの版を、リクエストからサーバーで作る。
送信画面の「一緒に送信される情報」も同じ処理で作るので、表示と保存が食い違わない。
添付の画像（5章）は media（kind = feedback。公開しない）。上げるのは送信の前（/{lang}/media/upload?kind=feedback）で、
送信のときに media の id を受け取って付ける。添付されなかった画像は1日で、チェック済みのものは180日で消す（worker）。
"""
from __future__ import annotations

import datetime
from typing import Any

import asyncpg
from fastapi import Request
from user_agents import parse as parse_ua

from app.core.cache import get_cache, set_cache
from app.core.logger import logger
from app.core.templating import version_at_least
from app.services import media_service
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
                          env: dict[str, Any], media_ids: list[int]) -> int:
    """フィードバックを保存し、添付の画像を付ける。投稿禁止中のユーザーも送れる。失敗したら FeedbackError。"""
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
        feedback_id = await db.fetchval(
            """
            INSERT INTO feedbacks (user_id, datetime, feedback_type, comment, platform, ui_lang, client_env)
            VALUES ($1, now(), $2, $3, $4, $5, $6)
            RETURNING id
            """,
            user_id, feedback_type, comment, env.get("platform"), env.get("ui_lang"), client,
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
