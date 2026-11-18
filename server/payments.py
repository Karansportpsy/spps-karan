"""Payment provider adapters for approval-first bookings (Stage 13B).

Payments are switched off in SPPS Version 1.0 - Free (SPPS_PAYMENT_PROVIDER=disabled). The only adapter is:
* RazorpayProvider — Razorpay TEST keys only (rzp_test_*). Live keys are refused
  until the owner explicitly approves live payments (Gate F in PRODUCTION_READINESS).

Every capture must pass verify_checkout() (signature) and, for real providers,
fetch_payment() readback before the database records it.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.request import Request as UrlRequest, urlopen


class PaymentError(RuntimeError):
    pass


@dataclass(frozen=True)
class VerifiedPayment:
    order_id: str
    payment_id: str
    amount_paise: int


class RazorpayProvider:
    name = "razorpay"
    base = "https://api.razorpay.com/v1"

    def __init__(self, key_id: str, key_secret: str, webhook_secret: str):
        if not key_id.startswith("rzp_test_"):
            raise PaymentError("Only Razorpay TEST keys are permitted until live payments are approved")
        if not key_secret or not webhook_secret:
            raise PaymentError("Razorpay key secret and webhook secret are required")
        self.key_id, self._secret, self._webhook = key_id, key_secret, webhook_secret

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        auth = base64.b64encode(f"{self.key_id}:{self._secret}".encode()).decode()
        data = json.dumps(payload).encode() if payload is not None else None
        req = UrlRequest(self.base + path, data=data, method=method,
                         headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=12) as resp:
                return json.loads(resp.read())
        except HTTPError as err:
            raise PaymentError(f"Payment provider returned HTTP {err.code}") from None
        except Exception:  # noqa: BLE001
            raise PaymentError("Payment provider unavailable") from None

    def create_order(self, amount_paise: int, receipt: str) -> str:
        order = self._request("POST", "/orders", {"amount": amount_paise, "currency": "INR", "receipt": receipt})
        if order.get("amount") != amount_paise or order.get("currency") != "INR" or not order.get("id"):
            raise PaymentError("Provider order mismatch")
        return order["id"]

    def _readback(self, order_id: str, payment_id: str, amount_paise: int) -> VerifiedPayment:
        p = self._request("GET", f"/payments/{payment_id}")
        if (p.get("status") != "captured" or p.get("order_id") != order_id
                or p.get("amount") != amount_paise or p.get("currency") != "INR"):
            raise PaymentError("Captured payment not verified")
        return VerifiedPayment(order_id, payment_id, amount_paise)

    def verify_checkout(self, order_id: str, payment_id: str, signature: str, amount_paise: int) -> VerifiedPayment:
        expected = hmac.new(self._secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature or ""):
            raise PaymentError("Invalid payment signature")
        return self._readback(order_id, payment_id, amount_paise)

    def verify_webhook(self, body: bytes, signature: str) -> VerifiedPayment | None:
        check = hmac.new(self._webhook.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(check, signature or ""):
            raise PaymentError("Invalid webhook signature")
        data = json.loads(body)
        if data.get("event") != "payment.captured":
            return None
        e = data.get("payload", {}).get("payment", {}).get("entity", {})
        return self._readback(e.get("order_id", ""), e.get("id", ""), int(e.get("amount", 0)))

    def refund(self, payment_id: str, amount_paise: int) -> str:
        r = self._request("POST", f"/payments/{payment_id}/refund", {"amount": amount_paise})
        if not r.get("id"):
            raise PaymentError("Refund not accepted by provider")
        return r["id"]

    def public_config(self) -> dict:
        return {"provider": self.name, "checkout": "razorpay", "key_id": self.key_id}


def load_providers(mode: str) -> dict:
    """Configured adapters by name."""
    providers = {}
    key = os.getenv("RAZORPAY_KEY_ID", "")
    if key:
        providers["razorpay"] = RazorpayProvider(key, os.getenv("RAZORPAY_KEY_SECRET", ""),
                                                 os.getenv("RAZORPAY_WEBHOOK_SECRET", ""))
    return providers


def active_provider_name(mode: str) -> str:
    return os.getenv("SPPS_PAYMENT_PROVIDER", "razorpay")
