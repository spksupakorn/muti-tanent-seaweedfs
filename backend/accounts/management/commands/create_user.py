from django.core.management.base import BaseCommand, CommandError

from accounts.models import User


class Command(BaseCommand):
    help = "Create a User and print its id. The password is visible in the process list; PoC only."

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument("password")

    def handle(self, *args, email, password, **opts):
        if User.objects.filter(email=email.strip().lower()).exists():
            raise CommandError(f"User {email!r} already exists")
        self.stdout.write(str(User.objects.create_user(email, password).id))
