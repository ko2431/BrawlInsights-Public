"""掲示板の投稿者判定を、一覧・カード・操作APIで確認する。外部DB・Redisは使わない。"""

import datetime
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.core.templating import templates
from app.main import app
from app.routers import boards
from app.services import board_service


IP = "192.0.2.1"
OTHER_IP = "192.0.2.2"
BOARDS = ("team", "friend", "club", "general")
OWNER_CASES = (
    pytest.param(10, 10, IP, True, id="account-owner"),
    pytest.param(10, 10, OTHER_IP, True, id="account-owner-new-ip"),
    pytest.param(20, 10, IP, False, id="different-account-same-ip"),
    pytest.param(None, 10, IP, False, id="guest-viewing-account-post"),
    pytest.param(10, None, IP, False, id="account-viewing-guest-post"),
    pytest.param(None, None, IP, True, id="guest-owner"),
    pytest.param(None, None, OTHER_IP, False, id="guest-different-ip"),
)


@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    """このモジュールでは、共通fixtureによるテストDBの初期化を行わない。"""
    yield


def _user(user_id, *, staff=False):
    if user_id is None:
        return None
    return SimpleNamespace(
        id=user_id, name="テスト", main_account="#TEST",
        has_perm=lambda permission: staff and permission == "board.delete_posts",
    )


def _request(user, ip=IP):
    request = Request({
        "type": "http", "scheme": "http", "server": ("test", 80),
        "path": "/", "root_path": "", "headers": [], "client": (ip, 1234), "app": app,
    })
    request.state.current_user = user
    return request


def _post(post_id=1, *, host_id=10, ip=IP, post_type="team"):
    post = board_service.Post()
    post.id, post.host_id, post.host_ip, post.type = post_id, host_id, ip, post_type
    post.created_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)
    post.chat_permission_level = 30
    post.is_deleted = False
    post.category = "x" if post_type == "general" else "trophy"
    post.mode = "gemGrab"
    post.link = "https://example.com/invite"
    post.conditions_application_type = "and"
    post.required_highest_trophies = 1_000_000
    post.hostplayer = board_service.HostPlayer()
    post.hostplayer.tag, post.hostplayer.name = "#TEST", "テスト"
    post.hostclub = board_service.HostClub()
    post.hostclub.tag, post.hostclub.name = "#CLUB", "テストクラブ"
    post.is_permitted_to_chat = AsyncMock(return_value=False)
    post.is_permitted_to_click_link = AsyncMock(return_value=False)
    return post


@pytest.fixture
def no_external_services(monkeypatch):
    monkeypatch.setattr(board_service, "get_cache", AsyncMock(return_value=None))
    monkeypatch.setattr(board_service, "set_cache", AsyncMock())
    monkeypatch.setattr(boards, "get_ip", lambda request: request.client.host)
    monkeypatch.setattr(boards, "get_blocked_ids", AsyncMock(return_value={"user_ids": [], "anonymous_ids": []}))
    monkeypatch.setattr(boards, "get_player_from_db", AsyncMock(return_value=None))
    monkeypatch.setattr(boards, "get_general_post_vote_summary", AsyncMock(return_value={}))
    monkeypatch.setattr(boards, "board_link_targets", AsyncMock(return_value={}))
    monkeypatch.setattr(boards, "get_general_post_repost_states", AsyncMock(return_value={}))
    monkeypatch.setattr(boards, "touch_board_campaign_user", AsyncMock())


async def _listing(db, request, post_type, *, own=False):
    kwargs = dict(page=1, limit=2, region="all", eliminate_duplicates=False)
    if post_type == "general":
        return await boards._fetch_general_board_posts(
            db, request, tab="own" if own else "latest", filter="all", **kwargs,
        )
    kwargs["filter"] = "only_own_posts" if own else "all"
    if post_type == "team":
        return await boards._fetch_team_board_posts(db, request, category="all", mode="all", **kwargs)
    return await boards._fetch_recruitment_board_posts(db, request, post_type=post_type, **kwargs)


