from functools import lru_cache

import boto3
from botocore.config import Config
from django.conf import settings


@lru_cache(maxsize=1)
def _s3():
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


def get(key: str) -> bytes:
    return _s3().get_object(Bucket=settings.S3_BUCKET, Key=key)["Body"].read()


def delete(key: str) -> None:
    _s3().delete_object(Bucket=settings.S3_BUCKET, Key=key)
