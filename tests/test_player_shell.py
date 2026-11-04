"""プレイヤー・クラブのシェルページが、ヘッダー・タブ・サブタブを先に描くことを検証する。"""

import re

import pytest
from httpx import AsyncClient

from app.routers import club as club_router
from app.routers import player as player_router

PLAYER_HEADER = {
    "name": "テスト'プレイヤー",
    "icon_path": "/static/images/player_icon/28000000.webp",
    "club_tag": "#2GG8VQ98P",
    "club_name": "テストクラブ",
}


@pytest.fixture
def known_player(monkeypatch):
    async def fake_header(tag):
        return PLAYER_HEADER
    monkeypatch.setattr(player_router, "_load_player_shell_header", fake_header)


@pytest.fixture
def unknown_player(monkeypatch):
    async def fake_header(tag):
        return None
    monkeypatch.setattr(player_router, "_load_player_shell_header", fake_header)


@pytest.mark.asyncio
@pytest.mark.parametrize("page, label, active", [
    ("profile", "プロフィール", "プロフィール"),
    ("battles", "バトル履歴", "バトル履歴"),
    ("stats", "推移グラフ", "推移グラフ"),
])
async def test_player_shell_renders_header_from_db(client: AsyncClient, known_player, page, label, active):
    response = await client.get(f"/ja/player/{page}/PCJJPRP")
    assert response.status_code == 200
    html = response.text
    assert f"<title>テスト&#39;プレイヤー (#PCJJPRP) {label} - Brawl Insights</title>" in html
    assert 'data-shell-name-raw="テスト&#39;プレイヤー"' in html
    assert "テストクラブ" in html and "/ja/club/profile/2GG8VQ98P" in html
    assert re.search(rf'class="tab-nav__link tab-nav__link--active">{active}</a>', html)
    # ブックマークはスマホ用とPC用の2つ。名前の ' はJSの文字列として逃がす
    assert html.count("data-shell-bookmark") == 2
    assert "bookmarkButtonHandler('#PCJJPRP', 'テスト\\&#39;プレイヤー')" in html
    assert "function bookmarkButtonHandler" in html
    assert "data-shell-pending" not in html


@pytest.mark.asyncio
async def test_player_shell_shows_placeholder_when_not_in_db(client: AsyncClient, unknown_player):
    response = await client.get("/ja/player/profile/PCJJPRP")
    assert response.status_code == 200
    html = response.text
    assert "<title>#PCJJPRP プロフィール - Brawl Insights</title>" in html
    assert "data-shell-pending" in html
    assert 'data-shell-tag>#PCJJPRP</p>' in html
    assert 'data-shell-slot-target="player-identity"' in html


@pytest.mark.asyncio
async def test_battles_shell_sub_tabs_keep_filters(client: AsyncClient, known_player):
    response = await client.get(
        "/en/player/battles/PCJJPRP?tab=trophy&brawler_id=16000000&mode_id=0&after=2026-01-01&before=bad"
    )
    assert response.status_code == 200
    html = response.text
    hrefs = dict(
        (tab, href.replace("&amp;", "&"))
        for href, tab in re.findall(r'<a href="([^"]+)"\s+class="sub-tab-nav__link[^"]*"\s+data-shell-sub-tab="([a-z]+)"', html)
    )
    # 読めない日付(before=bad)はフラグメントと同じく引き継がない
    assert hrefs == {
        tab: f"/en/player/battles/PCJJPRP?tab={tab}&brawler_id=16000000&mode_id=0&after=2026-01-01"
        for tab in ("all", "trophy", "powerleague", "friendly")
    }
    assert re.search(r'data-shell-sub-tab="trophy" aria-current="page"', html)
    assert 'data-shell-slot-target="battle-filters"' in html
    assert 'data-shell-slot-target="api-notice"' in html


@pytest.mark.asyncio
async def test_shell_escapes_query_in_fragment_url(client: AsyncClient, known_player):
    response = await client.get("/ja/player/profile/PCJJPRP?sort=x');alert(1)//")
    assert response.status_code == 200
    # Alpine が JS として評価する x-data の中で、文字列から抜け出せないこと
    loader = re.search(r'x-data="shellFragmentLoader\((.*?)\)"', response.text, re.S).group(1)
    assert "alert(1)" not in loader
    assert "sort=x%27%29%3Balert%281%29//" in loader


@pytest.mark.asyncio
async def test_club_shell_renders_header_from_db(client: AsyncClient, monkeypatch):
    async def fake_lookup(load, *, timeout, label):
        return {"name": "<c5>テスト</c>クラブ", "badge_id": 8000000}
    monkeypatch.setattr(club_router, "run_quick_db_lookup", fake_lookup)
    response = await client.get("/ja/club/profile/2GG8VQ98P")
    assert response.status_code == 200
    html = response.text
    # タイトルはタグを除いた名前、ヘッダーはカラータグごと描く
    assert "<title>テストクラブ (#2GG8VQ98P) クラブ情報 - Brawl Insights</title>" in html
    assert "<h1 class=\"club-header__name\" data-shell-name><c5>テスト</c>クラブ</h1>" in html
    assert "/static/images/club_badges/8000000.png" in html
    assert "function clubBookmarkButtonHandler" in html


@pytest.mark.asyncio
async def test_shell_header_lookup_is_skipped_without_db(client: AsyncClient):
    # テストではアプリのDBプールがないため、ヘッダーは省略して仮表示になる(エラーにはしない)
    response = await client.get("/ja/player/stats/PCJJPRP")
    assert response.status_code == 200
    assert "data-shell-pending" in response.text
