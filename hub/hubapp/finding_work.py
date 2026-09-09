"""Tenant-serialized queue, claim and targeted-result transitions. No code runs on the Hub."""

import json
import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .contracts import render_remediation
from .git_host_contract import HostError, validate_pr_url
from .models import Finding, FindingActivity, Observation, Organization
from .services import audit, require_writes, usage
from .targeted_contract import TargetedError, bounded, digest, validate_targeted
from .tenancy import current_tenant


class WorkConflict(ValueError):
    pass


def storage_budget(extra_bytes):
    limits = require_writes()
    if usage()["storage_bytes"] + max(0, extra_bytes) > limits["storage_mb"] * 1024 * 1024:
        raise ValidationError(
            "Workspace storage allowance reached; archive/export or increase capacity before adding work history."
        )


def latest_observation(finding):
    row = (
        Observation.objects.filter(finding=finding)
        .select_related("review_import")
        .order_by("-created_at", "-pk")
        .first()
    )
    if not row:
        raise ValidationError("Finding has no imported baseline.")
    return row


def remediation_text(finding, observation=None):
    row = observation or latest_observation(finding)
    return finding.implementation_text or render_remediation(row.remediation)


def context(finding):
    latest = latest_observation(finding)
    return {
        "finding_id": str(finding.id),
        "display_id": finding.display_id,
        "repository_external_id": finding.repository.external_id,
        "clone_url": finding.repository.clone_url,
        "fingerprint": finding.fingerprint,
        "revision": finding.implementation_revision,
        "lifecycle": finding.lifecycle,
        "to_implement": finding.implementation_requested,
        "baseline_observation_id": str(latest.id),
        "baseline_import_id": str(latest.review_import_id),
        "remediation": remediation_text(finding, latest),
        "finding": finding.data,
    }


def locked(finding_id):
    Organization.objects.select_for_update().get(pk=current_tenant(), suspended=False)
    return Finding.objects.select_for_update().select_related("repository").get(pk=finding_id)


def check_revision(finding, revision):
    if type(revision) is not int or revision != finding.implementation_revision:
        raise WorkConflict(
            "Finding changed. Refresh it and review the latest proposal before continuing."
        )


def invalidate_claims(finding, reason):
    FindingActivity.objects.filter(finding=finding, status="running").update(
        status="cancelled", comment=reason, payload_bytes=F("payload_bytes") + len(reason.encode())
    )


@transaction.atomic
def configure(finding_id, action, revision, actor, text=None):
    finding = locked(finding_id)
    require_writes()
    check_revision(finding, revision)
    if action not in {"implement", "cancel", "edit"}:
        raise ValidationError("Unknown implementation action.")
    if action != "cancel" and finding.lifecycle != "open":
        raise WorkConflict("Only open findings can be queued or edited for implementation.")
    if action == "edit":
        if not finding.implementation_requested or not bounded(text, 64000):
            raise ValidationError(
                "Queue the finding first and provide a nonempty remediation of at most 64,000 characters."
            )
        finding.implementation_text = text
    elif action == "implement":
        finding.implementation_requested = True
    else:
        finding.implementation_requested = False
    invalidate_claims(
        finding,
        "Implementation cancelled or proposal changed by a workspace user; previous worker cannot complete it.",
    )
    finding.implementation_revision += 1
    finding.save(
        update_fields=["implementation_text", "implementation_requested", "implementation_revision"]
    )
    audit("finding." + action, actor, finding.pk)
    return finding


