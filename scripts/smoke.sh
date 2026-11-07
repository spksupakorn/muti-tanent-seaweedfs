#!/bin/bash
# One command that proves the four claims of the PoC on the real stack:
#   1. a Tenant that asks for another Tenant's file id gets 404
#   2. with the application's own tenant filter taken out of the picture, the database still shows nothing of other Tenants
#   3. the bytes in SeaweedFS (object and raw volume files) are ciphertext
#   4. the stack can be rebuilt from a Backup plus the separately kept master key (scripts/restore-drill.sh)
# It also breaks RLS and encryption on purpose and requires the checks to notice, so a green run is not an accident.
# The last step is the restore drill, which WIPES this project's volumes and ./backups. SMOKE_SKIP_DRILL=1 skips it.
# Exit code 0 only if everything held. Plain bash 3.2 (macOS), needs docker, curl, jq.
set -uo pipefail
cd "$(dirname "$0")/.."

set -a; . ./.env; set +a
POSTGRES_DB=${POSTGRES_DB:-filestore}
API=http://127.0.0.1:8000/api
WORK=$(mktemp -d)
sfx=$RANDOM$RANDOM
PW="smoke-$sfx"
FAILED=0
SABOTAGE_RLS_ON=0

step() { printf '\n== %s\n' "$*"; }
note() { printf '   %s\n' "$*"; }
ops()  { docker compose run --rm -T ops "$@" | tail -n 1; }
http() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
psql_as() { # role password sql -> last line of output; errors are returned as text, never abort the script
  docker compose exec -T -e PGPASSWORD="$2" postgres psql -U "$1" -d "$POSTGRES_DB" -Atq -c "$3" 2>&1 | tail -n 1 || true; }
app_sql() { psql_as filestore_app "$DB_APP_PASSWORD" "$1"; }
ops_sql() { psql_as filestore_backoffice "$DB_BACKOFFICE_PASSWORD" "$1"; }
owner_sql() { psql_as filestore_owner "$DB_OWNER_PASSWORD" "$1"; }
login() { curl -sf -X POST "$API/auth/login" -H 'content-type: application/json' -d "{\"email\":\"$1\",\"password\":\"$PW\"}" | jq -r .access; }

restore_rls() {
  owner_sql "ALTER TABLE files_file ENABLE ROW LEVEL SECURITY; ALTER TABLE files_file FORCE ROW LEVEL SECURITY" >/dev/null
  SABOTAGE_RLS_ON=0
}
cleanup() {
  [ "$SABOTAGE_RLS_ON" = 1 ] && restore_rls   # never leave the database with row-level security switched off
  rm -rf "$WORK"
}
trap cleanup EXIT

# Each check prints "ok:" lines, or one "FAIL:" line, and returns 0/1. They never exit, so the sabotage
# section below can call them and expect a failure.
fail() { printf '   FAIL: %s\n' "$*"; return 1; }

check_cross_tenant_404() {
  local f=$FILE_B hA=(-H "Authorization: Bearer $TOK_A" -H "X-Tenant-ID: $TID_A")
  [ "$(http "${hA[@]}" "$API/files/$f/download")" = 404 ] || { fail "tenant A could download tenant B's file"; return 1; }
  [ "$(http "${hA[@]}" -X PATCH -H 'content-type: application/json' -d '{"name":"stolen"}' "$API/files/$f")" = 404 ] || { fail "tenant A could rename tenant B's file"; return 1; }
  [ "$(http "${hA[@]}" -X DELETE "$API/files/$f")" = 404 ] || { fail "tenant A could delete tenant B's file"; return 1; }
  [ "$(http "${hA[@]}" -X POST "$API/trash/$f/restore")" = 404 ] || { fail "tenant A could restore tenant B's file"; return 1; }
  local names; names=$(curl -s "${hA[@]}" "$API/files?folder=/" | jq -r '[.files[].name] | join(",")')
  case "$names" in *"$NAME_B"*) fail "tenant B's file shows in tenant A's list"; return 1 ;; esac
  case "$names" in *"$NAME_A"*) ;; *) fail "tenant A's own file is missing from its list (the check would prove nothing)"; return 1 ;; esac
  [ "$(http -H "Authorization: Bearer $TOK_A" -H "X-Tenant-ID: $TID_B" "$API/files")" = 403 ] || { fail "tenant A could act as a member of tenant B"; return 1; }
  curl -s -o "$WORK/b.out" -H "Authorization: Bearer $TOK_B" -H "X-Tenant-ID: $TID_B" "$API/files/$f/download"
  cmp -s "$WORK/b.out" "$WORK/plain-b" || { fail "tenant B's file was changed by tenant A's attempts"; return 1; }
  note "ok: tenant A got 404 on download, rename, delete and restore of B's file id, 403 for B's tenant id; B's file is intact"
}

