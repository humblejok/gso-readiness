import copy
import json
from datetime import timedelta
from io import StringIO
from unittest import mock

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from hubapp import change_requests, handoffs
from hubapp.models import (
    ChangeRequest,
    ChangeRequestActivity,
    Membership,
    ProjectRelationship,
    Repository,
    RequestHandoff,
    User,
)
from hubapp.services import create_workspace, issue_token
from hubapp.tenancy import tenant_scope


@pytest.fixture
def projects(org, owner):
    with tenant_scope(org.pk):
        rows = [
            Repository.objects.create(
                name=name, external_id="repo:" + name, clone_url="https://github.com/team/" + name
            )
            for name in ("backend", "vue", "mobile")
        ]
        for target in rows[1:]:
            handoffs.configure_relationship(
                rows[0].pk, target.pk, "Consumes the orders API", owner.username
            )
        return rows


@pytest.fixture
def proposal():
    return {
        "summary": "Allow authorized users to approve orders.",
        "contract": "POST /orders/{id}/approve, admin permission; returns state; 409 on concurrent change.",
        "compatibility": "Additive endpoint. Existing clients remain compatible.",
        "availability": "proposed",
        "availability_details": "PR only, not merged or deployed.",
        "implementation": {
            "commit_sha": "a" * 40,
            "pull_request": "https://github.com/team/backend/pull/7",
        },
        "targets": [
            {
                "repository_external_id": "repo:" + name,
                "requirements": f"Add an approval control in {name}.",
                "acceptance_criteria": "Authorized action refreshes order; denied and conflict responses are handled.",
            }
            for name in ("vue", "mobile")
        ],
    }


@pytest.fixture
def implemented(org, owner, projects):
    with tenant_scope(org.pk):
        # Manual implementation tracking is supported; publication still needs a human-supplied commit/PR.
        item = change_requests.create(
            kind="feature",
            description="Approve orders",
            actor=owner.username,
            repository_external_id=projects[0].external_id,
        )
        ChangeRequest.objects.filter(pk=item.pk).update(status="implemented")
        item.refresh_from_db()
        return item


def save(item, owner, proposal):
    return change_requests.update(
        item.pk,
        revision=item.revision,
        actor=owner.username,
        action="edit_handoff",
        handoff=proposal,
    )


def close(item, owner, **overrides):
    return change_requests.update(
        item.pk,
        **{
            "revision": item.revision,
            "actor": owner.username,
            "action": "transition",
            "status": "closed",
            "handoff_reviewed": True,
            **overrides,
        },
    )


def test_multiple_targets_only_publish_on_confirmed_close_and_retry_is_idempotent(
    org, owner, implemented, proposal
):
    with tenant_scope(org.pk):
        item = save(implemented, owner, proposal)
        assert RequestHandoff.objects.count() == 0
        with pytest.raises(ValidationError, match="Confirm human"):
            close(item, owner, handoff_reviewed=False)
        before = item.revision
        result = close(item, owner)
        assert result.status == "closed"
        assert close(item, owner).revision == result.revision
        links = list(
            RequestHandoff.objects.select_related("downstream_request").order_by(
                "target__external_id"
            )
        )
        assert len(links) == 2
        for link in links:
            child = link.downstream_request
            assert child.status == "open" and child.analysis == {} and child.handoff == {}
            assert child.repository_external_id == link.target.external_id
            assert link.snapshot["source_revision"] == before + 1
            assert link.snapshot["implementation"] == proposal["implementation"]
            assert link.snapshot["target"]["repository_external_id"] == child.repository_external_id
            assert link.snapshot["availability"] == "proposed"
        assert (
            ChangeRequestActivity.objects.filter(change_request=item, status="closed").count() == 1
        )
        with pytest.raises(ValidationError):
            close(item, owner, actor="different@example.invalid")
        with pytest.raises(ValidationError):
            save(result, owner, proposal)


