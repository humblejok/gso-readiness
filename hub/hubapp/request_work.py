"""Serialized approved-request claims/completions. The Hub never executes code."""

import json
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
    }


def locked(pk, repository_external_id):
    Organization.objects.select_for_update().get(pk=current_tenant(), suspended=False)
    item = ChangeRequest.objects.select_for_update().get(pk=pk)
    if not repository_external_id or item.repository_external_id != repository_external_id:
        raise RequestConflict("Request project changed; stale workers cannot update it.")
    return item


@transaction.atomic
def claim(pk, revision, request_id, actor, repository_external_id):
    item = locked(pk, repository_external_id)
    require_writes()
    if type(revision) is not int or revision != item.revision or item.status != "specified":
        raise RequestConflict("Only the current user-specified revision can be implemented.")
    current = context(item)
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
        ):
            raise RequestConflict("Claim identity is conflicting, cancelled, expired or completed.")
        return existing, current, False
    running = RequestImplementation.objects.filter(change_request=item, status="running").first()
    if running:
        if running.expires_at > timezone.now():
            raise RequestConflict(
                "Another implementation is running; wait for expiry or release its claim."
            )
        running.status = "expired"
        running.save(update_fields=["status"])
    frozen = specification(current)
    size = len(json.dumps(frozen).encode())
    storage_budget(size)
    attempt = RequestImplementation.objects.create(
        change_request=item,
        request_id=request_id,
        actor=str(actor),
        revision=revision,
        specification=frozen,
        payload_bytes=size,
        expires_at=timezone.now() + timedelta(hours=4),
    )
    audit("request.implementation.claimed", actor, attempt.pk)
    return attempt, current, True


def completion_result(attempt):
    return {
        "attempt_id": str(attempt.pk),
        "request_id": str(attempt.change_request_id),
        "status": attempt.status,
        "comment": attempt.comment,
        "result": attempt.data.get("result", {}),
    }


@transaction.atomic
def complete(pk, attempt_id, body, actor, repository_external_id):
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
        report = validate_request_report(body["report"])
        if (
            report["baseline"]
            != {
                "request_id": str(item.pk),
                "revision": attempt.revision,
                "specification_digest": digest(attempt.specification),
            }
            or report["repository_external_id"] != item.repository_external_id
            or report["result"]["status"] != "satisfied"
            or report["source"]["dirty"]
            or report["source"]["commit_sha"] != body["commit_sha"]
            or report["source"]["branch"] != "feature/" + display_id(item.pk)
            or not bounded(body["base_branch"], 200)
            or body["base_branch"] == report["source"]["branch"]
            or not body["pull_request"]
        ):
            raise ValidationError(
                "Success requires the current approved specification, clean verified commit and feature-branch PR."
            )
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