@transaction.atomic
def claim(finding_id, revision, request_id, actor):
    finding = locked(finding_id)
    request_id = uuid.UUID(str(request_id))
    existing = FindingActivity.objects.filter(request_id=request_id).first()
    if existing:
        if (
            existing.finding_id != finding.id
            or existing.actor != str(actor)
            or existing.revision != revision
        ):
            raise WorkConflict("Claim request ID conflicts with an earlier request.")
        if existing.status != "running" or existing.expires_at <= timezone.now():
            raise WorkConflict(
                "This claim is no longer active. Refresh the queue; do not reuse a completed claim ID."
            )
        return existing, False
    require_writes()
    check_revision(finding, revision)
    if (
        not finding.repository.active
        or finding.lifecycle != "open"
        or not finding.implementation_requested
    ):
        raise WorkConflict("Finding is not an active open implementation request.")
    running = FindingActivity.objects.filter(finding=finding, status="running").first()
    if running and running.expires_at > timezone.now():
        raise WorkConflict(
            "Another implementation is in progress. Cancel it in the Hub or wait for its lease to expire."
        )
    if running:
        running.status, running.comment = (
            "failed",
            "Implementation lease expired; no successful completion was received.",
        )
        running.payload_bytes += len(running.comment.encode())
        running.save(update_fields=["status", "comment", "payload_bytes"])
    proposal = remediation_text(finding)
    storage_budget(len(proposal.encode()))
    attempt = FindingActivity.objects.create(
        finding=finding,
        baseline_observation=latest_observation(finding),
        request_id=request_id,
        kind="implementation",
        status="running",
        actor=str(actor),
        revision=revision,
        proposal=proposal,
        payload_bytes=len(proposal.encode()),
        expires_at=timezone.now() + timedelta(hours=4),
    )
    audit("implementation.claimed", actor, attempt.pk)
    return attempt, True


def check_report(finding, report, latest):
    try:
        validate_targeted(report)
    except TargetedError as exc:
        raise ValidationError(str(exc)) from exc
    baseline = report["baseline"]
    original = latest.review_import.payload["review"]
    if (
        report["repository_external_id"] != finding.repository.external_id
        or baseline["fingerprint"] != finding.fingerprint
        or baseline["finding_id"] != finding.display_id
        or baseline["run_id"] != latest.review_import.run_id
        or baseline["review_digest"] != digest(original)
    ):
        raise WorkConflict(
            "Targeted result does not match this finding's latest imported baseline."
        )
    all_fingerprints = {
        f["fingerprint"]
        for group in ("findings", "inconclusive_findings", "rejected_findings")
        for f in original[group]
    }
    if finding.fingerprint not in all_fingerprints or set(
        report["not_revalidated"]
    ) != all_fingerprints - {finding.fingerprint}:
        raise ValidationError(
            "Targeted result must explicitly leave every other baseline finding not revalidated."
        )


def completion_result(attempt):
    return {
        "activity_id": str(attempt.id),
        "status": attempt.status,
        "finding_id": str(attempt.finding_id),
        "comment": attempt.comment,
        "result": attempt.data.get("result", {}),
    }


