from django.core.management.base import BaseCommand, CommandError

from accounts.models import User


class Command(BaseCommand):
    help = "Create a User and print its id. The password is visible in the process list; PoC only."

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument("password")
        parser.add_argument("--operator", action="store_true", help="a Platform operator: may sign in to the back office")

    def handle(self, *args, email, password, operator=False, **opts):
        if User.objects.filter(email=email.strip().lower()).exists():
            raise CommandError(f"User {email!r} already exists")
        user = User.objects.create_user(email, password)
        if operator:
            user.is_operator = True
            user.save(update_fields=["is_operator"])
        self.stdout.write(str(user.id))
