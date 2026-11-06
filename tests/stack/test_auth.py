import os
from datetime import timedelta

import httpx
import jwt
from django.utils import timezone

from conftest import ops


def _claims(sub, typ, exp):
    return {"sub": sub, "typ": typ, "iat": timezone.now(), "exp": exp}


def test_login_returns_working_tokens(login, backend_url):
    r = login("alice")
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    r = httpx.get(f"{backend_url}/api/tenants", headers={"Authorization": f"Bearer {body['access']}"})
    assert r.status_code == 200


def test_wrong_password_and_unknown_email_look_the_same(login, backend_url):
    wrong = login("alice", password="nope")
    unknown = httpx.post(f"{backend_url}/api/auth/login", json={"email": "nobody@example.com", "password": "x"})
    assert (wrong.status_code, wrong.json()) == (unknown.status_code, unknown.json()) == (401, wrong.json())


def test_refresh_issues_new_working_tokens(login, backend_url):
    refresh = login("alice").json()["refresh"]
    r = httpx.post(f"{backend_url}/api/auth/refresh", json={"refresh": refresh})
    assert r.status_code == 200
    r = httpx.get(f"{backend_url}/api/tenants", headers={"Authorization": f"Bearer {r.json()['access']}"})
    assert r.status_code == 200


def test_token_types_are_not_interchangeable(login, backend_url):
    tokens = login("alice").json()
    assert httpx.post(f"{backend_url}/api/auth/refresh", json={"refresh": tokens["access"]}).status_code == 401
    r = httpx.get(f"{backend_url}/api/tenants", headers={"Authorization": f"Bearer {tokens['refresh']}"})
    assert r.status_code == 401


def test_bad_tokens_are_rejected(world, backend_url):
    secret = os.environ["DJANGO_SECRET_KEY"]
    sub = "00000000-0000-0000-0000-000000000000"
    now = timezone.now()
    bad = {
        "expired": jwt.encode(_claims(sub, "access", now - timedelta(minutes=1)), secret, algorithm="HS256"),
        "wrong key": jwt.encode(_claims(sub, "access", now + timedelta(minutes=5)), "other-key" * 4, algorithm="HS256"),
        "alg none": jwt.encode(_claims(sub, "access", now + timedelta(minutes=5)), None, algorithm="none"),
        "garbage": "not-a-token",
    }
    for name, token in bad.items():
        r = httpx.get(f"{backend_url}/api/tenants", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401, name


def test_no_token_is_unauthorized(backend_url):
    assert httpx.get(f"{backend_url}/api/tenants").status_code == 401


def test_deactivated_user_loses_access_immediately(world, login, backend_url):
    email = f"dave-{world.password}@example.com"
    ops("create_user", email, world.password)
    r = httpx.post(f"{backend_url}/api/auth/login", json={"email": email, "password": world.password})
    access = r.json()["access"]
    headers = {"Authorization": f"Bearer {access}"}
    assert httpx.get(f"{backend_url}/api/tenants", headers=headers).status_code == 200

    from accounts.models import User

    User.objects.filter(email=email).update(is_active=False)
    assert httpx.get(f"{backend_url}/api/tenants", headers=headers).status_code == 401
    assert httpx.post(f"{backend_url}/api/auth/login", json={"email": email, "password": world.password}).status_code == 401
