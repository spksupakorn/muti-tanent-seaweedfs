#!/bin/sh
# Runs once, on first init of the data volume. After that, recreate the volume to change roles.
#   owner      : owns the schema; runs migrations only; subject to RLS (FORCE) like everyone else
#   app        : the Tenant API at runtime; not owner, no BYPASSRLS
#   backoffice : Platform operator tooling; BYPASSRLS on purpose
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v owner_pw="$DB_OWNER_PASSWORD" -v app_pw="$DB_APP_PASSWORD" -v bo_pw="$DB_BACKOFFICE_PASSWORD" \
  -v db="$POSTGRES_DB" <<'SQL'
CREATE ROLE filestore_owner LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD :'owner_pw';
CREATE ROLE filestore_app LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD :'app_pw';
CREATE ROLE filestore_backoffice LOGIN NOSUPERUSER BYPASSRLS PASSWORD :'bo_pw';
ALTER DATABASE :"db" OWNER TO filestore_owner;
ALTER SCHEMA public OWNER TO filestore_owner;
GRANT USAGE ON SCHEMA public TO filestore_app, filestore_backoffice;
ALTER DEFAULT PRIVILEGES FOR ROLE filestore_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO filestore_app, filestore_backoffice;
ALTER DEFAULT PRIVILEGES FOR ROLE filestore_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO filestore_app, filestore_backoffice;
SQL
