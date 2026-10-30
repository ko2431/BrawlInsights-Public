"""画像のアップロード（共通の土台。検討メモ notes_private/push_image_plan_2026-10-27.md の 6.1・6.2）。

POST /{lang}/media/upload?kind=… は、本番では Nginx が親サーバーのアプリだけに送る（ファイルは親のディスクに置く）。
種類ごとの決まり（上げられる人・回数の上限など）は media_service.KINDS。上げた画像は /media/… で Nginx が配る。
"""
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile
import asyncpg

from app.core.logger import logger
from app.db.db import get_shared_db
from app.services import media_service
from app.services.media_service import MediaError

router = APIRouter(
    prefix="/{lang}/media",
    tags=["media"],
)

# multipart の区切りなどの分の余裕
MAX_REQUEST_BYTES = media_service.MAX_UPLOAD_BYTES + 64 * 1024


def _error(lang: str, e: MediaError) -> JSONResponse:
    return JSONResponse({"success": False, "code": e.code, "message": e.message(lang)}, status_code=e.status)


@router.post("/upload", name="media_upload")
async def media_upload(
    request: Request,
    lang: str,
    kind: str = Query(..., max_length=32),
    db: asyncpg.Connection = Depends(get_shared_db),
):
    lang = "en" if lang == "en" else "ja"
    user = getattr(request.state, "current_user", None)
    if not user:
        return JSONResponse(
            {"success": False, "code": "login", "message": "ログインしてください。" if lang == "ja" else "Please log in."},
            status_code=401,
        )
    # 本文を読む前に、種類・権限・大きさ・回数を確かめる
    try:
        rule = media_service.get_rule(kind)
        media_service.check_permission(user, rule)
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_REQUEST_BYTES:
            raise MediaError("too_large", "画像は 10MB までです。", "Images must be 10 MB or smaller.", status=413)
        await media_service.consume_rate_limit(user.id, kind, rule)
    except MediaError as e:
        return _error(lang, e)

    try:
        form = await request.form(max_files=1, max_fields=4)
    except Exception as e:
        logger.info(f"画像のアップロードの本文を読めませんでした（{type(e).__name__}: {e}）")
        return _error(lang, MediaError("bad_request", "画像を受け取れませんでした。", "Could not receive the image."))
    try:
        file = form.get("file")
        if not isinstance(file, UploadFile):
            raise MediaError("no_file", "画像を選んでください。", "Please choose an image.")
        data = await file.read(media_service.MAX_UPLOAD_BYTES + 1)
        item = await media_service.upload(db, kind=kind, data=data, filename=file.filename, user_id=user.id)
    except MediaError as e:
        return _error(lang, e)
    except Exception as e:
        logger.error(f"画像のアップロード中にエラー（kind={kind}, ユーザー ID: {user.id}）: {e}", exc_info=True)
        return _error(lang, MediaError(
            "server", "画像を保存できませんでした。もう一度試してください。", "Could not save the image. Please try again.", status=500,
        ))
    finally:
        await form.close()
    return {"success": True, **item}
