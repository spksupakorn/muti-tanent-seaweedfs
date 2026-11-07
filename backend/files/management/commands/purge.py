from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from files import crypto
from files.models import File
from storage import client as storage


class Command(BaseCommand):
    help = (
        "Remove half-finished uploads older than 1 hour and Trash older than 7 days (record and stored object). "
        "A trashed file's object is only removed once the Backup holds a complete copy of it. "
        "Safe to re-run. Run it as the back-office role: it works across Tenants."
    )

    def add_arguments(self, parser):
        parser.add_argument("--pending-hours", type=float, default=1)
        parser.add_argument("--trash-days", type=float, default=7)
        parser.add_argument("--tenant", help="only this Tenant (by name)")

    def handle(self, *args, pending_hours, trash_days, tenant=None, **opts):
        now = timezone.now()
        stale = File.objects.filter(status=File.Status.PENDING, created_at__lt=now - timedelta(hours=pending_hours))
        expired = File.objects.filter(status=File.Status.TRASHED, trashed_at__lt=now - timedelta(days=trash_days))
        if tenant:
            stale, expired = stale.filter(tenant__name=tenant), expired.filter(tenant__name=tenant)

        pending_purged = sum(self._purge(f.id, File.Status.PENDING, stale) for f in stale)
        trash_purged = trash_waiting = 0
        for f in expired:
            if not self._in_backup(f):
                trash_waiting += 1
            elif self._purge(f.id, File.Status.TRASHED, expired):
                trash_purged += 1

        self.stdout.write(
            f"purged {pending_purged} half-finished upload(s) and {trash_purged} trashed file(s); "
            f"{trash_waiting} trashed file(s) kept until the Backup has them"
        )

    def _in_backup(self, f: File) -> bool:
        """The Backup keeps a complete copy: present under the same key, with the full stored size."""
        root = settings.BACKUP_FILES_DIR
        if not root:
            return False  # no Backup configured: never delete the last copy
        copy = Path(root) / f.object_key
        return copy.is_file() and copy.stat().st_size == crypto.stored_size(f.size)

    def _purge(self, file_id, status, still_matches) -> int:
        """Delete one file's object, then its record. Re-checks under a lock, so a file that was restored or
        finished uploading in the meantime is left alone. Object first: if the record delete fails, the next
        run finds the record again and finishes; the other order would leave an object nothing points to."""
        with transaction.atomic():
            f = still_matches.select_for_update().filter(id=file_id, status=status).first()
            if f is None:
                return 0
            storage.delete(f.object_key)  # deleting an absent object is not an error
            f.delete()
            return 1
