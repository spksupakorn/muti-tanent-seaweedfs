from datetime import datetime
from typing import Optional
from uuid import UUID

from ninja import Query, Router, Schema
from ninja.errors import HttpError

from accounts.auth import TenantAuth
from accounts.models import Membership

from .models import AuditEntry

router = Router()


class AuditOut(Schema):
    id: UUID
    action: str
    file_id: Optional[UUID]
    file_name: str
    file_folder: str
    user_email: Optional[str]
    created_at: datetime


class Paging(Schema):
    limit: int = 50
    offset: int = 0


@router.get("/audit", auth=TenantAuth(), response=list[AuditOut])
def list_audit(request, paging: Query[Paging]):
    if request.role != Membership.Role.ADMIN:
        raise HttpError(403, "Only a tenant admin can read the audit log")
    if not (1 <= paging.limit <= 200 and paging.offset >= 0):
        raise HttpError(422, "limit must be 1-200 and offset 0 or more")
    entries = (
        AuditEntry.objects.select_related("user")
        .filter(tenant=request.tenant)
        .order_by("-created_at", "-id")[paging.offset : paging.offset + paging.limit]
    )
    return [
        AuditOut(
            id=e.id,
            action=e.action,
            file_id=e.file_id,
            file_name=e.file_name,
            file_folder=e.file_folder,
            user_email=e.user.email if e.user else None,
            created_at=e.created_at,
        )
        for e in entries
    ]
