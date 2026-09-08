import copy
from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from hubapp.contracts import render_remediation
from hubapp.imports import ImportConflict, import_review
from hubapp.models import (
    AuditEvent,
    Finding,
    Observation,
    OutboxEvent,
    Repository,
    ReviewImport,
    Subscription,
)
from hubapp.services import create_workspace
from hubapp.tenancy import tenant_scope


def test_import_replay_conflict_and_no_jira(org, envelope, key):
    with tenant_scope(org.id):
        record, created = import_review(envelope, key)
        assert created and record.result["created_findings"] == 4
        assert record.result["jira"]["state"] == "not_configured"
        assert OutboxEvent.objects.count() == 0
        replay, created = import_review(envelope, key)
        assert not created and replay.id == record.id
        assert Observation.objects.count() == 4
        envelope["repository"]["name"] = "Different name"
        with pytest.raises(ImportConflict):
            import_review(envelope, key)
        assert AuditEvent.objects.filter(action="import.conflict").count() == 1


def test_same_repository_and_fingerprint_are_isolated(org, owner, envelope, key):
    second = create_workspace(owner, "Other team")
    with tenant_scope(org.id):
        first, _ = import_review(envelope, key)
    assert Finding.objects.count() == 0
    with tenant_scope(second.id):
        another, _ = import_review(envelope, key)
        assert another.repository_id != first.repository_id
        assert Finding.objects.count() == 4
        assert not ReviewImport.objects.filter(pk=first.pk).exists()


def test_absence_never_closes_and_explicit_reconciliation_does(org, envelope, key):
    with tenant_scope(org.id):
        import_review(envelope, key)
        next_review = copy.deepcopy(envelope)
        next_review["review"]["run"]["run_id"] = "absent"
        next_review["review"]["findings"] = []
        next_review["remediations"] = []
        import_review(next_review, "repo:orders:absent")
        assert Finding.objects.filter(lifecycle="open").count() == 2
        next_review["review"]["run"].update(
            run_id="resolved",
            mode="revalidate",
            finding_reconciliation=[
                {
                    "baseline_fingerprint": envelope["review"]["findings"][0]["fingerprint"],
                    "baseline_id": "DEPLOY-001",
                    "status": "resolved",
                    "rationale": "Configuration fixed.",
                    "evidence": [{"type": "source", "path": "settings.py"}],
                }
            ],
        )
        import_review(next_review, "repo:orders:resolved")
        assert Finding.objects.filter(lifecycle="resolved").count() == 1
        assert Observation.objects.filter(lifecycle="resolved").count() == 1


@pytest.mark.parametrize(
    "mutation",
    [
        lambda e: e.update(organization_id="spoofed"),
        lambda e: e["review"]["findings"][0].update(fingerprint="sha256:" + "a" * 64),
        lambda e: e["review"]["findings"].append(e["review"]["findings"][0]),
        lambda e: e["remediations"].clear(),
        lambda e: e["remediations"][0].update(generated_from_commit="deadbeef"),
        lambda e: e["remediations"][0]["implementation_steps"][0].update(order=2),
        lambda e: e["remediations"][0].update(validation_commands=[], validation_notes=""),
        lambda e: e["review"]["findings"][0].update(verification_status="inconclusive"),
        lambda e: e["review"]["findings"][0].update(description="-----BEGIN PRIVATE KEY-----"),
        lambda e: e["repository"].update(clone_url="https://token@git.example.invalid/repo"),
        lambda e: e["source"].update(commit_sha="invalid"),
        lambda e: e["remediations"][0].update(unknown="no"),
    ],
)
def test_invalid_imports_are_atomic(org, envelope, key, mutation):
    mutation(envelope)
    with tenant_scope(org.id):
        with pytest.raises(ValidationError):
            import_review(envelope, key)
        assert Repository.objects.count() == 0
        assert ReviewImport.objects.count() == 0


def test_expired_subscription_allows_replay_but_no_new_import(org, envelope, key):
    with tenant_scope(org.id):
        import_review(envelope, key)
        Subscription.objects.update(valid_until=timezone.now() - timedelta(days=1))
        assert import_review(envelope, key)[1] is False
        envelope["review"]["run"]["run_id"] = "another"
        with pytest.raises(PermissionDenied):
            import_review(envelope, "repo:orders:another")


def test_token_repository_restriction(org, envelope, key):
    with tenant_scope(org.id), pytest.raises(PermissionDenied):
        import_review(envelope, key, repository_restriction="repo:other")


def test_remediation_rendering_is_deterministic(envelope):
    plan = envelope["remediations"][0]
    assert render_remediation(plan) == render_remediation(copy.deepcopy(plan))
    assert "Source revision" in render_remediation(plan)


def test_manager_fails_closed_and_rejects_foreign_creates(org, owner):
    with pytest.raises(PermissionDenied):
        Repository.objects.create(name="No scope", external_id="no")
    other = create_workspace(owner, "Other")
    with tenant_scope(org.id), pytest.raises(PermissionDenied):
        Repository.objects.create(organization=other, name="Wrong", external_id="wrong")
