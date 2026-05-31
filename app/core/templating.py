from fastapi.templating import Jinja2Templates
from .context_processors import user_processor, settings_processor, platform_processor, version_processor, ip_processor, ad_banner_processor
from app.utils.utils import format_display_datetime, format_last_played_time
import re
from markupsafe import Markup

# context_processors をリストで渡す
templates = Jinja2Templates(
    directory="app/templates",
    context_processors=[user_processor, settings_processor, platform_processor, version_processor, ip_processor, ad_banner_processor] # type: ignore
)

# カスタムグローバル関数を登録
templates.env.globals['format_display_datetime'] = format_display_datetime
templates.env.globals['format_last_played_time'] = format_last_played_time

NG_WORDS = [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています], [この部分は公開用リポジトリでは非公開にされています]
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