# The application's tenant filter is out of the picture: these are plain, unfiltered queries run as the API's own
# database role, once with tenant A selected and once with nothing selected.
RLS_TABLES="files_file audit_auditentry files_tenantkey accounts_membership"
check_rls_unfiltered() {
  local t own foreign none
  for t in $RLS_TABLES; do
    own=$(app_sql "BEGIN; SELECT set_config('app.tenant_id', '$TID_A', true); SELECT count(*) FROM $t")
    foreign=$(app_sql "BEGIN; SELECT set_config('app.tenant_id', '$TID_A', true); SELECT count(*) FROM $t WHERE tenant_id <> '$TID_A'")
    [ "$foreign" = 0 ] || { fail "$t: the database showed $foreign row(s) of other tenants (got: $foreign)"; return 1; }
    [ "$own" -ge 1 ] 2>/dev/null || { fail "$t: tenant A sees none of its own rows (got: $own), so the check proves nothing"; return 1; }
    none=$(app_sql "SELECT count(*) FROM $t")
    [ "$none" = 0 ] || { fail "$t: with no tenant selected the database still showed $none row(s) (got: $none)"; return 1; }
  done
  local seen; seen=$(ops_sql "SELECT count(DISTINCT tenant_id) FROM files_file WHERE tenant_id IN ('$TID_A', '$TID_B')")
  [ "$seen" = 2 ] || { fail "control: the back-office role should see both tenants' files (got: $seen)"; return 1; }
  note "ok: as the API's database role, unfiltered queries on $RLS_TABLES show only tenant A's rows, and none with no tenant selected (control: the back-office role sees both tenants)"
}

# Reads what is actually stored: the object as the filer serves it, and the raw volume files on disk.
# SeaweedFS compresses compressible data on its volumes, so a search for plaintext only means something if the
# test data is incompressible: the files here are random bytes with the marker embedded (see `with_marker`).
# Positive control: such a plaintext object put in storage the same way IS found in the volume files.
# The search runs inside the container with grep -F (fixed string): the container's regex grep silently fails on binary.
volume_hits() { # marker -> number of lines of the raw volume files containing it
  docker compose exec -T -e M="$1" seaweedfs sh -c 'cat /data/*.dat | grep -F -a -c "$M"' 2>/dev/null | tr -d '\r\n '
}
check_ciphertext() { # tenant id, file id, marker, plaintext size
  local tid=$1 fid=$2 marker=$3 size=$4 stored
  docker compose exec -T seaweedfs wget -qO- "http://127.0.0.1:8888/buckets/${S3_BUCKET:-files}/$tid/$fid" > "$WORK/stored" 2>/dev/null
  stored=$(wc -c < "$WORK/stored" | tr -d ' ')
  [ "$stored" -gt 0 ] || { fail "could not read the object from SeaweedFS"; return 1; }
  LC_ALL=C grep -aqF -- "$marker" "$WORK/stored" && { fail "the stored object contains the plaintext"; return 1; }
  [ "$stored" -eq $((size + 16)) ] || { fail "stored object is $stored bytes, expected $((size + 16)) (content + one 16-byte tag)"; return 1; }

  local control="CONTROL-$sfx-$RANDOM" hits
  docker compose exec -T backend python manage.py shell -c \
    "import os; from storage import client; client.put('_smoke/control-$sfx', b''.join(os.urandom(30000) + b'$control' for _ in range(10)))" >/dev/null 2>&1
  hits=$(volume_hits "$control")
  docker compose exec -T backend python manage.py shell -c "from storage import client; client.delete('_smoke/control-$sfx')" >/dev/null 2>&1
  [ "${hits:-0}" -ge 1 ] 2>/dev/null || { fail "control: a plaintext object put in storage was not found in the volume files (the search is blind)"; return 1; }
  hits=$(volume_hits "$marker")
  [ "$hits" = 0 ] || { fail "the raw volume files contain the file's plaintext ($hits line(s))"; return 1; }
  note "ok: object is $stored bytes of ciphertext (content + tag); its plaintext is nowhere in the raw volume files, while a plaintext control object was found there"
}

# Random bytes with the marker embedded ten times, ~300 KB: incompressible, and large enough to land in a volume.
with_marker() { # marker file
  local i; : > "$2"
  for i in 1 2 3 4 5 6 7 8 9 10; do head -c 30000 /dev/urandom >> "$2"; printf '%s' "$1" >> "$2"; done
}

# ---------------------------------------------------------------------------------------------------------------
step "stack and test data"
docker compose up -d --build --wait >/dev/null || { echo "stack did not start" >&2; exit 1; }
TID_A=$(ops create_tenant "Smoke A $sfx"); TID_B=$(ops create_tenant "Smoke B $sfx")
ops create_user "alice-$sfx@example.com" "$PW" >/dev/null; ops create_user "bob-$sfx@example.com" "$PW" >/dev/null
ops add_membership "alice-$sfx@example.com" "Smoke A $sfx" admin >/dev/null
ops add_membership "bob-$sfx@example.com" "Smoke B $sfx" admin >/dev/null
TOK_A=$(login "alice-$sfx@example.com"); TOK_B=$(login "bob-$sfx@example.com")

