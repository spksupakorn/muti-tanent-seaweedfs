from django.db import migrations

# Only the selected Tenant's rows are visible; an unset or empty setting is NULL, which matches
# nothing (fail closed). WITH CHECK defaults to USING, so rows cannot be written for another Tenant.
FORWARD = """
ALTER TABLE files_file ENABLE ROW LEVEL SECURITY;
ALTER TABLE files_file FORCE ROW LEVEL SECURITY;
CREATE POLICY file_of_tenant ON files_file
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

ALTER TABLE files_tenantkey ENABLE ROW LEVEL SECURITY;
ALTER TABLE files_tenantkey FORCE ROW LEVEL SECURITY;
CREATE POLICY tenantkey_of_tenant ON files_tenantkey
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
-- The API creates a Tenant key on first upload; only the operator (back-office role) destroys it.
REVOKE UPDATE, DELETE ON files_tenantkey FROM filestore_app;
"""

BACKWARD = """
GRANT UPDATE, DELETE ON files_tenantkey TO filestore_app;
DROP POLICY tenantkey_of_tenant ON files_tenantkey;
ALTER TABLE files_tenantkey NO FORCE ROW LEVEL SECURITY;
ALTER TABLE files_tenantkey DISABLE ROW LEVEL SECURITY;
DROP POLICY file_of_tenant ON files_file;
ALTER TABLE files_file NO FORCE ROW LEVEL SECURITY;
ALTER TABLE files_file DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [("files", "0001_initial")]
    operations = [migrations.RunSQL(FORWARD, BACKWARD)]