@pytest.mark.parametrize("viewer_id,host_id,viewer_ip,expected", OWNER_CASES)
@pytest.mark.parametrize("post_type", BOARDS)
async def test_cards_follow_account_and_guest_ownership(
    monkeypatch, no_external_services, viewer_id, host_id, viewer_ip, expected, post_type,
):
    post = _post(host_id=host_id, post_type=post_type)
    monkeypatch.setattr(boards, "get_posts", AsyncMock(return_value=([post], 1)))
    user = _user(viewer_id)
    request = _request(user, viewer_ip)
    posts, blocked_ids, total = await _listing(object(), request, post_type)
    assert total == 1
    assert posts[post.id]["is_host"] is expected

    for lang in ("ja", "en"):
        html = templates.env.get_template(
            f"recruitment_board/fragments/includes/{post_type}_post_cards.html",
        ).render(
            request=request, posts=posts, current_user=user, ip=viewer_ip, lang=lang,
            blocked_ids=blocked_ids, platform="web", main_account=None,
            team_post_close_cooldown_seconds=board_service.TEAM_POST_CLOSE_COOLDOWN_SECONDS,
        )
        own_label = "あなたの投稿です" if lang == "ja" else "Your Post"
        assert (own_label in html) is expected
        operation = "close" if post_type == "team" else "delete"
        assert (f'id="{operation}-btn-{post.id}"' in html) is expected
        assert (f'id="report-btn-{post.id}"' in html) is not expected
        # 募集条件を満たさない場合、投稿者だけがリンクを開ける
        assert (f'href="{post.link}"' in html) is expected


@pytest.mark.parametrize("viewer_id,host_id,viewer_ip,expected", OWNER_CASES)
@pytest.mark.parametrize("action", ("delete", "close", "reopen"))
async def test_post_operations_enforce_ownership(
    monkeypatch, no_external_services, viewer_id, host_id, viewer_ip, expected, action,
):
    post = _post(host_id=host_id)
    post.is_closed = action == "reopen"
    mutation = AsyncMock()
    monkeypatch.setattr(post, action, mutation)
    monkeypatch.setattr(boards, "get_post", AsyncMock(return_value=post))
    request = _request(_user(viewer_id), viewer_ip)
    endpoint = getattr(boards, f"{action}_post")
    db = object()
    if expected:
        assert (await endpoint(request, post.id, db))["success"] is True
        mutation.assert_awaited_once_with(db)
    else:
        with pytest.raises(HTTPException) as error:
            await endpoint(request, post.id, db)
        assert error.value.status_code == 403
        mutation.assert_not_awaited()


@pytest.mark.parametrize("action", ("delete", "close", "reopen"))
async def test_staff_keep_delete_permission_without_becoming_owner(monkeypatch, no_external_services, action):
    post = _post()
    post.is_closed = action == "reopen"
    mutation = AsyncMock()
    monkeypatch.setattr(post, action, mutation)
    monkeypatch.setattr(boards, "get_post", AsyncMock(return_value=post))
    endpoint = getattr(boards, f"{action}_post")
    request = _request(_user(99, staff=True))
    if action == "delete":
        assert (await endpoint(request, post.id, object()))["success"] is True
        mutation.assert_awaited_once()
    else:
        with pytest.raises(HTTPException) as error:
            await endpoint(request, post.id, object())
        assert error.value.status_code == 403
        mutation.assert_not_awaited()


@pytest.mark.parametrize("post_type", BOARDS)
async def test_staff_cards_offer_delete_without_owner_privileges(monkeypatch, no_external_services, post_type):
    post = _post(post_type=post_type)
    monkeypatch.setattr(boards, "get_posts", AsyncMock(return_value=([post], 1)))
    user = _user(99, staff=True)
    request = _request(user)
    posts, blocked_ids, _ = await _listing(object(), request, post_type)
    html = templates.env.get_template(
        f"recruitment_board/fragments/includes/{post_type}_post_cards.html",
    ).render(request=request, posts=posts, current_user=user, ip=IP, lang="ja",
             blocked_ids=blocked_ids, platform="web", main_account=None)
    assert posts[post.id]["is_host"] is False
    assert f'id="delete-btn-{post.id}"' in html
    assert f'id="close-btn-{post.id}"' not in html
    assert f'id="reopen-btn-{post.id}"' not in html
    assert "あなたの投稿です" not in html
    assert f'href="{post.link}"' not in html


