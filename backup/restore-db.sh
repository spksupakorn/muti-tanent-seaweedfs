#!/bin/sh
# Restore the newest complete database dump into an empty database (roles already exist from init).
set -eu
DIR=${BACKUP_DIR:-/backup}
latest=$(ls "$DIR"/db/*.complete 2>/dev/null | sort | tail -n 1 || true)
[ -n "$latest" ] || { echo "no complete backup in $DIR/db" >&2; exit 1; }
dump="${latest%.complete}.dump"
echo "restoring $dump"

# The init script's default privileges would give the API roles full DML on every restored table, on top
# of the dump's own GRANTs. GRANTs only add, so that would undo the REVOKEs the migrations made (for
# example "the API role cannot touch audit entries"). Switch the defaults off while restoring, then back on.
psql -v ON_ERROR_STOP=1 <<'SQL'
ALTER DEFAULT PRIVILEGES FOR ROLE filestore_owner IN SCHEMA public
  REVOKE ALL ON TABLES FROM filestore_app, filestore_backoffice;
ALTER DEFAULT PRIVILEGES FOR ROLE filestore_owner IN SCHEMA public
  REVOKE ALL ON SEQUENCES FROM filestore_app, filestore_backoffice;
SQL

pg_restore --exit-on-error --no-owner --role=filestore_owner -d "$PGDATABASE" "$dump"

# Same as postgres/init/10-create-roles.sh, so tables created by later migrations behave as before.
psql -v ON_ERROR_STOP=1 <<'SQL'
ALTER DEFAULT PRIVILEGES FOR ROLE filestore_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO filestore_app, filestore_backoffice;
ALTER DEFAULT PRIVILEGES FOR ROLE filestore_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO filestore_app, filestore_backoffice;
SQL
