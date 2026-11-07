import os
import re
import uuid

import httpx
import psycopg
import pytest
from conftest import auth_headers, new_world, ops
from test_files import download, upload
from test_trash import restore, trash

from accounts.models import Membership, Tenant, User
from files.models import File

BASE = os.environ["BACKOFFICE_URL"]
API = os.environ["BACKEND_URL"]


class Admin:
    """A browser-like session on the back office (Django admin), with CSRF handled."""

    def __init__(self):
        self.http = httpx.Client(base_url=BASE, follow_redirects=False, timeout=60)

    def login(self, email, password):
        page = self.http.get("/admin/login/")
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.text).group(1)
        return self.http.post(
            "/admin/login/", data={"username": email, "password": password, "csrfmiddlewaretoken": token, "next": "/admin/"}
        )

    def signed_in(self):
        return self.http.get("/admin/").status_code == 200

    def post(self, path, data):
        data = {**data, "csrfmiddlewaretoken": self.http.cookies["backoffice_csrftoken"]}
        return self.http.post(path, data=data)


def operator_session():
    email, pw = f"operator-{uuid.uuid4().hex[:8]}@example.com", f"pw-{uuid.uuid4().hex[:8]}"
    ops("create_user", "--operator", email, pw)
    admin = Admin()
    assert admin.login(email, pw).status_code == 302
    return admin, email, pw


@pytest.fixture(scope="module")
def admin():
    return operator_session()[0]


@pytest.fixture(scope="module")
def tenants(world):
    return auth_headers(world, "alice", "a"), auth_headers(world, "bob", "b")


def act(admin, action, *file_ids):
    r = admin.post(
        "/admin/files/file/", {"action": action, "_selected_action": [str(i) for i in file_ids], "index": "0", "select_across": "0"}
    )
    assert r.status_code == 302, r.text[:300]
    return r


def api_audit(headers):
    return httpx.get(f"{API}/api/audit", headers=headers, params={"limit": 200}).json()


# --- who may sign in -----------------------------------------------------------------------------


def test_an_operator_signs_in(admin):
    assert admin.signed_in()


def test_a_tenant_member_cannot_sign_in_even_with_the_right_password(world):
    other = Admin()
    r = other.login(world.emails["alice"], world.password)  # alice is an admin of a Tenant, but not an operator
    assert r.status_code == 200  # the form again, with an error
    assert not other.signed_in()


def test_a_wrong_password_and_a_deactivated_operator_cannot_sign_in():
    _, email, pw = operator_session()
    assert not (lambda s: (s.login(email, "wrong"), s.signed_in())[1])(Admin())
    User.objects.filter(email=email).update(is_active=False)
    fresh = Admin()
    fresh.login(email, pw)
    assert not fresh.signed_in()


def test_back_office_pages_need_a_session():
    anonymous = httpx.Client(base_url=BASE, follow_redirects=False)
    for path in ("/admin/", "/admin/files/file/", "/admin/accounts/user/", "/admin/audit/auditentry/"):
        r = anonymous.get(path)
        assert r.status_code == 302 and "/admin/login/" in r.headers["location"], path


def test_the_tenant_api_is_not_served_here_and_the_admin_is_not_served_by_the_api():
    assert httpx.get(f"{BASE}/api/health").status_code == 404
    assert httpx.get(f"{API}/admin/").status_code == 404


# --- what an operator can see and do with files --------------------------------------------------


def test_files_of_every_tenant_are_listed(admin, tenants, world):
    tag = uuid.uuid4().hex[:8]
    a, b = tenants
    upload(API, a, b"a", name=f"visible-a-{tag}.txt")
    upload(API, b, b"b", name=f"visible-b-{tag}.txt")

    page = admin.http.get("/admin/files/file/", params={"q": f"visible-"}).text
    for expected in (f"visible-a-{tag}.txt", f"visible-b-{tag}.txt", world.names["a"], world.names["b"]):
        assert expected in page


