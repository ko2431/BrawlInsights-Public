"""プッシュ通知の端末の登録・削除と、受け取る種類の設定（検討メモ notes_private/push_image_plan_2026-10-27.md の 3.4・3.5）。

端末の登録はログインしている人だけ（PD15）。Service Worker（/sw.js）は main.py で配る。
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
import asyncpg
from pydantic import BaseModel, Field

from app.core.logger import logger
from app.db.db import get_shared_db
from app.services import push_rules, push_service
from app.services.user_service import User

router = APIRouter(
    prefix="/{lang}/push",
    tags=["push"],
)


async def get_current_active_user(request: Request) -> User:
    user = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


def _error(lang: str, code: int = status.HTTP_400_BAD_REQUEST) -> JSONResponse:
    message = "無効なリクエストです。" if lang == "ja" else "Invalid request."
    if code >= 500:
        message = ("データベースエラーのため、設定を更新できませんでした。" if lang == "ja"
                   else "Could not update settings due to a database error.")
    return JSONResponse({"success": False, "message": message}, status_code=code)


class DeviceRequest(BaseModel):
    platform: str
    token: str = Field(..., min_length=1, max_length=4096)
    p256dh: str | None = None
    auth: str | None = None


class DeviceRemoveRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=4096)


class PushSettingRequest(BaseModel):
    setting_key: str
    enabled: bool


@router.post("/devices", name="push_register_device")
async def register_device(
    request: Request,
    lang: str,
    payload: DeviceRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    """この端末で受け取る（Web はブラウザの購読、アプリは FCM のトークン）。同じ端末の再登録は最終確認の日時を延ばすだけ。"""
    platform = payload.platform
    # Web の購読はブラウザから、アプリのトークンはアプリからだけ受け付ける
    # （iPad のアプリは UA に iPad が入らず platform が "app" になることがあるので、アプリ同士は区別しない）
    from_app = request.state.platform != "web"
    if (platform == "web") == from_app or not push_service.validate_device(
            platform, payload.token, payload.p256dh, payload.auth):
        return _error(lang)
    if platform == "web" and not push_service.web_push_public_key():
        return _error(lang)
    try:
        await push_service.register_device(
            db, user_id=current_user.id, platform=platform, token=payload.token, p256dh=payload.p256dh,
            auth=payload.auth, lang=lang, app_version=request.state.app_client_version,
        )
    except asyncpg.PostgresError as e:
        logger.error(f"プッシュ通知の端末の登録中にエラー (User ID: {current_user.id}): {e}", exc_info=True)
        return _error(lang, status.HTTP_500_INTERNAL_SERVER_ERROR)
    return {"success": True}


@router.post("/devices/remove", name="push_remove_device")
async def remove_device(
    lang: str,
    payload: DeviceRemoveRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    """この端末で受け取らない（オフにしたとき・ログアウトの前・別のアカウントの購読が残っていたとき）。"""
    try:
        removed = await push_service.remove_device(db, payload.token)
    except asyncpg.PostgresError as e:
        logger.error(f"プッシュ通知の端末の削除中にエラー (User ID: {current_user.id}): {e}", exc_info=True)
        return _error(lang, status.HTTP_500_INTERNAL_SERVER_ERROR)
    return {"success": True, "removed": removed}


@router.post("/settings", name="push_update_setting")
async def update_setting(
    lang: str,
    payload: PushSettingRequest,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(get_current_active_user),
):
    if payload.setting_key not in push_rules.PUSH_SETTING_KEYS:
        return _error(lang)
    try:
        await push_service.update_push_setting(db, current_user.id, payload.setting_key, payload.enabled)
    except asyncpg.PostgresError as e:
        logger.error(f"プッシュ通知の設定の更新中にエラー (User ID: {current_user.id}): {e}", exc_info=True)
        return _error(lang, status.HTTP_500_INTERNAL_SERVER_ERROR)
    logger.info(
        f"{current_user.name} (ID: {current_user.id}) がプッシュ通知の設定 {payload.setting_key} を {payload.enabled} に変更しました。"
    )
    return {"success": True, "message": "設定を更新しました。" if lang == "ja" else "Settings updated successfully."}


# [この部分は公開用リポジトリでは非公開にされています]
