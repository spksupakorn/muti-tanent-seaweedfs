from django.contrib import admin

from .models import AuditEntry


@admin.register(AuditEntry)
class AuditEntryAdmin(admin.ModelAdmin):
    """The audit log of every Tenant, read-only."""

    list_display = ("created_at", "tenant", "action", "file_name", "file_folder", "user", "by_operator")
    list_filter = ("tenant", "action", "by_operator")
    search_fields = ("file_name", "file_folder", "user__email", "tenant__name")
    list_select_related = ("tenant", "user")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
