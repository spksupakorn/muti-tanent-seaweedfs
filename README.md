# Multi-tenant file storage (PoC)

Django Ninja + Postgres + SeaweedFS + a React app, all on docker compose. See `CONTEXT.md` for terms and `docs/adr/` for decisions.

## Run

```sh
cp .env.example .env   # fill every value; generate secrets with: openssl rand -hex 24
mkdir -p ~/.filestore && openssl rand -base64 32 > ~/.filestore/master.key   # then set MASTER_KEY_HOST_FILE in .env (absolute path)
docker compose up -d --build --wait
curl http://127.0.0.1:8000/api/health
```

Published on the host, loopback only: the React app (`127.0.0.1:5173`), the backend (`127.0.0.1:8000`) and the SeaweedFS admin UI (`127.0.0.1:12535`, user `admin`, password `SEAWEED_ADMIN_PASSWORD` from `.env`). Postgres, S3, filer and master stay inside the compose network.

Create a user and an organization first (see Operator commands), then sign in at http://127.0.0.1:5173.

## Master key

The master key wraps every Tenant key (ADR 0001). It is a file kept **outside this repo and outside every volume and Backup**; `MASTER_KEY_HOST_FILE` in `.env` is its absolute path on this machine. Compose mounts it into the backend only, as a secret. Losing it makes every file unreadable.

## API

All need a bearer token (`POST /api/auth/login`) and, except for the tenant list, `X-Tenant-ID`.

| | |
|---|---|
| `GET /api/tenants` | the organizations you belong to |
| `POST /api/files` | upload (multipart: `file`, optional `folder`, `name`); 100 MB limit; 409 if the name is taken in that folder |
| `GET /api/files?folder=/` | files and subfolders of a folder |
| `GET /api/files/{id}/download` | the decrypted file |
| `PATCH /api/files/{id}` | rename and/or move (`name`, `folder`) |
| `DELETE /api/files/{id}` | move to Trash |
| `GET /api/trash`, `POST /api/trash/{id}/restore` | list Trash; restore (409 if the name was reused) |
| `GET /api/audit?limit=&offset=` | audit log; organization admins only |

## Database roles

`postgres/init/10-create-roles.sh` creates three roles on first init of the data volume: `filestore_owner` (migrations only), `filestore_app` (the API; no `BYPASSRLS`) and `filestore_backoffice` (operator tooling; `BYPASSRLS`). To change roles or their passwords, recreate the volume (`docker compose down -v`).

## Operator commands

Tenants, Users and Memberships are created by the Platform operator, not through the API:

```sh
docker compose run --rm ops create_tenant "Acme"
docker compose run --rm ops create_user alice@example.com 'a-password'
docker compose run --rm ops add_membership alice@example.com "Acme" admin   # admin | member
docker compose run --rm ops destroy_tenant_key "Acme"    # its files become unreadable for good
docker compose run --rm ops check_file_objects           # every file record has its stored object
```

## Backup and restore

Off by default (it would copy every test upload). Enable with `COMPOSE_PROFILES=backup`:

```sh
COMPOSE_PROFILES=backup docker compose up -d --build --wait   # starts filer-backup and db-backup
COMPOSE_PROFILES=backup docker compose run --rm db-backup once   # take a Backup now
```

A Backup lives in `./backups`: `files/` (the stored, still-encrypted files, copied continuously, deletions not mirrored) and `db/` (a dump per Backup, valid only with its `.complete` marker, written once every file the dump refers to is in `files/`). It never contains the master key.

`./scripts/restore-drill.sh` proves it: takes a Backup, destroys the stack and its volumes, rebuilds from `./backups` plus the master key file, and checks that files list, download and decrypt, that no record points at a missing file, that the restored database keeps its privileges, and that the wrong master key reads nothing. **It wipes this project's volumes and `./backups`.**

## Test

```sh
./scripts/test.sh          # black-box API tests inside the compose network + host port checks (needs uv)
./scripts/restore-drill.sh # backup and restore (destructive, see above)
./scripts/e2e.sh           # the React app in a real browser (needs node and jq; downloads Chromium once)
```
