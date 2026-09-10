"""Workspace-serialized request editing, forward workflow and immutable history."""

from django.core.exceptions import ValidationError
from django.db import transaction

from .contracts import canonical
from .models import ChangeRequest, ChangeRequestActivity, Organization
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
def create(*, kind, description, actor):
    Organization.objects.select_for_update().get(pk=current_tenant(), suspended=False)
    require_writes()
    item = ChangeRequest(
        organization_id=current_tenant(),
        kind=kind,
        description=description.strip(),
        created_by=actor,
    )
    item.full_clean()
    return _record(item, actor, "created")


@transaction.atomic
def update(pk, *, revision, actor, action, kind=None, description=None, status=None):
    Organization.objects.select_for_update().get(pk=current_tenant(), suspended=False)
    item = ChangeRequest.objects.select_for_update().get(pk=pk)
    require_writes()
    if type(revision) is not int or revision != item.revision:
        raise RequestConflict(
            "This request changed since you opened it. Reload the page and review the latest version before saving."
        )
    if item.status == ChangeRequest.Status.CLOSED:
        raise ValidationError("Closed requests are read-only.")
    previous_status, previous_bytes = item.status, item.payload_bytes
    if action == "edit":
        if not isinstance(description, str):
            raise ValidationError("Provide a description.")
        item.kind, item.description = kind, description.strip()
        item.full_clean()
        if not item.description:
            raise ValidationError("Provide a description.")
        # A repeated save without changes does not add duplicate history.
        if ChangeRequest.objects.filter(
            pk=pk, kind=item.kind, description=item.description
        ).exists():
            return item
        event = "edited"
    elif action == "transition":
        if status != item.next_status:
            raise ValidationError("Move to the next workflow stage; stages cannot be skipped.")
        item.status = status
        event = "transitioned"
    else:
        raise ValidationError("Unknown request action.")
    item.revision += 1
    return _record(item, actor, event, previous_status, previous_bytes)
