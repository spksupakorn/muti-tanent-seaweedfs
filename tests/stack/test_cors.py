import httpx

ALLOWED = "http://127.0.0.1:5173"


def _preflight(backend_url, origin):
    return httpx.options(
        f"{backend_url}/api/tenants",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,x-tenant-id",
        },
    )


def test_react_origin_is_allowed_with_the_tenant_header(backend_url):
    r = _preflight(backend_url, ALLOWED)
    assert r.headers["access-control-allow-origin"] == ALLOWED
    assert "x-tenant-id" in r.headers["access-control-allow-headers"].lower()


def test_other_origins_get_no_cors_grant(backend_url):
    assert "access-control-allow-origin" not in _preflight(backend_url, "http://evil.example").headers
