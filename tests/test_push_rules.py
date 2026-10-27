"""プッシュ通知の決まりごとのテスト（push_rules・push_service.validate_device）。"""
from app.services import push_rules as rules
from app.services.push_service import validate_device

ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc:def"


def test_is_fresh():
    # 試合の開始などは15分を過ぎたら送らない（worker が止まっていたときに、遅れて届かないように）
    ready = {"src": "tn", "at": 1000, "notice": {"kind": "match_ready"}}
    assert rules.is_fresh(ready, 1000 + 600)
    assert not rules.is_fresh(ready, 1000 + 1000)
    finished = {"src": "tn", "at": 1000, "notice": {"kind": "tournament_finished"}}
    assert rules.is_fresh(finished, 1000 + 3600)
    assert not rules.is_fresh({"src": "board", "at": 1000}, 1000 + 7 * 3600)


def test_tournament_category():
    assert rules.tournament_category("match_ready") == rules.CAT_TN_PROGRESS
    assert rules.tournament_category("tournament_finished") == rules.CAT_TN_PROGRESS
    assert rules.tournament_category("chat_reply") == rules.CAT_TN_CHAT
    assert rules.tournament_category("chat_mention") == rules.CAT_TN_CHAT
    # @everyone とお知らせタブの投稿は「大会のお知らせ」
    assert rules.tournament_category("chat_mention", everyone=True) == rules.CAT_TN_NOTICE
    assert rules.tournament_category("notice_posted") == rules.CAT_TN_NOTICE


def test_board_enabled_follows_in_app_settings():
    settings = dict(rules.PUSH_SETTING_DEFAULTS)
    # 既定: 自分あて・明示的にオンにしたものは届く、いいね・リアクション・参加したスレッドは届かない
    assert rules.board_enabled("message_reply", settings)
    assert rules.board_enabled("subscribed_thread_message", settings)
    assert not rules.board_enabled("post_like", settings)
    assert not rules.board_enabled("message_reaction", settings)
    assert not rules.board_enabled("participated_thread_message", settings)
    # アプリ内の通知がオフの種類は、プッシュがオンでも届かない
    settings["notification_message_reply_enabled"] = False
    assert not rules.board_enabled("message_reply", settings)
    # 警告は設定にかかわらず届く
    assert rules.board_enabled("admin_warning", {})
    assert not rules.board_enabled("unknown_type", settings)


def test_tournament_enabled():
    settings = dict(rules.PUSH_SETTING_DEFAULTS)
    assert all(rules.tournament_enabled(c, settings) for c in rules.TOURNAMENT_SETTING_KEYS)
    settings["push_tournament_chat_enabled"] = False
    assert not rules.tournament_enabled(rules.CAT_TN_CHAT, settings)
    assert rules.tournament_enabled(rules.CAT_TN_PROGRESS, settings)


def test_setting_keys_match_defaults():
    assert set(rules.PUSH_SETTING_KEYS) == set(rules.PUSH_SETTING_DEFAULTS)


def test_board_group():
    assert rules.board_group("own_post_message") == rules.GROUP_THREAD
    assert rules.board_group("participated_thread_message") == rules.GROUP_THREAD
    assert rules.board_group("post_like") == rules.GROUP_LIKE
    assert rules.board_group("message_reaction") == rules.GROUP_REACTION
    assert rules.board_group("message_reply") == rules.GROUP_PAIR
    assert rules.board_group("token_gift") is None
    assert rules.board_group("admin_warning") is None


def test_group_decision():
    # 初めては送る
    assert rules.group_decision(None, 1, 1000.0) == (True, 1)
    # 1時間たつまでは送らない
    assert rules.group_decision(1000.0, 5, 1000.0 + 1800) == (False, 0)
    # 1時間たって新着があれば、件数を入れて送る
    assert rules.group_decision(1000.0, 12, 1000.0 + 3600) == (True, 12)
    assert rules.group_decision(1000.0, 0, 1000.0 + 7200) == (False, 0)


def test_board_titles_match_in_app_wording():
    assert rules.board_title("message_reply", "ja", actor="たろう", post_type="team") == "たろうさんがあなたのメッセージにリプライしました"
    assert rules.board_title("message_mention", "ja", actor="たろう", post_type="team") == "たろうさんがチーム募集のスレッドであなたをメンションしました"
    assert rules.board_title("own_post_message", "en", actor="taro", post_type="club") == "taro replied to your club recruitment post"
    assert rules.board_title("token_gift", "ja", actor="たろう", post_type=None, amount=1000) == "たろうさんがあなたに1,000トークンを贈りました"
    # まとめた文面
    assert rules.board_title("participated_thread_message", "ja", actor="", post_type="team", count=12) == "チーム募集のスレッドに新しいメッセージが12件あります"
    assert rules.board_title("message_reaction", "en", actor="", post_type=None, count=3) == "Your message got 3 new reactions"


