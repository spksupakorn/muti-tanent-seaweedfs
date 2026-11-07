"""Row-Level Security seen from the database itself, logging in as each role (no application code)."""
import os

import psycopg
import pytest
from conftest import connect
from psycopg import errors


def _tenants_visible(conn):
    return {str(r[0]) for r in conn.execute("SELECT DISTINCT tenant_id FROM accounts_membership")}


def test_runtime_role_cannot_bypass_rls(app):
    assert app.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user").fetchone() == (
        False,
        False,
    )
    owner = app.execute("SELECT tableowner FROM pg_tables WHERE tablename = 'accounts_membership'").fetchone()[0]
    assert owner == "filestore_owner"
    assert app.execute(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'accounts_membership'"
    ).fetchone() == (True, True)


def test_selected_tenant_shows_only_its_rows(app, world):
    for key, other in (("a", "b"), ("b", "a")):
        app.execute("SELECT set_config('app.tenant_id', %s, true)", [world.tenants[key]])
        assert _tenants_visible(app) == {world.tenants[key]}, f"tenant {key} saw {other}"
        app.rollback()


def test_unset_setting_shows_nothing_instead_of_failing(app):
    # A fresh session has never seen the setting: current_setting(..., true) must give NULL, not an error.
    assert _tenants_visible(app) == set()


def test_empty_setting_shows_nothing_instead_of_failing(app, world):
    # After a transaction-local set_config the session keeps an empty string; ''::uuid would raise.
    app.execute("SELECT set_config('app.tenant_id', %s, true)", [world.tenants["a"]])
    app.commit()
    assert app.execute("SHOW app.tenant_id").fetchone()[0] == ""
    assert _tenants_visible(app) == set()


def test_setting_does_not_survive_the_transaction(app, world):
    app.execute("SELECT set_config('app.tenant_id', %s, true)", [world.tenants["a"]])
    app.commit()
    assert _tenants_visible(app) == set()


def test_user_setting_shows_that_users_memberships(app, world):
    from accounts.models import User

    alice = str(User.objects.get(email=world.emails["alice"]).id)
    app.execute("SELECT set_config('app.user_id', %s, true)", [alice])
    assert _tenants_visible(app) == {world.tenants["a"]}


def test_runtime_role_cannot_write_accounts(app, world):
    with pytest.raises(errors.InsufficientPrivilege):
        app.execute("DELETE FROM accounts_membership")


def test_table_owner_is_bound_by_the_policy_too():
    with connect("filestore_owner", os.environ["DB_OWNER_PASSWORD"]) as owner:
        assert _tenants_visible(owner) == set()


def test_tenant_setting_takes_over_the_user_setting(app, world):
    # carol belongs to both Tenants. In a real request both settings are set; the selected
    # Tenant must win, so her row in the other Tenant stays hidden.
    from accounts.models import User

    carol = str(User.objects.get(email=world.emails["carol"]).id)
    for key in ("a", "b"):
        app.execute("SELECT set_config('app.user_id', %s, true)", [carol])
        app.execute("SELECT set_config('app.tenant_id', %s, true)", [world.tenants[key]])
        assert _tenants_visible(app) == {world.tenants[key]}
        app.rollback()
