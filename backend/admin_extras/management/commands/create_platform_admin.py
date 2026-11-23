"""Create an operator (platform admin) account, or give a forgotten one a new password.

    manage.py create_platform_admin --email you@example.com            # new operator
    manage.py create_platform_admin --email you@example.com --reset    # forgot the password

The password is asked for (hidden) unless given with --password. Operators
always sign in with an emailed code too, so the email must be one you read.
On a hosted database run it through scripts/hosted_operator.py.
Everything else (businesses, branches, admins, settings, jobs) is then done in
the operator console."""
import getpass
import re

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import User, UserBranchRole
from admin_extras.models import log_action


class Command(BaseCommand):
    help = "Create an operator (platform admin) for the console, or reset an operator's forgotten password."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True, help="Sign-in codes are sent here")
        parser.add_argument("--username", help="Defaults to the part of the email before @")
        parser.add_argument("--password", help="Prompted for if omitted")
        parser.add_argument("--reset", action="store_true",
                            help="The operator already exists: set a new password (and switch the account back on)")

    def _password(self, opts, user):
        password = opts["password"]
        if not password:
            password = getpass.getpass("New password for the operator (nothing will show): ")
            if password != getpass.getpass("Same password again: "):
                raise CommandError("The passwords don't match. Nothing was changed.")
        try:
            validate_password(password, user)
        except ValidationError as e:
            raise CommandError(" ".join(e.messages) + " Nothing was changed.")
        return password

    def handle(self, *args, **opts):
        email = opts["email"].strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise CommandError(f"{email!r} isn't an email address.")
        existing = User.objects.filter(email__iexact=email).first()
        if opts["reset"]:
            return self._reset(existing, email, opts)
        if existing is not None:
            hint = (" It is already an operator: add --reset to give it a new password." if existing.is_platform_admin
                    else " That is a shop account; an operator needs its own email address.")
            raise CommandError(f"Another account already uses {email}.{hint}")
        username = (opts["username"] or email.split("@")[0]).strip()
        if User.objects.filter(username__iexact=username).exists():
            raise CommandError(f"User {username!r} already exists. Choose another with --username.")
        user = User(username=username, email=email, is_platform_admin=True, email_verified=True)
        user.set_password(self._password(opts, user))
        with transaction.atomic():
            user.save()
            log_action(None, "platform.admin_created", user, via="create_platform_admin")
        self.stdout.write(self.style.SUCCESS(
            f"Operator {username!r} created. Sign in to the console with {username!r} or {email}; "
            f"the sign-in code goes to {email}."))

    def _reset(self, user, email, opts):
        if user is None:
            raise CommandError(f"No account uses {email}. Leave out --reset to create the operator.")
        if not user.is_platform_admin:
            raise CommandError(f"{email} is a shop account, not an operator. Reset it from the console "
                               "(Users), or with Forgot password? in the app.")
        if UserBranchRole.objects.filter(user=user).exists():
            raise CommandError(f"{email} holds shop roles, which operators must not. Nothing was changed.")
        password = self._password(opts, user)
        with transaction.atomic():
            user.set_password(password)
            user.is_active = True
            user.session_epoch += 1  # every old session ends
            user.save(update_fields=["password", "is_active", "session_epoch"])
            log_action(None, "auth.password_reset", user, via="create_platform_admin")
        self.stdout.write(self.style.SUCCESS(
            f"New password set for operator {user.username!r}. Sign in to the console with {user.username!r} or "
            f"{email}; the sign-in code goes to {email}."))
