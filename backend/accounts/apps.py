from django.apps import AppConfig
from django.db import connections
from django.db.models.signals import post_migrate

# What only the back office uses: Django's own auth, session, admin-log and bookkeeping tables.
BACK_OFFICE_ONLY_TABLES = """
    SELECT format('REVOKE ALL ON TABLE %I FROM filestore_app', tablename)
    FROM pg_tables
    WHERE schemaname = 'public'
      AND (tablename LIKE 'auth\\_%' OR tablename IN
           ('django_session', 'django_admin_log', 'django_content_type', 'django_migrations'))
"""


def keep_admin_tables_from_the_api_role(using, **kwargs):
    """The API role gets full access to every new table by default (see postgres/init); take it away from
    the tables only the back office needs, so a flaw in the API cannot read or forge admin sessions.
    Runs after every migrate (as the table owner) and is harmless to repeat."""
    with connections[using].cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'filestore_app'")
        if cur.fetchone() is None:
            return  # a database set up without postgres/init has no such role
        cur.execute(BACK_OFFICE_ONLY_TABLES)
        for (statement,) in cur.fetchall():
            cur.execute(statement)


class AccountsConfig(AppConfig):
    name = "accounts"

    def ready(self):
        post_migrate.connect(keep_admin_tables_from_the_api_role, sender=self, dispatch_uid="admin-tables-api-role")
