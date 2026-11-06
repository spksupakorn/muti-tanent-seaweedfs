from django.db import migrations

# Once a Tenant is selected (app.tenant_id) only that Tenant's Memberships are visible, so a
# tenant-scoped request never sees the caller's rows in other Tenants. Before one is chosen,
# the caller sees their own Memberships (app.user_id), which is how login finds its Tenants.
# An unset or empty setting becomes NULL, which matches no row: the policy fails closed
# instead of raising. FORCE makes the table owner obey the policy too.
FORWARD = """
ALTER TABLE accounts_membership ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounts_membership FORCE ROW LEVEL SECURITY;
CREATE POLICY membership_visible ON accounts_membership
    USING (
        CASE WHEN NULLIF(current_setting('app.tenant_id', true), '') IS NOT NULL
             THEN tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
             ELSE user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
        END
    );
-- Tenants, Users and Memberships are written by the Platform operator (back-office role), not the API.
REVOKE INSERT, UPDATE, DELETE ON accounts_user, accounts_tenant, accounts_membership FROM filestore_app;
"""

BACKWARD = """
GRANT INSERT, UPDATE, DELETE ON accounts_user, accounts_tenant, accounts_membership TO filestore_app;
DROP POLICY membership_visible ON accounts_membership;
ALTER TABLE accounts_membership NO FORCE ROW LEVEL SECURITY;
ALTER TABLE accounts_membership DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]
    operations = [migrations.RunSQL(FORWARD, BACKWARD)]
