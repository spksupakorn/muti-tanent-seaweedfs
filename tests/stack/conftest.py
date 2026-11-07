import io
import os
import uuid
from types import SimpleNamespace

import boto3
import django
import httpx
import psycopg
import pytest
from botocore.config import Config

django.setup()  # tests seed data through the real operator commands

from django.core.management import call_command  # noqa: E402


def _s3(access_key, secret_key):
    return boto3.client(
        "s3",
        endpoint_url=os.environ["S3_ENDPOINT_URL"],
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
        config=Config(
            s3={"addressing_style": "path"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


@pytest.fixture(scope="session")
def backend_url():
    return os.environ["BACKEND_URL"]


@pytest.fixture(scope="session")
def bucket():
    return os.environ["S3_BUCKET"]


@pytest.fixture(scope="session")
def backend_s3():
    return _s3(os.environ["S3_ACCESS_KEY"], os.environ["S3_SECRET_KEY"])


@pytest.fixture(scope="session")
def admin_s3():
    return _s3(os.environ["S3_ADMIN_ACCESS_KEY"], os.environ["S3_ADMIN_SECRET_KEY"])


def ops(*args):
    """Run an operator management command (back-office DB role) and return its output."""
    out = io.StringIO()
    call_command(*args, stdout=out)
    return out.getvalue().strip()


def new_world():
    """Two Tenants and three Users, unique per call so the suite can be repeated.

    alice: admin of A.  bob: member of B.  carol: member of A and admin of B.
    """
    sfx = uuid.uuid4().hex[:8]
    w = SimpleNamespace(password=f"pw-{sfx}", names={}, tenants={}, emails={})
    for key in ("a", "b"):
        w.names[key] = f"Tenant {key.upper()} {sfx}"
        w.tenants[key] = ops("create_tenant", w.names[key])
    for key in ("alice", "bob", "carol"):
        w.emails[key] = f"{key}-{sfx}@example.com"
        ops("create_user", w.emails[key], w.password)
    ops("add_membership", w.emails["alice"], w.names["a"], "admin")
    ops("add_membership", w.emails["bob"], w.names["b"], "member")
    ops("add_membership", w.emails["carol"], w.names["a"], "member")
    ops("add_membership", w.emails["carol"], w.names["b"], "admin")
    return w


@pytest.fixture(scope="session")
def world():
    return new_world()


def auth_headers(w, who, tenant_key):
    """Real login, then Authorization + X-Tenant-ID for the given Tenant ("a" or "b")."""
    r = httpx.post(
        f"{os.environ['BACKEND_URL']}/api/auth/login", json={"email": w.emails[who], "password": w.password}
    )
    return {"Authorization": f"Bearer {r.json()['access']}", "X-Tenant-ID": w.tenants[tenant_key]}


@pytest.fixture(scope="session")
def login(backend_url, world):
    def _login(who, password=None):
        return httpx.post(
            f"{backend_url}/api/auth/login",
            json={"email": world.emails[who], "password": password or world.password},
        )

    return _login


@pytest.fixture(scope="session")
def bearer(login):
    """Authorization header for a user, from a real login."""

    def _bearer(who):
        return {"Authorization": f"Bearer {login(who).json()['access']}"}

    return _bearer


def connect(role, password):
    """A plain database connection as one of the three roles (no application code in between)."""
    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"], dbname=os.environ["POSTGRES_DB"], user=role, password=password
    )


@pytest.fixture
def app():
    """The API's database role. Rolled back at the end: psycopg's `with` would COMMIT, so a test that wrongly
    succeeds at something it should be refused must not leave its change behind."""
    conn = connect("filestore_app", os.environ["DB_APP_PASSWORD"])
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()
