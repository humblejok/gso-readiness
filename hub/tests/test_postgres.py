from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import DatabaseError, close_old_connections, connection, transaction

from hubapp.imports import import_review
from hubapp.models import Finding, Repository, ReviewImport
from hubapp.services import create_workspace
from hubapp.tenancy import tenant_scope

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="PostgreSQL is required for database isolation/concurrency tests",
    ),
]


def test_postgres_concurrent_import_is_idempotent(org, envelope, key):
    def run():
        close_old_connections()
        try:
            with tenant_scope(org.id):
                return str(import_review(envelope, key)[0].id)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: run(), range(2)))
    assert len(set(results)) == 1
    with tenant_scope(org.id):
        assert ReviewImport.objects.count() == 1


def test_postgres_rls_raw_queries_and_cross_tenant_write(org, owner, envelope, key):
    other = create_workspace(owner, "Other")
    with tenant_scope(org.id):
        import_review(envelope, key)
    with connection.cursor() as cursor:
        cursor.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        assert not any(cursor.fetchone()), (
            "Run isolation tests using a non-superuser without BYPASSRLS"
        )
    with tenant_scope(other.id):
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM hubapp_finding")
            assert cursor.fetchone()[0] == 0
        with pytest.raises(DatabaseError), transaction.atomic():
            Repository.all_objects.create(organization=org, name="Forbidden", external_id="foreign")
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM hubapp_finding")
        assert cursor.fetchone()[0] == 0


def test_postgres_reference_trigger_rejects_cross_workspace_fk(org, owner, envelope, key):
    other = create_workspace(owner, "Other")
    with tenant_scope(org.id):
        import_review(envelope, key)
        repo = Repository.objects.get()
    with tenant_scope(other.id), pytest.raises(DatabaseError), transaction.atomic():
        Finding.all_objects.create(
            organization=other,
            repository=repo,
            fingerprint="sha256:" + "c" * 64,
            display_id="BAD",
            title="Bad",
            severity="low",
            category="security",
            verification_status="supported",
            data={},
        )
