"""Client IP behind a reverse proxy.

X-Forwarded-For is a list the client can pre-fill, so the *first* entry is
whatever the caller says. Each trusted proxy appends the address it saw, so
the real client is the entry IMS_TRUSTED_PROXIES positions from the right
(1 behind nginx; 0 = no proxy, use the socket address).
"""
from django.conf import settings


def client_ip(request) -> str | None:
    hops = settings.IMS_TRUSTED_PROXIES
    if hops > 0:
        chain = [p.strip() for p in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if p.strip()]
        if len(chain) >= hops:
            return chain[-hops]
    return request.META.get("REMOTE_ADDR")
