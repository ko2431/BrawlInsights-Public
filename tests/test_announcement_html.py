import datetime

import pytest

from app.services.announcement_html import (
    convert_legacy_body,
    convert_legacy_title,
    first_image_src,
    html_to_text,
    is_blank_body,
    normalize_only_value,
    only_matches,
    render_html,
    sanitize_body,
    sanitize_title,
)
from app.services.announcement_service import AnnouncementError, clean_post_input


def test_sanitize_body_keeps_editor_format_only():
    """エディターの書式だけを残し、script・イベント属性・危ないリンク・外部の画像は落とす。"""
    html = (
        '<p class="ta-center evil" onclick="x">a<b>b</b><c5>c</c5><span class="fs-l other" style="color:red">d</span></p>'
        '<script>alert(1)</script><a href="javascript:alert(1)">j</a><a href="//evil.example">p</a>'
        '<figure class="bi-figure" data-size="half"><img src="/static/images/ui/info.png" alt="i" width="10" onerror="x"></figure>'
        '<img src="https://evil.example/x.png"><div data-only="ios">i</div><div data-only="mac">m</div>'
    )
    out = sanitize_body(html)
    assert '<p class="ta-center">a<b>b</b><c5>c</c5><span class="fs-l">d</span></p>' in out
    assert "script" not in out and "onclick" not in out and "onerror" not in out and "style" not in out
    assert "javascript" not in out and "evil.example" not in out
    assert '<img src="/static/images/ui/info.png" alt="i" width="10">' in out
    assert '<div data-only="ios">i</div>' in out
    assert '<div>m</div>' in out  # 不正な端末の指定は外れ、中身は残る


def test_sanitize_title_is_one_line_inline():
    assert sanitize_title("<c9>【企画】</c9><br>本文<p>段落</p><a href='/x'>x</a>") == "<c9>【企画】</c9> 本文段落x"


def test_only_matches_and_normalize():
    assert normalize_only_value("android, web") == "web,android"
    assert normalize_only_value("web,ios,android") is None  # 3つとも＝制限なし
    assert normalize_only_value("mac") is None
    assert only_matches("ios", "ios") and not only_matches("ios", "web")
    assert only_matches("web,android", "app") and only_matches("ios", "app") and not only_matches("web", "app")
    assert only_matches("web", "unknown")  # 判定できない端末は Web として扱う


def test_render_filters_platform_and_localizes_links():
    html = sanitize_body(
        '<p>共通<span data-only="ios">iOS</span><span data-only="web,android">他</span></p>'
        '<div data-only="web"><p>Web だけ</p></div>'
        '<p><a href="/ja/account#giftcode">コード</a> <a href="https://x.com/BrawlInsights">X</a>'
        ' <a href="https://brawlinsights.com/ja/help">help</a></p>'
    )
    web = render_html(html, platform="web", lang="en")
    assert "iOS" not in web and "他" in web and "Web だけ" in web
    assert 'href="/en/account#giftcode"' in web  # 英語の本文の /ja/ のリンクも見ている人の言語に
    assert 'href="/en/help"' in web
    assert '<a href="https://x.com/BrawlInsights" target="_blank" rel="noopener noreferrer">' in web
    ios = render_html(html, platform="ios", lang="ja")
    assert "iOS" in ios and "他" not in ios and "Web だけ" not in ios
    assert "data-only" not in ios


def test_render_ios_replaces_text_only():
    """iOS の置き換えは文字の部分だけにかけ、URL は変えない。"""
    html = sanitize_body('<p>Google Play ギフトカード <a href="https://play.google.com/store">Google Play</a></p>')
    out = render_html(html, platform="ios", lang="ja")
    assert "ギフトカード" in out and "Google Play" not in out.split("href")[0]
    assert 'href="https://play.google.com/store"' in out
    assert render_html("<p>&lt;Terms&gt; &amp;</p>", platform="web", lang="en") == "<p>&lt;Terms&gt; &amp;</p>"


