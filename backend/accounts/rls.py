"""Per-request identity for Row-Level Security.

The values must live exactly as long as the request's transaction (`set_config(..., true)`),
so they cannot leak to another request on a reused connection. Policies read them with
`current_setting('app.user_id', true)` and fail closed when unset.
"""
from django.db import connection


def _set(name: str, value) -> None:
    if not connection.in_atomic_block:
        raise RuntimeError(f"{name} must be set inside the request transaction")
    with connection.cursor() as cur:
        cur.execute("SELECT set_config(%s, %s, true)", [name, str(value)])


def set_user(user_id) -> None:
    _set("app.user_id", user_id)


def set_tenant(tenant_id) -> None:
    _set("app.tenant_id", tenant_id)
