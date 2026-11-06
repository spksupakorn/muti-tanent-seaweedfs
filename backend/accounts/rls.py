"""Per-request identity for Row-Level Security.

The values must live exactly as long as a transaction (`set_config(..., true)`), so they cannot
leak to another request on a reused connection. Policies read them with
`current_setting('app.user_id', true)` and fail closed when unset.

Normal endpoints run in the request transaction (ATOMIC_REQUESTS) and the auth classes set the
values there. Work that must commit on its own (see `scope`) uses a second connection alias and
sets the values again, because a different connection has its own transaction.
"""
from contextlib import contextmanager

from django.db import connections, transaction


def _set(name: str, value, using: str) -> None:
    if not connections[using].in_atomic_block:
        raise RuntimeError(f"{name} must be set inside a transaction")
    with connections[using].cursor() as cur:
        cur.execute("SELECT set_config(%s, %s, true)", [name, str(value)])


def set_user(user_id, using: str = "default") -> None:
    _set("app.user_id", user_id, using)


def set_tenant(tenant_id, using: str = "default") -> None:
    _set("app.tenant_id", tenant_id, using)


@contextmanager
def scope(request, using: str):
    """A transaction on `using` that commits independently of the request, scoped to its Tenant."""
    with transaction.atomic(using=using):
        set_user(request.user_id, using)
        set_tenant(request.tenant.id, using)
        yield
