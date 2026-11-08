"""The emailed one-time codes: subject, plain text and HTML.

Email apps never run scripts, so there is no real "copy" button. Instead:

* the code leads the subject ("482915 is your IMS sign-in code"): Gmail shows
  its own "Copy code" button for such messages, iOS/macOS offer it as an
  autofill suggestion for the one-time-code field, and it shows in the
  notification preview;
* the code block is selectable in one tap or double-click (user-select: all);
* the sign-in page has a "Paste code" button.

The HTML is table-based with inline styles and system fonts (what email apps
render reliably), in the app's receipt look: a mono code on a dashed slip.
"""
from html import escape

from django.utils import timezone

from .models import OneTimeCode

ACCENT, INK, MUTED, LINE = "#14573f", "#171717", "#5f5f5f", "#d4d4d4"
MONO = "'IBM Plex Mono','SFMono-Regular',Menlo,Consolas,'Liberation Mono',monospace"
SANS = "-apple-system,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif"


def _copy(purpose, user):
    """(subject, heading, what to do, minutes, warning) for one purpose."""
    if purpose == OneTimeCode.LOGIN:
        return ("is your IMS sign-in code", "Your sign-in code", "Enter it on the IMS sign-in page to finish signing in.", 10,
                "Didn't just sign in? Someone may know your password: change it now and tell your administrator.")
    if purpose == OneTimeCode.RESET and not user.has_usable_password():
        return ("is your IMS account setup code", "Create your IMS account",
                f"You've been invited to IMS. Open {_app_url()}/signup, enter this email address and "
                "this code, and choose your password.", 15,
                "Not expecting an IMS account? You can ignore this email.")
    if purpose == OneTimeCode.RESET:
        return ("is your IMS password reset code", "Reset your password",
                "Enter it on the IMS sign-in page with your new password.", 15,
                "Didn't ask to reset your password? Ignore this email: your password stays the same.")
    return ("confirms your new IMS email", "Confirm your new email",
            "Enter it in IMS under My account to start using this address.", 30,
            "Didn't change your email in IMS? Tell your administrator.")


def _app_url():
    """The IMS app's address, from the trusted origins the server is set up for."""
    from django.conf import settings

    for origin in settings.CSRF_TRUSTED_ORIGINS:
        if origin.count(":") < 2:  # skip origins with a port (the console)
            return origin.rstrip("/")
    return "the IMS sign-in page"


def _sender_name(user):
    from admin_extras.system import system_settings

    group = system_settings().group_name
    if group:
        return group
    roles = list(user.branch_roles.select_related("org")[:2])
    return roles[0].org.name if len(roles) == 1 else "IMS"


def render(purpose, code, user):
    """-> (subject, text, html)"""
    tail, heading, action, minutes, warning = _copy(purpose, user)
    who = _sender_name(user)
    name = user.get_full_name() or user.username
    sent = timezone.localtime().strftime("%H:%M")
    subject = f"{code} {tail}"

    text = (f"{heading}\n\n{code}\n\nHi {name}, {action[0].lower() + action[1:]}\n"
            f"It works once and expires in {minutes} minutes (sent {sent} Nepal time).\n\n"
            f"{warning}\nNever share this code. Nobody from {who} or IMS will ask you for it.\n\n— {who} · IMS")

    e = escape
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>{e(heading)}</title></head>
<body style="margin:0;padding:0;background:#f4f4f5;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">Your code is {e(code)}. It expires in {minutes} minutes.&#8199;&#847;&#8199;&#847;&#8199;&#847;</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f5;">
<tr><td align="center" style="padding:32px 16px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;">
    <tr><td style="padding:0 4px 16px;font:500 14px/1.4 {SANS};color:{INK};">{e(who)} <span style="color:{MUTED};font-weight:400;">· IMS</span></td></tr>
    <tr><td style="background:#ffffff;border:1px solid #e4e4e7;border-radius:12px;padding:32px 28px;">
      <h1 style="margin:0 0 8px;font:500 22px/1.3 {SANS};color:{INK};">{e(heading)}</h1>
      <p style="margin:0 0 24px;font:400 15px/1.5 {SANS};color:{MUTED};">Hi {e(name)}. {e(action)}</p>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
        <tr><td align="center" style="background:#fffffd;border:1px dashed {LINE};border-radius:8px;padding:20px 12px;">
          <div style="font:500 13px/1.4 {SANS};color:{MUTED};margin:0 0 6px;">Your code</div>
          <div style="font:500 36px/1.2 {MONO};letter-spacing:6px;color:{INK};-webkit-user-select:all;user-select:all;" aria-label="Code {' '.join(code)}">{e(code)}</div>
          <div style="font:400 12px/1.4 {SANS};color:{MUTED};margin:8px 0 0;">Tap or double-click the code to select it, then copy</div>
        </td></tr>
      </table>
      <p style="margin:20px 0 0;font:400 14px/1.5 {SANS};color:{MUTED};">Works once and expires in <strong style="color:{INK};font-weight:500;">{minutes} minutes</strong> · sent {sent} Nepal time</p>
      <hr style="border:0;border-top:1px solid #e4e4e7;margin:24px 0 16px;">
      <p style="margin:0 0 8px;font:400 13px/1.5 {SANS};color:{MUTED};">{e(warning)}</p>
      <p style="margin:0;font:400 13px/1.5 {SANS};color:{MUTED};">Never share this code. Nobody from {e(who)} or IMS will ask you for it.</p>
    </td></tr>
    <tr><td style="padding:16px 4px 0;font:400 12px/1.5 {SANS};color:#8a8a8a;">You get this because an IMS account uses this address. <span style="color:{ACCENT};">IMS</span> · Inventory &amp; POS</td></tr>
  </table>
</td></tr>
</table>
</body></html>"""
    return subject, text, html