def test_trash_and_restore_work_on_any_tenants_file_and_are_audited(admin, tenants, world):
    a, b = tenants
    f = upload(API, b, b"belongs to B").json()["id"]

    act(admin, "trash_selected", f)
    assert File.objects.get(id=f).status == File.Status.TRASHED
    assert download(API, b, f).status_code == 404  # the owner sees it gone
    assert f in [t["id"] for t in httpx.get(f"{API}/api/trash", headers=b).json()]

    act(admin, "restore_selected", f)
    assert File.objects.get(id=f).status == File.Status.ACTIVE
    assert download(API, b, f).content == b"belongs to B"

    # Tenant B's own admin (carol) sees what the platform did, without learning who the operator is.
    carol_b = auth_headers(world, "carol", "b")
    mine = [e for e in api_audit(carol_b) if e["file_id"] == f]
    assert [(e["action"], e["by_operator"], e["user_email"]) for e in reversed(mine)] == [
        ("upload", False, world.emails["bob"]),
        ("delete", True, None),
        ("restore", True, None),
        ("download", False, world.emails["bob"]),  # bob's own download after the operator restored it
    ]
    # And Tenant A's admin sees nothing of it.
    assert f not in {e["file_id"] for e in api_audit(auth_headers(world, "alice", "a"))}


def test_the_back_office_records_what_the_operator_did(tenants):
    a, _ = tenants
    admin, email, _pw = operator_session()
    f = upload(API, a, b"x").json()["id"]
    act(admin, "trash_selected", f)
    with psycopg.connect(
        host=os.environ["POSTGRES_HOST"], dbname=os.environ["POSTGRES_DB"], user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"]
    ) as conn:
        logged = conn.execute("SELECT count(*) FROM django_admin_log WHERE object_id = %s", [f]).fetchone()[0]
    assert logged == 1


def test_restoring_onto_a_taken_name_is_refused(admin, tenants):
    a, _ = tenants
    folder = f"/ops-{uuid.uuid4().hex[:6]}"
    old = upload(API, a, b"old", name="same.txt", folder=folder).json()["id"]
    trash(API, a, old)
    newer = upload(API, a, b"newer", name="same.txt", folder=folder).json()["id"]

    r = act(admin, "restore_selected", old)

    assert File.objects.get(id=old).status == File.Status.TRASHED
    assert download(API, a, newer).content == b"newer"
    assert "name is taken" in admin.http.get(r.headers["location"]).text
    assert "restore" not in [e["action"] for e in api_audit(a) if e["file_id"] == old]


def test_files_cannot_be_added_edited_or_deleted_in_the_back_office(admin, tenants):
    a, _ = tenants
    f = upload(API, a, b"x").json()["id"]
    assert admin.http.get("/admin/files/file/add/").status_code == 403
    assert admin.http.get(f"/admin/files/file/{f}/delete/").status_code == 403
    assert admin.post(f"/admin/files/file/{f}/change/", {"name": "renamed"}).status_code == 403
    assert File.objects.get(id=f).name != "renamed"


# --- no way to read a file from here -------------------------------------------------------------


def test_no_page_shows_a_files_content_or_its_key(admin, tenants):
    a, _ = tenants
    marker = "TOP-SECRET-MARKER-" + uuid.uuid4().hex
    f = upload(API, a, marker.encode() * 50, name="secret.txt").json()["id"]

    view = admin.http.get(f"/admin/files/file/{f}/change/")
    assert view.status_code == 200 and "secret.txt" in view.text  # viewable...
    for page in (view.text, admin.http.get("/admin/files/file/", params={"q": "secret"}).text):
        assert marker not in page and "wrapped_data_key" not in page  # ...but never the content or the key
    # Django admin answers an unknown path under a model by redirecting to its change page: still no content.
    guess = admin.http.get(f"/admin/files/file/{f}/download/", follow_redirects=True)
    assert guess.headers["content-type"].startswith("text/html") and marker not in guess.text
    index = admin.http.get("/admin/").text
    assert "Tenant keys" not in index and "tenantkey" not in index


# --- managing tenants, users and memberships -----------------------------------------------------


def test_an_operator_creates_a_tenant_a_user_and_a_membership_that_then_work_in_the_api(admin):
    sfx = uuid.uuid4().hex[:8]
    email, pw, name = f"made-in-admin-{sfx}@example.com", f"pw-{sfx}", f"Made in admin {sfx}"

    assert admin.post("/admin/accounts/tenant/add/", {"name": name, "_save": "Save"}).status_code == 302
    assert admin.post(
        "/admin/accounts/user/add/", {"email": email, "password": pw, "is_active": "on", "_save": "Save"}
    ).status_code == 302
    tenant, user = Tenant.objects.get(name=name), User.objects.get(email=email)
    assert admin.post(
        "/admin/accounts/membership/add/", {"user": str(user.id), "tenant": str(tenant.id), "role": "admin", "_save": "Save"}
    ).status_code == 302

    token = httpx.post(f"{API}/api/auth/login", json={"email": email, "password": pw}).json()["access"]
    mine = httpx.get(f"{API}/api/tenants", headers={"Authorization": f"Bearer {token}"}).json()
    assert [(t["name"], t["role"]) for t in mine] == [(name, "admin")]


