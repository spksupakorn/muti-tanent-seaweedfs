# Multi-tenant file storage (PoC)

Django Ninja + Postgres + SeaweedFS, all on docker compose. See `CONTEXT.md` for terms and `docs/adr/` for decisions.

## Run

```sh
cp .env.example .env   # fill every value; generate with: openssl rand -hex 24
docker compose up -d --build --wait
curl http://127.0.0.1:8000/api/health
```

Published on the host, loopback only: the backend (`127.0.0.1:8000`) and the SeaweedFS admin UI (`127.0.0.1:12535`, user `admin`, password `SEAWEED_ADMIN_PASSWORD` from `.env`). Postgres, S3, filer and master stay inside the compose network.

## Database roles

`postgres/init/10-create-roles.sh` creates three roles on first init of the data volume: `filestore_owner` (migrations only), `filestore_app` (the API; no `BYPASSRLS`) and `filestore_backoffice` (operator tooling; `BYPASSRLS`). To change roles or their passwords, recreate the volume (`docker compose down -v`).

## Operator commands

Tenants, Users and Memberships are created by the Platform operator, not through the API:

```sh
docker compose run --rm ops create_tenant "Acme"
docker compose run --rm ops create_user alice@example.com 'a-password'
docker compose run --rm ops add_membership alice@example.com "Acme" admin   # admin | member
```

## Test

```sh
./scripts/test.sh
```

Starts the stack, runs the black-box tests inside the compose network (`tests/stack`), then checks from the host that internal ports are closed (`tests/host`, needs `uv`).
