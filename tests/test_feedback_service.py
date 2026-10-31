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
