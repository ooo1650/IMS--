"""JSON instead of Django's HTML error pages for API requests.

The app shows ``detail`` to the user, so every error the browser can get from
/api/ must be JSON with a sentence a cashier understands, plus a ``code`` the
app can act on. DRF already answers JSON inside views; these cover what
happens outside them: CSRF failures on the plain-Django sign-in views, unknown
URLs, crashes (500) and requests for a host Django doesn't serve (400).
Other paths keep Django's default pages.
"""
import sys

from django.db import InterfaceError, OperationalError, ProgrammingError
from django.http import JsonResponse
from django.views import defaults

SESSION_EXPIRED = "Your session expired or this page is out of date. Reload the page and try again."


def _is_api(request):
    return request.path.startswith("/api/")


def csrf_failure(request, reason=""):
    if _is_api(request):
        return JsonResponse({"detail": SESSION_EXPIRED, "code": "csrf"}, status=403)
    from django.views.csrf import csrf_failure as default

    return default(request, reason)


def bad_request(request, exception=None):
    if _is_api(request):
        return JsonResponse({"detail": "The server couldn't accept this request (check the address the app uses).",
                             "code": "bad_request"}, status=400)
    return defaults.bad_request(request, exception)


def not_found(request, exception=None):
    if _is_api(request):
        return JsonResponse({"detail": "Not found. Reload the page; if it keeps happening, the app may be out of "
                                       "date with the server.", "code": "not_found"}, status=404)
    return defaults.page_not_found(request, exception)


DB_DOWN = ("The server is running but can't reach its database right now. Try again in a minute; if it keeps "
           "happening, tell your administrator (the database may be paused, restarting or full).")


DB_OUTDATED = ("The server was updated but its database wasn't, so this feature can't work yet. Ask your "
               "administrator to run the database update (backend/scripts/update_hosted_db.py).")


def _schema_behind(exc):
    """A table or column the code expects isn't there: almost always migrations that weren't applied."""
    if isinstance(exc, ProgrammingError):
        return getattr(exc.__cause__, "sqlstate", None) in ("42P01", "42703")
    return isinstance(exc, OperationalError) and ("no such table" in str(exc) or "no such column" in str(exc))


def server_error(request):
    # Django calls this inside its except block, so the exception is still at hand.
    exc = sys.exc_info()[1]
    db_down = isinstance(exc, (OperationalError, InterfaceError))
    if _is_api(request):
        if _schema_behind(exc):
            # 500, not 503: the app treats 503 as being offline, which would hide this.
            return JsonResponse({"detail": DB_OUTDATED, "code": "db_outdated"}, status=500)
        if db_down:
            # 503, not 500: it's an outage, not a bug, and the app shows it as one.
            return JsonResponse({"detail": DB_DOWN, "code": "db_unavailable"}, status=503, headers={"Retry-After": "30"})
        return JsonResponse({"detail": "Something went wrong on the server. Try again; if it keeps happening, tell "
                                       "your administrator (the error has been logged).", "code": "server_error"},
                            status=500)
    return defaults.server_error(request)
