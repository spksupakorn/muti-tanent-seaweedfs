import uuid

import httpx
import pytest
from conftest import auth_headers
from test_files import download, upload

TIMEOUT = httpx.Timeout(60)


def trash(backend_url, headers, file_id):
    return httpx.delete(f"{backend_url}/api/files/{file_id}", headers=headers, timeout=TIMEOUT)


def trash_list(backend_url, headers):
    return httpx.get(f"{backend_url}/api/trash", headers=headers, timeout=TIMEOUT).json()


def restore(backend_url, headers, file_id):
    return httpx.post(f"{backend_url}/api/trash/{file_id}/restore", headers=headers, timeout=TIMEOUT)


def listed(backend_url, headers, folder):
    r = httpx.get(f"{backend_url}/api/files", headers=headers, params={"folder": folder}).json()
    return sorted(f["name"] for f in r["files"])


@pytest.fixture
def home():
    return f"/trash-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def a_headers(world):
    return auth_headers(world, "alice", "a")


@pytest.fixture(scope="module")
def b_headers(world):
    return auth_headers(world, "bob", "b")


def test_deleted_file_leaves_the_list_and_cannot_be_downloaded(backend_url, a_headers, home):
    f = upload(backend_url, a_headers, b"bye", name="gone.txt", folder=home).json()
    assert trash(backend_url, a_headers, f["id"]).status_code == 204

    assert listed(backend_url, a_headers, home) == []
    assert download(backend_url, a_headers, f["id"]).status_code == 404


def test_deleted_file_shows_up_in_trash_with_when_it_was_deleted(backend_url, a_headers, home):
    f = upload(backend_url, a_headers, b"x", name="t.txt", folder=home).json()
    trash(backend_url, a_headers, f["id"])

    entry = next(t for t in trash_list(backend_url, a_headers) if t["id"] == f["id"])
    assert (entry["name"], entry["folder"], entry["size"]) == ("t.txt", home, 1)
    assert entry["trashed_at"]


def test_most_recently_deleted_comes_first(backend_url, a_headers, home):
    first = upload(backend_url, a_headers, b"1", name="first.txt", folder=home).json()["id"]
    second = upload(backend_url, a_headers, b"2", name="second.txt", folder=home).json()["id"]
    trash(backend_url, a_headers, first)
    trash(backend_url, a_headers, second)

    ids = [t["id"] for t in trash_list(backend_url, a_headers)]
    assert ids.index(second) < ids.index(first)


def test_trash_is_per_tenant(backend_url, a_headers, b_headers, home):
    f = upload(backend_url, a_headers, b"private", name="a-trash.txt", folder=home).json()
    trash(backend_url, a_headers, f["id"])

    assert f["id"] in [t["id"] for t in trash_list(backend_url, a_headers)]
    assert f["id"] not in [t["id"] for t in trash_list(backend_url, b_headers)]


def test_the_stored_body_survives_in_trash(backend_url, a_headers, world, admin_s3, bucket, home):
    f = upload(backend_url, a_headers, b"keep my body", name="keep.txt", folder=home).json()
    key = f"{world.tenants['a']}/{f['id']}"
    before = admin_s3.head_object(Bucket=bucket, Key=key)["ContentLength"]

    trash(backend_url, a_headers, f["id"])

    assert admin_s3.head_object(Bucket=bucket, Key=key)["ContentLength"] == before


def test_restore_brings_the_file_back_unchanged(backend_url, a_headers, home):
    content = b"restore me " * 1000
    f = upload(backend_url, a_headers, content, name="back.txt", folder=f"{home}/deep/er").json()
    trash(backend_url, a_headers, f["id"])

    r = restore(backend_url, a_headers, f["id"])
    assert r.status_code == 200
    assert (r.json()["name"], r.json()["folder"]) == ("back.txt", f"{home}/deep/er")

    assert listed(backend_url, a_headers, f"{home}/deep/er") == ["back.txt"]
    assert download(backend_url, a_headers, f["id"]).content == content
    assert f["id"] not in [t["id"] for t in trash_list(backend_url, a_headers)]


def test_restore_when_the_name_was_reused_is_a_conflict_and_overwrites_nothing(backend_url, a_headers, home):
    old = upload(backend_url, a_headers, b"old", name="same.txt", folder=home).json()
    trash(backend_url, a_headers, old["id"])
    newer = upload(backend_url, a_headers, b"newer", name="same.txt", folder=home)
    assert newer.status_code == 200  # a trashed file does not hold its name

    r = restore(backend_url, a_headers, old["id"])
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]

    assert download(backend_url, a_headers, newer.json()["id"]).content == b"newer"
    assert old["id"] in [t["id"] for t in trash_list(backend_url, a_headers)]  # still recoverable

    # Once the name is free again, the restore works.
    assert trash(backend_url, a_headers, newer.json()["id"]).status_code == 204
    assert restore(backend_url, a_headers, old["id"]).status_code == 200
    assert download(backend_url, a_headers, old["id"]).content == b"old"


def test_a_trashed_file_cannot_be_renamed_or_deleted_again(backend_url, a_headers, home):
    f = upload(backend_url, a_headers, b"x", name="t.txt", folder=home).json()
    trash(backend_url, a_headers, f["id"])

    assert httpx.patch(f"{backend_url}/api/files/{f['id']}", headers=a_headers, json={"name": "u.txt"}).status_code == 404
    assert trash(backend_url, a_headers, f["id"]).status_code == 404


def test_an_active_file_cannot_be_restored(backend_url, a_headers, home):
    f = upload(backend_url, a_headers, b"x", name="live.txt", folder=home).json()
    assert restore(backend_url, a_headers, f["id"]).status_code == 404
    assert listed(backend_url, a_headers, home) == ["live.txt"]


def test_other_tenants_cannot_delete_or_restore_a_file(backend_url, a_headers, b_headers, home):
    live = upload(backend_url, a_headers, b"mine", name="live.txt", folder=home).json()
    gone = upload(backend_url, a_headers, b"mine too", name="gone.txt", folder=home).json()
    trash(backend_url, a_headers, gone["id"])

    assert trash(backend_url, b_headers, live["id"]).status_code == 404
    assert restore(backend_url, b_headers, gone["id"]).status_code == 404
    assert listed(backend_url, a_headers, home) == ["live.txt"]  # untouched for the owner
    assert gone["id"] in [t["id"] for t in trash_list(backend_url, a_headers)]


def test_unknown_ids_and_missing_credentials(backend_url, a_headers):
    assert trash(backend_url, a_headers, uuid.uuid4()).status_code == 404
    assert restore(backend_url, a_headers, uuid.uuid4()).status_code == 404
    assert trash(backend_url, {}, uuid.uuid4()).status_code == 401
    assert httpx.get(f"{backend_url}/api/trash").status_code == 401
    assert httpx.post(f"{backend_url}/api/trash/{uuid.uuid4()}/restore").status_code == 401
