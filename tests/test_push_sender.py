"""プッシュ通知の送る・送らないの判定（push_sender._plan）と間引きのテスト。Redis はメモリ上の簡易版に差し替える。"""
import pytest

from app.core import cache as cache_module
from app.services import push_rules as rules
from app.services import push_sender
from app.services.push_sender import BoardContext


class FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.ops = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __getattr__(self, name):
        def queue(*args, **kwargs):
            self.ops.append((name, args, kwargs))
        return queue

    async def execute(self):
        results = [await getattr(self.redis, name)(*args, **kwargs) for name, args, kwargs in self.ops]
        self.ops = []
        return results


class FakeRedis:
    def __init__(self):
        self.data = {}

    def pipeline(self, transaction=False):
        return FakePipeline(self)

    async def hincrby(self, key, field, amount):
        h = self.data.setdefault(key, {})
        h[field] = int(h.get(field, 0)) + amount
        return h[field]

    async def hget(self, key, field):
        value = self.data.get(key, {}).get(field)
        return None if value is None else str(value).encode()

    async def hset(self, key, mapping):
        self.data.setdefault(key, {}).update(mapping)

    async def expire(self, key, seconds, nx=False):
        return True

    async def incr(self, key):
        self.data[key] = int(self.data.get(key, 0)) + 1
        return self.data[key]

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True

    async def getdel(self, key):
        return self.data.pop(key, None)

    async def delete(self, key):
        self.data.pop(key, None)


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(cache_module, "redis_pool", fake)
    return fake


USER = {"id": 1, **rules.PUSH_SETTING_DEFAULTS, "notification_message_reply_enabled": True,
        "notification_own_post_message_enabled": True}


def board_ctx(nids=(), names=None, texts=None):
    ctx = BoardContext()
    ctx.alive = set(nids)
    ctx.names = names or {9: "たろう"}
    ctx.post_types = {10: "team"}
    ctx.texts = texts or {}
    return ctx


async def test_replies_from_same_person_are_capped(redis):
    ctx = board_ctx(nids=range(1, 10))
    results = []
    for nid in range(1, 7):
        job = {"src": "board", "nid": nid, "type": "message_reply", "actor": 9, "post": 10, "msg": 100 + nid}
        make = await push_sender._plan(job, USER, ctx, 1000.0)
        results.append(make("ja").title if make else None)
    # 3回まで送り、4回目は「ほかにも」のまとめ、5回目からは送らない
    assert results[:3] == ["たろうさんがあなたのメッセージにリプライしました"] * 3
    assert results[3] == "たろうさんから、ほかにもリプライ・メンションが届いています"
    assert results[4] is None and results[5] is None


async def test_thread_messages_are_grouped(redis):
    ctx = board_ctx(nids=range(1, 10), texts={101: "よろしく"})
    job = lambda nid: {"src": "board", "nid": nid, "type": "own_post_message", "actor": 9, "post": 10, "msg": 100 + nid}  # noqa: E731
    first = await push_sender._plan(job(1), USER, ctx, 1000.0)
    assert first and first("ja").body == "よろしく"
    # 1時間たつまでは送らない
    assert await push_sender._plan(job(2), USER, ctx, 1500.0) is None
    assert await push_sender._plan(job(3), USER, ctx, 2000.0) is None
    # 1時間たって新着があれば、件数を入れて出し直す（今回の分を含めて3件）
    again = await push_sender._plan(job(4), USER, ctx, 1000.0 + 3600)
    assert again and again("ja").title == "チーム募集のスレッドに新しいメッセージが3件あります"
    # スレッドを開いたら最初から（次の新着でまた1回送る）
    await redis.delete(push_sender.push_service.group_key(1, rules.GROUP_THREAD, 10))
    assert await push_sender._plan(job(5), USER, ctx, 1000.0 + 3700)


async def test_board_skips_read_and_disabled(redis):
    ctx = board_ctx(nids=[1])
    # 送る前に読まれた（消えた）通知は送らない
    assert await push_sender._plan({"src": "board", "nid": 2, "type": "message_reply", "actor": 9, "post": 10, "msg": 1},
                                   USER, ctx, 0.0) is None
    # プッシュの設定がオフの種類は送らない（いいねは既定でオフ）
    assert await push_sender._plan({"src": "board", "nid": 1, "type": "post_like", "actor": 9, "post": 10},
                                   USER, ctx, 0.0) is None


async def test_warning_is_always_sent(redis):
    ctx = board_ctx(nids=[1])
    user = {key: False for key in rules.PUSH_SETTING_KEYS} | {"id": 1}
    make = await push_sender._plan({"src": "board", "nid": 1, "type": "admin_warning", "actor": 9, "post": 10, "msg": 5},
                                   user, ctx, 0.0)
    assert make and make("ja").high and make("ja").body == ""


async def test_tournament_progress_is_not_throttled(redis):
    notice = {"id": 1, "kind": "match_ready", "path": "tournaments/a/matches/1", "t": "大会",
              "text": {"ja": "始まりました", "en": "Started"}, "cat": rules.CAT_TN_PROGRESS}
    for _ in range(50):
        make = await push_sender._plan({"src": "tn", "notice": notice}, USER, BoardContext(), 0.0)
        assert make and make("ja").time_sensitive
    user = USER | {"push_tournament_progress_enabled": False}
    assert await push_sender._plan({"src": "tn", "notice": notice}, user, BoardContext(), 0.0) is None


async def test_admin_notifications_are_grouped(redis):
    job = {"src": "admin", "event": "log_error", "title": "ERRORログ", "summary": "x", "path": "/admin/logs"}
    first = await push_sender._plan(job, USER, BoardContext(), 0.0)
    assert first and first("ja").title == "ERRORログ"
    # 10分の間は送らず、数えておく
    assert await push_sender._plan(job, USER, BoardContext(), 0.0) is None
    assert await push_sender._plan(job, USER, BoardContext(), 0.0) is None
    # 次に送るときに「ほか◯件」
    redis.data.pop("push:admin:1:log_error")
    later = await push_sender._plan(job, USER, BoardContext(), 0.0)
    assert later and later("ja").title == "ERRORログ（ほか2件）"
