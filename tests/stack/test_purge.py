import os
from datetime import timedelta
from pathlib import Path

import botocore.exceptions
import pytest
from conftest import auth_headers, new_world, ops
from django.conf import settings
from django.utils import timezone
from test_files import upload
from test_trash import trash

from files.models import File

HOURS = lambda n: timedelta(hours=n)  # noqa: E731
DAYS = lambda n: timedelta(days=n)  # noqa: E731


@pytest.fixture
def backup_dir(tmp_path, monkeypatch):
    """A stand-in Backup directory: purge only looks at which files it holds and how big they are."""
    monkeypatch.setattr(settings, "BACKUP_FILES_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def tenant():
    w = new_world()  # its own Tenant, so a purge of "everything old" cannot touch other tests' files
    return w, auth_headers(w, "alice", "a")


def new_file(tenant, content=b"payload", **fields):
    """Upload through the API, then age the record the way time would."""
    w, h = tenant
    file_id = upload(os.environ["BACKEND_URL"], h, content).json()["id"]
    if fields:
        File.objects.filter(id=file_id).update(**fields)
    return w.tenants["a"], file_id


def purge(w):
    """Purge just this test's Tenant: other tests' old rows on the shared stack must not change the counts."""
    return ops("purge", "--tenant", w.names["a"])


def in_storage(admin_s3, bucket, tenant_id, file_id) -> bool:
    try:
        admin_s3.head_object(Bucket=bucket, Key=f"{tenant_id}/{file_id}")
        return True
    except botocore.exceptions.ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def back_up(admin_s3, bucket, backup_dir: Path, tenant_id, file_id, *, truncate=0):
    body = admin_s3.get_object(Bucket=bucket, Key=f"{tenant_id}/{file_id}")["Body"].read()
    target = backup_dir / tenant_id / file_id
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body[: len(body) - truncate])


def exists(file_id) -> bool:
    return File.objects.filter(id=file_id).exists()


def test_half_finished_upload_older_than_an_hour_goes_with_its_object(tenant, admin_s3, bucket, backup_dir):
    w, _ = tenant
    now = timezone.now()
    old_t, old = new_file(tenant, status=File.Status.PENDING, created_at=now - HOURS(1.5))
    young_t, young = new_file(tenant, status=File.Status.PENDING, created_at=now - HOURS(0.5))

    out = purge(w)

    assert not exists(old) and not in_storage(admin_s3, bucket, old_t, old)
    assert exists(young) and in_storage(admin_s3, bucket, young_t, young)  # not old enough yet: untouched
    assert "purged 1 half-finished" in out


def test_pending_cleanup_does_not_need_a_backup(tenant, admin_s3, bucket, monkeypatch):
    w, _ = tenant
    monkeypatch.setattr(settings, "BACKUP_FILES_DIR", None)  # an upload that never finished was never in a Backup
    t, f = new_file(tenant, status=File.Status.PENDING, created_at=timezone.now() - HOURS(2))
    purge(w)
    assert not exists(f) and not in_storage(admin_s3, bucket, t, f)


def test_trash_older_than_seven_days_is_purged_for_good(tenant, admin_s3, bucket, backup_dir):
    w, h = tenant
    old_t, old = new_file(tenant)
    young_t, young = new_file(tenant)
    for _, f in ((old_t, old), (young_t, young)):
        assert trash(os.environ["BACKEND_URL"], h, f).status_code == 204
    now = timezone.now()
    File.objects.filter(id=old).update(trashed_at=now - DAYS(8))
    File.objects.filter(id=young).update(trashed_at=now - DAYS(6))
    back_up(admin_s3, bucket, backup_dir, old_t, old)
    back_up(admin_s3, bucket, backup_dir, young_t, young)

    out = purge(w)

    assert not exists(old) and not in_storage(admin_s3, bucket, old_t, old)
    assert exists(young) and in_storage(admin_s3, bucket, young_t, young)
    assert "purged 0 half-finished upload(s) and 1 trashed" in out
    assert (backup_dir / old_t / old).exists()  # the Backup keeps what purge removed


