from types import SimpleNamespace

import pytest

from app.services import feedback_service as fs
from app.services import media_service, media_storage

IOS_APP = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 BrawlInsightsApp/15.0"
ANDROID_APP = (
    "Mozilla/5.0 (Linux; Android 16; Pixel 6a Build/BP2A.250605.031; wv) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Version/4.0 Chrome/141.0.7390.122 Mobile Safari/537.36 BrawlInsightsApp/15.1"
)
MAC_SAFARI = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15"
ANDROID_REDUCED = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36"
IPAD_APP = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 BrawlInsightsApp/15.0"


def _env(ua: str, platform: str = "web", app_version: str | None = None, lang: str = "ja") -> dict:
    request = SimpleNamespace(headers={"user-agent": ua}, state=SimpleNamespace(platform=platform, app_client_version=app_version))
    return fs.client_env(request, lang, site_version="15.3.60")


def test_env_label_for_apps():
    assert fs.env_label(_env(IOS_APP, "ios", "15.0"), "ja") == "iOSアプリ 15.0・iOS 18.1・iPhone・日本語"
    android = _env(ANDROID_APP, "android", "15.1", lang="en")
    assert fs.env_label(android, "en") == "Android app 15.1 · Android 16 · Pixel 6a · English"
    # アプリでは WebView の版は出さないが、記録には残す
    assert android["browser"] == "WebView" and android["browser_version"] == "141"
    assert fs.env_label(_env(IPAD_APP, "app", "15.0"), "ja") == "アプリ 15.0・iPadOS・日本語"


def test_env_label_for_web_hides_frozen_versions():
    # macOS・Windows の UA の版は固定なので出さない。Chrome の「Android 10; K」は版と機種を伏せる
    assert fs.env_label(_env(MAC_SAFARI), "ja") == "Web版・macOS・Safari 18.1・日本語"
    assert fs.env_label(_env(ANDROID_REDUCED), "ja") == "Web版・Android・Chrome 141・日本語"
    env = _env(MAC_SAFARI)
    assert env["site_version"] == "15.3.60" and env["ua"] == MAC_SAFARI
    assert fs.env_label(env, "ja", with_site=True).endswith("・サイト 15.3.60")


def test_env_label_is_empty_without_record():
    assert fs.env_label(None, "ja") == ""
    row = {"platform": None, "ui_lang": None, "client_env": None}
    assert fs.row_env(row) is None


def test_ua_is_clipped():
    assert len(_env("x" * 2000)["ua"]) == fs.UA_MAX_CHARS


def test_file_picker_waits_for_new_ios_app():
    """iOS の古いアプリは選択のメニューの「写真を撮る」で落ちるので、15.3 から。"""
    assert fs.can_pick_files("ios", "15.0") is False
    assert fs.can_pick_files("app", "15.2.9") is False
    assert fs.can_pick_files("ios", "15.3") is True
    assert fs.can_pick_files("ios", None) is False
    assert fs.can_pick_files("android", "14.0") is True
    assert fs.can_pick_files("web", None) is True


def test_feedback_images_are_private():
    rule = media_service.get_rule("feedback")
    assert rule.perm is None and rule.dedupe is False
    media_service.check_permission(SimpleNamespace(has_perm=lambda key: False), rule)  # ログインしていれば誰でも
    with pytest.raises(media_service.MediaError):
        media_service.check_permission(None, rule)
    key = "abcdefghijklmnopqrstuvwx"
    assert media_storage.is_private("feedback") and not media_storage.is_private("announcement")
    assert media_storage.file_path("feedback", key).parts[-4:] == ("media_private", "feedback", "ab", f"{key}.webp")
    assert media_storage.accel_path("feedback", key, "s") == f"/_media_private/feedback/ab/{key}_s.webp"
    item = media_service.to_item(
        {"id": 1, "kind": "feedback", "key": key, "variants": {"s": [480, 360]}, "width": 800, "height": 600,
         "name": "", "bytes": 1}, lang="en",
    )
    assert item["src"] == f"/en/media/private/{key}" and item["thumb"] == f"/en/media/private/{key}/s"


def test_can_view_private():
    row = {"kind": "feedback", "owner_user_id": 5, "ref_type": "feedback"}
    owner = SimpleNamespace(id=5, has_perm=lambda key: False)
    other = SimpleNamespace(id=6, has_perm=lambda key: False)
    staff = SimpleNamespace(id=1, has_perm=lambda key: key == "page.feedbacks.view")
    assert media_service.can_view_private(owner, row)
    assert not media_service.can_view_private(other, row)
    assert not media_service.can_view_private(None, row)
    assert media_service.can_view_private(staff, row)
    # 送信する前（どこにも付いていない）の画像は本人だけ
    assert not media_service.can_view_private(staff, {**row, "ref_type": None})


