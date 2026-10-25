"""FonePay gateway abstraction (§14).

THE OFFICIAL FONEPAY API IS NOT CONFIRMED. Community libraries suggest
HMAC-SHA512 request signing and status polling by PRN. ``HttpFonepayGateway``
below is a skeleton following that shape; verify every field name, URL and the
signing scheme against the bank's official docs before enabling it.

``MockFonepayGateway`` (the default, FONEPAY_BACKEND=mock) simulates a sandbox
backed by the MockFonepayTransaction table so the whole flow — initiate,
customer pays, poll/callback, commit, expiry, reconciliation — works end to end.
"""
import hashlib
import hmac
import json
import secrets
import urllib.request
from dataclasses import dataclass

from django.conf import settings

SUCCESS, FAILED, PENDING, EXPIRED = "success", "failed", "pending", "expired"


@dataclass
class QrResult:
    prn: str
    qr_payload: str


@dataclass
class StatusResult:
    status: str  # success | failed | pending | expired
    paid_paisa: int | None = None
    trx_id: str = ""


def sign(message: str, secret: str | None = None) -> str:
    secret = secret if secret is not None else settings.FONEPAY["SECRET"]
    return hmac.new(secret.encode(), message.encode(), hashlib.sha512).hexdigest()


def verify_signature(message: str, signature: str) -> bool:
    return hmac.compare_digest(sign(message), signature or "")


def new_prn() -> str:
    return "PRN" + secrets.token_hex(8).upper()


class MockFonepayGateway:
    def create_qr(self, prn: str, amount_paisa: int, remarks: str) -> QrResult:
        from .models import MockFonepayTransaction

        MockFonepayTransaction.objects.create(prn=prn, amount_paisa=amount_paisa)
        payload = f"fonepay://mock?prn={prn}&amount={amount_paisa / 100:.2f}&r={remarks}"
        return QrResult(prn=prn, qr_payload=payload)

    def check_status(self, prn: str) -> StatusResult:
        from .models import MockFonepayTransaction

        txn = MockFonepayTransaction.objects.filter(prn=prn).first()
        if txn is None:
            return StatusResult(FAILED)
        return StatusResult(txn.status, txn.paid_paisa, txn.trx_id)

    def settlement_report(self, date):
        """[{prn, amount_paisa, status, trx_id}] for reconciliation."""
        from .models import MockFonepayTransaction

        return [
            {"prn": t.prn, "amount_paisa": t.paid_paisa, "status": t.status, "trx_id": t.trx_id}
            for t in MockFonepayTransaction.objects.filter(created_at__date=date, status=SUCCESS)
        ]

    # Dev helper: simulate the customer scanning and paying.
    def simulate_payment(self, prn: str, paid_paisa: int | None = None, fail=False):
        from .models import MockFonepayTransaction

        txn = MockFonepayTransaction.objects.get(prn=prn)
        txn.status = FAILED if fail else SUCCESS
        txn.paid_paisa = None if fail else (txn.amount_paisa if paid_paisa is None else paid_paisa)
        txn.trx_id = "" if fail else "MOCKTRX" + secrets.token_hex(6).upper()
        txn.save()
        return txn


class HttpFonepayGateway:
    """UNVERIFIED skeleton — confirm against official FonePay merchant docs."""

    def __init__(self):
        cfg = settings.FONEPAY
        if not cfg["BASE_URL"] or not cfg["MERCHANT_CODE"]:
            raise RuntimeError("FonePay credentials are not configured")
        self.cfg = cfg

    def _post(self, path, body):
        req = urllib.request.Request(
            self.cfg["BASE_URL"].rstrip("/") + path,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def create_qr(self, prn, amount_paisa, remarks):
        amount = f"{amount_paisa / 100:.2f}"
        message = ",".join([amount, prn, self.cfg["MERCHANT_CODE"], remarks, remarks])
        data = self._post("/merchant/merchantDetailsForThirdParty/thirdPartyDynamicQrDownload", {
            "amount": amount, "remarks1": remarks, "remarks2": remarks, "prn": prn,
            "merchantCode": self.cfg["MERCHANT_CODE"], "dataValidation": sign(message),
            "username": self.cfg["USERNAME"], "password": self.cfg["PASSWORD"],
        })
        return QrResult(prn=prn, qr_payload=data.get("qrMessage", ""))

    def check_status(self, prn):
        message = ",".join([prn, self.cfg["MERCHANT_CODE"]])
        data = self._post("/merchant/merchantDetailsForThirdParty/thirdPartyDynamicQrGetStatus", {
            "prn": prn, "merchantCode": self.cfg["MERCHANT_CODE"], "dataValidation": sign(message),
            "username": self.cfg["USERNAME"], "password": self.cfg["PASSWORD"],
        })
        status = {"success": SUCCESS, "failed": FAILED}.get(str(data.get("paymentStatus", "")).lower(), PENDING)
        paid = data.get("amount")
        return StatusResult(status, int(round(float(paid) * 100)) if paid else None, str(data.get("traceId", "")))

    def settlement_report(self, date):
        raise NotImplementedError("Settlement report format pending official FonePay docs")


def get_gateway():
    backend = settings.FONEPAY["BACKEND"]
    if backend == "mock":
        return MockFonepayGateway()
    if backend == "http":
        return HttpFonepayGateway()
    raise RuntimeError(f"Unknown FONEPAY_BACKEND {backend!r}")
