import uuid

import httpx


def _get(backend_url, path, headers):
    return httpx.get(f"{backend_url}{path}", headers=headers)


def test_tenant_list_has_only_the_callers_tenants(world, bearer, backend_url):
    def listing(who):
        return {t["name"]: t["role"] for t in _get(backend_url, "/api/tenants", bearer(who)).json()}

    assert listing("alice") == {world.names["a"]: "admin"}
    assert listing("bob") == {world.names["b"]: "member"}
    assert listing("carol") == {world.names["a"]: "member", world.names["b"]: "admin"}


def test_selected_tenant_reports_the_role_in_that_tenant(world, bearer, backend_url):
    for key, role in (("a", "member"), ("b", "admin")):
        r = _get(backend_url, "/api/tenant", {**bearer("carol"), "X-Tenant-ID": world.tenants[key]})
        assert r.status_code == 200
        assert r.json() == {"id": world.tenants[key], "name": world.names[key], "role": role}


def test_tenant_without_membership_is_rejected(world, bearer, backend_url):
    for who, key in (("alice", "b"), ("bob", "a")):
        r = _get(backend_url, "/api/tenant", {**bearer(who), "X-Tenant-ID": world.tenants[key]})
        assert r.status_code == 403


def test_nonexistent_tenant_looks_like_no_membership(world, bearer, backend_url):
    r = _get(backend_url, "/api/tenant", {**bearer("alice"), "X-Tenant-ID": str(uuid.uuid4())})
    assert r.status_code == 403


def test_missing_or_malformed_tenant_header_is_a_client_error(bearer, backend_url):
    assert _get(backend_url, "/api/tenant", bearer("alice")).status_code == 400
    assert _get(backend_url, "/api/tenant", {**bearer("alice"), "X-Tenant-ID": "nope"}).status_code == 400


def test_tenant_endpoint_needs_a_token(world, backend_url):
    assert _get(backend_url, "/api/tenant", {"X-Tenant-ID": world.tenants["a"]}).status_code == 401
