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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="audit_entries")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=10, choices=Action.choices)
    # Plain values, not a foreign key: the entry must outlive a purged File and say which one it was.
    file_id = models.UUIDField(null=True)
    file_name = models.CharField(max_length=255)
    file_folder = models.CharField(max_length=1024)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["tenant", "-created_at"])]
