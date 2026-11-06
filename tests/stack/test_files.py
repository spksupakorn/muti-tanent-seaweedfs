import os
import uuid

import httpx
import pytest
from conftest import auth_headers, new_world, ops

SEGMENT = 1 << 20
TAG = 16
LIMIT = 100 * 1024 * 1024
TIMEOUT = httpx.Timeout(120)


@pytest.fixture(scope="module")
def a_headers(world):
    return auth_headers(world, "alice", "a")


@pytest.fixture(scope="module")
def b_headers(world):
    return auth_headers(world, "bob", "b")


def upload(backend_url, headers, content, name=None, folder="/", content_type="application/pdf"):
    # A fresh name each time: names are unique per folder, and tests share Tenants.
    name = name or f"{uuid.uuid4().hex}.bin"
    return httpx.post(
        f"{backend_url}/api/files",
        headers=headers,
        files={"file": (name, content, content_type)},
        data={"folder": folder},
        timeout=TIMEOUT,
    )


def download(backend_url, headers, file_id):
    return httpx.get(f"{backend_url}/api/files/{file_id}/download", headers=headers, timeout=TIMEOUT)


def stored(admin_s3, bucket, tenant_id, file_id):
    return admin_s3.get_object(Bucket=bucket, Key=f"{tenant_id}/{file_id}")["Body"].read()


def overwrite(admin_s3, bucket, tenant_id, file_id, data):
    admin_s3.put_object(Bucket=bucket, Key=f"{tenant_id}/{file_id}", Body=data)


def assert_download_fails(backend_url, headers, file_id):
    """A bad file may be refused with a status, or cut off mid-stream; it must never arrive as a 200 body."""
    try:
        r = download(backend_url, headers, file_id)
    except httpx.HTTPError:
        return
    assert r.status_code >= 500, f"tampered file was served: {r.status_code}"


@pytest.mark.parametrize(
    "size", [0, 1, SEGMENT - 1, SEGMENT, SEGMENT + 1, 3 * SEGMENT + 5], ids=lambda n: f"{n}B"
)
def test_upload_then_download_returns_the_same_bytes(backend_url, a_headers, size):
    content = os.urandom(size)
    name = f"{uuid.uuid4().hex}.bin"
    r = upload(backend_url, a_headers, content, name=name)
    assert r.status_code == 200, r.text
    assert r.json()["size"] == size
    d = download(backend_url, a_headers, r.json()["id"])
    assert d.status_code == 200
    assert d.content == content
    assert name in d.headers["content-disposition"]


def test_large_upload_uses_multipart_and_round_trips(backend_url, a_headers):
    content = os.urandom(20 * 1024 * 1024 + 123)  # > the 8 MiB multipart threshold
    r = upload(backend_url, a_headers, content)
    assert r.status_code == 200, r.text
    assert download(backend_url, a_headers, r.json()["id"]).content == content


def test_exactly_the_limit_is_accepted_and_one_byte_more_is_not(backend_url, a_headers):
    r = upload(backend_url, a_headers, b"\0" * LIMIT)
    assert r.status_code == 200, r.text
    over = upload(backend_url, a_headers, b"\0" * (LIMIT + 1))
    assert over.status_code == 413
    assert "100 MB" in over.json()["detail"]
    listed = httpx.get(f"{backend_url}/api/files", headers=a_headers).json()["files"]
    assert len(listed) >= 1 and all(f["size"] <= LIMIT for f in listed)


def test_list_shows_files_and_subfolders_of_a_folder(backend_url, world):
    h = auth_headers(world, "carol", "a")  # carol is a member of A; use a folder tree of our own
    tag = uuid.uuid4().hex[:6]
    for folder, name in ((f"/{tag}", "top.txt"), (f"/{tag}/x", "in-x.txt"), (f"/{tag}/x/deep", "deep.txt"), (f"/{tag}/y", "in-y.txt")):
        assert upload(backend_url, h, b"data", name=name, folder=folder).status_code == 200

    root = httpx.get(f"{backend_url}/api/files", headers=h, params={"folder": f"/{tag}"}).json()
    assert [f["name"] for f in root["files"]] == ["top.txt"]
    assert root["folders"] == ["x", "y"]

    # The Tenant root has files directly in it (other tests) and our new folder below it;
    # the root's own files must not show up as an empty-named subfolder.
    assert upload(backend_url, h, b"data", name="at-root.txt", folder="/").status_code == 200
    top = httpx.get(f"{backend_url}/api/files", headers=h, params={"folder": "/"}).json()
    assert tag in top["folders"]
    assert "" not in top["folders"]
    assert "at-root.txt" in [f["name"] for f in top["files"]]

    x = httpx.get(f"{backend_url}/api/files", headers=h, params={"folder": f"/{tag}/x"}).json()
    assert [f["name"] for f in x["files"]] == ["in-x.txt"]
    assert x["folders"] == ["deep"]


