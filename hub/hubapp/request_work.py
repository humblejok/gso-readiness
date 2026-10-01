"""Serialized approved-request claims/completions. The Hub never executes code."""

import json
import re
import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from . import handoffs
from .change_requests import RequestConflict, _record
from .finding_work import storage_budget
from .git_host_contract import validate_pr_url
from .models import ChangeRequest, Organization, Repository, RequestImplementation
from .request_contract import display_id, specification, validate_request_report
from .request_protocol import PROTOCOL, completion_checks, validate_binding
from .services import audit, require_writes
from .targeted_contract import bounded, digest
from .tenancy import current_tenant


def context(item):
    approved = specification(
        {
            "id": str(item.pk),
            "revision": item.revision,
            "repository_external_id": item.repository_external_id,
            "kind": item.kind,
            "description": item.description,
            "analysis": item.analysis,
        }
    )
    repo = Repository.objects.get(external_id=item.repository_external_id, active=True)
    return {
        **approved,
        "status": item.status,
        "display_id": display_id(item.pk),
        "specification_digest": digest(approved),
        "clone_url": repo.clone_url,
        "related_projects": handoffs.relationships(item.repository_external_id),
        "upstream_handoff": handoffs.incoming(item),
        "implementation_protocol": PROTOCOL,
    }


def locked(pk, repository_external_id):
    Organization.objects.select_for_update().get(pk=current_tenant(), suspended=False)
    item = ChangeRequest.objects.select_for_update().get(pk=pk)
    if not repository_external_id or item.repository_external_id != repository_external_id:
        raise RequestConflict("Request project changed; stale workers cannot update it.")
    return item


@transaction.atomic
def claim(pk, revision, request_id, actor, repository_external_id, binding=None):
    item = locked(pk, repository_external_id)
    require_writes()
    if type(revision) is not int or revision != item.revision or item.status != "specified":
        raise RequestConflict("Only the current user-specified revision can be implemented.")
    current = context(item)
    if binding is not None:
        check_binding(item, binding, actor, current)
    request_id = uuid.UUID(str(request_id))
    existing = RequestImplementation.objects.filter(request_id=request_id).first()
    if existing:
        if (
            existing.change_request_id != item.pk
            or existing.actor != str(actor)
            or existing.revision != revision
            or existing.status != "running"
            or existing.expires_at <= timezone.now()
            or digest(existing.specification) != current["specification_digest"]
            or (binding is not None and existing.data.get("binding") != binding)
        ):
            raise RequestConflict("Claim identity is conflicting, cancelled, expired or completed.")
        return existing, claim_context(current, existing), False
    running = RequestImplementation.objects.filter(change_request=item, status="running").first()
    if running:
        if running.expires_at > timezone.now():
            raise RequestConflict(
                "Another implementation is running; wait for expiry or release its claim."
            )
        running.status = "expired"
        running.save(update_fields=["status"])
    frozen = specification(current)
    metadata = (
        {"binding": binding, "clone_url": current["clone_url"]} if binding is not None else {}
    )
    size = len(json.dumps(frozen).encode()) + len(json.dumps(metadata).encode())
    storage_budget(size)
    attempt = RequestImplementation.objects.create(
        change_request=item,
        request_id=request_id,
        actor=str(actor),
        revision=revision,
        specification=frozen,
        payload_bytes=size,
        data=metadata,
        expires_at=timezone.now() + timedelta(hours=4),
    )
    audit("request.implementation.claimed", actor, attempt.pk)
    return attempt, claim_context(current, attempt), True


def claim_context(current, attempt):
    return {
        **current,
        "implementation_binding": attempt.data.get("binding"),
        "required_run_id": attempt.data.get("required_run_id", ""),
    }


def check_binding(item, binding, actor, current):
    validate_binding(binding, item.pk)
    if binding["pull_request"]:
        validate_pr_url(binding["pull_request"], current["clone_url"])
    if binding["predecessor_attempt_id"]:
        old = RequestImplementation.objects.get(
            pk=binding["predecessor_attempt_id"], change_request=item, actor=str(actor)
        )
        if (
            old.status != "failed"
            or old.data.get("completion", {}).get("pull_request")
            or {**old.specification, "revision": old.revision + 1} != specification(current)
            or not old.data.get("completion", {}).get("commit_sha")
            or old.data.get("clone_url", current["clone_url"]) != current["clone_url"]
        ):
            raise RequestConflict(
                "Adoption requires this actor's failed committed attempt and unchanged scope at the next revision."
            )


