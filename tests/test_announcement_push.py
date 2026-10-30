import pytest

from app.services import announcement_push_service as aps
from app.services import push_rules as rules

KEY = "abcdefghijklmnopqrstuvwx"


def _post(**overrides):
    post = {
        "id": 12,
        "show_web": True, "show_ios": False, "show_android": True, "show_ja": True, "show_en": False,
        "title_ja": "<c9>【企画】</c9>第12回プレゼント企画", "title_en": "Giveaway",
        "body_ja": (f'<p>本文の<b>先頭</b><span data-only="ios">iOS だけ</span></p>'
                    f'<figure class="bi-figure"><img src="/media/announcement/ab/{KEY}.webp"></figure>'),
        "body_en": '<figure class="bi-figure"><img src="/static/images/ui/info.png"></figure><p>Body</p>',
    }
    post.update(overrides)
    return post


def test_targets_follow_post_settings():
    assert aps.targets(_post()) == (["web", "android"], ["ja"])


def test_build_pushes_defaults_from_post():
    pushes = aps.build_pushes(_post(), {"with_image": True}, ["web", "ios"], ["ja", "en"])
    web = pushes[("web", "ja")]
    assert web.title == "【企画】第12回プレゼント企画"
    assert web.body == "本文の先頭"  # 端末別の「iOS だけ」は Web には出さない
    assert "iOS だけ" in pushes[("ios", "ja")].body
    assert web.url == "/ja/help/announcements/12" and web.tag == "news12" and web.channel == rules.CAT_NEWS
    assert web.image == f"/media/announcement/ab/{KEY}_og.jpg"  # 上げた画像は JPEG のサイズ違い
    assert pushes[("web", "en")].image == "/static/images/ui/info.png"


def test_build_pushes_custom_texts_and_no_image():
    pushes = aps.build_pushes(_post(), {"title_ja": " 当選発表 ", "body_ja": "", "with_image": False}, ["web"], ["ja"])
    push = pushes[("web", "ja")]
    assert push.title == "当選発表" and push.body == "本文の先頭" and push.image == ""


def test_announcement_push_clips_and_falls_back():
    push = rules.announcement_push("en", post_id=3, title="", body="x" * 200)
    assert push.title == "News" and len(push.body) == rules.ANNOUNCEMENT_BODY_MAX and push.body.endswith("…")


def test_payloads_include_image():
    push = rules.announcement_push("ja", post_id=3, title="t", body="b", image="/media/announcement/ab/x_og.jpg")
    assert rules.web_payload(push)["image"] == "/media/announcement/ab/x_og.jpg"
    message = rules.fcm_message("token", push, now=0)
    assert message["notification"]["image"] == "https://brawlinsights.com/media/announcement/ab/x_og.jpg"
    assert message["android"]["notification"]["channel_id"] == "news"
    assert "image" not in rules.web_payload(rules.announcement_push("ja", post_id=3, title="t", body="b"))


def test_clean_push_input():
    values = aps.clean_push_input({"enabled": 1, "title_ja": "  a\n b ", "body_ja": "x" * 500})
    assert values["enabled"] is True and values["title_ja"] == "a b" and len(values["body_ja"]) == aps.BODY_MAX_CHARS
    assert values["with_image"] is True
    with pytest.raises(aps.PushInputError):
        aps.clean_push_input("on")


def test_push_for_editor_states():
    assert aps.push_for_editor(None)["editable"] is True
    row = {key: 0 for key in ("target_count", "sent_count", "failed_count", "gone_count")}
    row.update({"title_ja": "", "title_en": "", "body_ja": "", "body_en": "", "with_image": True,
                "started_at": None, "finished_at": None})
    assert aps.push_for_editor({**row, "status": "pending"})["enabled"] is True
    assert aps.push_for_editor({**row, "status": "canceled"})["enabled"] is False
    sent = aps.push_for_editor({**row, "status": "sent"})
    assert sent["editable"] is False and sent["enabled"] is True


def test_announcement_setting_is_off_by_default():
    assert rules.ANNOUNCEMENT_SETTING_KEY in rules.PUSH_SETTING_KEYS
    assert rules.PUSH_SETTING_DEFAULTS[rules.ANNOUNCEMENT_SETTING_KEY] is False
