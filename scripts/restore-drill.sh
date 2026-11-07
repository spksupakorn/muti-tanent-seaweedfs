#!/bin/bash
# Restore drill: take a Backup, DESTROY the stack (volumes included), rebuild it from the Backup alone,
# and prove the Files list, download and decrypt, using only the master key kept outside the repo.
# It wipes this project's volumes and ./backups. Run it on a throwaway stack.
# (Plain bash 3.2 on purpose: macOS ships it, so no associative arrays.)
set -euo pipefail
cd "$(dirname "$0")/.."

set -a; . ./.env; set +a
[ -f "$MASTER_KEY_HOST_FILE" ] || { echo "MASTER_KEY_HOST_FILE ($MASTER_KEY_HOST_FILE) does not exist" >&2; exit 1; }
export COMPOSE_PROFILES=backup   # the backup services are off by default; the drill needs them
BACKUP_DIR=${BACKUP_DIR:-./backups}
POSTGRES_DB=${POSTGRES_DB:-filestore}   # same default as compose.yaml
API=http://127.0.0.1:8000/api
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
sfx=$RANDOM$RANDOM
PW="drill-$sfx"
EMAIL="drill-$sfx@example.com"
TENANT="Drill $sfx"

step() { printf '\n== %s\n' "$*"; }
ok()   { printf '   ok: %s\n' "$*"; }
die()  { printf '   FAIL: %s\n' "$*" >&2; exit 1; }
sha()  { shasum -a 256 "$1" | cut -d' ' -f1; }
ops()  { docker compose run --rm -T ops "$@" | tail -n 1; }
setid() { echo "$2" > "$WORK/id.$1"; }
idof()  { cat "$WORK/id.$1"; }

