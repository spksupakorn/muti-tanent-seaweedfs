from functools import lru_cache

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import ClientError
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

MIB = 1 << 20

# Parts are buffered in memory: 2 workers x 8 MiB parts keeps one upload near 16-24 MiB.
_TRANSFER = TransferConfig(multipart_threshold=8 * MIB, multipart_chunksize=8 * MIB, max_concurrency=2)


@lru_cache(maxsize=1)
def _s3():
    if not settings.S3_ACCESS_KEY:  # the back office is deliberately started without storage access
        raise ImproperlyConfigured("This process has no S3 credentials")
    return boto3.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        region_name="us-east-1",
        config=Config(
            s3={"addressing_style": "path"},
            signature_version="s3v4",
            # boto3 >= 1.36 adds CRC checksums by default; keep requests plain for SeaweedFS.
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


def put(key: str, body: bytes) -> None:
    _s3().put_object(Bucket=settings.S3_BUCKET, Key=key, Body=body)


def put_stream(key: str, fileobj) -> None:
    """Upload from a file-like object without holding the whole body in memory."""
    _s3().upload_fileobj(fileobj, settings.S3_BUCKET, key, Config=_TRANSFER)


def get(key: str) -> bytes:
    return _s3().get_object(Bucket=settings.S3_BUCKET, Key=key)["Body"].read()


def open_stream(key: str):
    """Streaming body of an object; raises botocore ClientError (NoSuchKey) if it is missing."""
    return _s3().get_object(Bucket=settings.S3_BUCKET, Key=key)["Body"]


def delete(key: str) -> None:
    _s3().delete_object(Bucket=settings.S3_BUCKET, Key=key)


def stored_size(key: str):
    """Size in bytes of a stored object, or None if it does not exist."""
    try:
        return _s3().head_object(Bucket=settings.S3_BUCKET, Key=key)["ContentLength"]
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return None
        raise
