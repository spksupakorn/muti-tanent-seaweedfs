from .models import AuditEntry


def record(request, action: str, file, using: str = "default") -> None:
    """Call only after the action succeeded, in the same transaction as the change it describes."""
    AuditEntry.objects.using(using).create(
        tenant_id=request.tenant.id,
        user_id=request.user_id,
        action=action,
        file_id=file.id,
        file_name=file.name,
        file_folder=file.folder,
    )


def record_by_operator(operator, action: str, file) -> None:
    """An action a Platform operator took in the back office on a Tenant's File."""
    AuditEntry.objects.create(
        tenant_id=file.tenant_id,
        user=operator,
        action=action,
        file_id=file.id,
        file_name=file.name,
        file_folder=file.folder,
        by_operator=True,
    )
