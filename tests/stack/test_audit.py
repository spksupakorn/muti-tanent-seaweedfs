import os
import uuid
from contextlib import contextmanager

import httpx
import psycopg
import pytest
from conftest import auth_headers
from test_files import download, overwrite, stored, upload
from test_trash import restore, trash

TIMEOUT = httpx.Timeout(60)


@pytest.fixture(scope="module")
def alice_a(world):  # admin of Tenant A
    return auth_headers(world, "alice", "a")


@pytest.fixture(scope="module")
def carol_a(world):  # member of Tenant A
    return auth_headers(world, "carol", "a")


@pytest.fixture(scope="module")
def carol_b(world):  # admin of Tenant B
    return auth_headers(world, "carol", "b")


@pytest.fixture(scope="module")
def bob_b(world):  # member of Tenant B
    return auth_headers(world, "bob", "b")


@pytest.fixture
def home():
    return f"/audit-{uuid.uuid4().hex[:8]}"


def audit(backend_url, headers, **params):
    return httpx.get(f"{backend_url}/api/audit", headers=headers, params=params, timeout=TIMEOUT)


def entries_for(backend_url, headers, file_id):
    """Oldest first, the entries about one file."""
    rows = audit(backend_url, headers, limit=200).json()
    return [e for e in reversed(rows) if e["file_id"] == file_id]


def actions(backend_url, headers, file_id):
    return [e["action"] for e in entries_for(backend_url, headers, file_id)]


def test_every_kind_of_action_is_recorded_with_who_which_file_and_when(backend_url, alice_a, world, home):
    f = upload(backend_url, alice_a, b"audited", name="one.txt", folder=home).json()
    assert download(backend_url, alice_a, f["id"]).status_code == 200
    renamed = httpx.patch(f"{backend_url}/api/files/{f['id']}", headers=alice_a, json={"name": "two.txt"})
    assert renamed.status_code == 200
    moved = httpx.patch(f"{backend_url}/api/files/{f['id']}", headers=alice_a, json={"folder": f"{home}/sub"})
    assert moved.status_code == 200
    assert trash(backend_url, alice_a, f["id"]).status_code == 204
    assert restore(backend_url, alice_a, f["id"]).status_code == 200

    entries = entries_for(backend_url, alice_a, f["id"])
    assert [e["action"] for e in entries] == ["upload", "download", "rename", "move", "delete", "restore"]
    assert {e["user_email"] for e in entries} == {world.emails["alice"]}
    assert all(e["created_at"] for e in entries)
    # Each entry says what the file was called and where at that moment.
    assert (entries[0]["file_name"], entries[0]["file_folder"]) == ("one.txt", home)
    assert (entries[2]["file_name"], entries[2]["file_folder"]) == ("two.txt", home)
    assert (entries[3]["file_name"], entries[3]["file_folder"]) == ("two.txt", f"{home}/sub")


def test_rename_and_move_in_one_request_make_two_entries(backend_url, alice_a, home):
    f = upload(backend_url, alice_a, b"x", name="a.txt", folder=home).json()
    r = httpx.patch(f"{backend_url}/api/files/{f['id']}", headers=alice_a, json={"name": "b.txt", "folder": f"{home}/n"})
    assert r.status_code == 200
    assert actions(backend_url, alice_a, f["id"]) == ["upload", "rename", "move"]


def test_entries_name_the_user_who_acted(backend_url, alice_a, carol_a, world, home):
    f = upload(backend_url, alice_a, b"shared", name="s.txt", folder=home).json()
    assert download(backend_url, carol_a, f["id"]).status_code == 200  # carol, a member of A

    by_user = {e["action"]: e["user_email"] for e in entries_for(backend_url, alice_a, f["id"])}
    assert by_user == {"upload": world.emails["alice"], "download": world.emails["carol"]}


def test_newest_entries_come_first_and_paging_works(backend_url, alice_a, home):
    for i in range(3):
        upload(backend_url, alice_a, b"x", name=f"p{i}.txt", folder=home)
    newest = audit(backend_url, alice_a, limit=3).json()
    assert [e["file_name"] for e in newest] == ["p2.txt", "p1.txt", "p0.txt"]
    page2 = audit(backend_url, alice_a, limit=1, offset=1).json()
    assert [e["file_name"] for e in page2] == ["p1.txt"]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"offset": -1}])
def test_bad_paging_is_rejected(backend_url, alice_a, params):
    assert audit(backend_url, alice_a, **params).status_code == 422


def test_a_member_cannot_read_the_audit_log_but_an_admin_can(backend_url, alice_a, carol_a, bob_b, carol_b):
    assert audit(backend_url, carol_a).status_code == 403  # member of A
    assert audit(backend_url, bob_b).status_code == 403  # member of B
    assert audit(backend_url, alice_a).status_code == 200  # admin of A
    assert audit(backend_url, carol_b).status_code == 200  # the same carol is admin of B