def test_board_push():
    push = rules.board_push("message_reply", "ja", actor="たろう", post_type="team", post_id=10, message_id=55,
                            text="  了解です\nよろしく  ")
    assert push.url == "/ja/boards/chat/10?from=notifications&message_id=55"
    assert push.body == "了解です よろしく"
    assert push.channel == "board" and not push.high
    # スレッドの一般メッセージは、同じスレッドの通知を置き換える
    thread = rules.board_push("own_post_message", "ja", actor="a", post_type="team", post_id=10, message_id=56)
    assert thread.tag == "bt10"
    # いいねは自分の投稿の一覧へ
    like = rules.board_push("post_like", "en", actor="a", post_type="general", post_id=3, message_id=None)
    assert like.url == "/en/boards/general?tab=own"
    # 警告は内容を出さず、すぐ届ける
    warning = rules.board_push("admin_warning", "ja", actor="admin", post_type="team", post_id=10, message_id=57,
                               text="警告の本文")
    assert warning.body == "" and warning.high
    assert "警告が届いています" in warning.title


def test_clip():
    assert rules.clip("a" * 200, 10) == "a" * 9 + "…"
    assert rules.clip(None) == ""


def test_tournament_push():
    notice = {"id": 7, "kind": "match_ready", "path": "tournaments/abc/matches/3", "t": "テスト大会",
              "text": {"ja": "試合が始まりました", "en": "Your match started"}, "cat": rules.CAT_TN_PROGRESS}
    push = rules.tournament_push(notice, "en")
    assert push.title == "テスト大会" and push.body == "Your match started"
    assert push.url == "/en/tournaments/abc/matches/3"
    assert push.high and push.time_sensitive and push.ttl == rules.SHORT_TTL_SECONDS
    assert push.channel == "tn_progress"
    chat = rules.tournament_push(notice | {"kind": "chat_reply", "cat": rules.CAT_TN_CHAT}, "ja")
    assert not chat.high and not chat.time_sensitive and chat.channel == "tournament"
    assert chat.ttl == rules.DEFAULT_TTL_SECONDS


def test_time_sensitive_kinds_are_known():
    from app.services.tournament_notify import notification_text
    payload = {"t": "t", "pid": "p", "mt_ja": "m", "mt_en": "m", "team": "a", "name": "n", "minutes": 5}
    for kind in rules.TIME_SENSITIVE_KINDS:
        assert notification_text(kind, payload, "ja"), kind


def test_admin_push():
    push = rules.admin_push("ja", event_key="log_error", title="ERRORログ", summary="x", target_path="/admin/logs", more=4)
    assert push.url == "/ja/admin/logs" and push.title == "ERRORログ（ほか4件）"
    assert push.tag == "adlog_error"


def test_is_allowed_web_endpoint():
    assert rules.is_allowed_web_endpoint(ENDPOINT)
    assert rules.is_allowed_web_endpoint("https://updates.push.services.mozilla.com/wpush/v2/abc")
    assert rules.is_allowed_web_endpoint("https://web.push.apple.com/abc")
    assert rules.is_allowed_web_endpoint("https://wns2-par02p.notify.windows.com/w/?token=abc")
    # サーバーから送る先なので、ほかのホスト・http は受け付けない
    assert not rules.is_allowed_web_endpoint("http://fcm.googleapis.com/fcm/send/abc")
    assert not rules.is_allowed_web_endpoint("https://example.com/fcm.googleapis.com")
    assert not rules.is_allowed_web_endpoint("https://evilfcm.googleapis.com.example.com/")
    assert not rules.is_allowed_web_endpoint("https://127.0.0.1/")


def test_validate_device():
    assert validate_device("web", ENDPOINT, "p" * 87, "a" * 22)
    assert not validate_device("web", ENDPOINT, None, "a")
    assert not validate_device("web", "https://example.com/x", "p", "a")
    assert validate_device("ios", "fcm-token", None, None)
    assert not validate_device("ios", "", None, None)
    assert not validate_device("windows", "x", None, None)


def test_fcm_message():
    push = rules.Push(title="t", body="b", url="/ja/x", tag="tn7", channel="tn_progress", high=True,
                      time_sensitive=True, ttl=900, kind="match_ready")
    msg = rules.fcm_message("tok", push, 3, now=1000)
    assert msg["token"] == "tok"
    assert msg["data"]["url"] == "/ja/x"
    assert msg["android"]["priority"] == "high" and msg["android"]["ttl"] == "900s"
    assert msg["android"]["notification"]["notification_count"] == 3
    aps = msg["apns"]["payload"]["aps"]
    assert aps["badge"] == 3 and aps["interruption-level"] == "time-sensitive"
    assert msg["apns"]["headers"]["apns-priority"] == "10"
    assert msg["apns"]["headers"]["apns-expiration"] == "1900"
    # バッジの数が分からないときは付けない（前の数のまま）
    quiet = rules.fcm_message("tok", rules.Push(title="t", body="", url="/", tag="x", channel="board"), None, now=0)
    assert "badge" not in quiet["apns"]["payload"]["aps"] and quiet["apns"]["headers"]["apns-priority"] == "5"
    assert quiet["apns"]["payload"]["aps"]["interruption-level"] == "active"


def test_web_payload():
    push = rules.Push(title="t", body="b", url="/ja/x", tag="bt1", channel="board")
    assert rules.web_payload(push) == {"title": "t", "body": "b", "url": "/ja/x", "tag": "bt1", "icon": rules.ICON_PATH}
