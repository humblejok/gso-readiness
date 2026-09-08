"""PostgreSQL defense in depth. SQLite uses the same application scope but has no RLS."""

from django.db import migrations

TABLES = {
    "repository": [],
    "reviewimport": [("repository_id", "repository")],
    "finding": [("repository_id", "repository")],
    "observation": [("finding_id", "finding"), ("review_import_id", "reviewimport")],
    "jiraconnection": [],
    "jirabinding": [
        ("repository_id", "repository"),
        ("connection_id", "jiraconnection"),
    ],
    "jiraassociation": [("finding_id", "finding"), ("connection_id", "jiraconnection")],
    "outboxevent": [("observation_id", "observation"), ("binding_id", "jirabinding")],
    "deliveryattempt": [("event_id", "outboxevent")],
    "auditevent": [],
    "invitation": [],
}


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("""
        CREATE FUNCTION hub_check_tenant_reference() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE related_tenant uuid; foreign_id uuid;
        BEGIN
          IF TG_OP = 'UPDATE' AND OLD.organization_id <> NEW.organization_id THEN
            RAISE EXCEPTION 'Workspace ownership is immutable';
          END IF;
          IF TG_NARGS > 0 THEN
            foreign_id := (to_jsonb(NEW)->>TG_ARGV[0])::uuid;
            EXECUTE format('SELECT organization_id FROM %%I WHERE id = $1', TG_ARGV[1]) INTO related_tenant USING foreign_id;
            IF related_tenant IS DISTINCT FROM NEW.organization_id THEN
              RAISE EXCEPTION 'Cross-workspace reference rejected';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
    """)
    for table, references in TABLES.items():
        name = "hubapp_" + table
        schema_editor.execute(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY")
        schema_editor.execute(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY")
        schema_editor.execute(
            f"CREATE POLICY tenant_isolation ON {name} USING (organization_id = NULLIF(current_setting('hub.organization_id', true), '')::uuid) WITH CHECK (organization_id = NULLIF(current_setting('hub.organization_id', true), '')::uuid)"
        )
        schema_editor.execute(
            f"CREATE TRIGGER tenant_owner_guard BEFORE UPDATE ON {name} FOR EACH ROW EXECUTE FUNCTION hub_check_tenant_reference()"
        )
        for index, (field, related) in enumerate(references):
            schema_editor.execute(
                f"CREATE TRIGGER tenant_reference_{index} BEFORE INSERT OR UPDATE ON {name} FOR EACH ROW EXECUTE FUNCTION hub_check_tenant_reference('{field}', 'hubapp_{related}')"
            )
    # Organization IDs are also immutable on the small authentication/billing control tables.
    for name in ("hubapp_apiclient", "hubapp_subscription", "hubapp_membership"):
        schema_editor.execute(
            f"CREATE TRIGGER tenant_owner_guard BEFORE UPDATE ON {name} FOR EACH ROW EXECUTE FUNCTION hub_check_tenant_reference()"
        )


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for table, references in TABLES.items():
        name = "hubapp_" + table
        for index, _ in enumerate(references):
            schema_editor.execute(f"DROP TRIGGER tenant_reference_{index} ON {name}")
        schema_editor.execute(f"DROP TRIGGER tenant_owner_guard ON {name}")
        schema_editor.execute(f"DROP POLICY tenant_isolation ON {name}")
        schema_editor.execute(f"ALTER TABLE {name} DISABLE ROW LEVEL SECURITY")
    for name in ("hubapp_apiclient", "hubapp_subscription", "hubapp_membership"):
        schema_editor.execute(f"DROP TRIGGER tenant_owner_guard ON {name}")
    schema_editor.execute("DROP FUNCTION hub_check_tenant_reference()")


class Migration(migrations.Migration):
    dependencies = [("hubapp", "0001_initial")]
    operations = [migrations.RunPython(install, uninstall)]
