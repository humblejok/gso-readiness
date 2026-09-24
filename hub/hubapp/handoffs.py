"""Human-approved multi-target publication, serialized with request closure."""

import copy

from django.core.exceptions import ValidationError
from django.db import transaction

from .contracts import canonical
from .git_host_contract import validate_pr_url
from .handoff_contract import validate_handoff
from .models import (
    Organization,
    ProjectRelationship,
    Repository,
    RequestHandoff,
    RequestImplementation,
)
from .services import audit, require_writes, usage
from .tenancy import current_tenant


def relationships(external_id):
    return [
        {
            "repository_external_id": link.target.external_id,
            "name": link.target.name,
            "description": link.description,
        }
        for link in ProjectRelationship.objects.filter(
            source__external_id=external_id, source__active=True, target__active=True
        )
        .select_related("target")
        .order_by("target__external_id")
    ]


def incoming(item):
    link = RequestHandoff.objects.filter(downstream_request=item).first()
    return copy.deepcopy(link.snapshot) if link else None


@transaction.atomic
def configure_relationship(source_id, target_id, description, actor, remove=False):
    Organization.objects.select_for_update().get(pk=current_tenant(), suspended=False)
    limits = require_writes()
    source = Repository.objects.get(pk=source_id)
    target = Repository.objects.get(pk=target_id)
    if source == target:
        raise ValidationError("A project cannot consume itself.")
    query = ProjectRelationship.objects.filter(source=source, target=target)
    if remove:
        query.delete()
    else:
        if not source.active or not target.active:
            raise ValidationError("Select active projects.")
        if not isinstance(description, str) or not description.strip() or len(description) > 1000:
            raise ValidationError("Describe the relationship (maximum 1,000 characters).")
        previous = query.first()
        delta = (
            len(description) * 4 + 256 - (len(previous.description) * 4 + 256 if previous else 0)
        )
        if usage()["storage_bytes"] + delta > limits["storage_mb"] * 1024 * 1024:
            raise ValidationError("Workspace storage allowance reached.")
        if not previous and query.model.objects.filter(source=source).count() >= 20:
            raise ValidationError("A project supports at most 20 consumer relationships.")
        ProjectRelationship.objects.update_or_create(
            organization_id=current_tenant(),
            source=source,
            target=target,
            defaults={"description": description.strip()},
        )
    audit(
        "project.relationship.removed" if remove else "project.relationship.saved",
        actor,
        f"{source.pk}->{target.pk}",
    )


def checked(item, proposal):
    if (
        item.repository_external_id
        and not Repository.objects.filter(
            external_id=item.repository_external_id, active=True
        ).exists()
    ):
        raise ValidationError("The source project must be active before approving a handoff.")
    try:
        validate_handoff(proposal)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    allowed = {row["repository_external_id"] for row in relationships(item.repository_external_id)}
    if any(row["repository_external_id"] not in allowed for row in proposal["targets"]):
        raise ValidationError(
            "Every target must be an active configured consumer in this workspace."
        )
    # Published source references cannot be silently changed when a verified attempt exists.
    attempt = (
        RequestImplementation.objects.filter(change_request=item, status="succeeded")
        .order_by("-created_at")
        .first()
    )
    reference = proposal.get("implementation", {})
    if attempt:
        actual = {key: attempt.data["completion"][key] for key in ("commit_sha", "pull_request")}
        if reference and reference != actual:
            raise ValidationError("Handoff reference must match the verified implementation.")
        proposal = {**proposal, "implementation": actual}
    elif reference.get("pull_request"):
        repo = Repository.objects.get(external_id=item.repository_external_id, active=True)
        try:
            validate_pr_url(reference["pull_request"], repo.clone_url)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
    return copy.deepcopy(proposal)


def publish(item, actor):
    """Called only under closure's workspace lock and transaction; all targets or none."""
    from .change_requests import create

    proposal = checked(item, item.handoff)
    reference = proposal.get("implementation", {})
    if proposal["targets"] and not (reference.get("commit_sha") and reference.get("pull_request")):
        raise ValidationError("Downstream handoffs require the exact implementation commit and PR.")
    limits = require_writes()
    for target in proposal["targets"]:
        repo = Repository.objects.get(external_id=target["repository_external_id"], active=True)
        # The closure retry guard is the first defense; this unique key is the second.
        if RequestHandoff.objects.filter(source_request=item, target=repo).exists():
            raise ValidationError("Handoff already published; do not republish or overwrite it.")
        snapshot = {
            "source_request_id": str(item.pk),
            "source_revision": item.revision,
            "source_repository_external_id": item.repository_external_id,
            "source_description": item.description,
            "implementation": reference,
            **{
                key: proposal[key]
                for key in (
                    "summary",
                    "contract",
                    "compatibility",
                    "availability",
                    "availability_details",
                )
            },
            "target": target,
            "approved_by": actor,
        }
        size = len(canonical(snapshot).encode())
        if usage()["storage_bytes"] + size > limits["storage_mb"] * 1024 * 1024:
            raise ValidationError("Workspace storage allowance reached; no handoffs published.")
        child = create(
            kind=item.kind,
            description=f"Handoff from request {item.pk} ({item.repository_external_id})\n\n{proposal['summary']}\n\nRequired work:\n{target['requirements']}",
            actor=actor,
            repository_external_id=repo.external_id,
        )
        # Account for child and its history before inserting the shared snapshot.
        if usage()["storage_bytes"] + size > limits["storage_mb"] * 1024 * 1024:
            raise ValidationError("Workspace storage allowance reached; no handoffs published.")
        RequestHandoff.objects.create(
            source_request=item,
            target=repo,
            downstream_request=child,
            approved_by=actor,
            snapshot=snapshot,
            payload_bytes=size,
        )
        audit("request.handoff.published", actor, child.pk)
