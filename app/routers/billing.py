import datetime
import json
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.config import settings
from app.core.logger import logger
from app.db.db import get_shared_db
from app.services.user_service import User, get_user_include_invalid
from app.services.admin_notification_service import emit_admin_notification

router = APIRouter(prefix="/billing", tags=["Billing Webhook"])
lang_router = APIRouter(prefix="/{lang}/billing", tags=["Billing"])

PROVIDER_REVENUECAT = "revenuecat"
REMOVE_ADS_PRODUCT_ID = "com.brawlinsights.remove_ads"
REMOVE_ADS_ENTITLEMENT_ID = "remove_ads"
SUPPORT_PRODUCT_PREFIX = "com.brawlinsights.support."
SUPPORT_PRODUCT_PRICE_TEXT = {
    "com.brawlinsights.support.tier1": "100円",
    "com.brawlinsights.support.tier2": "500円",
    "com.brawlinsights.support.tier3": "1,000円",
    "com.brawlinsights.support.tier4": "2,000円",
    "com.brawlinsights.support.tier5": "3,000円",
    "com.brawlinsights.support.tier6": "5,000円",
}

ACTIVATE_ENTITLEMENT_EVENTS = {
    "INITIAL_PURCHASE",
    "NON_RENEWING_PURCHASE",
    "RENEWAL",
    "UNCANCELLATION",
    "TRANSFER",
}
DEACTIVATE_ENTITLEMENT_EVENTS = {
    "CANCELLATION",
    "REFUND",
    "EXPIRATION",
    "SUBSCRIPTION_PAUSED",
}


def _require_login_user(request: Request) -> User:
    user = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def _normalize_event_timestamp(value: Any) -> datetime.datetime | None:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        # RevenueCatの *_ms はミリ秒。秒が来ても扱えるようにする。
        ts = float(value)
        if ts > 10_000_000_000:
            ts = ts / 1000
        return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)

    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            dt = datetime.datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt.astimezone(datetime.timezone.utc)
        except ValueError:
            return None

    return None


def _parse_user_id(app_user_id: str | None) -> int | None:
    if not app_user_id:
        return None
    value = app_user_id.strip()
    if value.isdigit():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def _is_valid_webhook_auth(authorization: str | None, expected: str | None) -> bool:
    if not expected:
        return True

    provided = (authorization or "").strip()
    if not provided:
        return False

    if provided == expected:
        return True

    return provided == f"Bearer {expected}"


def _extract_event(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("event"), dict):
        return payload["event"]
    return payload


def _format_user_label(name: str | None, user_id: int | None) -> str:
    display_name = (name or "不明なユーザー").strip() or "不明なユーザー"
    if user_id is None:
        return f"{display_name} (ID: 不明)"
    return f"{display_name} (ID: {user_id})"


def _support_price_text(product_id: str | None) -> str:
    if not product_id:
        return "金額不明"
    return SUPPORT_PRODUCT_PRICE_TEXT.get(product_id, product_id)


def _extract_entitlement_id(event: dict[str, Any]) -> str | None:
    entitlement_ids = event.get("entitlement_ids")
    if isinstance(entitlement_ids, list):
        for value in entitlement_ids:
            if isinstance(value, str) and value.strip():
                return value.strip()

    entitlement_id = event.get("entitlement_id")
    if isinstance(entitlement_id, str) and entitlement_id.strip():
        return entitlement_id.strip()

    return None


def _is_remove_ads_event(event_type: str, product_id: str | None, entitlement_id: str | None) -> bool:
    if event_type not in (ACTIVATE_ENTITLEMENT_EVENTS | DEACTIVATE_ENTITLEMENT_EVENTS):
        return False

    if product_id == REMOVE_ADS_PRODUCT_ID:
        return True

    if entitlement_id == REMOVE_ADS_ENTITLEMENT_ID:
        return True

    return False


