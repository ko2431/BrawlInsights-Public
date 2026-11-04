"""テンプレートの url_for(照合結果を使い回す版)が、Starlette の url_for と同じ結果を返すことを検証する。"""

import pytest
from starlette.requests import Request
from starlette.routing import NoMatchFound

from app.core.templating import _get_url_path_resolver, cached_url_for, templates
from app.main import app


def _request(host: str = "example.com", scheme: str = "https", port: int = 443) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"host", host.encode())],
        "query_string": b"",
        "scheme": scheme,
        "server": (host.split(":")[0], port),
        "router": app.router,
        "app": app,
    }
    return Request(scope)


@pytest.mark.parametrize("name, params", [
    ("static", {"path": "/css/pages/player.css"}),
    ("static", {"path": "/images/ui/trophy.png"}),
    ("home", {"lang": "ja"}),
    ("get_player_profile", {"lang": "ja", "tag": "PCJJPRP"}),
    ("get_player_battles", {"lang": "en", "tag": "PCJJPRP"}),
    ("get_club_profile", {"lang": "ja", "tag": "2YQ8PCQ"}),
])
def test_cached_url_for_matches_starlette(name, params):
    template = templates.env.from_string("{{ url_for(name, **params) }}")
    for request in (_request(), _request("localhost:8000", "http", 8000)):
        expected = str(request.url_for(name, **params))
        # 1回目は照合し、2回目は使い回した結果を返す
        assert template.render(request=request, name=name, params=params) == expected
        assert template.render(request=request, name=name, params=params) == expected


def test_cached_url_for_reuses_route_lookup():
    request = _request()
    resolver = _get_url_path_resolver(app.router)
    hits_before = resolver.cache_info().hits
    for _ in range(3):
        cached_url_for({"request": request}, "static", path="/images/ui/club.webp")
    assert resolver.cache_info().hits >= hits_before + 2


def test_cached_url_for_raises_for_unknown_route():
    with pytest.raises(NoMatchFound):
        cached_url_for({"request": _request()}, "no_such_route_name")