def test_the_role_is_per_tenant_not_per_person(backend_url, carol_a, carol_b):
    assert audit(backend_url, carol_a).status_code == 403
    assert audit(backend_url, carol_b).status_code == 200


def test_unauthenticated_requests_are_refused(backend_url):
    assert audit(backend_url, {}).status_code == 401


def test_an_admin_sees_only_their_own_tenants_log(backend_url, alice_a, carol_b, home):
    a_file = upload(backend_url, alice_a, b"a", name="only-a.txt", folder=home).json()
    b_file = upload(backend_url, carol_b, b"b", name="only-b.txt", folder=home).json()

    a_log = {e["file_id"] for e in audit(backend_url, alice_a, limit=200).json()}
    b_log = {e["file_id"] for e in audit(backend_url, carol_b, limit=200).json()}
    assert a_file["id"] in a_log and b_file["id"] not in a_log
    assert b_file["id"] in b_log and a_file["id"] not in b_log


def test_failed_actions_leave_no_success_entry(backend_url, alice_a, world, admin_s3, bucket, home):
    taken = upload(backend_url, alice_a, b"first", name="taken.txt", folder=home).json()
    other = upload(backend_url, alice_a, b"second", name="other.txt", folder=home).json()

    ghost = uuid.uuid4()
    assert download(backend_url, alice_a, ghost).status_code == 404
    assert trash(backend_url, alice_a, ghost).status_code == 404
    assert restore(backend_url, alice_a, ghost).status_code == 404
    assert httpx.patch(f"{backend_url}/api/files/{ghost}", headers=alice_a, json={"name": "x"}).status_code == 404
    # name conflicts: rename, move, upload, restore
    assert httpx.patch(f"{backend_url}/api/files/{other['id']}", headers=alice_a, json={"name": "taken.txt"}).status_code == 409
    assert upload(backend_url, alice_a, b"dup", name="taken.txt", folder=home).status_code == 409
    gone = upload(backend_url, alice_a, b"gone", name="reused.txt", folder=home).json()
    trash(backend_url, alice_a, gone["id"])
    upload(backend_url, alice_a, b"reuse", name="reused.txt", folder=home)
    assert restore(backend_url, alice_a, gone["id"]).status_code == 409
    # a download that fails its integrity check
    overwrite(admin_s3, bucket, world.tenants["a"], taken["id"], b"\0" * 64)
    assert download(backend_url, alice_a, taken["id"]).status_code == 500

    assert "download" not in actions(backend_url, alice_a, taken["id"])
    assert actions(backend_url, alice_a, other["id"]) == ["upload"]  # the conflicting rename left nothing
    assert actions(backend_url, alice_a, gone["id"]) == ["upload", "delete"]  # and neither did the restore


def test_an_upload_that_fails_leaves_no_entry(backend_url, alice_a, home):
    r = upload(backend_url, alice_a, b"x", name="..", folder=home)  # rejected before anything is stored
    assert r.status_code == 422
    assert all(e["file_folder"] != home for e in audit(backend_url, alice_a, limit=200).json())


def test_entries_outlive_the_file_they_describe(backend_url, alice_a, world, home):
    from audit.models import AuditEntry
    from files.models import File

    f = upload(backend_url, alice_a, b"temp", name="ephemeral.txt", folder=home).json()
    File.objects.filter(id=f["id"]).delete()  # what a purge will do

    entry = entries_for(backend_url, alice_a, f["id"])[0]
    assert entry["file_name"] == "ephemeral.txt"
    assert AuditEntry.objects.filter(file_id=f["id"]).exists()


# --- the table itself, as the API's database role -----------------------------------------------


@contextmanager
def _app():
    conn = psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        dbname=os.environ["POSTGRES_DB"],
        user="filestore_app",
        password=os.environ["DB_APP_PASSWORD"],
    )
    try:
        yield conn
    finally:
        conn.rollback()  # never commit what a test left behind
        conn.close()


def _tenants_in_log(conn):
    return {str(r[0]) for r in conn.execute("SELECT DISTINCT tenant_id FROM audit_auditentry")}


def test_database_shows_only_the_selected_tenants_entries(backend_url, world, alice_a, carol_b, home):
    upload(backend_url, alice_a, b"a", folder=home)
    upload(backend_url, carol_b, b"b", folder=home)
    with _app() as conn:
        assert _tenants_in_log(conn) == set()  # nothing selected: nothing visible
        for key in ("a", "b"):
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", [world.tenants[key]])
            assert _tenants_in_log(conn) == {world.tenants[key]}
            conn.rollback()


def test_the_api_role_cannot_alter_or_erase_entries(world):
    with _app() as conn:
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", [world.tenants["a"]])
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM audit_auditentry")
        conn.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE audit_auditentry SET file_name = 'forged'")
