"""Workspace-serialized request analysis, human acceptance and immutable history."""

import hashlib
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction

from .contracts import canonical
from .models import ChangeRequest, ChangeRequestActivity, Organization, Repository, RequestHandoff
from .services import audit, require_writes, usage
from .tenancy import current_tenant


class RequestConflict(ValidationError):
    pass


def _record(item, actor, action, previous_status="", previous_bytes=0):
    snapshot = {
        "kind": item.kind,
        "description": item.description,
        "status": item.status,
        "revision": item.revision,
        "repository_external_id": item.repository_external_id,
        "analysis": item.analysis,
        "handoff": item.handoff,
    }
    item.payload_bytes = len(canonical(snapshot).encode("utf-8"))
    history_bytes = len(
        canonical(
            {**snapshot, "actor": actor, "action": action, "previous_status": previous_status}
        ).encode("utf-8")
    )
    limits = require_writes()
    if (
        usage()["storage_bytes"] + item.payload_bytes - previous_bytes + history_bytes
        > limits["storage_mb"] * 1024 * 1024
    ):
        raise ValidationError(
            "Workspace storage allowance reached. Increase capacity before saving more request history."
        )
    item.save()
    ChangeRequestActivity.objects.create(
        change_request=item,
        actor=actor,
        action=action,
        previous_status=previous_status,
        payload_bytes=history_bytes,
        **snapshot,
    )
    audit("request." + action, actor, item.pk)
    return item


@transaction.atomic
def create(*, kind, description, actor, repository_external_id=""):
    Organization.objects.select_for_update().get(pk=current_tenant(), suspended=False)
    require_writes()
    validate_project(repository_external_id)
    item = ChangeRequest(
        organization_id=current_tenant(),
        kind=kind,
        description=description.strip(),
        created_by=actor,
        repository_external_id=repository_external_id,
    )
    item.full_clean()
    return _record(item, actor, "created")


@transaction.atomic
def update(
    pk,
    *,
    revision,
    actor,
    action,
    kind=None,
    description=None,
    status=None,
    repository_external_id=None,
    analysis=None,
    handoff=None,
    handoff_reviewed=False,
):
    Organization.objects.select_for_update().get(pk=current_tenant(), suspended=False)
    item = ChangeRequest.objects.select_for_update().get(pk=pk)
    require_writes()
    if (
        item.status == "closed"
        and action == "transition"
        and status == "closed"
        and handoff_reviewed is True
        and type(revision) is int
        and revision == item.closed_from_revision
        and actor == item.closed_by
    ):
        return item
    if type(revision) is not int or revision != item.revision:
        raise RequestConflict(
            "This request changed since you opened it. Reload the page and review the latest version before saving."
        )
    if item.status in {"closed", "cancelled"}:
        raise ValidationError(f"{item.status.title()} requests are read-only.")
    previous_status, previous_bytes = item.status, item.payload_bytes
    if action == "edit":
        if item.status == "implemented":
            raise ValidationError("Implemented requests can only be closed.")
        if not isinstance(description, str):
            raise ValidationError("Provide a description.")
        item.kind, item.description = kind, description.strip()
        if repository_external_id is not None:
            if (
                repository_external_id != item.repository_external_id
                and RequestHandoff.objects.filter(downstream_request=item).exists()
            ):
                raise ValidationError(
                    "A published handoff stays assigned to its approved target project."
                )
            validate_project(repository_external_id)
            item.repository_external_id = repository_external_id
        item.full_clean()
        if not item.description:
            raise ValidationError("Provide a description.")
        # A repeated save without changes does not add duplicate history.
        if ChangeRequest.objects.filter(
            pk=pk,
            kind=item.kind,
            description=item.description,
            repository_external_id=item.repository_external_id,
        ).exists():
            return item
        event = "edited"
        item.status, item.analysis, item.handoff = "open", {}, {}
    elif action == "cancel":
        if actor != item.created_by:
            raise ValidationError("Only the request creator can cancel it.")
        if item.status not in {"open", "analyzed", "specified"}:
            raise ValidationError("Implemented or closed requests cannot be cancelled.")
        item.status, event = "cancelled", "transitioned"
    elif action == "edit_analysis":
        if item.status not in {"analyzed", "specified"}:
            raise ValidationError("Only analyzed or specified requests have an editable analysis.")
        validate_analysis(analysis)
        if analysis == item.analysis:
            return item
        item.analysis, item.status, item.handoff, event = analysis, "analyzed", {}, "edited"
    elif action == "edit_handoff":
        from .handoffs import checked

        if item.status != "implemented":
            raise ValidationError("Review handoffs after implementation, before closing.")
        proposal = checked(item, handoff)
        if proposal == item.handoff:
            return item
        item.handoff, event = proposal, "edited"
    elif action == "accept":
        if item.status != "analyzed":
            raise ValidationError("Only an analyzed request can be accepted.")
        validate_analysis(item.analysis)
        item.status, event = "specified", "transitioned"
    elif action == "transition":
        if status != item.next_status:
            raise ValidationError("Move to the next workflow stage; stages cannot be skipped.")
        if status in {"analyzed", "specified"}:
            raise ValidationError("Submit an analysis, then explicitly accept it after review.")
        if status == "closed":
            from .handoffs import checked

            if handoff_reviewed is not True:
                raise ValidationError(
                    "Confirm human validation and review of downstream impacts before closing."
                )
            if not item.handoff:
                raise ValidationError(
                    "Save the handoff review first, including a reason when no targets are affected."
                )
            item.handoff = checked(item, item.handoff)
            item.closed_from_revision, item.closed_by = item.revision, actor
        item.status = status
        event = "transitioned"
    else:
        raise ValidationError("Unknown request action.")
    item.revision += 1
    item.analysis_submission_id, item.analysis_submission_digest = None, ""
    # Serialize with claim/completion under the workspace lock already held above.
    from .models import RequestImplementation

    RequestImplementation.objects.filter(change_request=item, status="running").update(
        status="cancelled"
    )
    result = _record(item, actor, event, previous_status, previous_bytes)
    if action == "transition" and status == "closed":
        from .handoffs import publish

        publish(item, actor)
    return result


