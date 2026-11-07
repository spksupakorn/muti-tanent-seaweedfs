import uuid

from django.conf import settings
from django.db import models

from accounts.models import Tenant


class AuditEntry(models.Model):
    """Append-only record of one successful action on a File in a Tenant."""

    class Action(models.TextChoices):
        UPLOAD = "upload"
        DOWNLOAD = "download"
        RENAME = "rename"
        MOVE = "move"
        DELETE = "delete"
        RESTORE = "restore"
        KEY_DESTROYED = "key_destroyed"  # an operator destroyed the Tenant key; no File is involved
        # An operator changed who can get into the Tenant (they can also take over a User by resetting the password).
        MEMBER_ADDED = "member_added"
        MEMBER_CHANGED = "member_changed"
        MEMBER_REMOVED = "member_removed"
        PASSWORD_RESET = "password_reset"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="audit_entries")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=20, choices=Action.choices)
    # Plain values, not a foreign key: the entry must outlive a purged File and say which one it was.
    file_id = models.UUIDField(null=True)
    file_name = models.CharField(max_length=255)
    file_folder = models.CharField(max_length=1024)
    # What an event without a File is about, for example the User whose Membership changed.
    detail = models.CharField(max_length=255, blank=True, default="")
    # Done by a Platform operator in the back office, not by a member of the Tenant.
    by_operator = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["tenant", "-created_at"])]