def test_a_trashed_file_the_backup_does_not_have_yet_is_kept_then_purged_once_it_does(
    tenant, admin_s3, bucket, backup_dir
):
    w, h = tenant
    t, f = new_file(tenant)
    trash(os.environ["BACKEND_URL"], h, f)
    File.objects.filter(id=f).update(trashed_at=timezone.now() - DAYS(8))

    out = purge(w)
    assert exists(f) and in_storage(admin_s3, bucket, t, f)
    assert "1 trashed file(s) kept until the Backup has them" in out

    back_up(admin_s3, bucket, backup_dir, t, f)  # the Backup catches up
    purge(w)
    assert not exists(f) and not in_storage(admin_s3, bucket, t, f)


def test_a_partial_backup_copy_does_not_count(tenant, admin_s3, bucket, backup_dir):
    w, h = tenant
    t, f = new_file(tenant, content=os.urandom(5000))
    trash(os.environ["BACKEND_URL"], h, f)
    File.objects.filter(id=f).update(trashed_at=timezone.now() - DAYS(8))
    back_up(admin_s3, bucket, backup_dir, t, f, truncate=1)  # one byte short, as an unfinished copy would be

    purge(w)
    assert exists(f) and in_storage(admin_s3, bucket, t, f)


def test_nothing_trashed_is_purged_when_no_backup_is_configured(tenant, admin_s3, bucket, monkeypatch):
    monkeypatch.setattr(settings, "BACKUP_FILES_DIR", None)
    w, h = tenant
    t, f = new_file(tenant)
    trash(os.environ["BACKEND_URL"], h, f)
    File.objects.filter(id=f).update(trashed_at=timezone.now() - DAYS(30))

    purge(w)
    assert exists(f) and in_storage(admin_s3, bucket, t, f)  # never delete the last copy


def test_active_files_are_never_purged(tenant, admin_s3, bucket, backup_dir):
    w, h = tenant
    long_active = new_file(tenant, created_at=timezone.now() - DAYS(400))
    restored = new_file(tenant)
    trash(os.environ["BACKEND_URL"], h, restored[1])
    # restored from Trash after being trashed long ago: no longer trashed, so not eligible
    File.objects.filter(id=restored[1]).update(trashed_at=timezone.now() - DAYS(30))
    from test_trash import restore

    assert restore(os.environ["BACKEND_URL"], h, restored[1]).status_code == 200
    for t, f in (long_active, restored):
        back_up(admin_s3, bucket, backup_dir, t, f)

    purge(w)
    for t, f in (long_active, restored):
        assert exists(f) and in_storage(admin_s3, bucket, t, f)


def test_running_it_again_changes_nothing_and_does_not_fail(tenant, admin_s3, bucket, backup_dir):
    w, h = tenant
    t, f = new_file(tenant)
    trash(os.environ["BACKEND_URL"], h, f)
    File.objects.filter(id=f).update(trashed_at=timezone.now() - DAYS(8))
    back_up(admin_s3, bucket, backup_dir, t, f)
    keep = new_file(tenant)

    purge(w)
    before = set(File.objects.values_list("id", flat=True))
    again = purge(w)

    assert set(File.objects.values_list("id", flat=True)) == before
    assert "purged 0 half-finished upload(s) and 0 trashed file(s)" in again
    assert exists(keep[1]) and in_storage(admin_s3, bucket, *keep)


def test_it_finishes_a_purge_whose_object_is_already_gone(tenant, admin_s3, bucket, backup_dir):
    """A run that died after deleting the object but before the record leaves a record to clean up."""
    w, h = tenant
    t, f = new_file(tenant)
    trash(os.environ["BACKEND_URL"], h, f)
    File.objects.filter(id=f).update(trashed_at=timezone.now() - DAYS(8))
    back_up(admin_s3, bucket, backup_dir, t, f)
    admin_s3.delete_object(Bucket=bucket, Key=f"{t}/{f}")

    purge(w)
    assert not exists(f)


def test_the_audit_trail_survives_a_purge(tenant, backup_dir, admin_s3, bucket):
    from audit.models import AuditEntry

    w, h = tenant
    t, f = new_file(tenant)
    trash(os.environ["BACKEND_URL"], h, f)
    File.objects.filter(id=f).update(trashed_at=timezone.now() - DAYS(8))
    back_up(admin_s3, bucket, backup_dir, t, f)

    purge(w)
    assert not exists(f)
    assert set(AuditEntry.objects.filter(file_id=f).values_list("action", flat=True)) == {"upload", "delete"}
