from .models import AuditEntry


def _file_entry(file, action, using="default", **who) -> None:
    AuditEntry.objects.using(using).create(
        action=action, file_id=file.id, file_name=file.name, file_folder=file.folder, **who
    )


def record(request, action, file, using: str = "default") -> None:
    """Call only after the action succeeded, in the same transaction as the change it describes."""
    _file_entry(file, action, using, tenant_id=request.tenant.id, user_id=request.user_id)


def record_by_operator(operator, action, file) -> None:
    """An action a Platform operator took in the back office on a Tenant's File."""
    _file_entry(file, action, tenant_id=file.tenant_id, user=operator, by_operator=True)


def record_operator_event(tenant, action, detail: str = "", operator=None) -> None:
    """Something a Platform operator did to a Tenant that involves no File (an access change, the key destroyed).
    `operator` is None when it was done from the command line, where nobody is signed in."""
    AuditEntry.objects.create(
        tenant=tenant, user=operator, action=action, file_name="", file_folder="", detail=detail, by_operator=True
    )
