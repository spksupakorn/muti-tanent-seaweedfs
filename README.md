# Multi-tenant file storage (PoC)

Django Ninja + Postgres + SeaweedFS, all on docker compose. See `CONTEXT.md` for terms and `docs/adr/` for decisions.

## Run

```sh
cp .env.example .env   # fill every value; generate with: openssl rand -hex 24
docker compose up -d --build --wait
curl http://127.0.0.1:8000/api/health
```

Only the backend is published, on `127.0.0.1:8000`. Postgres, S3, filer and master stay inside the compose network.

## Test

```sh
./scripts/test.sh
```

Starts the stack, runs the black-box tests inside the compose network (`tests/stack`), then checks from the host that internal ports are closed (`tests/host`, needs `uv`).
