"""Keep the operator console API (/api/v1/platform/) to the console.

Behind the bundled nginx this is done by the web server (404 on the IMS host,
IP allowlist on the console's server block). Where there is no such proxy
(e.g. frontends on Vercel, backend on Render) this middleware does the same job:

* ``IMS_PLATFORM_ORIGINS``: the console's address(es). A platform request must
  come from a page on one of them (browser ``Origin``, or ``Referer`` for GETs),
  so the IMS app's address can't reach the console API;
* ``IMS_PLATFORM_ALLOW``: optional networks (CIDRs) operators connect from,
  checked against ``ims.net.client_ip``.

Both empty (the default) = no check here (nginx does it). Refusals answer 404,
like nginx, so the endpoint's existence isn't advertised.
"""
import ipaddress
from urllib.parse import urlsplit

from django.conf import settings
from django.http import JsonResponse

from .net import client_ip

PREFIX = "/api/v1/platform/"


def _origin(value):
    parts = urlsplit(value or "")
    return f"{parts.scheme}://{parts.netloc}".lower() if parts.scheme and parts.netloc else ""


def _allowed_ip(ip, networks):
    try:
        addr = ipaddress.ip_address(ip or "")
    except ValueError:
        return False
    return any(addr in net for net in networks)


class PlatformGuardMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.origins = {o.rstrip("/").lower() for o in settings.IMS_PLATFORM_ORIGINS}
        self.networks = [ipaddress.ip_network(c, strict=False) for c in settings.IMS_PLATFORM_ALLOW]

    def __call__(self, request):
        if request.path.startswith(PREFIX) and not self.allowed(request):
            return JsonResponse({"detail": "Not found."}, status=404)
        return self.get_response(request)

    def allowed(self, request):
        if self.origins:
            came_from = _origin(request.headers.get("Origin")) or _origin(request.headers.get("Referer"))
            if came_from not in self.origins:
                return False
        if self.networks and not _allowed_ip(client_ip(request), self.networks):
            return False
        return True