async def _reconcile_remove_ads_state(
    db: asyncpg.Connection,
    current_user: User,
) -> tuple[bool, dict[str, Any] | None]:
    """
    purchase_events を参照して users.is_delete_ads を再同期する。
    - user_id だけでなく app_user_id(文字列) でも拾う
    - 最新イベントが有効化/無効化どちらかで状態を決める
    """
    relevant_event_types = tuple(ACTIVATE_ENTITLEMENT_EVENTS | DEACTIVATE_ENTITLEMENT_EVENTS)
    row = await db.fetchrow(
        """
        SELECT event_type, product_id, entitlement_id, app_user_id, user_id, event_timestamp, created_at
        FROM purchase_events
        WHERE (user_id = $1 OR app_user_id = $2)
          AND (product_id = $3 OR entitlement_id = $4)
          AND event_type = ANY($5::text[])
        ORDER BY COALESCE(event_timestamp, created_at) DESC
        LIMIT 1
        """,
        current_user.id,
        str(current_user.id),
        REMOVE_ADS_PRODUCT_ID,
        REMOVE_ADS_ENTITLEMENT_ID,
        list(relevant_event_types),
    )

    if not row:
        return False, None

    desired_state = row["event_type"] in ACTIVATE_ENTITLEMENT_EVENTS
    if bool(current_user.is_delete_ads) != desired_state:
        current_user.is_delete_ads = desired_state
        await current_user.update(db)
        if desired_state:
            await current_user.convert_all_tickets_to_tokens(db, token_rate=6)
        logger.info(
            f"remove_ads状態を再同期: user_id={current_user.id}, desired={desired_state}, event_type={row['event_type']}"
        )
        reconciled = True
    else:
        reconciled = False

    return (
        reconciled,
        {
            "event_type": row["event_type"],
            "product_id": row["product_id"],
            "entitlement_id": row["entitlement_id"],
            "app_user_id": row["app_user_id"],
            "user_id": row["user_id"],
            "event_timestamp": row["event_timestamp"].isoformat() if row["event_timestamp"] else None,
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        },
    )


async def _insert_purchase_event(
    db: asyncpg.Connection,
    *,
    user_id: int | None,
    app_user_id: str | None,
    event_type: str,
    product_id: str | None,
    entitlement_id: str | None,
    transaction_id: str | None,
    original_transaction_id: str | None,
    environment: str | None,
    is_sandbox: bool,
    event_timestamp: datetime.datetime | None,
    external_event_id: str | None,
    payload: dict[str, Any] | list[Any] | str,
) -> int | None:
    # asyncpg側でjson/jsonbコーデックを設定済みのため、dict/listをそのまま渡す。
    # ここでjson.dumpsすると、文字列として二重保存されるケースがある。
    payload_obj: dict[str, Any] | list[Any]
    if isinstance(payload, (dict, list)):
        payload_obj = payload
    elif isinstance(payload, str):
        normalized: Any = payload
        # payloadがJSON文字列のJSON文字列...のような多重状態でも可能な限り展開する
        for _ in range(3):
            if not isinstance(normalized, str):
                break
            try:
                normalized = json.loads(normalized)
            except json.JSONDecodeError:
                break

        if isinstance(normalized, (dict, list)):
            payload_obj = normalized
        else:
            payload_obj = {"raw_payload": str(payload)}
    else:
        payload_obj = {"raw_payload": str(payload)}

    if external_event_id:
        row = await db.fetchrow(
            """
            INSERT INTO purchase_events (
                provider,
                external_event_id,
                user_id,
                app_user_id,
                event_type,
                product_id,
                entitlement_id,
                transaction_id,
                original_transaction_id,
                environment,
                is_sandbox,
                event_timestamp,
                raw_payload
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13
            )
            ON CONFLICT (external_event_id) DO NOTHING
            RETURNING id
            """,
            PROVIDER_REVENUECAT,
            external_event_id,
            user_id,
            app_user_id,
            event_type,
            product_id,
            entitlement_id,
            transaction_id,
            original_transaction_id,
            environment,
            is_sandbox,
            event_timestamp,
            payload_obj,
        )
        return row["id"] if row else None

    row = await db.fetchrow(
        """
        INSERT INTO purchase_events (
            provider,
            external_event_id,
            user_id,
            app_user_id,
            event_type,
            product_id,
            entitlement_id,
            transaction_id,
            original_transaction_id,
            environment,
            is_sandbox,
            event_timestamp,
            raw_payload
        ) VALUES (
            $1, NULL, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12
        )
        RETURNING id
        """,
        PROVIDER_REVENUECAT,
        user_id,
        app_user_id,
        event_type,
        product_id,
        entitlement_id,
        transaction_id,
        original_transaction_id,
        environment,
        is_sandbox,
        event_timestamp,
        payload_obj,
    )
    return row["id"] if row else None


