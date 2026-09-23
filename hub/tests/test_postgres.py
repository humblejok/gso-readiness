import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import DatabaseError, close_old_connections, connection, transaction

from hubapp import change_requests, finding_work, request_work
from hubapp.imports import import_review
from hubapp.models import (
    ChangeRequest,
    ChangeRequestActivity,
    Finding,
    FindingActivity,
    Repository,
    RequestImplementation,
    ReviewImport,
)
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


def test_postgres_activity_rls_and_foreign_references(org, owner, envelope, key):
    other = create_workspace(owner, "Activity isolation")
    with tenant_scope(org.id):
        import_review(envelope, key)
        finding = Finding.objects.filter(lifecycle="open").first()
        finding = finding_work.configure(
            finding.id, "implement", finding.implementation_revision, "owner"
        )
        attempt, _ = finding_work.claim(
            finding.id, finding.implementation_revision, uuid.uuid4(), "worker"
        )
    with tenant_scope(other.id):
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM hubapp_findingactivity")
            assert cursor.fetchone()[0] == 0
        with pytest.raises(DatabaseError), transaction.atomic():
            FindingActivity.all_objects.create(
                organization=other,
                finding=finding,
                baseline_observation_id=attempt.baseline_observation_id,
                request_id=uuid.uuid4(),
                kind="revalidation",
                status="succeeded",
                revision=1,
            )
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM hubapp_findingactivity")
        assert cursor.fetchone()[0] == 0


def test_postgres_concurrent_claim_has_one_winner(org, envelope, key):
    with tenant_scope(org.id):
        import_review(envelope, key)
        finding = Finding.objects.filter(lifecycle="open").first()
        finding = finding_work.configure(
            finding.id, "implement", finding.implementation_revision, "owner"
        )

    def run():
        close_old_connections()
        try:
            with tenant_scope(org.id):
                try:
                    finding_work.claim(
                        finding.id, finding.implementation_revision, uuid.uuid4(), "worker"
                    )
                    return "claimed"
                except finding_work.WorkConflict:
                    return "conflict"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: run(), range(2))) == ["claimed", "conflict"]
    with tenant_scope(org.id):
        assert FindingActivity.objects.filter(status="running").count() == 1


def test_postgres_change_requests_rls_and_references(org, owner):
    other = create_workspace(owner, "Request isolation")
    with tenant_scope(org.id):
        item = change_requests.create(
            kind="bug", description="Private request", actor=owner.username
        )
    with tenant_scope(other.id):
        for table in ("hubapp_changerequest", "hubapp_changerequestactivity"):
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                assert cursor.fetchone()[0] == 0
        with pytest.raises(DatabaseError), transaction.atomic():
            ChangeRequest.all_objects.create(organization=org, kind="bug", description="Forbidden")
        with pytest.raises(DatabaseError), transaction.atomic():
            ChangeRequestActivity.all_objects.create(
                organization=other,
                change_request=item,
                actor="Intruder",
                action="created",
                revision=2,
                kind="bug",
                description="Foreign reference",
                status="open",
            )
    with tenant_scope(org.id), pytest.raises(DatabaseError), transaction.atomic():
        ChangeRequest.all_objects.filter(pk=item.pk).update(organization=other)
    for table in ("hubapp_changerequest", "hubapp_changerequestactivity"):
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            assert cursor.fetchone()[0] == 0


def test_postgres_concurrent_request_update_has_one_winner(org, owner):
    with tenant_scope(org.id):
        item = change_requests.create(
            kind="feature", description="New feature", actor=owner.username
        )

    def run():
        close_old_connections()
        try:
            with tenant_scope(org.id):
                try:
                    change_requests.update(
                        item.pk,
                        revision=1,
                        actor=owner.username,
                        action="edit",
                        kind="feature",
                        description="Revised feature",
                    )
                    return "updated"
                except change_requests.RequestConflict:
                    return "conflict"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: run(), range(2))) == ["conflict", "updated"]
    with tenant_scope(org.id):
        assert ChangeRequestActivity.objects.count() == 2
        assert ChangeRequest.objects.get(pk=item.pk).revision == 2


def test_postgres_request_implementation_isolation_and_concurrent_claim(org, owner):
    from datetime import timedelta

    from django.utils import timezone

    other = create_workspace(owner, "Implementation isolation")
    with tenant_scope(org.id):
        Repository.objects.create(external_id="repo:orders", name="Orders")
        item = change_requests.create(
            kind="feature",
            description="Export orders",
            actor=owner.username,
            repository_external_id="repo:orders",
        )
        item = change_requests.submit_analysis(
            item.pk,
            revision=1,
            actor="analyzer",
            repository_external_id="repo:orders",
            submission_id=str(uuid.uuid4()),
            analysis={
                "project_kind": "backend",
                "specification": "Authorized exports",
                "new_interfaces": "POST /exports",
                "changed_interfaces": "None",
                "breaking_changes": "None",
            },
        )
        item = change_requests.update(item.pk, revision=2, actor=owner.username, action="accept")

    def run():
        close_old_connections()
        try:
            with tenant_scope(org.id):
                try:
                    request_work.claim(item.pk, 3, uuid.uuid4(), "worker", "repo:orders")
                    return "claimed"
                except change_requests.RequestConflict:
                    return "conflict"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: run(), range(2))) == ["claimed", "conflict"]
    with tenant_scope(org.id):
        attempt = RequestImplementation.objects.get()
    with tenant_scope(other.id):
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM hubapp_requestimplementation")
            assert cursor.fetchone()[0] == 0
        with pytest.raises(DatabaseError), transaction.atomic():
            RequestImplementation.all_objects.create(
                organization=other,
                change_request=item,
                request_id=uuid.uuid4(),
                actor="intruder",
                revision=3,
                specification={},
                expires_at=timezone.now() + timedelta(hours=1),
            )
    with tenant_scope(org.id), pytest.raises(DatabaseError), transaction.atomic():
        RequestImplementation.all_objects.filter(pk=attempt.pk).update(organization=other)
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM hubapp_requestimplementation")
        assert cursor.fetchone()[0] == 0