MARKER="SMOKE-SECRET-$sfx-$(openssl rand -hex 8)"
NAME_A="a-$sfx.txt"; NAME_B="b-$sfx.txt"
with_marker "$MARKER" "$WORK/plain-b"      # ~300 KB of random bytes with a recognisable secret embedded
printf 'tenant A document %s' "$sfx" > "$WORK/plain-a"
upload() { curl -sf -X POST "$API/files" -H "Authorization: Bearer $1" -H "X-Tenant-ID: $2" -F "file=@$3;filename=$4" | jq -r .id; }
FILE_A=$(upload "$TOK_A" "$TID_A" "$WORK/plain-a" "$NAME_A"); FILE_B=$(upload "$TOK_B" "$TID_B" "$WORK/plain-b" "$NAME_B")
[ -n "$FILE_A" ] && [ -n "$FILE_B" ] && note "tenants A and B, one file each; B's file holds a recognisable secret"
SIZE_B=$(wc -c < "$WORK/plain-b" | tr -d ' ')

step "1. another tenant's file id gets 404"
check_cross_tenant_404 || FAILED=1
step "2. row-level security holds with the application's filter out of the picture"
check_rls_unfiltered || FAILED=1
step "3. what is stored in SeaweedFS is ciphertext"
check_ciphertext "$TID_B" "$FILE_B" "$MARKER" "$SIZE_B" || FAILED=1

step "the checks notice breakage (a green run is not an accident)"
# 2 must fail when row-level security is switched off on a table.
SABOTAGE_RLS_ON=1
owner_sql "ALTER TABLE files_file DISABLE ROW LEVEL SECURITY" >/dev/null
out=$(check_rls_unfiltered)
if [ $? -eq 0 ]; then note "FAIL: check 2 still passed with row-level security switched off on files_file"; FAILED=1
else note "caught (RLS off): ${out##*FAIL: }"; fi
restore_rls
check_rls_unfiltered >/dev/null || { note "FAIL: row-level security did not come back after the experiment"; FAILED=1; }

# 3 must fail when a file is stored without encryption (here: the object is replaced by its plaintext).
SAB_MARKER="SABOTAGE-$sfx-$(openssl rand -hex 6)"
with_marker "$SAB_MARKER" "$WORK/plain-sab"
FILE_S=$(upload "$TOK_B" "$TID_B" "$WORK/plain-sab" "sab-$sfx.txt")
docker compose exec -T backend python manage.py shell -c \
  "from storage import client; client.put('$TID_B/$FILE_S', open('/dev/stdin','rb').read())" < "$WORK/plain-sab" >/dev/null 2>&1
out=$(check_ciphertext "$TID_B" "$FILE_S" "$SAB_MARKER" "$(wc -c < "$WORK/plain-sab" | tr -d ' ')")
if [ $? -eq 0 ]; then note "FAIL: check 3 still passed with a plaintext object in storage"; FAILED=1
else note "caught (no encryption): ${out##*FAIL: }"; fi
# Second layer: the raw volume files must show the overwritten plaintext too.
hits=$(volume_hits "$SAB_MARKER")
if [ "${hits:-0}" -ge 1 ] 2>/dev/null; then note "  (and the raw volume scan sees that plaintext too: $hits line(s))"
else note "FAIL: the raw volume scan did not see the plaintext overwrite"; FAILED=1; fi

# 1 must fail when a tenant can see another's file: simulate with a membership that should not exist.
ops add_membership "alice-$sfx@example.com" "Smoke B $sfx" admin >/dev/null
out=$(check_cross_tenant_404)
if [ $? -eq 0 ]; then note "FAIL: check 1 still passed after tenant A was given access to tenant B"; FAILED=1
else note "caught (alice made a member of B): ${out##*FAIL: }"; fi
docker compose run --rm -T --no-deps ops shell -c \
  "from accounts.models import Membership; Membership.objects.filter(user__email='alice-$sfx@example.com', tenant_id='$TID_B').delete()" >/dev/null 2>&1
check_cross_tenant_404 >/dev/null || { note "FAIL: the experiment's membership could not be removed"; FAILED=1; }

if [ "$FAILED" != 0 ]; then
  printf '\nSMOKE TEST FAILED (checks 1-3 or the breakage proofs); the restore drill was not run\n'
  exit 1
fi

step "4. the stack can be rebuilt from a Backup and the separately kept master key"
if [ "${SMOKE_SKIP_DRILL:-0}" = 1 ]; then
  note "skipped (SMOKE_SKIP_DRILL=1)"
elif ./scripts/restore-drill.sh; then
  note "ok: restore drill passed"
else
  FAILED=1
fi

if [ "$FAILED" != 0 ]; then printf '\nSMOKE TEST FAILED\n'; exit 1; fi
printf '\nSMOKE TEST PASSED%s\n' "$([ "${SMOKE_SKIP_DRILL:-0}" = 1 ] && echo " (restore drill skipped)")"
