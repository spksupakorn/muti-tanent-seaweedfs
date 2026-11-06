import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from conftest import auth_headers
from test_files import download, upload

TIMEOUT = httpx.Timeout(60)


def patch(backend_url, headers, file_id, **body):
    return httpx.patch(f"{backend_url}/api/files/{file_id}", headers=headers, json=body, timeout=TIMEOUT)


def names(backend_url, headers, folder):
    r = httpx.get(f"{backend_url}/api/files", headers=headers, params={"folder": folder}).json()
    return sorted(f["name"] for f in r["files"])


@pytest.fixture
def home():
    """A folder of our own, so tests do not see each other's files."""
    return f"/names-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def a_headers(world):
    return auth_headers(world, "alice", "a")


@pytest.fixture(scope="module")
def b_headers(world):
    return auth_headers(world, "bob", "b")


def test_rename_changes_list_and_download_name_but_not_content(backend_url, a_headers, home):
    created = upload(backend_url, a_headers, b"the content", name="old.txt", folder=home).json()
    r = patch(backend_url, a_headers, created["id"], name="new.txt")
    assert r.status_code == 200
    assert (r.json()["name"], r.json()["folder"]) == ("new.txt", home)

    assert names(backend_url, a_headers, home) == ["new.txt"]
    d = download(backend_url, a_headers, created["id"])
    assert d.content == b"the content"
    assert "new.txt" in d.headers["content-disposition"]


def test_move_changes_the_folder(backend_url, a_headers, home):
    created = upload(backend_url, a_headers, b"moving", name="m.txt", folder=home).json()
    r = patch(backend_url, a_headers, created["id"], folder=f"{home}/archive")
    assert r.status_code == 200 and r.json()["folder"] == f"{home}/archive"

    assert names(backend_url, a_headers, home) == []
    assert names(backend_url, a_headers, f"{home}/archive") == ["m.txt"]
    top = httpx.get(f"{backend_url}/api/files", headers=a_headers, params={"folder": home}).json()
    assert top["folders"] == ["archive"]
    assert download(backend_url, a_headers, created["id"]).content == b"moving"


def test_rename_and_move_in_one_request(backend_url, a_headers, home):
    created = upload(backend_url, a_headers, b"both", name="a.txt", folder=home).json()
    r = patch(backend_url, a_headers, created["id"], name="b.txt", folder=f"{home}/sub")
    assert r.status_code == 200
    assert names(backend_url, a_headers, f"{home}/sub") == ["b.txt"]


def test_rename_to_a_taken_name_is_a_conflict_and_changes_nothing(backend_url, a_headers, home):
    first = upload(backend_url, a_headers, b"first", name="taken.txt", folder=home).json()
    second = upload(backend_url, a_headers, b"second", name="other.txt", folder=home).json()

    r = patch(backend_url, a_headers, second["id"], name="taken.txt")
    assert r.status_code == 409

    assert names(backend_url, a_headers, home) == ["other.txt", "taken.txt"]
    assert download(backend_url, a_headers, first["id"]).content == b"first"  # the original is not overwritten
    assert download(backend_url, a_headers, second["id"]).content == b"second"


def test_move_onto_a_taken_name_is_a_conflict_and_changes_nothing(backend_url, a_headers, home):
    upload(backend_url, a_headers, b"here", name="same.txt", folder=f"{home}/one")
    moving = upload(backend_url, a_headers, b"there", name="same.txt", folder=f"{home}/two").json()

    assert patch(backend_url, a_headers, moving["id"], folder=f"{home}/one").status_code == 409

    assert names(backend_url, a_headers, f"{home}/one") == ["same.txt"]
    assert names(backend_url, a_headers, f"{home}/two") == ["same.txt"]
    assert download(backend_url, a_headers, moving["id"]).content == b"there"


def test_conflict_does_not_break_the_next_request_on_the_connection(backend_url, a_headers, home):
    a = upload(backend_url, a_headers, b"a", name="a.txt", folder=home).json()
    upload(backend_url, a_headers, b"b", name="b.txt", folder=home)
    for _ in range(3):  # repeated conflicts, then a good rename
        assert patch(backend_url, a_headers, a["id"], name="b.txt").status_code == 409
    assert patch(backend_url, a_headers, a["id"], name="c.txt").status_code == 200


def test_renaming_to_its_own_name_is_not_a_conflict(backend_url, a_headers, home):
    created = upload(backend_url, a_headers, b"x", name="stay.txt", folder=home).json()
    assert patch(backend_url, a_headers, created["id"], name="stay.txt").status_code == 200
    assert patch(backend_url, a_headers, created["id"], folder=home).status_code == 200