@router.post("/revenuecat/webhook", name="revenuecat_webhook")
async def revenuecat_webhook(
    request: Request,
    authorization: str | None = Header(default=None),
    db: asyncpg.Connection = Depends(get_shared_db),
):
    """
    RevenueCat Webhook受信用エンドポイント。
    - external_event_id で冪等化
    - remove_ads の有効/無効を users.is_delete_ads へ反映
    """
    if not _is_valid_webhook_auth(authorization, settings.REVENUECAT_WEBHOOK_AUTH):
        logger.warning("RevenueCat webhook rejected: invalid authorization header")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"success": False, "message": "Invalid JSON payload"}, status_code=400)

    if not isinstance(payload, dict):
        return JSONResponse({"success": False, "message": "Invalid payload type"}, status_code=400)

    event = _extract_event(payload)
    if not isinstance(event, dict):
        return JSONResponse({"success": False, "message": "Missing event object"}, status_code=400)

    event_type = str(event.get("type") or "UNKNOWN").upper()
    external_event_id = event.get("id") or event.get("event_id")
    external_event_id = str(external_event_id).strip() if external_event_id else None

    app_user_id = event.get("app_user_id") or event.get("original_app_user_id")
    app_user_id = str(app_user_id).strip() if app_user_id else None
    user_id = _parse_user_id(app_user_id)

    product_id = event.get("product_id")
    product_id = str(product_id).strip() if isinstance(product_id, str) and product_id.strip() else None

    entitlement_id = _extract_entitlement_id(event)

    transaction_id = event.get("transaction_id")
    transaction_id = str(transaction_id).strip() if isinstance(transaction_id, str) and transaction_id.strip() else None

    original_transaction_id = event.get("original_transaction_id")
    original_transaction_id = (
        str(original_transaction_id).strip()
        if isinstance(original_transaction_id, str) and original_transaction_id.strip()
        else None
    )

    environment = event.get("environment")
    environment = str(environment).strip() if isinstance(environment, str) and environment.strip() else None
    is_sandbox = environment == "SANDBOX"

    event_timestamp = (
        _normalize_event_timestamp(event.get("event_timestamp_ms"))
        or _normalize_event_timestamp(event.get("purchased_at_ms"))
        or _normalize_event_timestamp(event.get("event_timestamp"))
        or _normalize_event_timestamp(event.get("purchased_at"))
    )

    logger.info(
        "RevenueCat webhook received: "
        f"event_type={event_type}, product_id={product_id}, entitlement_id={entitlement_id}, "
        f"app_user_id={app_user_id}, parsed_user_id={user_id}, external_event_id={external_event_id}, env={environment}"
    )

    try:
        inserted_event_id = await _insert_purchase_event(
            db,
            user_id=user_id,
            app_user_id=app_user_id,
            event_type=event_type,
            product_id=product_id,
            entitlement_id=entitlement_id,
            transaction_id=transaction_id,
            original_transaction_id=original_transaction_id,
            environment=environment,
            is_sandbox=is_sandbox,
            event_timestamp=event_timestamp,
            external_event_id=external_event_id,
            payload=payload,
        )
    except asyncpg.PostgresError as e:
        logger.error(f"RevenueCat webhook DB保存エラー: {e}", exc_info=True)
        return JSONResponse({"success": False, "message": "Database error"}, status_code=500)

    remove_ads_updated = False
    target_user: User | None = None
    if user_id is not None:
        try:
            target_user = await get_user_include_invalid(db, user_id)
        except Exception as e:
            logger.warning(f"ユーザー情報取得失敗 (user_id={user_id}): {e}")

    if inserted_event_id is not None and user_id is not None and _is_remove_ads_event(event_type, product_id, entitlement_id):
        try:
            user = target_user or await get_user_include_invalid(db, user_id)
            if user:
                if event_type in ACTIVATE_ENTITLEMENT_EVENTS and not user.is_delete_ads:
                    user.is_delete_ads = True
                    await user.update(db)
                    await user.convert_all_tickets_to_tokens(db, token_rate=6)
                    remove_ads_updated = True
                elif event_type in DEACTIVATE_ENTITLEMENT_EVENTS and user.is_delete_ads:
                    user.is_delete_ads = False
                    await user.update(db)
                    remove_ads_updated = True
        except Exception as e:
            logger.error(f"remove_ads反映エラー (user_id={user_id}, event_type={event_type}): {e}", exc_info=True)
            return JSONResponse({"success": False, "message": "Failed to update user state"}, status_code=500)

    if inserted_event_id is not None:
        user_label = _format_user_label(target_user.name if target_user else None, user_id)

        if event_type in ACTIVATE_ENTITLEMENT_EVENTS and (
            product_id == REMOVE_ADS_PRODUCT_ID or entitlement_id == REMOVE_ADS_ENTITLEMENT_ID
        ):
            logger.info(f"{user_label}が広告の削除を購入しました。")

        if (
            product_id
            and product_id.startswith(SUPPORT_PRODUCT_PREFIX)
            and event_type in {"INITIAL_PURCHASE", "NON_RENEWING_PURCHASE"}
        ):
            logger.info(f"{user_label}がアプリを支援する({_support_price_text(product_id)})を購入しました。")

        if event_type in {"INITIAL_PURCHASE", "NON_RENEWING_PURCHASE"}:
            purchase_event_key = "purchase_new"
            purchase_title = "新しい購入"
        elif event_type in {"CANCELLATION", "REFUND", "EXPIRATION"}:
            purchase_event_key = "purchase_refund"
            purchase_title = "購入がキャンセル／返金されました"
        else:
            purchase_event_key = "purchase_other"
            purchase_title = "購入イベント"
        await emit_admin_notification(
            db,
            purchase_event_key,
            title=purchase_title,
            summary=f"{event_type} / {product_id or entitlement_id or '-'} / {user_label}",
            payload={
                "event_id": inserted_event_id,
                "event_type": event_type,
                "product_id": product_id,
                "user_id": user_id,
            },
        )

    return JSONResponse(
        {
            "success": True,
            "stored": inserted_event_id is not None,
            "event_id": inserted_event_id,
            "remove_ads_updated": remove_ads_updated,
        },
        status_code=200,
    )


