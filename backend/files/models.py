import uuid

from django.db import models

from accounts.models import Tenant


class TenantKey(models.Model):
    """A Tenant's random key, stored wrapped by the master key. Destroying it makes its Files unreadable."""

    tenant = models.OneToOneField(Tenant, on_delete=models.CASCADE, related_name="key")
    wrapped_key = models.BinaryField(null=True)  # NULL once destroyed
    destroyed_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)


class File(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"  # row exists, body not (fully) stored yet
        ACTIVE = "active"
        TRASHED = "trashed"  # deleted but kept (body included) until purged; frees its name

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="files")
    folder = models.CharField(max_length=1024, default="/")
    name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=255, default="application/octet-stream")
    size = models.BigIntegerField(default=0)  # plaintext bytes
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    wrapped_data_key = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)
    trashed_at = models.DateTimeField(null=True)

    class Meta:
        indexes = [models.Index(fields=["tenant", "folder"])]
        # One active File per name in a Folder of a Tenant. Pending uploads and (later) Trash do not count.
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "folder", "name"],
                condition=models.Q(status="active"),
                name="unique_active_file_name_per_folder",
            )
        ]

    @property
    def object_key(self) -> str:
        # Ids only: no real file name ever reaches the storage layer.
        return f"{self.tenant_id}/{self.id}"
