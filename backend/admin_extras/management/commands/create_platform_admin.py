"""Create the first operator (platform admin) account. Run once per install:

    docker compose run --rm migrate manage create_platform_admin --username op --email you@example.com

Everything else (businesses, branches, admins, settings, jobs) is then done in
the operator console. The password is asked for interactively unless given
with --password; platform admins always sign in with an emailed code too."""
import getpass

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from accounts.models import User


class Command(BaseCommand):
    help = "Create an operator (platform admin) account for the console."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--email", required=True, help="Sign-in codes are sent here")
        parser.add_argument("--password", help="Prompted for if omitted")

    def handle(self, *args, **opts):
        username, email = opts["username"].strip(), opts["email"].strip().lower()
        if User.objects.filter(username__iexact=username).exists():
            raise CommandError(f"User {username!r} already exists")
        if User.objects.filter(email__iexact=email).exists():
            raise CommandError(f"Another account already uses {email}")
        password = opts["password"]
        if not password:
            password = getpass.getpass("Password: ")
            if password != getpass.getpass("Password again: "):
                raise CommandError("The passwords don't match")
        user = User(username=username, email=email, is_platform_admin=True)
        try:
            validate_password(password, user)
        except ValidationError as e:
            raise CommandError(" ".join(e.messages))
        user.set_password(password)
        user.save()
        self.stdout.write(self.style.SUCCESS(f"Platform admin {username!r} created. Sign in to the console; "
                                             f"the sign-in code goes to {email}."))
