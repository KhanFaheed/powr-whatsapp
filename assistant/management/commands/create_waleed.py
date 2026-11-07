"""Create or reset the single desk account used to test POWR Concierge.

Usage
-----
    WALEED_PASSWORD='...' python manage.py create_waleed
    python manage.py create_waleed --password '...'   # explicit password
    python manage.py create_waleed --reset            # new generated password

The account is created as a staff/superuser (so it also opens ``/admin/``) and
must match ``settings.ALLOWED_LOGIN_USERNAMES`` — the allow-list enforced by
``assistant/auth.py`` is what keeps the OpenRouter seat to one person.
"""
import os
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create or reset the allow-listed desk account (default: waleed)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            default="waleed",
            help="Account to create (default: waleed).",
        )
        parser.add_argument(
            "--password",
            default=None,
            help="Password to set. Defaults to $WALEED_PASSWORD, else a generated one.",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Set a new password even when the account already exists.",
        )

    def handle(self, *args, **options):
        username = options["username"].strip().lower()
        allowed = {str(name).strip().lower() for name in getattr(settings, "ALLOWED_LOGIN_USERNAMES", [])}
        if allowed and username not in allowed:
            raise CommandError(
                f"{username!r} is not allow-listed (ALLOWED_LOGIN_USERNAMES={sorted(allowed)}). "
                "Add it to POWR_ALLOWED_USERNAMES first."
            )

        User = get_user_model()
        field = User.USERNAME_FIELD
        user = User.objects.filter(**{f"{field}__iexact": username}).first()
        created = user is None
        if created:
            user = User(**{field: username})

        explicit = options["password"] or os.environ.get("WALEED_PASSWORD")
        password = explicit or self._generate_password()
        should_set_password = created or options["reset"] or bool(explicit)

        user.is_active = True
        user.is_staff = True
        user.is_superuser = True
        if should_set_password:
            user.set_password(password)
        user.save()

        if not should_set_password:
            self.stdout.write(
                self.style.WARNING(
                    f"Desk account {username!r} already exists; password left unchanged. "
                    "Use --reset to issue a new one."
                )
            )
            return

        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} desk account {username!r}."))
        self.stdout.write(f"  username: {username}")
        self.stdout.write(f"  password: {password}")
        self.stdout.write("  rotate it later with: python manage.py create_waleed --reset")

    @staticmethod
    def _generate_password():
        """Readable, strong, one-time password (no ambiguous characters)."""
        alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        raw = "".join(secrets.choice(alphabet) for _ in range(16))
        return f"{raw[:4]}-{raw[4:8]}-{raw[8:12]}-{raw[12:]}"
