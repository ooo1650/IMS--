"""Email backend that takes its SMTP settings from the database
(SystemSettings, edited in the operator console) each time it opens a
connection, so changing the mail server needs no restart and no .env edit.

Without an SMTP host it writes messages to the server log, like Django's
console backend, so sign-in codes can still be read during setup.
"""
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.backends.console import EmailBackend as ConsoleBackend
from django.core.mail.backends.smtp import EmailBackend as SmtpBackend


class DatabaseSmtpBackend(BaseEmailBackend):
    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently)
        self.kwargs = kwargs

    def _backend(self):
        from .system import system_settings

        s = system_settings()
        if not s.smtp_host:
            return ConsoleBackend(fail_silently=self.fail_silently)
        return SmtpBackend(host=s.smtp_host, port=s.smtp_port, username=s.smtp_user, password=s.smtp_password,
                           use_tls=s.smtp_use_tls, timeout=15, fail_silently=self.fail_silently)

    def send_messages(self, email_messages):
        return self._backend().send_messages(email_messages)


def from_address():
    from django.conf import settings

    from .system import system_settings

    s = system_settings()
    return s.from_email or settings.DEFAULT_FROM_EMAIL
