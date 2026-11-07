"""The back office and the API are separate processes holding separate secrets. Run on the host."""
import socket
import subprocess
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]


def env_of(service: str) -> dict[str, str]:
    out = subprocess.run(
        ["docker", "compose", "exec", "-T", service, "env"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


@pytest.fixture(scope="module")
def backoffice():
    return env_of("backoffice")


@pytest.fixture(scope="module")
def backend():
    return env_of("backend")


def test_the_back_office_has_no_master_key_and_no_storage_access(backoffice):
    for name in ("MASTER_KEY_FILE", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_ENDPOINT_URL", "S3_ADMIN_ACCESS_KEY"):
        assert backoffice.get(name, "") == "", f"{name} is set in the back office"
    secrets = subprocess.run(
        ["docker", "compose", "exec", "-T", "backoffice", "sh", "-c", "ls /run/secrets 2>/dev/null | wc -l"],
        cwd=ROOT, capture_output=True, text=True,
    ).stdout.strip()
    assert secrets == "0"


def test_the_back_office_connects_as_the_back_office_role_only(backoffice):
    assert backoffice["POSTGRES_USER"] == "filestore_backoffice"
    assert "filestore_app" not in " ".join(backoffice.values())


def test_the_api_cannot_use_the_back_office_role_or_secret(backend, backoffice):
    assert backend["POSTGRES_USER"] == "filestore_app"
    assert backend["DJANGO_SECRET_KEY"] != backoffice["DJANGO_SECRET_KEY"]  # sessions and tokens do not cross over
    for value in (backoffice["POSTGRES_PASSWORD"], backoffice["DJANGO_SECRET_KEY"]):
        assert value not in " ".join(backend.values())


def test_the_back_office_is_published_on_loopback_only():
    assert httpx.get("http://127.0.0.1:8001/admin/login/").status_code == 200
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.0.2.1", 9))  # no traffic is sent; this only picks the outbound interface
        lan_ip = s.getsockname()[0]
    if lan_ip.startswith("127."):
        pytest.skip("host has no non-loopback address")
    with pytest.raises(OSError):
        socket.create_connection((lan_ip, 8001), timeout=2).close()
