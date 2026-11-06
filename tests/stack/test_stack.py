import uuid

import botocore.exceptions
import httpx
import pytest


def test_backend_health_covers_db_and_storage(backend_url):
    r = httpx.get(f"{backend_url}/api/health", timeout=10)
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_backend_identity_can_use_its_bucket(backend_s3, bucket):
    key = f"_test/{uuid.uuid4()}"
    backend_s3.put_object(Bucket=bucket, Key=key, Body=b"hello")
    assert backend_s3.get_object(Bucket=bucket, Key=key)["Body"].read() == b"hello"
    backend_s3.delete_object(Bucket=bucket, Key=key)


def test_backend_identity_cannot_touch_another_bucket(backend_s3, admin_s3):
    other = f"other-{uuid.uuid4().hex[:8]}"
    admin_s3.create_bucket(Bucket=other)
    try:
        with pytest.raises(botocore.exceptions.ClientError) as e:
            backend_s3.put_object(Bucket=other, Key="x", Body=b"nope")
        assert e.value.response["ResponseMetadata"]["HTTPStatusCode"] == 403
        # Control: the same write is allowed for the admin identity, so the 403 is the
        # restriction, not a broken bucket.
        admin_s3.put_object(Bucket=other, Key="x", Body=b"ok")
        with pytest.raises(botocore.exceptions.ClientError) as e:
            backend_s3.get_object(Bucket=other, Key="x")
        assert e.value.response["ResponseMetadata"]["HTTPStatusCode"] == 403
    finally:
        admin_s3.delete_object(Bucket=other, Key="x")
        admin_s3.delete_bucket(Bucket=other)
