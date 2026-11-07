import os

import pytest
from conftest import auth_headers, new_world, ops
from django.core.management import call_command
from django.core.management.base import CommandError
from test_files import upload

SEGMENT = 1 << 20


@pytest.fixture
def tenant():
    """A Tenant of its own, so files other tests broke on purpose do not count here."""
    w = new_world()
    return w, auth_headers(w, "alice", "a")


def test_reports_ok_for_files_of_every_size(tenant):
    w, h = tenant
    for size in (0, 1, SEGMENT, SEGMENT + 1, 3 * SEGMENT + 7):
        assert upload(os.environ["BACKEND_URL"], h, os.urandom(size)).status_code == 200
    assert "ok: 5 file(s)" in ops("check_file_objects", "--tenant", w.names["a"])


def test_names_a_file_whose_object_is_gone(tenant, admin_s3, bucket):
    w, h = tenant
    url = os.environ["BACKEND_URL"]
    keep = upload(url, h, b"stays").json()["id"]
    gone = upload(url, h, b"vanishes").json()["id"]
    admin_s3.delete_object(Bucket=bucket, Key=f"{w.tenants['a']}/{gone}")

    with pytest.raises(CommandError) as e:
        call_command("check_file_objects", "--tenant", w.names["a"])
    assert gone in str(e.value) and "missing" in str(e.value)
    assert keep not in str(e.value)


def test_names_a_file_whose_object_is_incomplete(tenant, admin_s3, bucket):
    w, h = tenant
    file_id = upload(os.environ["BACKEND_URL"], h, os.urandom(3 * SEGMENT)).json()["id"]
    key = f"{w.tenants['a']}/{file_id}"
    body = admin_s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    admin_s3.put_object(Bucket=bucket, Key=key, Body=body[: SEGMENT])  # cut short, as an unfinished copy would be

    with pytest.raises(CommandError) as e:
        call_command("check_file_objects", "--tenant", w.names["a"])
    assert file_id in str(e.value) and "bytes" in str(e.value)