@pytest.mark.parametrize("folder", ["/a/../b", "/a/./b", "/a\\b"])
def test_bad_folder_paths_are_rejected(backend_url, a_headers, folder):
    assert upload(backend_url, a_headers, b"x", folder=folder).status_code == 422


@pytest.mark.parametrize("name", ["a/b.txt", "..", "a\\b", "   "])
def test_bad_names_are_rejected(backend_url, a_headers, name):
    # Django already reduces a multipart *filename* to its basename, so send the name explicitly.
    r = httpx.post(
        f"{backend_url}/api/files",
        headers=a_headers,
        files={"file": ("ok.bin", b"x", "application/octet-stream")},
        data={"name": name},
    )
    assert r.status_code == 422


def test_storage_holds_only_ciphertext_under_an_id_only_key(backend_url, a_headers, world, admin_s3, bucket):
    secret = b"TOP-SECRET-MARKER-" * 64
    name = f"payroll-{uuid.uuid4().hex[:6]}.pdf"
    r = upload(backend_url, a_headers, secret, name=name)
    file_id = r.json()["id"]
    key = f"{world.tenants['a']}/{file_id}"

    blob = admin_s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    assert b"TOP-SECRET-MARKER" not in blob
    assert len(blob) == len(secret) + TAG
    assert name not in key and "payroll" not in key


def test_every_object_of_a_multi_segment_file_is_ciphertext(backend_url, a_headers, world, admin_s3, bucket):
    plain = b"A" * (2 * SEGMENT + 10)  # highly repetitive: any leaked plaintext would be obvious
    file_id = upload(backend_url, a_headers, plain).json()["id"]
    blob = stored(admin_s3, bucket, world.tenants["a"], file_id)
    assert len(blob) == len(plain) + 3 * TAG
    assert b"A" * 64 not in blob


def test_a_modified_byte_is_detected(backend_url, a_headers, world, admin_s3, bucket):
    file_id = upload(backend_url, a_headers, os.urandom(5000)).json()["id"]
    blob = bytearray(stored(admin_s3, bucket, world.tenants["a"], file_id))
    blob[100] ^= 0x01
    overwrite(admin_s3, bucket, world.tenants["a"], file_id, bytes(blob))
    r = download(backend_url, a_headers, file_id)
    assert r.status_code == 500


def test_a_truncated_object_is_detected(backend_url, a_headers, world, admin_s3, bucket):
    plain = os.urandom(3 * SEGMENT)
    file_id = upload(backend_url, a_headers, plain).json()["id"]
    blob = stored(admin_s3, bucket, world.tenants["a"], file_id)
    block = SEGMENT + TAG

    overwrite(admin_s3, bucket, world.tenants["a"], file_id, blob[:block])  # whole segments dropped
    assert_download_fails(backend_url, a_headers, file_id)
    overwrite(admin_s3, bucket, world.tenants["a"], file_id, blob[:-1])  # one byte short
    assert_download_fails(backend_url, a_headers, file_id)
    overwrite(admin_s3, bucket, world.tenants["a"], file_id, blob[: 2 * block])  # the final segment dropped
    assert_download_fails(backend_url, a_headers, file_id)


def test_appended_or_reordered_segments_are_detected(backend_url, a_headers, world, admin_s3, bucket):
    plain = os.urandom(3 * SEGMENT)
    file_id = upload(backend_url, a_headers, plain).json()["id"]
    blob = stored(admin_s3, bucket, world.tenants["a"], file_id)
    block = SEGMENT + TAG
    s0, s1, s2 = blob[:block], blob[block : 2 * block], blob[2 * block :]

    overwrite(admin_s3, bucket, world.tenants["a"], file_id, s1 + s0 + s2)  # swapped
    assert_download_fails(backend_url, a_headers, file_id)
    overwrite(admin_s3, bucket, world.tenants["a"], file_id, blob + s0)  # appended
    assert_download_fails(backend_url, a_headers, file_id)


