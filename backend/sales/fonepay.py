"""FonePay gateway abstraction (§14).

THE OFFICIAL FONEPAY API IS NOT CONFIRMED. Community libraries suggest
HMAC-SHA512 request signing and status polling by PRN. ``HttpFonepayGateway``
below is a skeleton following that shape; verify every field name, URL and the
signing scheme against the bank's official docs before enabling it.

``MockFonepayGateway`` (the default, FONEPAY_BACKEND=mock) simulates a sandbox
backed by the MockFonepayTransaction table so the whole flow — initiate,
customer pays, poll/callback, commit, expiry, reconciliation — works end to end.

Merchant accounts are per branch (``FonepayMerchant``, edited in Admin →
Branches & registers). FONEPAY_BACKEND picks the gateway for the whole server;
the branch's merchant row supplies the credentials and can switch FonePay off
for that branch.
"""
import hashlib
import hmac
import json
import secrets
import urllib.request
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError

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
    secret = secret if secret is not None else settings.FONEPAY_MOCK_SECRET
    return hmac.new(secret.encode(), message.encode(), hashlib.sha512).hexdigest()


def verify_signature(message: str, signature: str, secret: str | None = None) -> bool:
    secret = secret if secret is not None else settings.FONEPAY_MOCK_SECRET
    if not secret:  # an empty key would let anyone forge a signature
        return False
    return hmac.compare_digest(sign(message, secret), signature or "")


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
    """UNVERIFIED skeleton — confirm against official FonePay merchant docs.

    The API base URL is server-wide (FONEPAY_BASE_URL); the merchant code,
    username, password and signing secret come from the branch's merchant."""

    def __init__(self, merchant, base_url):
        if not base_url:
            raise ValidationError("The FonePay API address is not set (Admin → Branches & registers → FonePay).")
        if not merchant.is_complete():
            raise ValidationError(f"This branch's FonePay merchant is missing: {', '.join(merchant.missing())}.")
        self.secret = merchant.secret
        self.cfg = {"BASE_URL": base_url, "MERCHANT_CODE": merchant.merchant_code,
                    "USERNAME": merchant.username, "PASSWORD": merchant.password}

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
            "merchantCode": self.cfg["MERCHANT_CODE"], "dataValidation": sign(message, self.secret),
            "username": self.cfg["USERNAME"], "password": self.cfg["PASSWORD"],
        })
        return QrResult(prn=prn, qr_payload=data.get("qrMessage", ""))

    def check_status(self, prn):
        message = ",".join([prn, self.cfg["MERCHANT_CODE"]])
        data = self._post("/merchant/merchantDetailsForThirdParty/thirdPartyDynamicQrGetStatus", {
            "prn": prn, "merchantCode": self.cfg["MERCHANT_CODE"], "dataValidation": sign(message, self.secret),
            "username": self.cfg["USERNAME"], "password": self.cfg["PASSWORD"],
        })
        status = {"success": SUCCESS, "failed": FAILED}.get(str(data.get("paymentStatus", "")).lower(), PENDING)
        paid = data.get("amount")
        return StatusResult(status, int(round(float(paid) * 100)) if paid else None, str(data.get("traceId", "")))

    def settlement_report(self, date):
        raise NotImplementedError("Settlement report format pending official FonePay docs")


def merchant_for(branch):
    from .models import FonepayMerchant

    return FonepayMerchant.objects.filter(branch=branch).first() if branch is not None else None


def server_settings(org):
    """A business's FonePay mode and API address (database, not env).
    Without a saved row: demo mode where it is allowed (dev servers), else off."""
    from .models import FonepaySettings

    row = FonepaySettings.objects.filter(org_id=getattr(org, "pk", org)).first() if org is not None else None
    if row is None:
        return (FonepaySettings.MOCK if settings.FONEPAY_MOCK_ALLOWED else FonepaySettings.DISABLED), ""
    mode = row.mode
    if mode == FonepaySettings.MOCK and not settings.FONEPAY_MOCK_ALLOWED:
        mode = FonepaySettings.DISABLED  # demo mode saved on a server that doesn't allow it
    return mode, row.base_url


def server_mode(org) -> str:
    return server_settings(org)[0]


def allowed_api_url(url: str) -> bool:
    """https on a FonePay domain (settings.FONEPAY_ALLOWED_HOSTS), so merchant
    credentials can't be pointed at another server."""
    from urllib.parse import urlsplit

    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(host == d or host.endswith("." + d) for d in settings.FONEPAY_ALLOWED_HOSTS)


def branch_backend(branch) -> str:
    """What FonePay means at this branch: "disabled", "mock" or "http".

    Live (http) needs the branch's merchant to be switched on and complete.
    The mock gateway works without a merchant row (demo), unless an admin has
    saved one and switched it off."""
    backend = server_mode(branch.org)
    if backend == "disabled":
        return "disabled"
    merchant = merchant_for(branch)
    if backend == "mock":
        return "disabled" if merchant is not None and not merchant.enabled else "mock"
    if backend == "http":
        return "http" if merchant is not None and merchant.enabled and merchant.is_complete() else "disabled"
    raise RuntimeError(f"Unknown FonePay mode {backend!r}")


def callback_secret(branch) -> str:
    """Key for verifying FonePay callbacks about a payment at ``branch``."""
    merchant = merchant_for(branch)
    if merchant is not None and merchant.secret_enc:
        return merchant.secret
    # No branch secret: only the in-process mock gateway signs with the dev key.
    return settings.FONEPAY_MOCK_SECRET if branch is not None and server_mode(branch.org) == "mock" else ""


def get_gateway(branch):
    mode = branch_backend(branch)
    if mode == "disabled":
        raise ValidationError("FonePay is not set up for this branch. Take another payment method.")
    if mode == "mock":
        return MockFonepayGateway()
    return HttpFonepayGateway(merchant_for(branch), server_settings(branch.org)[1])
