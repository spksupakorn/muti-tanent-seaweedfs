"""Row-Level Security on File and TenantKey, seen as the runtime role, plus bulk_create under RLS."""
import os
import uuid

import psycopg
import pytest
from django.db import connections, transaction
from django.db.utils import ProgrammingError

from files.models import File


def connect(role, password):
    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"], dbname=os.environ["POSTGRES_DB"], user=role, password=password
    )


@pytest.fixture
def app():
    with connect("filestore_app", os.environ["DB_APP_PASSWORD"]) as conn:
        yield conn


@pytest.fixture
def two_tenants_with_files(world):
    for key in ("a", "b"):
        File.objects.create(
            tenant_id=world.tenants[key], name=f"rls-{key}.bin", wrapped_data_key=b"x", status=File.Status.ACTIVE
        )
    return world


def _file_tenants(conn):
    return {str(r[0]) for r in conn.execute("SELECT DISTINCT tenant_id FROM files_file")}


def test_selected_tenant_sees_only_its_files(app, two_tenants_with_files):
    w = two_tenants_with_files
    for key in ("a", "b"):
        app.execute("SELECT set_config('app.tenant_id', %s, true)", [w.tenants[key]])
        assert _file_tenants(app) == {w.tenants[key]}
        app.rollback()


def test_no_tenant_selected_means_no_files(app, two_tenants_with_files):
    assert _file_tenants(app) == set()  # never set in this session
    app.execute("SELECT set_config('app.tenant_id', %s, true)", [two_tenants_with_files.tenants["a"]])
    app.commit()
    assert _file_tenants(app) == set()  # set to '' after the first transaction


def test_user_setting_alone_does_not_reveal_files(app, two_tenants_with_files):
    from accounts.models import User

    carol = str(User.objects.get(email=two_tenants_with_files.emails["carol"]).id)
    app.execute("SELECT set_config('app.user_id', %s, true)", [carol])
    assert _file_tenants(app) == set()


def test_tenant_key_table_is_isolated_and_the_api_role_cannot_destroy_keys(app, world):
    from files.models import TenantKey

    TenantKey.objects.get_or_create(tenant_id=world.tenants["a"], defaults={"wrapped_key": b"k"})
    TenantKey.objects.get_or_create(tenant_id=world.tenants["b"], defaults={"wrapped_key": b"k"})
    app.execute("SELECT set_config('app.tenant_id', %s, true)", [world.tenants["a"]])
    seen = {str(r[0]) for r in app.execute("SELECT tenant_id FROM files_tenantkey")}
    assert seen == {world.tenants["a"]}
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app.execute("UPDATE files_tenantkey SET wrapped_key = NULL")


@pytest.fixture
def app_alias():
    """A Django connection that logs in as the runtime role, so ORM calls are subject to RLS."""
    settings_dict = dict(connections.databases["default"])
    settings_dict.update(USER="filestore_app", PASSWORD=os.environ["DB_APP_PASSWORD"])
    connections.databases["app"] = settings_dict  # copied from "default", so every default is already filled in
    yield "app"
    connections["app"].close()
    del connections.databases["app"]
    del connections["app"]


def _select_tenant(alias, tenant_id):
    with connections[alias].cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])


def test_bulk_create_works_for_the_selected_tenant(app_alias, world):
    tenant = world.tenants["a"]
    rows = [File(tenant_id=tenant, name=f"bulk-{i}", wrapped_data_key=b"x") for i in range(3)]
    with transaction.atomic(using=app_alias):
        _select_tenant(app_alias, tenant)
        File.objects.using(app_alias).bulk_create(rows)
        assert File.objects.using(app_alias).filter(name__startswith="bulk-").count() >= 3
        transaction.set_rollback(True, using=app_alias)


def test_bulk_create_for_another_tenant_is_refused_by_the_database(app_alias, world):
    other = [File(tenant_id=world.tenants["b"], name="smuggled", wrapped_data_key=b"x")]
    with pytest.raises(ProgrammingError, match="row-level security"):
        with transaction.atomic(using=app_alias):
            _select_tenant(app_alias, world.tenants["a"])
            File.objects.using(app_alias).bulk_create(other)
    assert not File.objects.filter(name="smuggled").exists()


def test_bulk_create_without_a_selected_tenant_is_refused(app_alias, world):
    with pytest.raises(ProgrammingError, match="row-level security"):
        with transaction.atomic(using=app_alias):
            File.objects.using(app_alias).bulk_create(
                [File(tenant_id=world.tenants["a"], name=f"unscoped-{uuid.uuid4().hex[:4]}", wrapped_data_key=b"x")]
            )
