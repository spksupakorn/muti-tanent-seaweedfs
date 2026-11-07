from django.db import migrations

# Same rule as File: only the selected Tenant's rows, fail closed when no Tenant is selected.
# Append-only for the API role: it can write entries but never change or delete them.
FORWARD = """
ALTER TABLE audit_auditentry ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_auditentry FORCE ROW LEVEL SECURITY;
CREATE POLICY audit_of_tenant ON audit_auditentry
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
REVOKE UPDATE, DELETE ON audit_auditentry FROM filestore_app;
"""

BACKWARD = """
GRANT UPDATE, DELETE ON audit_auditentry TO filestore_app;
DROP POLICY audit_of_tenant ON audit_auditentry;
ALTER TABLE audit_auditentry NO FORCE ROW LEVEL SECURITY;
ALTER TABLE audit_auditentry DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]
    operations = [migrations.RunSQL(FORWARD, BACKWARD)]
