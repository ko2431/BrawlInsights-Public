"""Web版のクライアント到達確認と、高負荷フラグメントAPIのゲート。"""
from __future__ import annotations

import re
from typing import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response

from app.core.config import settings
from app.core.logger import logger
from app.core.templating import templates

COOKIE_NAME = "bi_ok"
COOKIE_MAX_AGE_SECONDS = 15 * 60
integrity_router = APIRouter()


def is_integrity_ok(request: Request) -> bool:
    result = True
    # [この部分は公開用リポジトリでは非公開にされています]
    return result


def is_integrity_subject(request: Request) -> bool:
    """ゲート対象のWebユーザーか（アプリ・広告削除・管理者は対象外）。"""
    result = False
    # [この部分は公開用リポジトリでは非公開にされています]
    return result


# [この部分は公開用リポジトリでは非公開にされています]


@integrity_router.post("/api/client-ok", include_in_schema=False, name="client_ok")
async def client_ok(request: Request) -> JSONResponse:
    response = JSONResponse({"ok": True})
    # [この部分は公開用リポジトリでは非公開にされています]
    return response


class IntegrityGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[StarletteRequest], Awaitable[Response]],
    ) -> Response:
        # [この部分は公開用リポジトリでは非公開にされています]
        return await call_next(request)
