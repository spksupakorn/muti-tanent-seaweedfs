from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.models import Tenant
from files.models import TenantKey


class Command(BaseCommand):
    help = "Destroy a Tenant key (crypto-shredding): all of the Tenant's Files become unreadable, for good."

    def add_arguments(self, parser):
        parser.add_argument("tenant", help="Tenant name")

    def handle(self, *args, tenant, **opts):
        try:
            tenant_obj = Tenant.objects.get(name=tenant)
        except Tenant.DoesNotExist:
            raise CommandError(f"No tenant named {tenant!r}")
        # Keep a marker row so the next upload cannot quietly mint a fresh key.
        TenantKey.objects.update_or_create(
            tenant=tenant_obj, defaults={"wrapped_key": None, "destroyed_at": timezone.now()}
        )
        self.stdout.write("destroyed")