def test_same_name_is_fine_in_another_folder_and_in_another_tenant(backend_url, a_headers, b_headers, home):
    assert upload(backend_url, a_headers, b"1", name="dup.txt", folder=f"{home}/x").status_code == 200
    assert upload(backend_url, a_headers, b"2", name="dup.txt", folder=f"{home}/y").status_code == 200
    assert upload(backend_url, b_headers, b"3", name="dup.txt", folder=f"{home}/x").status_code == 200


def test_upload_with_a_taken_name_is_a_conflict_and_leaves_the_original(backend_url, a_headers, home):
    original = upload(backend_url, a_headers, b"original", name="doc.txt", folder=home).json()

    r = upload(backend_url, a_headers, b"impostor", name="doc.txt", folder=home)
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]

    assert names(backend_url, a_headers, home) == ["doc.txt"]
    assert download(backend_url, a_headers, original["id"]).content == b"original"


def test_two_simultaneous_uploads_of_one_name_leave_exactly_one(backend_url, a_headers, world, home):
    from files.models import File

    # Large bodies, so both requests pass the API's early name check before either upload activates;
    # the loser must then be stopped by the unique index. (The backend logs "lost the race" for it.)
    def go(i):
        return upload(backend_url, a_headers, bytes([i]) * 30_000_000, name="race.txt", folder=home).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = sorted(pool.map(go, range(2)))

    assert codes == [200, 409]
    assert names(backend_url, a_headers, home) == ["race.txt"]
    leftovers = File.objects.filter(tenant_id=world.tenants["a"], folder=home, status=File.Status.PENDING)
    assert not leftovers.exists()  # the loser cleaned up after itself


def test_nothing_to_change_is_a_client_error(backend_url, a_headers, home):
    created = upload(backend_url, a_headers, b"x", folder=home).json()
    assert patch(backend_url, a_headers, created["id"]).status_code == 422


@pytest.mark.parametrize("body", [{"name": "a/b"}, {"name": "  "}, {"name": ".."}, {"folder": "/a/../b"}])
def test_invalid_new_names_and_folders_are_rejected(backend_url, a_headers, home, body):
    created = upload(backend_url, a_headers, b"x", name="fine.txt", folder=home).json()
    assert patch(backend_url, a_headers, created["id"], **body).status_code == 422
    assert names(backend_url, a_headers, home) == ["fine.txt"]


def test_another_tenants_file_cannot_be_renamed_or_moved(backend_url, a_headers, b_headers, home):
    mine = upload(backend_url, a_headers, b"mine", name="mine.txt", folder=home).json()

    assert patch(backend_url, b_headers, mine["id"], name="stolen.txt").status_code == 404
    assert patch(backend_url, b_headers, mine["id"], folder="/elsewhere").status_code == 404
    assert names(backend_url, a_headers, home) == ["mine.txt"]


def test_unknown_and_unauthenticated_requests_are_refused(backend_url, a_headers):
    assert patch(backend_url, a_headers, uuid.uuid4(), name="x.txt").status_code == 404
    assert patch(backend_url, {}, uuid.uuid4(), name="x.txt").status_code == 401


def test_a_pending_file_cannot_be_renamed(backend_url, a_headers, world, home):
    from files.models import File

    pending = File.objects.create(
        tenant_id=world.tenants["a"], folder=home, name="half.bin", wrapped_data_key=b"x", status=File.Status.PENDING
    )
    assert patch(backend_url, a_headers, pending.id, name="done.bin").status_code == 404


def test_a_pending_row_does_not_block_the_name(backend_url, a_headers, world, home):
    from files.models import File

    File.objects.create(
        tenant_id=world.tenants["a"], folder=home, name="held.bin", wrapped_data_key=b"x", status=File.Status.PENDING
    )
    assert upload(backend_url, a_headers, b"real", name="held.bin", folder=home).status_code == 200


def test_the_database_itself_refuses_two_active_files_with_one_name(world, home):
    # The backstop for the race above: even a writer that skips the API's check cannot create a duplicate.
    from django.db import IntegrityError, transaction

    from files.models import File

    def make():
        return File.objects.create(
            tenant_id=world.tenants["a"], folder=home, name="dup.bin", wrapped_data_key=b"x", status=File.Status.ACTIVE
        )

    make()
    with pytest.raises(IntegrityError), transaction.atomic():
        make()
