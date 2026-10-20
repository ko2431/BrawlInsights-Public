"""
管理画面の表セルに置く「絞込」ボタン・バッジ用のURL生成ヘルパー。

現在のクエリを維持したまま1つのキーだけを上書きし、ページ位置と
その画面の「個別指定」キー（1件だけを指すID）は自動で外す。
すでに同じ値で絞り込み中の場合は、そのキーだけを外す（トグル）。
"""
from urllib.parse import urlencode

from fastapi import Request

# ページ位置を表すキー。絞込条件が変わるので常に外す。
_PAGINATION_KEYS = ("page", "c")

# 画面ごとの「個別指定」キー。残すと結果が1件のままになるため、絞込時に外す。
_EXCLUSIVE_KEYS_BY_ROUTE: dict[str, tuple[str, ...]] = {
    "admin_messages": ("message_id",),
    "admin_posts": ("post_id",),
    "admin_reports": ("report_id",),
    "admin_token_gifts": ("gift_id",),
    "admin_reactions": ("reaction_id",),
    "admin_blocks": ("block_id",),
    "admin_feedbacks": ("feedback_id",),
    "admin_image_generation_jobs": ("job_id",),
    "admin_purchases": ("purchase_id",),
}


def _normalize(value) -> str:
    # タグは "#" の有無と大文字小文字を区別せずに比較する（IDやIPには影響しない）
    return str(value if value is not None else "").strip().lstrip("#").casefold()


def _route_name(request: Request) -> str:
    route = request.scope.get("route")
    return getattr(route, "name", "") or ""


def _base_for_key(request: Request, key: str) -> tuple[str, str]:
    """キーごとの「他の条件だけを残したクエリ」と現在値をリクエスト内でキャッシュする。

    ログ画面などは1ページに数千行あり、行ごとにクエリを組み立て直すと重いため。
    """
    cache = getattr(request.state, "admin_filter_link_cache", None)
    if cache is None:
        cache = {}
        request.state.admin_filter_link_cache = cache
    cached = cache.get(key)
    if cached is None:
        drop_keys = set(_PAGINATION_KEYS) | set(_EXCLUSIVE_KEYS_BY_ROUTE.get(_route_name(request), ()))
        drop_keys.add(key)
        params = [(k, v) for k, v in request.query_params.multi_items() if k not in drop_keys]
        cached = (urlencode(params), _normalize(request.query_params.get(key)))
        cache[key] = cached
    return cached


def admin_filter_active(request: Request, key: str, value) -> bool:
    """指定キーが現在その値で絞り込まれているか。"""
    _, current = _base_for_key(request, key)
    return current != "" and current == _normalize(value)


def admin_filter_url(request: Request, key: str, value) -> str:
    """絞込（または絞込済みなら解除）後の相対URL（?から始まる）を返す。"""
    base, _ = _base_for_key(request, key)
    if admin_filter_active(request, key, value):
        # クエリが空になる場合も "?" を返し、同じパスのまま開き直す
        return f"?{base}"
    added = urlencode([(key, str(value))])
    return f"?{base}&{added}" if base else f"?{added}"
