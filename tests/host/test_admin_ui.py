"""SeaweedFS admin UI: password-protected, loopback only. Runs on the host."""
import re
import socket
from pathlib import Path

import httpx
import pytest

URL = "http://127.0.0.1:12535"


def _password():
    for line in (Path(__file__).resolve().parents[2] / ".env").read_text().splitlines():
        if line.startswith("SEAWEED_ADMIN_PASSWORD="):
            return line.split("=", 1)[1]
    raise AssertionError("SEAWEED_ADMIN_PASSWORD missing from .env")


def _login(client, password):
    page = client.get(f"{URL}/login").text
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page).group(1)
    return client.post(f"{URL}/login", data={"username": "admin", "password": password, "csrf_token": token})


def test_admin_requires_login():
    r = httpx.get(f"{URL}/admin")
    assert r.status_code == 307 and r.headers["location"].endswith("/login")


def test_wrong_password_is_rejected():
    with httpx.Client() as c:
        _login(c, "wrong-password")
        r = c.get(f"{URL}/admin")
        assert r.status_code == 307 and r.headers["location"].endswith("/login")


def test_right_password_opens_the_ui():
    with httpx.Client() as c:
        _login(c, _password())
        assert c.get(f"{URL}/admin").status_code == 200


def test_not_reachable_from_the_network():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.0.2.1", 9))  # no traffic sent; only picks the outbound interface
        lan_ip = s.getsockname()[0]
    if lan_ip.startswith("127."):
        pytest.skip("host has no non-loopback address")
    with pytest.raises(OSError):
        socket.create_connection((lan_ip, 12535), timeout=2).close()