def validate_project(value):
    if value and not Repository.objects.filter(external_id=value, active=True).exists():
        raise ValidationError("Select an active project in this workspace.")


def validate_analysis(value):
    fields = {
        "project_kind",
        "specification",
        "new_interfaces",
        "changed_interfaces",
        "breaking_changes",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValidationError("Provide the complete structured implementation analysis.")
    if value["project_kind"] not in {"backend", "frontend", "fullstack"}:
        raise ValidationError("Select backend, frontend or fullstack project scope.")
    for key in fields - {"project_kind"}:
        maximum = 64000 if key == "specification" else 16000
        if not isinstance(value[key], str) or not value[key].strip() or len(value[key]) > maximum:
            raise ValidationError(
                f"Provide {key} (maximum {maximum} characters); explain explicitly if none apply."
            )


@transaction.atomic
def submit_analysis(pk, *, revision, actor, repository_external_id, submission_id, analysis):
    Organization.objects.select_for_update().get(pk=current_tenant(), suspended=False)
    item = ChangeRequest.objects.select_for_update().get(pk=pk)
    require_writes()
    validate_analysis(analysis)
    submission_id = uuid.UUID(str(submission_id))
    checksum = hashlib.sha256(
        canonical(
            {
                "revision": revision,
                "actor": actor,
                "repository_external_id": repository_external_id,
                "analysis": analysis,
            }
        ).encode()
    ).hexdigest()
    if item.repository_external_id != repository_external_id or not repository_external_id:
        raise RequestConflict("Request project changed or has not been assigned.")
    validate_project(repository_external_id)
    if item.analysis_submission_id == submission_id:
        if item.analysis_submission_digest != checksum:
            raise RequestConflict("Analysis submission identity conflicts with saved content.")
        return item
    if type(revision) is not int or revision != item.revision or item.status != "open":
        raise RequestConflict(
            "Request was edited, cancelled or already analyzed. Review it again; do not overwrite."
        )
    previous_bytes = item.payload_bytes
    item.analysis, item.status = analysis, "analyzed"
    item.analysis_submission_id, item.analysis_submission_digest = submission_id, checksum
    item.revision += 1
    return _record(item, actor, "transitioned", "open", previous_bytes)
