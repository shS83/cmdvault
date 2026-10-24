from getpass import getpass
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "Create the initial owner interactively (password is never a CLI argument)."

    def add_arguments(self, parser):
        parser.add_argument("username")

    def handle(self, username, **options):
        User = get_user_model()
        if User.objects.exists():
            raise CommandError("An owner already exists; use changepassword for recovery.")
        password = getpass("Password: ")
        if password != getpass("Confirm password: "):
            raise CommandError("Passwords do not match.")
        user = User(username=username)
        try:
            user.full_clean(exclude=["password"])
            validate_password(password, user)
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from None
        with transaction.atomic():
            # Serialize concurrent first-owner commands, even on an empty auth table.
            from django.db import connection
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(68471102)")
            if User.objects.exists():
                raise CommandError("An owner already exists.")
            user.set_password(password)
            user.save()
        self.stdout.write(self.style.SUCCESS("Owner created."))