def test_frozen_ios_version_is_shown_as_lower_bound():
    """iOS 26 から UA の版は 18.x に固定なので、18.6 以上は「以降」を付ける。iPad は iPadOS。"""
    ipad = "Mozilla/5.0 (iPad; CPU OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 BrawlInsightsApp/15.0"
    env = _env(ipad, "ios", "15.0")
    assert env["os_version_frozen"] is True
    assert fs.env_label(env, "ja") == "iOSアプリ 15.0・iPadOS 18.7以降・iPad・日本語"
    assert fs.env_label(env, "en") == "iOS app 15.0 · iPadOS 18.7+ · iPad · Japanese"
    assert fs.env_label(_env(IOS_APP, "ios", "15.0"), "ja").startswith("iOSアプリ 15.0・iOS 18.1・")


@pytest.mark.asyncio
async def test_comment_needs_min_chars():
    with pytest.raises(fs.FeedbackError) as e:
        await fs.create_feedback(None, user_id=1, feedback_type="bug_report", comment="  ああああ ", env={}, media_ids=[])
    assert "短すぎる" in e.value.message("ja")


def test_answer_items_follow_template_shape():
    items = [
        {"public_text_ja": "〇〇を追加して欲しいです", "answer_ja": "近日中に実装予定です。\n\nお待ちください！",
         "public_text_en": "", "answer_en": ""},
        {"public_text_ja": "a < b & c", "answer_ja": "詳しくは https://example.com/x?a=1 へ",
         "public_text_en": "Bug", "answer_en": "Fixed."},
    ]
    html_ja, count_ja = fs.answer_items_html(items, "ja")
    assert count_ja == 2
    assert html_ja.startswith('<ol><li><p><b><c9>"〇〇を追加して欲しいです"</c9></b></p></li></ol><p>近日中に実装予定です。</p><p></p>')
    assert '<ol start="2"><li><p><b><c9>"a &lt; b &amp; c"</c9></b></p></li></ol>' in html_ja
    assert '<a href="https://example.com/x?a=1">https://example.com/x?a=1</a>' in html_ja
    # 英語が空のものは英語版に入れず、番号は 1 から
    html_en, count_en = fs.answer_items_html(items, "en")
    assert count_en == 1 and html_en.startswith('<ol><li><p><b><c9>"Bug"</c9></b></p></li></ol><p>Fixed.</p>')
    body = f"<p>前置き</p><p> {fs.TEMPLATE_MARKER_TEXT} </p><p>締め</p>"
    assert fs.has_marker(body) and not fs.has_marker("<p>前置き</p>")
    assert fs.fill_template(body, "<p>X</p>") == "<p>前置き</p><p>X</p><p>締め</p>"


def test_clean_answer_input_needs_pairs():
    out = fs.clean_answer_input({"public_text_ja": " 要約 \r\n\r\n\r\n\r\n続き ", "answer_ja": "回答"})
    assert out["public_text_ja"] == "要約\n\n続き" and out["answer_en"] == ""
    with pytest.raises(fs.FeedbackError):
        fs.clean_answer_input({"public_text_ja": "要約", "answer_ja": ""})
    with pytest.raises(fs.FeedbackError):
        fs.clean_answer_input({"public_text_en": "Summary", "answer_en": "Answer"})  # 英語だけは載せられない


def test_linkify_escapes_text():
    assert fs.linkify('<b>x</b> http://a.example/p?q="1"') == (
        '&lt;b&gt;x&lt;/b&gt; <a href="http://a.example/p?q=">http://a.example/p?q=</a>"1"'
    )


def test_feedback_pushes():
    from app.services import push_rules as rules
    reply = rules.feedback_reply_push("en", feedback_id=9, text="x" * 300)
    assert reply.url == "/en/account?feedback=9#feedback-history" and reply.tag == "fbr9" and len(reply.body) == rules.BODY_MAX_CHARS
    answer = rules.feedback_answer_push("ja", post_id=270, title="【お知らせ】フィードバックへの回答です！")
    assert answer.url == "/ja/help/announcements/270" and answer.title.startswith("フィードバックへの回答")
    assert rules.FEEDBACK_SETTING_KEY in rules.PUSH_SETTING_KEYS and rules.PUSH_SETTING_DEFAULTS[rules.FEEDBACK_SETTING_KEY] is True


def test_feedback_route_permissions():
    from app.core import admin_permissions as ap
    resolve = ap.resolve_admin_route_permission
    assert resolve("GET", "/ja/admin/feedbacks/9") == ap.PERM_FEEDBACKS_VIEW
    assert resolve("POST", "/ja/admin/feedbacks/9/reply") == ap.PERM_FEEDBACKS_REPLY
    assert resolve("POST", "/ja/admin/feedbacks/9/replies/3/withdraw") == ap.PERM_FEEDBACKS_REPLY
    assert resolve("POST", "/ja/admin/feedbacks/9/answer") == ap.PERM_FEEDBACKS_ANSWER
    assert resolve("POST", "/ja/admin/feedbacks/announcement") == ap.PERM_FEEDBACKS_ANSWER
    assert resolve("POST", "/ja/admin/feedbacks/9/check") == ap.PERM_FEEDBACKS_EDIT
    assert resolve("POST", "/ja/admin/feedbacks/update") == ap.PERM_FEEDBACKS_EDIT
    assert resolve("POST", "/ja/admin/feedbacks/9/other") == "admin"
