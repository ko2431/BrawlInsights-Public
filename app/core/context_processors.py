from fastapi import Request
from app.core.logger import logger
from app.core.config import settings
from app.services.ad_banner_service import get_random_ad_banner

# 同期関数に変更し、request.stateからユーザー情報を取得
def user_processor(request: Request) -> dict:
    """
    request.state から現在のユーザー情報を取得し、テンプレートコンテキストに渡す。
    この関数は UserToStateMiddleware によって request.state.current_user が
    設定された後に呼び出されることを想定している。
    """
    current_user_from_state = getattr(request.state, "current_user", None)
    # logger.debug(f"Context Processor: current_user from state is type: {type(current_user_from_state)}")
    return {"current_user": current_user_from_state}

def platform_processor(request: Request) -> dict:
    """
    request.state からプラットフォーム情報を取得し、テンプレートコンテキストに渡す。
    この関数は PlatformDetectionMiddleware によって request.state.platform が
    設定された後に呼び出されることを想定している。
    """
    platform_from_state = getattr(request.state, "platform", "web") # デフォルトは 'web'
    return {"platform": platform_from_state}

def version_processor(request: Request) -> dict:
    """
    request.state からバージョン情報を取得し、テンプレートコンテキストに渡す。
    """
    app_client_version_from_state = getattr(request.state, "app_client_version", None)
    return {"app_client_version": app_client_version_from_state}

def settings_processor(request: Request) -> dict:
    """
    設定情報(settings)をテンプレートコンテキストに渡す。
    """
    return {"settings": settings}

def ip_processor(request: Request) -> dict:
    """
    クライアントのIPアドレスを取得し、テスト用IPかどうかを判定して
    テンプレートコンテキストに渡す。
    """
    from app.utils.utils import get_remote_ip
    client_ip = get_remote_ip(request)
    is_test_ip = (client_ip == settings.HOME_IP) if settings.HOME_IP else False
    return {
        "client_ip": client_ip,
        "is_test_ip": is_test_ip
    }


def ad_banner_processor(request: Request) -> dict:
    """
    バナー広告を抽選してテンプレートコンテキストに渡す。
    ログインユーザーで is_delete_ads=True かつ is_admin=False の場合はバナーを非表示（None）。
    包括匿名ユーザーの広告削除対応はテンプレート側の Alpine.jsで行う（localStorage:'bi_guest_remove_ads'）。
    """
    current_user = getattr(request.state, "current_user", None)
    platform = getattr(request.state, "platform", "web")
    # パスパラメータから lang を取得（/{lang}/ を含むルートのみ。ない場合は 'ja' をデフォルト）
    lang = request.path_params.get("lang", "ja")

    # 広告削除ユーザー（管理者を除く）にはバナーを返さない
    if current_user and current_user.is_delete_ads and not current_user.is_admin:
        return {"ad_banner": None}

    try:
        banner = get_random_ad_banner(lang=lang, platform=platform)
    except Exception:
        logger.warning("ad_banner_processorでバナー抽選に失敗しました。バナーは非表示になります。", exc_info=True)
        banner = None

    return {"ad_banner": banner}
