"""Run on the host (not in the compose network): internal services must not be reachable."""
import socket

import pytest


def _open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False


@pytest.mark.parametrize("port,name", [(5432, "postgres"), (8333, "s3"), (8888, "filer"), (9333, "master")])
def test_internal_service_not_published(port, name):
    assert not _open(port), f"{name} reachable on host port {port}"


def test_backend_published_on_loopback():
    assert _open(8000)
