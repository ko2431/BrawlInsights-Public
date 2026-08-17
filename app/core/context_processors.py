from fastapi import Request
from app.core.logger import logger
from app.core.config import settings
from app.services.ad_banner_service import get_random_ad_banner

IOS_APP_STORE_URL = "https:// [この部分は公開用リポジトリでは非公開にされています]


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
    platform = getattr(request.state, "platform", "web")
    use_admob_test_ads = is_test_ip or bool(current_user and current_user.is_admin)
    #TODO: Play 未公開の間は Android は常にテスト広告にする。公開後にこの固定を外す。
    if platform == "android":
        use_admob_test_ads = True
    return {
        "client_ip": client_ip,
        "is_test_ip": is_test_ip,
        "use_admob_test_ads": use_admob_test_ads,
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
