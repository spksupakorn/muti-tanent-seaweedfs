import uuid
from datetime import timedelta

import jwt
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from ninja.errors import HttpError
from ninja.security import HttpBearer

from . import rls
from .models import Membership, User

ALGORITHM = "HS256"
ACCESS_TTL = timedelta(minutes=15)
REFRESH_TTL = timedelta(days=7)


def issue(user_id, typ: str, ttl: timedelta) -> str:
    now = timezone.now()
    claims = {"sub": str(user_id), "typ": typ, "iat": now, "exp": now + ttl}
    return jwt.encode(claims, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode(token: str, typ: str):
    """Return the user id for a valid token of the given type, else None."""
    try:
        claims = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[ALGORITHM], options={"require": ["exp", "sub", "typ"]}
        )
        if claims["typ"] != typ:
            return None
        return uuid.UUID(claims["sub"])
    except (jwt.InvalidTokenError, ValueError):
        return None


class UserAuth(HttpBearer):
    """Valid access token of an active user. Sets app.user_id for the request transaction."""

    def authenticate(self, request, token):
        user_id = decode(token, "access")
        if user_id is None:
            return None
        # Inside the request transaction this is a savepoint and the settings outlive it; the
        # atomic block also makes this work for a view that opts out of the request transaction.
        with transaction.atomic():
            rls.set_user(user_id)
            if not User.objects.filter(id=user_id, is_active=True).exists():
                return None
            self.select_tenant(request, user_id)
        request.user_id = user_id
        return user_id

    def select_tenant(self, request, user_id):
        pass


class TenantAuth(UserAuth):
    """UserAuth plus X-Tenant-ID, validated against the caller's Membership before use."""

    def select_tenant(self, request, user_id):
        raw = request.headers.get("X-Tenant-ID")
        if not raw:
            raise HttpError(400, "X-Tenant-ID header required")
        try:
            tenant_id = uuid.UUID(raw)
        except ValueError:
            raise HttpError(400, "X-Tenant-ID must be a UUID")
        membership = Membership.objects.select_related("tenant").filter(user_id=user_id, tenant_id=tenant_id).first()
        if membership is None:
            raise HttpError(403, "Not a member of this tenant")
        rls.set_tenant(tenant_id)
        request.tenant = membership.tenant
        request.role = membership.role