def test_text_helpers():
    body = sanitize_body('<p><b>景品</b></p><p></p><figure class="bi-figure"><img src="/static/images/a.png"></figure><p>詳細</p>')
    assert html_to_text(body) == "景品 詳細"
    assert html_to_text("<p>" + "あ" * 50 + "</p>", limit=10) == "あ" * 9 + "…"
    assert first_image_src(body) == "/static/images/a.png"
    assert is_blank_body("<p></p>") and is_blank_body("") and not is_blank_body(body)
    assert not is_blank_body('<figure class="bi-figure"><img src="/static/images/a.png"></figure>')


def test_render_adds_lazy_loading():
    out = render_html('<figure class="bi-figure"><img src="/static/images/a.png" alt="a"></figure>', platform="web", lang="ja")
    assert '<img src="/static/images/a.png" alt="a" loading="lazy" decoding="async">' in out


def test_convert_legacy_body_lines_and_styles():
    legacy = (
        '<span style="font-size: larger;"><b>■ 景品</b></span><br>本文<br><br>'
        '<a href="/ja/account#giftcode" style="text-decoration: underline; font-weight: bold;" target="_blank">コード</a><br>'
        '<span style="font-size: smaller;">規約1<br>規約2</span><br>'
        '<span style="font-weight: bold; color: rgb(209, 14, 14);">赤</span>'
    )
    assert convert_legacy_body(legacy) == (
        '<p><span class="fs-l"><b>■ 景品</b></span></p><p>本文</p><p></p>'
        '<p><b><a href="/ja/account#giftcode">コード</a></b></p>'
        '<p><span class="fs-s">規約1</span></p><p><span class="fs-s">規約2</span></p>'
        '<p><b><c2>赤</c2></b></p>'
    )


def test_convert_legacy_body_fixes_broken_markup():
    """文字として書いた <Terms and Conditions> を表示し、閉じていない・ずれたタグも整える。"""
    assert convert_legacy_body("<b><Terms and Conditions></b>") == "<p><b>&lt;Terms and Conditions&gt;</b></p>"
    assert convert_legacy_body("<c4>青<br>続き") == "<p><c4>青</c4></p><p><c4>続き</c4></p>"
    assert convert_legacy_body("<b>太<i>斜</b>斜だけ</i>") == "<p><b>太<i>斜</i></b><i>斜だけ</i></p>"
    assert convert_legacy_body("a\nb") == "<p>a</p><p>b</p>"  # 定型文の改行
    assert convert_legacy_body(" <br>x") == "<p></p><p>x</p>"
    # 最後の <br> の後ろはブラウザでは空の行にならない
    assert convert_legacy_body("a<br>") == "<p>a</p>"
    assert convert_legacy_body("a<br><br>") == "<p>a</p><p></p>"
    assert convert_legacy_body('<span style="font-size: smaller;">a<br></span>') == '<p><span class="fs-s">a</span></p>'


def test_convert_legacy_title():
    assert convert_legacy_title('<c9>【<c2>特別</c2>企画】</c9><br>〈まで〉') == "<c9>【<c2>特別</c2>企画】</c9> 〈まで〉"


def _post_input(**overrides):
    data = {
        "status": "published", "category": "4",
        "show_web": True, "show_ios": True, "show_android": True, "show_ja": True, "show_en": True,
        "on_home": True, "listed": True,
        "title_ja": "<c9>【企画】</c9>開始", "title_en": "Start",
        "body_ja": "<p>本文</p><script>x</script>", "body_en": "<p>Body</p>",
        "published_at": None,
    }
    data.update(overrides)
    return data


def test_clean_post_input_requires_texts_for_each_language():
    out = clean_post_input(_post_input())
    assert out["category"] == 4 and out["body_ja"] == "<p>本文</p>"
    assert isinstance(out["published_at"], datetime.datetime)  # 公開で日時が空なら今
    with pytest.raises(AnnouncementError):
        clean_post_input(_post_input(body_en="<p></p>"))
    out = clean_post_input(_post_input(body_en="<p></p>", title_en="", show_en=False))
    assert out["show_en"] is False and out["body_en"] == ""
    out = clean_post_input(_post_input(status="draft", title_ja="", body_ja=""))  # 下書きは空でも保存できる
    assert out["published_at"] is None
    with pytest.raises(AnnouncementError):
        clean_post_input(_post_input(show_web=False, show_ios=False, show_android=False))
    with pytest.raises(AnnouncementError):
        clean_post_input(_post_input(category="9"))
