from django.contrib import admin, messages
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.records import record_by_operator

from .models import File


@admin.register(File)
class FileAdmin(admin.ModelAdmin):
    """Operators see every Tenant's files (name, owner, size, status) and can move them to Trash or back.

    Read-only on purpose: no add, edit or delete, and the wrapped data key is not shown. This process holds
    neither the master key nor S3 credentials, so there is no way to read a file's content from here.
    """

    list_display = ("name", "folder", "tenant", "size", "status", "created_at", "trashed_at")
    list_filter = ("status", "tenant")
    search_fields = ("name", "folder", "tenant__name")
    list_select_related = ("tenant",)
    exclude = ("wrapped_data_key",)
    actions = ["trash_selected", "restore_selected"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Move selected files to Trash")
    def trash_selected(self, request, queryset):
        moved = 0
        for f in queryset.filter(status=File.Status.ACTIVE):
            f.status, f.trashed_at = File.Status.TRASHED, timezone.now()
            f.save(update_fields=["status", "trashed_at"])
            record_by_operator(request.user, "delete", f)
            self.log_change(request, f, "Moved to Trash")
            moved += 1
        self.message_user(request, f"{moved} file(s) moved to Trash.")

    @admin.action(description="Restore selected files from Trash")
    def restore_selected(self, request, queryset):
        restored, blocked = 0, []
        for f in queryset.filter(status=File.Status.TRASHED):
            f.status, f.trashed_at = File.Status.ACTIVE, None
            try:
                with transaction.atomic():  # a savepoint: a name conflict must not poison the request
                    f.save(update_fields=["status", "trashed_at"])
            except IntegrityError:
                blocked.append(f"{f.name} ({f.tenant})")
                continue
            record_by_operator(request.user, "restore", f)
            self.log_change(request, f, "Restored from Trash")
            restored += 1
        self.message_user(request, f"{restored} file(s) restored.")
        if blocked:
            self.message_user(
                request,
                "Not restored, the name is taken in that folder: " + ", ".join(blocked),
                level=messages.ERROR,
            )
