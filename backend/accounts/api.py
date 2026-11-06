from uuid import UUID

from django.contrib.auth.hashers import check_password, make_password
from ninja import Router, Schema
from ninja.errors import HttpError

from . import auth
from .models import Membership, User

router = Router()

# Checked when the email is unknown, so unknown and wrong-password logins take similar time.
_DUMMY_HASH = make_password("unused")


class LoginIn(Schema):
    email: str
    password: str


class RefreshIn(Schema):
    refresh: str


class TokensOut(Schema):
    access: str
    refresh: str
    token_type: str = "bearer"


class TenantOut(Schema):
    id: UUID
    name: str
    role: str


def _tokens(user_id) -> TokensOut:
    return TokensOut(
        access=auth.issue(user_id, "access", auth.ACCESS_TTL),
        refresh=auth.issue(user_id, "refresh", auth.REFRESH_TTL),
    )


@router.post("/auth/login", auth=None, response=TokensOut)
def login(request, data: LoginIn):
    user = User.objects.filter(email=data.email.strip().lower()).first()
    ok = check_password(data.password, user.password if user else _DUMMY_HASH)
    if not (user and ok and user.is_active):
        raise HttpError(401, "Invalid credentials")
    return _tokens(user.id)


@router.post("/auth/refresh", auth=None, response=TokensOut)
def refresh(request, data: RefreshIn):
    user_id = auth.decode(data.refresh, "refresh")
    if user_id is None or not User.objects.filter(id=user_id, is_active=True).exists():
        raise HttpError(401, "Invalid refresh token")
    return _tokens(user_id)


@router.get("/tenants", auth=auth.UserAuth(), response=list[TenantOut])
def my_tenants(request):
    memberships = Membership.objects.select_related("tenant").filter(user_id=request.user_id).order_by("tenant__name")
    return [TenantOut(id=m.tenant_id, name=m.tenant.name, role=m.role) for m in memberships]


@router.get("/tenant", auth=auth.TenantAuth(), response=TenantOut)
def current_tenant(request):
    return TenantOut(id=request.tenant.id, name=request.tenant.name, role=request.role)
