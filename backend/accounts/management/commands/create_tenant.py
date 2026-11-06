from django.core.management.base import BaseCommand, CommandError

from accounts.models import Tenant


class Command(BaseCommand):
    help = "Create a Tenant and print its id."

    def add_arguments(self, parser):
        parser.add_argument("name")

    def handle(self, *args, name, **opts):
        if Tenant.objects.filter(name=name).exists():
            raise CommandError(f"Tenant {name!r} already exists")
        self.stdout.write(str(Tenant.objects.create(name=name).id))
