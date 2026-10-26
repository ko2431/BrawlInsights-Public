from fastapi import Request
from app.core.logger import logger
from app.core.config import settings
# [この部分は公開用リポジトリでは非公開にされています]
from app.services.ad_banner_service import get_random_ad_banner

IOS_APP_STORE_URL = "https://apps.apple.com/app/id6751238098"
ANDROID_PLAY_STORE_URL = "https://play.google.com/store/apps/details?id=com.brawlinsights.app"

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


def store_url_processor(request: Request) -> dict:
    """WebのAndroid端末だけ Play、それ以外（iOS端末・Windows等）は App Store へ誘導する。"""
    platform = getattr(request.state, "platform", "web")
    user_agent = request.headers.get("user-agent", "")
    prefer_play_store = platform == "android" or (platform == "web" and "Android" in user_agent)
    return {
        "prefer_play_store": prefer_play_store,
        "app_store_url": ANDROID_PLAY_STORE_URL if prefer_play_store else IOS_APP_STORE_URL,
        "ios_app_store_url": IOS_APP_STORE_URL,
        "android_play_store_url": ANDROID_PLAY_STORE_URL,
    }

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

# [この部分は公開用リポジトリでは非公開にされています]


def ip_processor(request: Request) -> dict:
    """
    クライアントのIPアドレスを取得し、テスト用IPかどうかを判定して
    テンプレートコンテキストに渡す。
    """
    from app.utils.utils import get_remote_ip
    client_ip = get_remote_ip(request)
    is_test_ip = (client_ip == settings.HOME_IP) if settings.HOME_IP else False
    # [この部分は公開用リポジトリでは非公開にされています]
    current_user = getattr(request.state, "current_user", None)
    use_admob_test_ads = is_test_ip or bool(current_user and current_user.is_admin)
    return {
        "client_ip": client_ip,
        "is_test_ip": is_test_ip,
        "use_admob_test_ads": use_admob_test_ads,
        # [この部分は公開用リポジトリでは非公開にされています]
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

    # [この部分は公開用リポジトリでは非公開にされています]

    # 広告削除ユーザー（管理者を除く）にはバナーを返さない
    if current_user and current_user.is_delete_ads and not current_user.is_admin:
        return {"ad_banner": None}

    try:
        banner = get_random_ad_banner(lang=lang, platform=platform)
    except Exception:
        logger.warning("ad_banner_processorでバナー抽選に失敗しました。バナーは非表示になります。", exc_info=True)
        banner = None

    return {"ad_banner": banner}


def page_integrity_processor(request: Request) -> dict:
    """Web版の計測スクリプト到達確認用フラグをテンプレートへ渡す。"""
    from app.core.page_integrity import is_integrity_ok, is_integrity_subject
    return {
        "page_integrity_ok": is_integrity_ok(request),
        "page_integrity_gate_active": is_integrity_subject(request),
    }


def board_notification_processor(request: Request) -> dict:
    """
    募集掲示板タブ用の通知バッジ情報をテンプレートコンテキストに渡す。
    UserToStateMiddleware が request.state.board_notification_context を設定済みであること前提。
    """
    context = getattr(request.state, "board_notification_context", None)
    if not isinstance(context, dict):
        return {
            "unread_badge_count": 0,
            "show_notification_badge": False,
            "notification_badge_text": "",
        }
    return {
        "unread_badge_count": int(context.get("unread_badge_count", 0) or 0),
        "show_notification_badge": bool(context.get("show_notification_badge")),
        "notification_badge_text": context.get("notification_badge_text") or "",
    }


# [この部分は公開用リポジトリでは非公開にされています]


def tournament_notification_processor(request: Request) -> dict:
    """
    ツールタブの大会の通知バッジと、画面内の通知（WebSocket）の接続をテンプレートに渡す。
    UserToStateMiddleware が request.state.tournament_notification_context を設定済みであること前提。
    """
    context = getattr(request.state, "tournament_notification_context", None) or {}
    unread = int(context.get("unread") or 0)
    return {
        "tn_badge_enabled": bool(context.get("enabled")),
        "tn_unread": unread,
        "tn_badge_text": f"{unread}" if unread < 100 else "99+",
        "tn_notify_connect": bool(context.get("connect")),
    }
