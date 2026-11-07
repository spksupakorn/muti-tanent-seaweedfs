from django.core.management.base import BaseCommand, CommandError

from files import crypto
from files.models import File
from storage import client as storage


class Command(BaseCommand):
    help = (
        "Fail if any File record (active or in Trash) points at an object that is missing from storage "
        "or has the wrong stored size. Run as the back-office role, so it sees every Tenant."
    )
    def add_arguments(self, parser):
        parser.add_argument("--tenant", help="check only this Tenant (by name)")

    def handle(self, *args, tenant=None, **opts):
        bad = []
        files = File.objects.filter(status__in=[File.Status.ACTIVE, File.Status.TRASHED])
        if tenant:
            files = files.filter(tenant__name=tenant)
        checked = 0
        for f in files.iterator():
            checked += 1
            size = storage.object_size(f.object_key)
            if size != crypto.expected_stored_size(f.size):
                bad.append(f"{f.object_key} ({'missing' if size is None else f'{size} bytes'})")
        if bad:
            raise CommandError(f"{len(bad)} of {checked} file(s) are missing or incomplete:\n" + "\n".join(bad))
        self.stdout.write(f"ok: {checked} file(s) all present with the expected size")