def test_selected_targets_and_no_impact_are_explicit(org, owner, implemented, proposal):
    with tenant_scope(org.pk):
        with pytest.raises(ValidationError, match="Save the handoff review"):
            close(implemented, owner)
        proposal["targets"] = []
        proposal.pop("implementation")
        proposal["summary"] = "Internal cleanup only; no contracts or consumer behavior changed."
        result = close(save(implemented, owner, proposal), owner)
        assert result.status == "closed" and not RequestHandoff.objects.exists()


def test_cancel_never_publishes(org, owner, implemented, proposal):
    with tenant_scope(org.pk):
        ChangeRequest.objects.filter(pk=implemented.pk).update(status="specified", handoff=proposal)
        result = change_requests.update(
            implemented.pk, revision=implemented.revision, actor=owner.username, action="cancel"
        )
        assert result.status == "cancelled" and not RequestHandoff.objects.exists()
        with pytest.raises(ValidationError):
            close(result, owner)


@pytest.mark.parametrize(
    "failure",
    [
        "missing_relationship",
        "inactive_target",
        "self",
        "foreign",
        "duplicate",
        "no_reference",
        "foreign_pr",
    ],
)
def test_invalid_target_or_reference_blocks_all_publication(
    org, owner, implemented, proposal, projects, failure
):
    with tenant_scope(org.pk):
        item = save(implemented, owner, proposal)
        if failure == "missing_relationship":
            ProjectRelationship.objects.filter(target=projects[2]).delete()
        elif failure == "inactive_target":
            Repository.objects.filter(pk=projects[2].pk).update(active=False)
        else:
            changed = copy.deepcopy(proposal)
            if failure == "self":
                changed["targets"][0]["repository_external_id"] = "repo:backend"
            if failure == "foreign":
                changed["targets"][0]["repository_external_id"] = "repo:foreign"
            if failure == "duplicate":
                changed["targets"].append(changed["targets"][0])
            if failure == "no_reference":
                changed.pop("implementation")
            if failure == "foreign_pr":
                changed["implementation"]["pull_request"] = "https://github.com/foreign/repo/pull/1"
            if failure in {"no_reference"}:
                item = save(item, owner, changed)
            else:
                with pytest.raises(ValidationError):
                    save(item, owner, changed)
                return
        with pytest.raises(ValidationError):
            close(item, owner)
        item.refresh_from_db()
        assert item.status == "implemented"
        assert not RequestHandoff.objects.exists()


def test_atomic_rollback_and_stale_review(org, owner, implemented, proposal):
    with tenant_scope(org.pk):
        item = save(implemented, owner, proposal)
        stale = item.revision
        item = save(item, owner, {**proposal, "summary": "Reviewed feature wording"})
        with pytest.raises(change_requests.RequestConflict):
            close(item, owner, revision=stale)
        original = change_requests.create

        def fail_second(**kwargs):
            if kwargs["repository_external_id"] == "repo:mobile":
                raise ValidationError("Simulated quota failure")
            return original(**kwargs)

        with (
            mock.patch.object(change_requests, "create", side_effect=fail_second),
            pytest.raises(ValidationError),
        ):
            close(item, owner)
        item.refresh_from_db()
        assert item.status == "implemented"
        assert ChangeRequest.objects.count() == 1
        assert not RequestHandoff.objects.exists()
        assert not ChangeRequestActivity.objects.filter(status="closed").exists()