@transaction.atomic
def complete(finding_id, attempt_id, body, actor):
    finding = locked(finding_id)
    attempt = FindingActivity.objects.get(
        pk=attempt_id, finding=finding, kind="implementation", actor=str(actor)
    )
    if set(body) != {"outcome", "comment", "report", "pull_request", "commit_sha", "base_branch"}:
        raise ValidationError("Invalid implementation completion fields.")
    if body["outcome"] not in {"succeeded", "failed"} or not bounded(body["comment"], 8000):
        raise ValidationError("Provide a short factual implementation summary or failure reason.")
    if len(json.dumps(body).encode()) > 256 * 1024:
        raise ValidationError("Implementation completion exceeds 256 KiB.")
    if not all(isinstance(body[key], str) for key in ("pull_request", "commit_sha", "base_branch")):
        raise ValidationError("Invalid branch/commit/PR metadata.")
    if body["pull_request"]:
        try:
            validate_pr_url(body["pull_request"], finding.repository.clone_url)
        except HostError as exc:
            raise ValidationError(str(exc)) from exc
    payload_hash = digest(body)
    if (
        attempt.status in {"succeeded", "failed"}
        and attempt.data.get("payload_hash") == payload_hash
    ):
        return completion_result(attempt)
    if attempt.status != "running" or attempt.expires_at <= timezone.now():
        raise WorkConflict(
            "Claim is cancelled, expired or already completed with different content."
        )
    require_writes()
    check_revision(finding, attempt.revision)
    latest = latest_observation(finding)
    if (
        latest.id != attempt.baseline_observation_id
        or finding.lifecycle != "open"
        or not finding.implementation_requested
    ):
        raise WorkConflict("Finding or its baseline changed while implementation was running.")
    if body["outcome"] == "succeeded":
        report = body["report"]
        check_report(finding, report, latest)
        if (
            report["result"]["status"] != "resolved"
            or report["source"]["dirty"]
            or report["source"]["commit_sha"] != body["commit_sha"]
            or report["source"]["branch"] != "feature/" + finding.display_id
            or not bounded(body["base_branch"], 200)
            or body["base_branch"] == report["source"]["branch"]
            or not body["pull_request"]
        ):
            raise ValidationError(
                "Success requires a clean, resolved feature-branch revalidation, matching commit and a pull request to the original branch."
            )
        finding.lifecycle = "resolved"
        finding.implementation_requested = False
        finding.last_seen = timezone.now()
    finding.implementation_revision += 1
    finding.save(
        update_fields=[
            "lifecycle",
            "implementation_requested",
            "implementation_revision",
            "last_seen",
        ]
    )
    attempt.status = body["outcome"]
    attempt.comment = body["comment"]
    if attempt.status == "succeeded":
        attempt.comment += "\nResolved on feature branch; pull request created, not yet merged."
    result = {"lifecycle": finding.lifecycle, "to_implement": finding.implementation_requested}
    attempt.data = {"payload_hash": payload_hash, "completion": body, "result": result}
    size = (
        len(json.dumps(attempt.data).encode())
        + len(attempt.comment.encode())
        + len(attempt.proposal.encode())
    )
    storage_budget(size - attempt.payload_bytes)
    attempt.payload_bytes = size
    attempt.save(update_fields=["status", "comment", "data", "payload_bytes"])
    audit("implementation." + attempt.status, actor, attempt.pk)
    return completion_result(attempt)


@transaction.atomic
def revalidate(finding_id, revision, report, actor):
    finding = locked(finding_id)
    validate_targeted(report)
    request_id = uuid.UUID(report["run_id"])
    prior = FindingActivity.objects.filter(request_id=request_id).first()
    if prior:
        if (
            prior.finding_id == finding.id
            and prior.kind == "revalidation"
            and prior.data.get("report") == report
        ):
            return completion_result(prior)
        raise WorkConflict("Revalidation ID already exists with different content.")
    require_writes()
    check_revision(finding, revision)
    latest = latest_observation(finding)
    check_report(finding, report, latest)
    if report["source"]["dirty"]:
        raise ValidationError(
            "Commit changes and revalidate the clean revision before updating Hub lifecycle."
        )
    if FindingActivity.objects.filter(
        finding=finding, status="running", expires_at__gt=timezone.now()
    ).exists():
        raise WorkConflict(
            "An implementation is running; complete its claim instead of bypassing the workflow."
        )
    finding.lifecycle = "resolved" if report["result"]["status"] == "resolved" else "open"
    if finding.lifecycle == "resolved":
        finding.implementation_requested = False
    finding.implementation_revision += 1
    finding.last_seen = timezone.now()
    finding.save(
        update_fields=[
            "lifecycle",
            "implementation_requested",
            "implementation_revision",
            "last_seen",
        ]
    )
    size = len(json.dumps(report).encode()) + len(report["result"]["rationale"].encode()) + 256
    storage_budget(size)
    attempt = FindingActivity.objects.create(
        finding=finding,
        baseline_observation=latest,
        request_id=request_id,
        kind="revalidation",
        status="succeeded",
        actor=str(actor),
        revision=revision,
        comment=report["result"]["rationale"],
        payload_bytes=size,
        data={
            "report": report,
            "result": {
                "lifecycle": finding.lifecycle,
                "to_implement": finding.implementation_requested,
            },
        },
    )
    audit("finding.revalidated", actor, finding.pk)
    return completion_result(attempt)