def claim_status(item, attempt_id, actor):
    """Read-only server-clock assessment; never acquire or renew a claim here."""
    attempt = RequestImplementation.objects.get(pk=attempt_id, change_request=item)
    return {
        "attempt_id": str(attempt.pk),
        "request_id": str(attempt.request_id),
        "revision": attempt.revision,
        "specification_digest": digest(attempt.specification),
        "status": attempt.status,
        "expires_at": attempt.expires_at,
        "expired": attempt.expires_at <= timezone.now(),
        "owned_by_caller": attempt.actor == str(actor),
        "binding": attempt.data.get("binding"),
        "required_run_id": attempt.data.get("required_run_id", ""),
        "completion_digest": attempt.data.get("payload_hash", ""),
        "last_rejection": attempt.data.get("last_rejection", {}),
    }


@transaction.atomic
def recover_claim(pk, revision, request_id, predecessor_id, actor, repository_external_id):
    item = locked(pk, repository_external_id)
    predecessor = RequestImplementation.objects.get(
        pk=predecessor_id, change_request=item, actor=str(actor)
    )
    current = context(item)
    if (
        predecessor.status not in {"running", "expired"}
        or predecessor.expires_at > timezone.now()
        or predecessor.revision != revision
        or digest(predecessor.specification) != current["specification_digest"]
        or str(predecessor.request_id) == str(request_id)
        or predecessor.data.get("clone_url", current["clone_url"]) != current["clone_url"]
    ):
        raise RequestConflict(
            "Recovery requires this actor's expired claim and unchanged approved specification."
        )
    existing = RequestImplementation.objects.filter(request_id=request_id).first()
    if existing and existing.data.get("recovery_of") != str(predecessor.pk):
        raise RequestConflict("Recovery identity is already bound to another attempt.")
    attempt, current, created = claim(
        pk, revision, request_id, actor, repository_external_id, predecessor.data.get("binding")
    )
    if created:
        attempt.data = {**attempt.data, "recovery_of": str(predecessor.pk)}
        extra = len(json.dumps(attempt.data).encode())
        storage_budget(extra)
        attempt.payload_bytes += extra
        attempt.save(update_fields=["data", "payload_bytes"])
        audit("request.implementation.recovered", actor, attempt.pk)
    return attempt, current, created


def completion_result(attempt):
    return {
        "attempt_id": str(attempt.pk),
        "request_id": str(attempt.change_request_id),
        "status": attempt.status,
        "comment": attempt.comment,
        "result": attempt.data.get("result", {}),
    }


@transaction.atomic
def resume_claim(
    pk,
    revision,
    request_id,
    predecessor_id,
    actor,
    repository_external_id,
    binding,
    previous_completion_digest,
):
    """Explicit same-actor successor; never revive or edit terminal completion history."""
    item = locked(pk, repository_external_id)
    require_writes()
    old = RequestImplementation.objects.get(
        pk=predecessor_id, change_request=item, actor=str(actor)
    )
    current = context(item)
    if previous_completion_digest and not re.fullmatch(
        r"sha256:[a-f0-9]{64}", previous_completion_digest
    ):
        raise ValidationError("Invalid retained completion digest.")
    intent = {
        "resume_of": str(old.pk),
        "binding": binding,
        "previous_completion_digest": previous_completion_digest,
    }
    existing = RequestImplementation.objects.filter(request_id=request_id).first()
    if existing:
        if (
            existing.actor != str(actor)
            or existing.change_request_id != item.pk
            or existing.data.get("resume_intent") != intent
        ):
            raise RequestConflict("Resume identity belongs to another operation.")
        return claim(pk, revision, request_id, actor, repository_external_id, binding)
    if (
        item.status != "specified"
        or revision != item.revision
        or old.revision != revision
        or old.status not in {"running", "expired"}
        or digest(old.specification) != current["specification_digest"]
        or str(old.request_id) == str(request_id)
        or old.data.get("clone_url", current["clone_url"]) != current["clone_url"]
    ):
        raise RequestConflict(
            "Resume requires an unchanged Specified request and this actor's unfinished claim."
        )
    check_binding(item, binding, actor, current)
    previous_binding = old.data.get("binding")
    if previous_binding and any(
        previous_binding[key] and previous_binding[key] != binding[key] for key in previous_binding
    ):
        raise RequestConflict(
            "Resume cannot change the existing branch, target, adoption or pinned delivery."
        )
    # Legacy claims can be upgraded explicitly, not silently relabelled by complete().
    old.status = "expired" if old.expires_at <= timezone.now() else "cancelled"
    old.save(update_fields=["status"])
    attempt, current, created = claim(
        pk, revision, request_id, actor, repository_external_id, binding
    )
    extra_data = {
        "resume_of": str(old.pk),
        "resume_intent": intent,
        "required_run_id": str(uuid.uuid4()),
    }
    extra = len(json.dumps(extra_data).encode())
    storage_budget(extra)
    attempt.data.update(extra_data)
    attempt.payload_bytes += extra
    attempt.save(update_fields=["data", "payload_bytes"])
    audit("request.implementation.resumed", actor, attempt.pk)
    return attempt, claim_context(current, attempt), created


