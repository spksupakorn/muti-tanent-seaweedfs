# Multi-tenant file storage (PoC)

English · [ภาษาไทย](README.th.md)

Django Ninja + Postgres + SeaweedFS + a React app, all on docker compose. See `CONTEXT.md` for terms and `docs/adr/` for decisions.

## Run

There are two ways to run it. Both start with the same one-time setup: a `.env` with secrets, and a master key file.

**Skip this block if `.env` and `~/.filestore/master.key` already exist** (as they do on a machine where the stack has run before). Changing either afterwards breaks that machine's data: a new master key makes every stored file unreadable for good, and new database passwords no longer match the existing database volume (the roles are only created the first time), so you would have to `docker compose down -v`, which deletes all data. The commands below are written to leave existing files alone.

```sh
if [ ! -f .env ]; then
  cp .env.example .env && chmod 660 .env
  # Put a random secret in every empty value (macOS; on Linux remove the '' after -i):
  for k in DJANGO_SECRET_KEY POSTGRES_PASSWORD S3_ACCESS_KEY S3_SECRET_KEY S3_ADMIN_ACCESS_KEY S3_ADMIN_SECRET_KEY \
           SEAWEED_ADMIN_PASSWORD DB_OWNER_PASSWORD DB_APP_PASSWORD DB_BACKOFFICE_PASSWORD BACKOFFICE_SECRET_KEY; do
    sed -i '' "s|^$k=.*|$k=$(openssl rand -hex 24)|" .env
  done
  # The master key is a file outside this repo. MASTER_KEY_HOST_FILE must be an absolute path.
  sed -i '' "s|^MASTER_KEY_HOST_FILE=.*|MASTER_KEY_HOST_FILE=$HOME/.filestore/master.key|" .env
fi
mkdir -p ~/.filestore
if [ ! -f ~/.filestore/master.key ]; then   # keep a safe copy of this file: without it no stored file can be read again
  openssl rand -base64 32 > ~/.filestore/master.key && chmod 600 ~/.filestore/master.key
fi
```

### A. Everything in Docker Compose

```sh
docker compose up -d --build --wait    # the first run takes a few minutes: it builds the images and pulls SeaweedFS (about 1 GB)

# Nothing exists yet: create an organization, a person in it, and a Platform operator for the back office.
docker compose run --rm ops create_tenant "Acme"
docker compose run --rm ops create_user alice@example.com 'a-password'
docker compose run --rm ops add_membership alice@example.com "Acme" admin
docker compose run --rm ops create_user --operator you@example.com 'another-password'
```

Open these, all on loopback only:

| | |
|---|---|
| http://127.0.0.1:5173 | the app: sign in as `alice@example.com` |
| http://127.0.0.1:8001/admin/ | the back office: sign in as the operator |
| http://127.0.0.1:12535 | SeaweedFS admin: user `admin`, password `SEAWEED_ADMIN_PASSWORD` from `.env` |
| http://127.0.0.1:8000/api/health | the API's health check |

Postgres, S3, filer and master stay inside the compose networks. There are three (`db`, `storage`, `web`), so the back office and the React app have no network path to SeaweedFS, whose filer takes unauthenticated requests.

Everyday commands:

```sh
docker compose ps                  # what is running
docker compose logs -f backend     # follow one service: backend, backoffice, seaweedfs, maintenance, ...
docker compose stop                # stop everything, keep the data
docker compose down                # remove the containers, keep the data (the volumes)
docker compose down -v             # also delete the data (database and files): start again from nothing
COMPOSE_PROFILES=backup docker compose up -d --build --wait   # also run the Backup services (see Backup and restore)
```

### B. Backend and frontend on your machine, the rest in Docker

Postgres and SeaweedFS stay in Docker. Their ports are closed by default, so `compose.dev.yaml` opens just those two on loopback. You need `uv` (it fetches Python) and Node 20 or newer.

```sh
export COMPOSE_FILE=compose.yaml:compose.dev.yaml    # in every terminal that runs `docker compose` below
docker compose up -d --wait postgres seaweedfs storage-init migrate    # database, storage, the bucket, the schema
```

Terminal 1, the backend (do not also start the `backend` container: both want port 8000):

```sh
set -a; . ./.env; set +a
cd backend
POSTGRES_DB=filestore POSTGRES_USER=filestore_app POSTGRES_PASSWORD="$DB_APP_PASSWORD" POSTGRES_HOST=127.0.0.1 \
S3_ENDPOINT_URL=http://127.0.0.1:8333 S3_BUCKET=files MASTER_KEY_FILE="$MASTER_KEY_HOST_FILE" \
uv run --python 3.12 --no-project --with-requirements requirements.txt python manage.py runserver 127.0.0.1:8000
```