def test_another_files_valid_ciphertext_does_not_authenticate(backend_url, a_headers, world, admin_s3, bucket):
    victim = upload(backend_url, a_headers, b"victim").json()["id"]
    other = upload(backend_url, a_headers, b"other!").json()["id"]
    overwrite(admin_s3, bucket, world.tenants["a"], victim, stored(admin_s3, bucket, world.tenants["a"], other))
    assert download(backend_url, a_headers, victim).status_code == 500


def test_a_missing_object_is_an_error_not_an_empty_file(backend_url, a_headers, world, admin_s3, bucket):
    file_id = upload(backend_url, a_headers, b"data").json()["id"]
    admin_s3.delete_object(Bucket=bucket, Key=f"{world.tenants['a']}/{file_id}")
    assert download(backend_url, a_headers, file_id).status_code == 500


def test_other_tenants_cannot_see_or_fetch_a_file(backend_url, a_headers, b_headers, world):
    file_id = upload(backend_url, a_headers, b"only for A", name="a-only.txt").json()["id"]

    assert download(backend_url, b_headers, file_id).status_code == 404
    listed = httpx.get(f"{backend_url}/api/files", headers=b_headers).json()
    assert file_id not in [f["id"] for f in listed["files"]]
    # Bob really is in B, so this is not a failed login: his own request works.
    assert upload(backend_url, b_headers, b"bob's").status_code == 200
    # Naming Tenant A is refused outright for a non-member.
    wrong = {**b_headers, "X-Tenant-ID": world.tenants["a"]}
    assert download(backend_url, wrong, file_id).status_code == 403


def test_file_id_that_does_not_exist_is_404(backend_url, a_headers):
    assert download(backend_url, a_headers, uuid.uuid4()).status_code == 404


def test_pending_files_are_not_listed_or_downloadable(backend_url, a_headers, world):
    from files.models import File

    pending = File.objects.create(
        tenant_id=world.tenants["a"], name="half-uploaded.bin", wrapped_data_key=b"x", status=File.Status.PENDING
    )
    listed = httpx.get(f"{backend_url}/api/files", headers=a_headers).json()["files"]
    assert str(pending.id) not in [f["id"] for f in listed]
    assert download(backend_url, a_headers, pending.id).status_code == 404


def test_unauthenticated_and_non_member_requests_are_refused(backend_url, world):
    assert httpx.get(f"{backend_url}/api/files").status_code == 401
    assert upload(backend_url, {}, b"x").status_code == 401


def test_destroying_a_tenant_key_makes_only_that_tenants_files_unreadable():
    w = new_world()
    url = os.environ["BACKEND_URL"]
    a, b = auth_headers(w, "alice", "a"), auth_headers(w, "bob", "b")
    fa = upload(url, a, b"alpha").json()["id"]
    fb = upload(url, b, b"beta").json()["id"]
    assert download(url, a, fa).content == b"alpha"

    ops("destroy_tenant_key", w.names["a"])

    r = download(url, a, fa)
    assert r.status_code == 410
    assert upload(url, a, b"new").status_code == 410  # no quiet replacement key
    assert download(url, b, fb).content == b"beta"
    assert upload(url, b, b"still works").status_code == 200


def test_tenants_have_different_keys_so_ciphertext_does_not_transplant(
    backend_url, a_headers, b_headers, world, admin_s3, bucket
):
    plain = b"same plaintext in both tenants"
    fa = upload(backend_url, a_headers, plain).json()["id"]
    fb = upload(backend_url, b_headers, plain).json()["id"]
    blob_a = stored(admin_s3, bucket, world.tenants["a"], fa)
    blob_b = stored(admin_s3, bucket, world.tenants["b"], fb)
    assert blob_a != blob_b
    overwrite(admin_s3, bucket, world.tenants["b"], fb, blob_a)
    assert download(backend_url, b_headers, fb).status_code == 500