class CompletionMismatch(ValidationError):
    def __init__(self, checks):
        self.checks = checks
        super().__init__(
            "Success requires the current approved specification, clean verified commit and feature-branch PR."
        )


@transaction.atomic
def record_rejection(pk, attempt_id, actor, details):
    Organization.objects.select_for_update().get(pk=current_tenant())
    attempt = RequestImplementation.objects.filter(
        pk=attempt_id, change_request_id=pk, actor=str(actor), status="running"
    ).first()
    if not attempt:
        return
    previous = len(json.dumps(attempt.data).encode())
    attempt.data["last_rejection"] = {**details, "at": timezone.now().isoformat()}
    delta = len(json.dumps(attempt.data).encode()) - previous
    try:
        storage_budget(delta)
    except ValidationError:
        return  # The console diagnostic is still available when storage is full.
    attempt.payload_bytes += delta
    attempt.save(update_fields=["data", "payload_bytes"])


@transaction.atomic
def complete(pk, attempt_id, body, actor, repository_external_id, *, validate_only=False):
    item = locked(pk, repository_external_id)
    attempt = RequestImplementation.objects.get(
        pk=attempt_id, change_request=item, actor=str(actor)
    )
    if (
        not isinstance(body, dict)
        or set(body)
        not in (
            {"outcome", "comment", "report", "pull_request", "commit_sha", "base_branch"},
            {
                "outcome",
                "comment",
                "report",
                "pull_request",
                "commit_sha",
                "base_branch",
                "handoff",
            },
        )
        or body["outcome"] not in {"succeeded", "failed"}
        or not bounded(body["comment"], 8000)
        or len(json.dumps(body).encode()) > 256 * 1024
        or not all(
            isinstance(body[key], str) for key in ("pull_request", "commit_sha", "base_branch")
        )
    ):
        raise ValidationError("Invalid implementation completion.")
    checksum = digest(body)
    if attempt.status in {"succeeded", "failed"} and attempt.data.get("payload_hash") == checksum:
        return completion_result(attempt)
    require_writes()
    if (
        attempt.status != "running"
        or attempt.expires_at <= timezone.now()
        or item.status != "specified"
        or item.revision != attempt.revision
    ):
        raise RequestConflict("Claim or approved specification changed, expired or was cancelled.")
    current = context(item)
    if digest(attempt.specification) != current["specification_digest"]:
        raise RequestConflict("Approved specification changed; obtain a new accepted claim.")
    if body["pull_request"]:
        validate_pr_url(body["pull_request"], current["clone_url"])
    if body["outcome"] == "succeeded":
        validate_request_report(body["report"])
        checks = completion_checks(
            item,
            attempt.revision,
            digest(attempt.specification),
            body,
            attempt.data.get("binding") or {},
            attempt.data.get("clone_url", current["clone_url"]) == current["clone_url"],
            attempt.data.get("required_run_id", ""),
        )
        if (
            validate_only
            and not body["pull_request"]
            and not (attempt.data.get("binding") or {}).get("pull_request")
        ):
            checks = [key for key in checks if key != "pull_request"]
        if checks:
            raise CompletionMismatch(checks)
        if "handoff" in body:
            proposal = handoffs.checked(item, body["handoff"])
            if proposal["availability"] != "proposed":
                raise ValidationError(
                    "Implementation may propose a contract, not attest deployment availability."
                )
            proposal["implementation"] = {key: body[key] for key in ("commit_sha", "pull_request")}
            item.handoff = proposal
    elif body["report"] is not None:
        raise ValidationError("A failed completion must not carry a success report.")
    elif "handoff" in body:
        raise ValidationError("A failed completion cannot propose a handoff.")
    if validate_only:
        return {"status": "ready_for_delivery", "attempt_id": str(attempt.pk)}
    previous_bytes = item.payload_bytes
    if body["outcome"] == "succeeded":
        item.status = "implemented"
    item.revision += 1
    attempt.status = body["outcome"]
    attempt.comment = body["comment"] + (
        "\nImplemented on feature branch; PR created, not merged or deployed."
        if attempt.status == "succeeded"
        else ""
    )
    attempt.data = {
        **{key: value for key, value in attempt.data.items() if key != "last_rejection"},
        "payload_hash": checksum,
        "completion": body,
        "result": {"status": item.status, "revision": item.revision},
    }
    size = (
        len(json.dumps(attempt.specification).encode())
        + len(json.dumps(attempt.data).encode())
        + len(attempt.comment.encode())
    )
    storage_budget(size - attempt.payload_bytes)
    attempt.payload_bytes = size
    attempt.save(update_fields=["status", "comment", "data", "payload_bytes"])
    _record(item, str(actor), "transitioned", "specified", previous_bytes)
    audit("request.implementation." + attempt.status, actor, attempt.pk)
    return completion_result(attempt)