def test_ui_edit_select_close_and_downstream_visibility(client, org, owner, implemented, proposal):
    client.force_login(owner)
    url = f"/w/{org.pk}/requests/{implemented.pk}/"
    page = client.get(url)
    assert b"Close and publish approved handoffs" in page.content
    fields = {
        key: value for key, value in proposal.items() if key not in {"targets", "implementation"}
    }
    fields.update(proposal["implementation"])
    fields.update(
        action="edit_handoff",
        revision=implemented.revision,
        **{"targets-TOTAL_FORMS": 2, "targets-INITIAL_FORMS": 2},
    )
    for index, target in enumerate(proposal["targets"]):
        fields.update({f"targets-{index}-{key}": value for key, value in target.items()})
    fields["targets-0-selected"] = "on"  # only Vue approved
    assert client.post(url, fields).status_code == 302
    with tenant_scope(org.pk):
        implemented.refresh_from_db()
    closing = {"action": "transition", "revision": implemented.revision, "status": "closed"}
    assert client.post(url, closing).status_code == 200  # checkbox missing
    assert client.post(url, {**closing, "handoff_reviewed": "on"}).status_code == 302
    assert client.post(url, {**closing, "handoff_reviewed": "on"}).status_code == 302
    with tenant_scope(org.pk):
        link = RequestHandoff.objects.get()
        child = link.downstream_request
        assert child.repository_external_id == "repo:vue"
        with pytest.raises(ValidationError, match="approved target"):
            change_requests.update(
                child.pk,
                revision=1,
                actor=owner.username,
                action="edit",
                description="Move work",
                kind="feature",
                repository_external_id="repo:mobile",
            )
    assert b"repo:vue" in client.get(url).content or b"vue" in client.get(url).content
    assert b"Approved upstream handoff" in client.get(f"/w/{org.pk}/requests/{child.pk}/").content
    exported = json.loads(b"".join(client.get(f"/w/{org.pk}/export/").streaming_content))
    assert isinstance(exported["request_handoffs"][0]["snapshot"], dict)
    assert len(exported["project_relationships"]) == 2


def test_relationship_permissions_tenant_scope_csrf_and_token_sharing(
    client, org, owner, implemented, proposal, projects
):
    other = create_workspace(owner, "Other workspace")
    with tenant_scope(other.pk):
        foreign = Repository.objects.create(name="Foreign", external_id="repo:foreign")
    client.force_login(owner)
    url = f"/w/{org.pk}/project-relationships/"
    assert client.get(url).status_code == 200
    assert (
        client.post(
            url, {"source": projects[0].pk, "target": foreign.pk, "description": "Forbidden"}
        ).status_code
        == 200
    )
    with tenant_scope(org.pk):
        assert ProjectRelationship.objects.count() == 2
        close(save(implemented, owner, proposal), owner)
        child = RequestHandoff.objects.get(target=projects[1]).downstream_request
        raw = issue_token("Vue analyzer", ["requests:read", "requests:analyse"], "repo:vue")
    headers = {"HTTP_AUTHORIZATION": "Bearer " + raw}
    context = client.get(f"/api/v1/requests/{child.pk}/analysis", **headers)
    assert context.status_code == 200
    assert context.json()["upstream_handoff"]["contract"] == proposal["contract"]
    assert client.get(f"/api/v1/requests/{implemented.pk}/analysis", **headers).status_code == 404
    viewer = User.objects.create_user(username="viewer@example.invalid", email_verified=True)
    Membership.objects.create(user=viewer, organization=org, role="viewer")
    client.force_login(viewer)
    assert client.get(url).status_code == 403
    assert (
        client.post(f"/w/{org.pk}/requests/{child.pk}/", {"action": "edit_handoff"}).status_code
        == 403
    )
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(owner)
    assert (
        csrf_client.post(
            url, {"source": projects[0].pk, "target": projects[1].pk, "action": "remove"}
        ).status_code
        == 403
    )


def test_handoffs_and_relationships_erased_with_workspace(org, owner, implemented, proposal):
    with tenant_scope(org.pk):
        close(save(implemented, owner, proposal), owner)
    org.deletion_requested_at = timezone.now() - timedelta(days=8)
    org.save(update_fields=["deletion_requested_at"])
    call_command(
        "erase_workspace",
        str(org.pk),
        execute=True,
        confirm=str(org.pk),
        operator="test",
        stdout=StringIO(),
    )
    assert not RequestHandoff.all_objects.filter(organization_id=org.pk).exists()
    assert not ProjectRelationship.all_objects.filter(organization_id=org.pk).exists()
