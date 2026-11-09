"""JSON instead of Django's HTML error pages for API requests.

The app shows ``detail`` to the user, so every error the browser can get from
/api/ must be JSON with a sentence a cashier understands, plus a ``code`` the
app can act on. DRF already answers JSON inside views; these cover what
happens outside them: CSRF failures on the plain-Django sign-in views, unknown
URLs, crashes (500) and requests for a host Django doesn't serve (400).
Other paths keep Django's default pages.
"""
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


def server_error(request):
    if _is_api(request):
        return JsonResponse({"detail": "Something went wrong on the server. Try again; if it keeps happening, tell "
                                       "your administrator (the error has been logged).", "code": "server_error"},
                            status=500)
    return defaults.server_error(request)