class _PostsDB:
    """一覧・直近投稿の共通SQL部分を、メモリ内のデータで実行する。"""

    def __init__(self, posts):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("""
            CREATE TABLE posts (
                id INTEGER, host_id INTEGER, host_ip TEXT, type TEXT,
                created_at INTEGER, reposted_at INTEGER, is_deleted BOOLEAN
            )
        """)
        self.connection.executemany(
            "INSERT INTO posts VALUES (?, ?, ?, ?, ?, NULL, ?)",
            [(p.id, p.host_id, p.host_ip, p.type, p.id, p.is_deleted) for p in posts],
        )

    async def fetchrow(self, sql, *params, **kwargs):
        return self.connection.execute(sql, params).fetchone()

    async def fetchval(self, sql, *params):
        row = await self.fetchrow(sql, *params)
        return row[0] if row else None

    async def fetch(self, sql, *params, **kwargs):
        return self.connection.execute(sql, params).fetchall()


@pytest.fixture
def stored_posts(monkeypatch, no_external_services):
    databases = []

    def create(post_type):
        posts = [
            _post(1, host_id=10, post_type=post_type),
            _post(2, host_id=None, post_type=post_type),
            _post(3, host_id=20, post_type=post_type),
            _post(4, host_id=10, ip=OTHER_IP, post_type=post_type),
            _post(5, host_id=None, ip=OTHER_IP, post_type=post_type),
            _post(6, host_id=None, post_type=post_type),
        ]
        posts[-1].is_deleted = True
        by_id = {post.id: post for post in posts}
        monkeypatch.setattr(board_service, "get_post", AsyncMock(side_effect=lambda db, id, **kw: by_id[id]))
        db = _PostsDB(posts)
        databases.append(db)
        return db

    yield create
    for db in databases:
        db.connection.close()


@pytest.mark.parametrize("post_type", BOARDS)
@pytest.mark.parametrize("viewer_id,expected", ((10, [4, 1]), (None, [2])))
async def test_own_post_queries_return_only_owned_rows(stored_posts, post_type, viewer_id, expected):
    posts, _, total = await _listing(stored_posts(post_type), _request(_user(viewer_id)), post_type, own=True)
    assert list(posts) == expected
    assert total == len(expected)
    assert all(post["is_host"] for post in posts.values())


@pytest.mark.parametrize("viewer_id,expected", ((10, 4), (None, 2)))
async def test_last_post_excludes_other_accounts_and_deleted_posts(stored_posts, viewer_id, expected):
    post = await board_service.get_last_post(stored_posts("friend"), IP, type="friend", user_id=viewer_id)
    assert post.id == expected


async def test_account_filter_takes_precedence_over_ip(stored_posts):
    posts, total = await board_service.get_posts(
        stored_posts("friend"), type="friend", author_user_id=10, author_ip=IP,
    )
    assert [post.id for post in posts] == [4, 1]
    assert total == 2


async def test_normal_listing_keeps_other_peoples_posts(stored_posts):
    posts, total = await board_service.get_posts(stored_posts("friend"), type="friend")
    assert [post.id for post in posts] == [5, 4, 3, 2, 1]
    assert total == 5


@pytest.mark.parametrize("viewer_id", (10, None))
async def test_joinable_filter_uses_the_same_ownership_rule(no_external_services, viewer_id):
    db = SimpleNamespace(fetchrow=AsyncMock(return_value=(0,)))
    await board_service.get_posts(
        db, type="friend", only_joinable=True, target_user=_user(viewer_id), viewer_ip=IP,
    )
    sql, *params = db.fetchrow.call_args.args
    if viewer_id is not None:
        assert IP not in params
        assert "host_ip =" not in sql
        assert viewer_id in params
    else:
        assert "host_id IS NULL AND host_ip =" in sql
        assert IP in params


async def test_guest_queries_ignore_old_cached_account_posts(monkeypatch, stored_posts):
    # 旧キーに他人のアカウント投稿が残っていても、新しい絞り込みで取り直す
    db = stored_posts("friend")
    old_keys = {f"last_post_info:friend_{board_service.get_normalized_ip(IP)}": 3}
    await board_service.get_posts(db, type="friend", author_ip=IP)
    new_key = board_service.set_cache.call_args.args[0]
    assert new_key.endswith("_owner_v2")
    old_keys[new_key.removesuffix("_owner_v2")] = {"posts": [_post(3, host_id=20).to_dict()], "total": 1}
    monkeypatch.setattr(board_service, "get_cache", AsyncMock(side_effect=old_keys.get))
    posts, total = await board_service.get_posts(db, type="friend", author_ip=IP)
    assert [post.id for post in posts] == [2] and total == 1
    assert (await board_service.get_last_post(db, IP, type="friend")).id == 2
