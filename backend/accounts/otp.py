"""Emailed one-time codes: sign-in (2FA), password reset, email verification.

A code is 6 random digits. Only an HMAC of it (keyed with SECRET_KEY) is
stored, so a database leak doesn't reveal live codes. Issuing a new code
retires the previous one of the same purpose; a code is single-use, expires,
and dies after MAX_ATTEMPTS wrong guesses. Rate limits are counted from the
table itself, so they hold across gunicorn workers and restarts.
"""
import logging
import secrets
import threading
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from .models import OneTimeCode

log = logging.getLogger(__name__)

LIFETIME = {
    OneTimeCode.LOGIN: timedelta(minutes=10),
    OneTimeCode.RESET: timedelta(minutes=15),
    OneTimeCode.EMAIL: timedelta(minutes=30),
}
MAX_ATTEMPTS = 5
RESEND_AFTER = timedelta(seconds=60)
MAX_PER_HOUR = 6  # codes per user and purpose

# Subject, text and HTML of each code email live in accounts/emails.py.

def _hash(user_id, purpose, code):
    return salted_hmac("ims.otp", f"{user_id}:{purpose}:{code}", algorithm="sha256").hexdigest()


def mask_email(email):
    """s****@example.com: enough for the user to recognise, not to harvest."""
    name, _, domain = email.partition("@")
    return f"{name[:1]}{'*' * max(len(name) - 1, 3)}@{domain}"


def can_send(user, purpose):
    """None, or why a new code can't be sent yet."""
    now = timezone.now()
    recent = OneTimeCode.objects.filter(user=user, purpose=purpose, created_at__gte=now - timedelta(hours=1))
    last = recent.order_by("-created_at").first()
    if last and now - last.created_at < RESEND_AFTER:
        return "Wait a minute before asking for another code."
    if recent.count() >= MAX_PER_HOUR:
        return "Too many codes requested. Try again in an hour."
    return None


def issue(user, purpose, email=None):
    """Create a code for ``user`` and email it (to ``email`` or the user's address)."""
    to = email or user.email
    code = f"{secrets.randbelow(10 ** 6):06d}"
    now = timezone.now()
    OneTimeCode.objects.filter(user=user, purpose=purpose, used_at__isnull=True).update(used_at=now)
    OneTimeCode.objects.filter(user=user, created_at__lt=now - timedelta(days=7)).delete()
    OneTimeCode.objects.create(user=user, purpose=purpose, code_hash=_hash(user.id, purpose, code), sent_to=to,
                               expires_at=now + LIFETIME[purpose])
    from .emails import render

    subject, text, html = render(purpose, code, user)
    _send(to, subject, text, html)
    return to


def verify(user, purpose, code, email=None):
    """True if ``code`` is the user's live code for ``purpose`` (and was sent to ``email``, when given)."""
    otp = (OneTimeCode.objects.select_for_update()
           .filter(user=user, purpose=purpose, used_at__isnull=True, expires_at__gt=timezone.now())
           .order_by("-created_at").first())
    if otp is None or otp.attempts >= MAX_ATTEMPTS or (email and otp.sent_to.lower() != email.lower()):
        return False
    code = "".join(ch for ch in str(code) if ch.isdigit())
    if not constant_time_compare(otp.code_hash, _hash(user.id, purpose, code)):
        otp.attempts += 1
        otp.save(update_fields=["attempts"])
        return False
    otp.used_at = timezone.now()
    otp.save(update_fields=["used_at"])
    return True


def _send(to, subject, body, html=None):
    """Send in the background (IMS_EMAIL_ASYNC): the reply doesn't wait for SMTP,
    and its timing can't reveal whether an account exists."""
    def run():
        try:
            from admin_extras.mail import from_address

            send_mail(subject, body, from_address(), [to], html_message=html)
        except Exception:  # noqa: BLE001 — a mail outage must not break sign-in pages
            log.exception("Could not send %r to %s", subject, to)

    if settings.IMS_EMAIL_ASYNC:
        threading.Thread(target=run, daemon=True).start()
    else:
        run()
