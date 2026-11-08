from django.contrib.auth.backends import ModelBackend

from .models import User


class EmailOrUsernameBackend(ModelBackend):
    """Sign in with a username or an email address (case-insensitive)."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or password is None:
            return None
        ident = username.strip()
        field = "email__iexact" if "@" in ident else "username__iexact"
        user = User.objects.filter(**{field: ident}).first()
        if user is None:
            User().set_password(password)  # same hashing cost as a real user: no timing hint
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
