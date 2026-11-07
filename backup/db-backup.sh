#!/bin/sh
# One Backup = a database dump plus proof that every File it refers to is already in the file backup.
# The dump comes first; only then do we wait for `filer.backup` to catch up, so the restored database
# never points at a file the Backup lacks. Usage: db-backup.sh [once]   (default: loop forever)
set -eu
DIR=${BACKUP_DIR:-/backup}
TIMEOUT=${BACKUP_CATCHUP_TIMEOUT:-600}
INTERVAL=${BACKUP_INTERVAL:-3600}

backup_once() {
  ts=$(date -u +%Y%m%dT%H%M%SZ)
  mkdir -p "$DIR/db"
  # -Z0: uncompressed, so the "no master key inside" check can search the raw bytes.
  pg_dump -Fc -Z0 -f "$DIR/db/$ts.dump.partial"
  mv "$DIR/db/$ts.dump.partial" "$DIR/db/$ts.dump"

  # Queried after the dump, so this covers at least every File the dump holds. The size is the stored
  # (encrypted) size: plaintext + 16 bytes of tag per 1 MiB segment (an empty file is one segment).
  psql -At -F ' ' -c "SELECT tenant_id || '/' || id, size + 16 * GREATEST(1, CEIL(size / 1048576.0))::bigint
                      FROM files_file WHERE status IN ('active', 'trashed')" > "$DIR/db/$ts.manifest.partial"

  deadline=$(( $(date +%s) + TIMEOUT ))
  while :; do
    missing=0
    while read -r key size; do
      f="$DIR/files/$key"
      if [ ! -f "$f" ] || [ "$(stat -c %s "$f")" -ne "$size" ]; then missing=$((missing + 1)); fi
    done < "$DIR/db/$ts.manifest.partial"
    [ "$missing" -eq 0 ] && break
    if [ "$(date +%s)" -ge "$deadline" ]; then
      echo "backup $ts INCOMPLETE: $missing file(s) not in the file backup after ${TIMEOUT}s" >&2
      return 1
    fi
    sleep 2
  done
  mv "$DIR/db/$ts.manifest.partial" "$DIR/db/$ts.manifest"
  touch "$DIR/db/$ts.complete"   # only a backup with this marker may be restored
  echo "backup $ts complete: $(wc -l < "$DIR/db/$ts.manifest") file(s)"
}

if [ "${1:-}" = "once" ]; then backup_once; exit; fi
while :; do backup_once || echo "backup failed; retrying in ${INTERVAL}s" >&2; sleep "$INTERVAL"; done