Terminal 2, the frontend (do not also start the `frontend` container: both want port 5173):

```sh
cd frontend
npm install
npm run dev        # http://127.0.0.1:5173, calls the API at http://127.0.0.1:8000
```

Create the first organization and user with the same `docker compose run --rm ops ...` commands as in A; they use the same database. Open the app through `127.0.0.1` or `localhost` on port 5173: those are the only origins the API allows (`CORS_ALLOWED_ORIGINS`). The back office is not started in this mode; add `backoffice` to the `up` command if you want it. Do not run `scripts/test.sh` with `compose.dev.yaml` active: one of its checks asserts that Postgres and S3 are not published.

To stop: Ctrl-C in the two terminals, then `docker compose down` (or `stop`).

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
docker compose run --rm ops create_user --operator you@example.com 'a-password'   # a Platform operator: may sign in to the back office
docker compose run --rm ops add_membership alice@example.com "Acme" admin   # admin | member
docker compose run --rm ops destroy_tenant_key "Acme"    # its files become unreadable for good
docker compose run --rm ops check_file_objects           # every file record has its stored object
docker compose run --rm ops purge                         # see "Purge" below
```

## Back office

Platform operators use Django admin at http://127.0.0.1:8001/admin/ (loopback only). It is its own service: it connects to Postgres as `filestore_backoffice`, has its own secret key (`BACKOFFICE_SECRET_KEY`), and gets **neither the master key nor S3 credentials**, so nothing in it can read or decrypt a file. Only users created with `create_user --operator` (or with "is operator" set in the back office) can sign in.

What an operator can do: see every organization's files (name, folder, size, status), move any file to Trash and restore it (a restore is refused if the name was reused), create and edit organizations, users, memberships and roles, and read every organization's audit log. Files, organizations and users cannot be deleted from here; the file list is read-only.

An operator can reset any user's password or add themselves to an organization, and then sign in to the app as that user and read the files. That cannot be prevented while operators manage users, so it is made visible instead: every file action, membership change, password reset and key destruction by an operator appears in the organization's own audit log as "Platform operator" (without naming the person; the back office's own admin log does name them).

## Purge

`ops purge` removes half-finished uploads older than 1 hour (record and stored object) and Trash older than 7 days (record and stored object). It is safe to repeat. It deletes a trashed file's stored object only when the Backup already holds a complete copy of it (same key, full size), so with the Backup off, or while it is behind, expired Trash simply stays until a later run. Options: `--tenant NAME`, `--pending-hours`, `--trash-days`.

The `maintenance` service runs it every `PURGE_INTERVAL` seconds (default 3600; the first run comes one interval after start). With the `backup` profile off that only cleans half-finished uploads.

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
./scripts/smoke.sh         # the four claims of the PoC in one command (see below); ends with the destructive restore drill
./scripts/restore-drill.sh # backup and restore (destructive, see above)
./scripts/e2e.sh           # the React app in a real browser (needs node and jq; downloads Chromium once)
```

### Smoke test

`./scripts/smoke.sh` proves, on the real stack, that (1) a tenant asking for another tenant's file id gets 404, (2) unfiltered queries as the API's database role show nothing of other tenants (and nothing when no tenant is selected), (3) what SeaweedFS stores is ciphertext, read both as the object and from the raw volume files, and (4) the stack can be rebuilt from a Backup plus the separately kept master key (the restore drill). It exits non-zero if anything fails.

It also breaks RLS, encryption and tenant access on purpose and requires its own checks to notice, so a green run is not an accident, and each "found nothing" has a positive control. `SMOKE_SKIP_DRILL=1` skips the destructive last step for a quick run (about 40 seconds).

Two things learned about SeaweedFS while building it: it compresses compressible data on its volumes, and busybox `grep` in regex mode cannot search binary data. Searching volume files for plaintext therefore needs incompressible test data and `grep -F`.

## Known limits (a local proof of concept)

- No TLS; everything is on loopback. Tokens are kept in the browser's `localStorage`; refresh tokens cannot be revoked.
- No login throttling, on the API or the back office.
- `GET /api/health` is unauthenticated and does an S3 write, read and delete per call (it doubles as the container healthcheck).
- The SeaweedFS S3 `admin` identity exists in every deployment although only the tests use it.
- An upload larger than 100 MB is spooled to disk before the API refuses it (only signed-in tenant members get that far); the React app refuses it before sending.
- A download whose later segment fails its integrity check is cut off mid-stream (the first segment is checked before the response starts); it is still recorded in the audit log.
- `./backups` holds a plaintext database dump (every password hash and every wrapped Tenant key; useless without the master key). It is created owner-only; keep it that way.
