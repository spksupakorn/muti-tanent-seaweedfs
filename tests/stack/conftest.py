import os

import boto3
import pytest
from botocore.config import Config


def _s3(access_key, secret_key):
    return boto3.client(
        "s3",
        endpoint_url=os.environ["S3_ENDPOINT_URL"],
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
        config=Config(
            s3={"addressing_style": "path"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


@pytest.fixture(scope="session")
def backend_url():
    return os.environ["BACKEND_URL"]


@pytest.fixture(scope="session")
def bucket():
    return os.environ["S3_BUCKET"]


@pytest.fixture(scope="session")
def backend_s3():
    return _s3(os.environ["S3_ACCESS_KEY"], os.environ["S3_SECRET_KEY"])


@pytest.fixture(scope="session")
def admin_s3():
    return _s3(os.environ["S3_ADMIN_ACCESS_KEY"], os.environ["S3_ADMIN_SECRET_KEY"])