@lang_router.get("/status", name="billing_status")
async def billing_status(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(_require_login_user),
):
    del request
    del lang

    reconciled, remove_ads_event = await _reconcile_remove_ads_state(db, current_user)

    latest = await db.fetchrow(
        """
        SELECT event_type, product_id, event_timestamp, created_at
        FROM purchase_events
        WHERE user_id = $1
        ORDER BY COALESCE(event_timestamp, created_at) DESC
        LIMIT 1
        """,
        current_user.id,
    )

    last_event = None
    if latest:
        last_event = {
            "event_type": latest["event_type"],
            "product_id": latest["product_id"],
            "event_timestamp": latest["event_timestamp"].isoformat() if latest["event_timestamp"] else None,
            "created_at": latest["created_at"].isoformat() if latest["created_at"] else None,
        }

    return JSONResponse(
        {
            "success": True,
            "is_delete_ads": bool(current_user.is_delete_ads),
            "last_event": last_event,
            "remove_ads_reconciled": reconciled,
            "remove_ads_event": remove_ads_event,
        }
    )


@lang_router.get("/support-history", name="billing_support_history")
async def support_history(
    request: Request,
    lang: str,
    limit: int = Query(default=30, ge=1, le=100),
    db: asyncpg.Connection = Depends(get_shared_db),
    current_user: User = Depends(_require_login_user),
):
    del request
    del lang

    rows = await db.fetch(
        """
        SELECT id, event_type, product_id, transaction_id, event_timestamp, created_at, is_sandbox
        FROM purchase_events
        WHERE user_id = $1
          AND product_id LIKE $2
          AND event_type IN ('INITIAL_PURCHASE', 'NON_RENEWING_PURCHASE')
        ORDER BY COALESCE(event_timestamp, created_at) DESC
        LIMIT $3
        """,
        current_user.id,
        f"{SUPPORT_PRODUCT_PREFIX}%",
        limit,
    )

    history = [
        {
            "id": row["id"],
            "event_type": row["event_type"],
            "product_id": row["product_id"],
            "transaction_id": row["transaction_id"],
            "event_timestamp": row["event_timestamp"].isoformat() if row["event_timestamp"] else None,
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "is_sandbox": bool(row["is_sandbox"]),
        }
        for row in rows
    ]

    return JSONResponse({"success": True, "history": history, "count": len(history)})


class RestoreLogRequest(BaseModel):
    restored_remove_ads: bool = True


@lang_router.post("/log-restore", name="billing_log_restore")
async def billing_log_restore(
    request: Request,
    lang: str,
    payload: RestoreLogRequest,
    current_user: User = Depends(_require_login_user),
):
    del request
    del lang

    if payload.restored_remove_ads:
        logger.info(f"{_format_user_label(current_user.name, current_user.id)}が広告削除の復元を行いました。")
        return JSONResponse({"success": True, "logged": True})

    return JSONResponse({"success": True, "logged": False})
