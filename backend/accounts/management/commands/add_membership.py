from django.core.management.base import BaseCommand, CommandError

from accounts.models import Membership, Tenant, User


class Command(BaseCommand):
    help = "Give a User a Role in a Tenant (the Tenant is found by name)."

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument("tenant")
        parser.add_argument("role", choices=Membership.Role.values)

    def handle(self, *args, email, tenant, role, **opts):
        try:
            user = User.objects.get(email=email.strip().lower())
            tenant_obj = Tenant.objects.get(name=tenant)
        except (User.DoesNotExist, Tenant.DoesNotExist) as e:
            raise CommandError(str(e))
        _, created = Membership.objects.update_or_create(user=user, tenant=tenant_obj, defaults={"role": role})
        self.stdout.write("created" if created else "updated")
