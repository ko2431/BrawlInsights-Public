from fastapi.templating import Jinja2Templates
from .context_processors import (
    user_processor,
    settings_processor,
    platform_processor,
    store_url_processor,
    version_processor,
    ip_processor,
    ad_banner_processor,
    page_integrity_processor,
    board_notification_processor,
    # [この部分は公開用リポジトリでは非公開にされています]
    tournament_notification_processor,
)
from app.utils.utils import format_display_datetime, format_last_played_time
# [この部分は公開用リポジトリでは非公開にされています]
from app.services.map_mode_catalog import mode_icon_candidates
from app.services.board_colors import board_dark_color
import functools
import re
from typing import Any, Callable
from jinja2 import pass_context
from markupsafe import Markup
from starlette.datastructures import URL, URLPath
from starlette.requests import Request

# context_processors をリストで渡す
templates = Jinja2Templates(
    directory="app/templates",
    context_processors=[
        user_processor,
        settings_processor,
        platform_processor,
        store_url_processor,
        version_processor,
        ip_processor,
        ad_banner_processor,
        page_integrity_processor,
        board_notification_processor,
        # [この部分は公開用リポジトリでは非公開にされています]
        tournament_notification_processor,
    ],  # type: ignore
)

def parse_app_version(value: str | None) -> tuple[int, ...]:
    """'14.0' や '13.5' を整数タプルにする。数字以外が来た区間で打ち切る。"""
    if not value:
        return ()
    parts: list[int] = []
    for raw in str(value).split("."):
        digits: list[str] = []
        for ch in raw:
            if ch.isdigit():
                digits.append(ch)
            else:
                break
        if not digits:
            break
        parts.append(int("".join(digits)))
    return tuple(parts)


def version_at_least(current: str | None, minimum: str) -> bool:
    """アプリ版数を数値比較する。未設定や不正な値は False。"""
    current_tuple = parse_app_version(current)
    if not current_tuple:
        return False
    minimum_tuple = parse_app_version(minimum)
    length = max(len(current_tuple), len(minimum_tuple), 1)
    current_padded = current_tuple + (0,) * (length - len(current_tuple))
    minimum_padded = minimum_tuple + (0,) * (length - len(minimum_tuple))
    return current_padded >= minimum_padded


# Starlette の url_for は呼ぶたびに全ルートを先頭から照合する（static は400番目付近）。
# キャラや戦績の一覧では1回の描画で千回以上呼ばれ、それだけで1〜2秒かかるため、照合の結果を使い回す。
# ルート表は起動後に変わらないため、出力は変わらない。
URL_PATH_CACHE_SIZE = 20000
_url_path_resolvers: dict[int, tuple[Any, Callable[[str, tuple], URLPath]]] = {}


def _get_url_path_resolver(provider: Any) -> Callable[[str, tuple], URLPath]:
    """ルーター(またはアプリ)ごとに、ルート名とパラメータからURLパスを引く関数(キャッシュ付き)を返す。"""
    entry = _url_path_resolvers.get(id(provider))
    if entry is None or entry[0] is not provider:
        @functools.lru_cache(maxsize=URL_PATH_CACHE_SIZE)
        def resolve(name: str, items: tuple) -> URLPath:
            return provider.url_path_for(name, **dict(items))
        entry = (provider, resolve)
        _url_path_resolvers[id(provider)] = entry
    return entry[1]


@pass_context
def cached_url_for(context: dict[str, Any], name: str, /, **path_params: Any) -> URL:
    """Starlette の url_for と同じ結果を返す。見つからない場合の例外も同じ(例外はキャッシュしない)。"""
    request: Request = context["request"]
    provider = request.scope.get("router") or request.scope.get("app")
    if provider is None:
        return request.url_for(name, **path_params)
    items = tuple(sorted(path_params.items()))
    try:
        hash(items)
    except TypeError:
        # ハッシュできない値は、キャッシュせずにそのまま引く
        return request.url_for(name, **path_params)
    url_path = _get_url_path_resolver(provider)(name, items)
    return url_path.make_absolute_url(base_url=request.base_url)


# カスタムグローバル関数を登録
templates.env.globals['url_for'] = cached_url_for
templates.env.globals['format_display_datetime'] = format_display_datetime
templates.env.globals['format_last_played_time'] = format_last_played_time
templates.env.globals['mode_icon_candidates'] = mode_icon_candidates
templates.env.globals['board_dark_color'] = board_dark_color
templates.env.globals['version_at_least'] = version_at_least
# [この部分は公開用リポジトリでは非公開にされています]

NG_WORDS = [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています]
def censor_filter(text: str) -> str:
    # textが文字列でない場合は、エラーを避けるためにそのまま返す
    if not isinstance(text, str):
        return text
    
    # NGワードリストをループ処理
    for word in NG_WORDS:
        replacement = "*" * len(word)
        text = text.replace(word, replacement)
        
    return text

# 伏字フィルターを登録
templates.env.filters['censor'] = censor_filter


def bold_numbers(text: str | None) -> str | Markup:
    if not text:
        return ""
    if not isinstance(text, str):
        return text
    
    # 整数および小数を太字にするregex
    # 例: 300 -> <b>300</b>, 1.5 -> <b>1.5</b>
    pattern = r"(\d+(?:\.\d+)?%?)"
    result = re.sub(pattern, r"<b>\1</b>", text)
    return Markup(result)

templates.env.filters['bold_numbers'] = bold_numbers
