"""Клиент ProOnline CRM: персональные анкеты оплаты и проверка webhook."""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Standard Webhooks (whsec_…): допуск по времени подписи
SIGNATURE_TOLERANCE_SEC = 5 * 60


class ProOnlineError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def _webhook_signing_key(secret: str) -> bytes:
    raw = (secret or "").strip()
    if raw.startswith("whsec_"):
        raw = raw[len("whsec_") :]
        try:
            return base64.b64decode(raw)
        except Exception:
            return raw.encode("utf-8")
    return raw.encode("utf-8")


def verify_webhook_signature(
    *,
    body: bytes,
    headers: dict[str, str],
    secret: str,
) -> bool:
    """
    Проверка подписи исходящего webhook ProOnline.

    Секрет вида whsec_… — формат Standard Webhooks / Svix:
    headers webhook-id, webhook-timestamp, webhook-signature (v1,<base64>).

    Дополнительно принимаем простой HMAC-SHA256(body) в hex/base64
    в заголовках X-ProOnline-Signature / X-Webhook-Signature / Webhook-Signature.
    """
    secret = (secret or "").strip()
    if not secret:
        logger.error("PROONLINE_WEBHOOK_SECRET is empty")
        return False

    normalized = {str(k).lower(): str(v) for k, v in headers.items() if v is not None}

    msg_id = normalized.get("webhook-id") or normalized.get("svix-id")
    timestamp = normalized.get("webhook-timestamp") or normalized.get("svix-timestamp")
    signature_hdr = (
        normalized.get("webhook-signature")
        or normalized.get("svix-signature")
        or normalized.get("x-proonline-signature")
        or normalized.get("x-webhook-signature")
    )

    if msg_id and timestamp and signature_hdr and secret.startswith("whsec_"):
        try:
            ts = int(timestamp)
        except ValueError:
            logger.warning("ProOnline webhook: bad timestamp %s", timestamp)
            return False
        if abs(int(time.time()) - ts) > SIGNATURE_TOLERANCE_SEC:
            logger.warning("ProOnline webhook: timestamp outside tolerance")
            return False

        key = _webhook_signing_key(secret)
        to_sign = f"{msg_id}.{timestamp}.".encode("utf-8") + body
        digest = hmac.new(key, to_sign, hashlib.sha256).digest()
        expected_b64 = base64.b64encode(digest).decode("ascii")
        expected_hex = digest.hex()

        for part in signature_hdr.replace(" ", ",").split(","):
            part = part.strip()
            if not part:
                continue
            if part.startswith("v1,"):
                part = part[3:]
            elif part.startswith("v1="):
                part = part[3:]
            if hmac.compare_digest(part, expected_b64) or hmac.compare_digest(
                part.lower(), expected_hex
            ):
                return True
        logger.warning("ProOnline webhook: Standard Webhooks signature mismatch")
        return False

    # Fallback: HMAC over raw body with secret string (or decoded whsec key)
    if signature_hdr:
        key = _webhook_signing_key(secret)
        digest = hmac.new(key, body, hashlib.sha256).digest()
        expected_hex = digest.hex()
        expected_b64 = base64.b64encode(digest).decode("ascii")
        candidates = []
        for part in signature_hdr.replace(" ", ",").split(","):
            part = part.strip()
            if part.startswith("sha256="):
                part = part[7:]
            if part.startswith("v1,"):
                part = part[3:]
            if part:
                candidates.append(part)
        for cand in candidates:
            if hmac.compare_digest(cand.lower(), expected_hex) or hmac.compare_digest(
                cand, expected_b64
            ):
                return True
        # also try utf-8 secret without base64 decode
        alt = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        for cand in candidates:
            if hmac.compare_digest(cand.lower(), alt):
                return True
        logger.warning("ProOnline webhook: HMAC body signature mismatch")
        return False

    logger.warning("ProOnline webhook: missing signature headers")
    return False


class ProOnlineClient:
    def __init__(self) -> None:
        self.base_url = (settings.PROONLINE_API_BASE or "").rstrip("/")
        self.api_key = (settings.PROONLINE_API_KEY or "").strip()

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    async def create_payment_form_session(
        self,
        *,
        external_order_id: str,
        title: str,
        amount: float,
    ) -> dict[str, Any]:
        if not self.configured:
            raise ProOnlineError("ProOnline API не настроен (PROONLINE_API_KEY)")

        payload = {
            "external_order_id": external_order_id,
            "title": title,
            "amount": float(amount),
        }
        url = f"{self.base_url}/payment-form-sessions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            logger.exception("ProOnline create session network error")
            raise ProOnlineError(f"Сеть ProOnline: {exc}") from exc

        data: Any
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text[:500]}

        if resp.status_code not in (200, 201):
            msg = (
                (data.get("error") or {}).get("message")
                if isinstance(data, dict)
                else None
            ) or f"HTTP {resp.status_code}"
            logger.error("ProOnline create session failed: %s %s", resp.status_code, data)
            raise ProOnlineError(msg, status_code=resp.status_code, payload=data)

        if not isinstance(data, dict) or not data.get("questionnaire_url"):
            raise ProOnlineError("ProOnline не вернул questionnaire_url", payload=data)
        return data

    async def get_payment_form_session(self, external_order_id: str) -> dict[str, Any]:
        if not self.configured:
            raise ProOnlineError("ProOnline API не настроен (PROONLINE_API_KEY)")
        url = f"{self.base_url}/payment-form-sessions/{external_order_id}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers)
        data = resp.json() if resp.content else {}
        if resp.status_code != 200:
            raise ProOnlineError(
                f"Статус сессии: HTTP {resp.status_code}",
                status_code=resp.status_code,
                payload=data,
            )
        return data

    async def register_webhook(self, *, url: str, secret: str) -> dict[str, Any]:
        """PUT /webhook — привязать URL и секрет на стороне CRM (один раз)."""
        if not self.configured:
            raise ProOnlineError("ProOnline API не настроен")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        payload = {"url": url, "secret": secret}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.put(
                f"{self.base_url}/webhook", json=payload, headers=headers
            )
        data = resp.json() if resp.content else {}
        if resp.status_code not in (200, 201):
            raise ProOnlineError(
                f"Регистрация webhook: HTTP {resp.status_code}",
                status_code=resp.status_code,
                payload=data,
            )
        return data if isinstance(data, dict) else {"ok": True}
