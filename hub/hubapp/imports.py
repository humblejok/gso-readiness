"""Atomic, tenant-scoped, immutable import and explicit lifecycle reconciliation."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .contracts import canonical, digest, validate_envelope
from .models import (
    Finding,
    JiraBinding,
    Observation,
    Organization,
    OutboxEvent,
    Repository,
    ReviewImport,
)
from .services import audit, require_writes, usage
from .tenancy import current_tenant


class ImportConflict(Exception):
    pass


def import_review(data, key, actor="", repository_restriction=""):
    plans = validate_envelope(data, key)
    if repository_restriction and data["repository"]["external_id"] != repository_restriction:
        raise PermissionDenied("This token is restricted to another repository.")
    try:
        return _import(data, plans, actor)
    except ImportConflict:
        audit("import.conflict", actor, data["review"]["run"]["run_id"])
        raise


@transaction.atomic
def _import(data, plans, actor):
    org = Organization.objects.select_for_update().get(pk=current_tenant(), suspended=False)
    review, meta = data["review"], data["repository"]
    repo = Repository.objects.filter(external_id=meta["external_id"]).first()
    payload_hash = digest(data)
    run = review["run"]
    if repo:
        previous = ReviewImport.objects.filter(repository=repo, run_id=run["run_id"]).first()
        if previous:
            if previous.payload_hash != payload_hash:
                raise ImportConflict("Run identity already exists with different content.")
            return previous, False
    limits = require_writes()
    used = usage()
    size = len(canonical(data).encode())
    if (
        used["imports_month"] >= limits["imports_month"]
        or used["storage_bytes"] + size > limits["storage_mb"] * 1024 * 1024
    ):
        raise PermissionDenied("Workspace import/storage limit reached.")
    repository_created = repo is None
    if not repo:
        if Repository.objects.count() >= limits["repositories"]:
            raise PermissionDenied("Repository allowance reached.")
        repo = Repository.objects.create(**meta)
    if not repo.active:
        raise PermissionDenied("Repository is archived.")
    record = ReviewImport.objects.create(
        repository=repo,
        run_id=run["run_id"],
        payload_hash=payload_hash,
        payload=data,
        payload_bytes=size,
        source_commit=data["source"]["commit_sha"],
        source_branch=data["source"]["branch"],
        mode=run["mode"],
    )
    result = {
        "import_id": str(record.id),
        "organization_id": str(org.id),
        "repository_id": str(repo.id),
        "repository_created": repository_created,
        "run_id": record.run_id,
        "created_findings": 0,
        "updated_findings": 0,
        "lifecycle_changes": 0,
        "jira": {"state": "not_configured", "queued_deliveries": 0},
    }
    changes = {r["baseline_fingerprint"]: r for r in run["finding_reconciliation"]}
    # A claimed baseline must already exist in this workspace/repository.
    for fp in changes:
        if not Finding.objects.filter(repository=repo, fingerprint=fp).exists():
            raise ValidationError("Reconciliation references an unknown baseline finding.")
    observations = {}
    for group, initial in (
        ("findings", "open"),
        ("inconclusive_findings", "inconclusive"),
        ("rejected_findings", "rejected"),
    ):
        for item in review[group]:
            finding = Finding.objects.filter(
                repository=repo, fingerprint=item["fingerprint"]
            ).first()
            created = finding is None
            if created:
                finding = Finding(
                    organization=org,
                    repository=repo,
                    fingerprint=item["fingerprint"],
                    lifecycle=initial,
                )
            before = finding.lifecycle
            disposition = changes.get(finding.fingerprint)
            if disposition:
                finding.lifecycle = (
                    "open" if disposition["status"] == "still_present" else disposition["status"]
                )
            finding.display_id = item["id"]
            for field in ("title", "severity", "category", "verification_status"):
                setattr(finding, field, item[field])
            finding.data, finding.last_seen = item, timezone.now()
            finding.save()
            result["created_findings" if created else "updated_findings"] += 1
            result["lifecycle_changes"] += int(not created and before != finding.lifecycle)
            observations[finding.fingerprint] = Observation.objects.create(
                finding=finding,
                review_import=record,
                data=item,
                lifecycle=finding.lifecycle,
                remediation=plans.get(finding.fingerprint),
                reconciliation=disposition,
            )
    for fp, disposition in changes.items():
        if fp in observations:
            continue
        finding = Finding.objects.get(repository=repo, fingerprint=fp)
        before = finding.lifecycle
        finding.lifecycle = (
            "open" if disposition["status"] == "still_present" else disposition["status"]
        )
        finding.replacement_fingerprint = disposition.get("replacement_fingerprint", "")
        finding.save(update_fields=["lifecycle", "replacement_fingerprint"])
        result["lifecycle_changes"] += int(before != finding.lifecycle)
        observations[fp] = Observation.objects.create(
            finding=finding,
            review_import=record,
            data=finding.data,
            lifecycle=finding.lifecycle,
            reconciliation=disposition,
        )
    from django.conf import settings

    binding = (
        JiraBinding.objects.select_related("connection")
        .filter(repository=repo, enabled=True, connection__enabled=True)
        .first()
    )
    if (
        binding
        and settings.OUTBOUND_INTEGRATIONS_ENABLED
        and record.source_branch == repo.default_branch
    ):
        if binding.connection.organization_id != org.id:
            raise ValidationError("Cross-workspace integration binding.")
        from .models import JiraAssociation

        for obs in observations.values():
            eligible = obs.lifecycle == "open" and obs.data["verification_status"] in {
                "supported",
                "partially_supported",
            }
            associated = JiraAssociation.objects.filter(
                finding=obs.finding, connection=binding.connection
            ).exists()
            if eligible or associated:
                OutboxEvent.objects.create(observation=obs, binding=binding)
                result["jira"]["queued_deliveries"] += 1
        result["jira"]["state"] = "queued" if result["jira"]["queued_deliveries"] else "skipped"
    record.result = result
    record.save(update_fields=["result"])
    audit("review.imported", actor, record.pk)
    return record, True