def test_a_new_user_needs_a_password_and_the_password_is_stored_hashed(admin):
    email = f"nopw-{uuid.uuid4().hex[:8]}@example.com"
    r = admin.post("/admin/accounts/user/add/", {"email": email, "is_active": "on", "_save": "Save"})
    assert r.status_code == 200 and "needs a password" in r.text and not User.objects.filter(email=email).exists()

    admin.post("/admin/accounts/user/add/", {"email": email, "password": "plain-secret", "is_active": "on", "_save": "Save"})
    stored = User.objects.get(email=email).password
    assert "plain-secret" not in stored and stored.startswith("pbkdf2_")


def test_a_role_and_a_password_can_be_changed(admin, world):
    w = new_world()
    m = Membership.objects.get(user__email=w.emails["bob"], tenant_id=w.tenants["b"])
    assert admin.post(f"/admin/accounts/membership/{m.id}/change/", {"user": str(m.user_id), "tenant": str(m.tenant_id), "role": "admin", "_save": "Save"}).status_code == 302
    assert Membership.objects.get(id=m.id).role == "admin"

    user = User.objects.get(email=w.emails["bob"])
    admin.post(f"/admin/accounts/user/{user.id}/change/", {"email": user.email, "password": "changed-pw", "is_active": "on", "_save": "Save"})
    assert httpx.post(f"{API}/api/auth/login", json={"email": user.email, "password": "changed-pw"}).status_code == 200
    assert httpx.post(f"{API}/api/auth/login", json={"email": user.email, "password": w.password}).status_code == 401


def test_tenants_and_users_cannot_be_deleted_here(admin, world):
    assert admin.http.get(f"/admin/accounts/tenant/{world.tenants['a']}/delete/").status_code == 403
    user = User.objects.get(email=world.emails["alice"])
    assert admin.http.get(f"/admin/accounts/user/{user.id}/delete/").status_code == 403


# --- the database roles stay apart ---------------------------------------------------------------


def _as(role, password):
    return psycopg.connect(host=os.environ["POSTGRES_HOST"], dbname=os.environ["POSTGRES_DB"], user=role, password=password)


@pytest.mark.parametrize("table", ["django_session", "django_admin_log", "auth_permission", "django_content_type", "django_migrations"])
def test_the_api_role_cannot_touch_the_admins_own_tables(table):
    conn = _as("filestore_app", os.environ["DB_APP_PASSWORD"])
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(f"SELECT count(*) FROM {table}")
    finally:
        conn.rollback()
        conn.close()


@pytest.mark.parametrize(
    "login,password_env,target",
    [("filestore_app", "DB_APP_PASSWORD", "filestore_backoffice"), ("filestore_backoffice", "DB_BACKOFFICE_PASSWORD", "filestore_app")],
)
def test_neither_role_can_become_the_other(login, password_env, target):
    password = os.environ.get(password_env) or os.environ["POSTGRES_PASSWORD"]  # the tests container logs in as back office by default
    conn = _as(login, password)
    try:
        assert conn.execute("SELECT pg_has_role(%s, %s, 'USAGE')", [login, target]).fetchone()[0] is False
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(f"SET ROLE {target}")
    finally:
        conn.rollback()
        conn.close()


def test_the_back_office_role_sees_across_tenants_while_the_api_role_sees_nothing_unscoped(tenants):
    a, _ = tenants
    upload(API, a, b"x")
    back = _as(os.environ["POSTGRES_USER"], os.environ["POSTGRES_PASSWORD"])
    api = _as("filestore_app", os.environ["DB_APP_PASSWORD"])
    try:
        assert back.execute("SELECT count(DISTINCT tenant_id) FROM files_file").fetchone()[0] >= 2
        assert api.execute("SELECT count(*) FROM files_file").fetchone()[0] == 0
    finally:
        back.rollback()
        api.rollback()
        back.close()
        api.close()