login() { curl -sf -X POST "$API/auth/login" -H 'content-type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PW\"}" | jq -r .access; }
upload() { # local file, name -> file id
  curl -sf -X POST "$API/files" -H "Authorization: Bearer $TOK" -H "X-Tenant-ID: $TID" \
    -F "file=@$1;filename=$2" -F folder=/drill | jq -r .id; }
get() { curl -s -o "$2" -w '%{http_code}' -H "Authorization: Bearer $TOK" -H "X-Tenant-ID: $TID" "$API/files/$1/download"; }

step "fresh stack and seed data"
docker compose down -v --remove-orphans >/dev/null 2>&1
rm -rf "$BACKUP_DIR"; mkdir -p "$BACKUP_DIR/files"
docker compose up -d --build --wait >/dev/null
TID=$(ops create_tenant "$TENANT")
ops create_user "$EMAIL" "$PW" >/dev/null
ops add_membership "$EMAIL" "$TENANT" admin >/dev/null
TOK=$(login)

printf 'hello from the drill\n' > "$WORK/small.txt"
head -c 2621440 /dev/urandom > "$WORK/multi.bin"        # 2.5 MiB: three encrypted segments
: > "$WORK/empty.dat"
printf 'this one is lost from storage after the backup\n' > "$WORK/lost.txt"
printf 'this one sits in Trash\n' > "$WORK/trashed.txt"
for f in small.txt multi.bin empty.dat lost.txt trashed.txt; do setid "$f" "$(upload "$WORK/$f" "$f")"; done
curl -sf -X DELETE "$API/files/$(idof trashed.txt)" -H "Authorization: Bearer $TOK" -H "X-Tenant-ID: $TID" >/dev/null
ok "uploaded 5 files for tenant $TID, one moved to Trash"

step "take a Backup (database dump first, then wait for the file backup to catch up)"
docker compose run --rm -T db-backup once
ls "$BACKUP_DIR"/db/*.complete >/dev/null || die "no complete backup marker"

step "the Backup holds no master key"
python3 - "$MASTER_KEY_HOST_FILE" "$BACKUP_DIR" <<'PY' || die "master key found inside the Backup"
import base64, os, sys
b64 = open(sys.argv[1]).read().strip().encode()
raw = base64.b64decode(b64)
scanned = 0
for root, _, names in os.walk(sys.argv[2]):
    for n in names:
        data = open(os.path.join(root, n), "rb").read()
        scanned += 1
        for label, needle in (("base64", b64), ("raw", raw)):
            if needle in data:
                sys.exit(f"{label} master key found in {os.path.join(root, n)}")
print(f"   ok: searched {scanned} files (dump, manifest and stored files) for the key, base64 and raw")
PY

step "a file is lost from storage after the Backup (stands in for a purge); the Backup keeps it"
docker compose run --rm -T --no-deps backend python manage.py shell -c \
  "from storage import client; client.delete('$TID/$(idof lost.txt)')" >/dev/null
[ -f "$BACKUP_DIR/files/$TID/$(idof lost.txt)" ] || die "deleted file is not in the Backup"
ok "object gone from storage, still in $BACKUP_DIR/files"

step "destroy the stack, volumes included"
docker compose down -v --remove-orphans >/dev/null 2>&1
[ -z "$(docker volume ls -q --filter name=filestore_)" ] || die "volumes survived"
ok "no volumes left; only $BACKUP_DIR and the master key file remain"

step "restore: empty database and bucket first, then the dump and the files"
docker compose up -d --wait postgres seaweedfs >/dev/null
docker compose run --rm -T --no-deps storage-init >/dev/null
docker compose --profile restore run --rm -T --no-deps db-restore
docker compose --profile restore run --rm -T --no-deps files-restore >/dev/null
docker compose up -d --build --wait >/dev/null
docker compose logs migrate 2>&1 | grep -q "No migrations to apply" || die "migrate had work to do: the schema did not come from the dump"
ok "stack is up again (migrate found nothing to apply: the schema came from the dump)"

step "list, download and decrypt every file with the separately kept master key"
TOK=$(login)
listed=$(curl -sf "$API/files?folder=/drill" -H "Authorization: Bearer $TOK" -H "X-Tenant-ID: $TID" | jq -r '[.files[].name] | sort | join(",")')
[ "$listed" = "empty.dat,lost.txt,multi.bin,small.txt" ] || die "unexpected list: $listed"
for f in small.txt multi.bin empty.dat lost.txt; do
  [ "$(get "$(idof $f)" "$WORK/out-$f")" = 200 ] || die "download of $f failed"
  [ "$(sha "$WORK/$f")" = "$(sha "$WORK/out-$f")" ] || die "$f differs from the original"
done
ok "4 files identical to the originals, including the one deleted from storage after the Backup"
curl -sf "$API/trash" -H "Authorization: Bearer $TOK" -H "X-Tenant-ID: $TID" \
  | jq -e --arg id "$(idof trashed.txt)" 'map(.id) | index($id) != null' >/dev/null || die "the trashed file is not in Trash"
ok "the file in Trash is still in Trash"

step "no restored record points at a missing file"
docker compose run --rm -T ops check_file_objects

step "the restored database enforces the same privileges and row-level security"
# A denied statement makes psql exit non-zero; that is the expected result here, so the helpers always succeed
# and the caller judges the output (otherwise pipefail would fail the check that proves the denial).
as_app() { docker compose exec -T -e PGPASSWORD="$DB_APP_PASSWORD" postgres psql -U filestore_app -d "$POSTGRES_DB" -At -c "$1" 2>&1 || true; }
as_ops() { docker compose exec -T -e PGPASSWORD="$DB_BACKOFFICE_PASSWORD" postgres psql -U filestore_backoffice -d "$POSTGRES_DB" -At -c "$1" 2>&1 || true; }
as_app "DELETE FROM audit_auditentry" | grep -q "permission denied" || die "the API role can delete audit entries"
as_app "UPDATE files_tenantkey SET wrapped_key = NULL" | grep -q "permission denied" || die "the API role can alter Tenant keys"
as_app "INSERT INTO accounts_tenant (id, name, created_at) VALUES (gen_random_uuid(), 'x', now())" | grep -q "permission denied" \
  || die "the API role can create Tenants"
[ "$(as_app "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'files_file'")" = t ] || die "RLS is not forced on files_file"
[ "$(as_app "SELECT count(*) FROM files_file")" = 0 ] || die "the API role sees files with no Tenant selected"
[ "$(as_ops "SELECT count(*) FROM files_file")" -ge 5 ] || die "the back-office role cannot see the restored files"
ok "API role: no write access to accounts, keys or audit; sees no rows without a selected Tenant (back-office sees them all)"
step "without the master key the files cannot be read"
openssl rand -base64 32 > "$WORK/wrong.key"
MASTER_KEY_HOST_FILE="$WORK/wrong.key" docker compose up -d --wait --force-recreate backend >/dev/null
TOK=$(login)
code=$(get "$(idof small.txt)" "$WORK/out-wrong")
[ "$code" != 200 ] || die "downloaded with the wrong master key"
if grep -q 'hello from the drill' "$WORK/out-wrong"; then die "plaintext leaked with the wrong master key"; fi
ok "wrong key: download refused (HTTP $code)"
docker compose up -d --wait --force-recreate backend >/dev/null
TOK=$(login)
[ "$(get "$(idof small.txt)" "$WORK/out-right")" = 200 ] && [ "$(sha "$WORK/small.txt")" = "$(sha "$WORK/out-right")" ] \
  || die "the right key no longer works"
ok "right key restored: readable again"

printf '\nRESTORE DRILL PASSED\n'
