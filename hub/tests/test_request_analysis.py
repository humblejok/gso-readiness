import copy
import uuid

import pytest
from django.core.exceptions import ValidationError

from hubapp import change_requests, user_tokens
from hubapp.models import ChangeRequestActivity, Membership, Repository, Subscription, User
from hubapp.services import create_workspace, issue_token
from hubapp.tenancy import tenant_scope


@pytest.fixture
def analysis():
    return {
        "project_kind": "backend",
        "specification": "Add an export controller with authorization and regression tests.",
        "new_interfaces": "POST /exports, admin only, returns 202 and job ID.",
        "changed_interfaces": "None: additive endpoint.",
        "breaking_changes": "None: existing consumers unchanged.",
    }


@pytest.fixture
def item(org, owner):
    with tenant_scope(org.id):
        Repository.objects.create(organization_id=org.id, external_id="repo:orders", name="Orders")
        return change_requests.create(
            kind="feature",
            description="Export orders",
            actor=owner.username,
            repository_external_id="repo:orders",
        )


def token(org, scopes=("requests:read", "requests:analyse"), repository="repo:orders"):
    with tenant_scope(org.id):
        raw = issue_token("Request analyzer", list(scopes), repository)
    return {"HTTP_AUTHORIZATION": "Bearer " + raw}


def body(item, analysis):
    return {
        "revision": item.revision,
        "repository_external_id": item.repository_external_id,
        "submission_id": str(uuid.uuid4()),
        "analysis": analysis,
    }


def post(client, item, data, headers):
    return client.post(
        f"/api/v1/requests/{item.pk}/analysis", data, content_type="application/json", **headers
    )


def test_analysis_then_human_edit_accept_and_reaccept(client, owner, org, item, analysis):
    headers = token(org)
    data = body(item, analysis)
    response = post(client, item, data, headers)
    assert response.status_code == 200 and response.json()["status"] == "analyzed"
    assert post(client, item, data, headers).status_code == 200
    with tenant_scope(org.id):
        assert ChangeRequestActivity.objects.count() == 2  # exact retry adds nothing
    client.force_login(owner)
    url = f"/w/{org.id}/requests/{item.pk}/"
    assert b"Accept analysis" in client.get(url).content
    edited = {
        **analysis,
        "specification": "<script>unsafe</script>\nReviewed design",
        "revision": 2,
        "action": "edit_analysis",
    }
    assert client.post(url, edited).status_code == 302
    assert b"&lt;script&gt;" in client.get(url).content
    assert b"<script>unsafe" not in client.get(url).content
    assert (
        client.post(url, {"action": "accept", "status": "specified", "revision": 2}).status_code
        == 200
    )
    assert (
        client.post(url, {"action": "accept", "status": "specified", "revision": 3}).status_code
        == 302
    )
    edited.update(revision=4, specification="Further changes require review again")
    assert client.post(url, edited).status_code == 302
    with tenant_scope(org.id):
        item.refresh_from_db()
        assert item.status == "analyzed" and item.revision == 5
        assert ChangeRequestActivity.objects.get(revision=2).analysis == analysis
    exported = client.get(f"/w/{org.id}/export/")
    assert b"Further changes require review again" in b"".join(exported.streaming_content)
    assert post(client, item, data, headers).status_code == 409


@pytest.mark.parametrize("stage", ["open", "analyzed", "specified"])
def test_creator_can_cancel_and_stale_analysis_cannot_win(
    client, owner, org, item, analysis, stage
):
    data, headers = body(item, analysis), token(org)
    with tenant_scope(org.id):
        if stage != "open":
            item = change_requests.submit_analysis(item.pk, actor="analyzer", **data)
        if stage == "specified":
            item = change_requests.update(
                item.pk, revision=item.revision, actor=owner.username, action="accept"
            )
    client.force_login(owner)
    url = f"/w/{org.id}/requests/{item.pk}/"
    assert b"Cancel request" in client.get(url).content
    assert (
        client.post(
            url, {"action": "cancel", "status": "cancelled", "revision": item.revision}
        ).status_code
        == 302
    )
    assert post(client, item, data, headers).status_code == 409
    with tenant_scope(org.id):
        item.refresh_from_db()
        assert item.status == "cancelled" and item.next_status is None


def test_cancellation_ownership_and_terminal_limits(client, owner, org, item, analysis):
    other = User.objects.create_user(username="reviewer@example.invalid", email_verified=True)
    Membership.objects.create(user=other, organization=org, role="admin")
    client.force_login(other)
    url = f"/w/{org.id}/requests/{item.pk}/"
    assert b"Cancel request" not in client.get(url).content
    assert (
        client.post(url, {"action": "cancel", "status": "cancelled", "revision": 1}).status_code
        == 403
    )
    with tenant_scope(org.id):
        item = change_requests.submit_analysis(item.pk, actor="analyzer", **body(item, analysis))
        item = change_requests.update(item.pk, revision=2, actor=owner.username, action="accept")
        item = change_requests.update(
            item.pk, revision=3, actor=owner.username, action="transition", status="implemented"
        )
        with pytest.raises(ValidationError, match="cannot be cancelled"):
            change_requests.update(item.pk, revision=4, actor=owner.username, action="cancel")
        item = change_requests.update(
            item.pk, revision=4, actor=owner.username, action="transition", status="closed"
        )
        with pytest.raises(ValidationError, match="read-only"):
            change_requests.update(item.pk, revision=5, actor=owner.username, action="cancel")


def test_project_or_description_edits_require_fresh_analysis(org, owner, item, analysis):
    with tenant_scope(org.id):
        data = body(item, analysis)
        item = change_requests.submit_analysis(item.pk, actor="analyzer", **data)
        item = change_requests.update(
            item.pk,
            revision=2,
            actor=owner.username,
            action="edit",
            kind="feature",
            description="Export different data",
        )
        assert item.status == "open" and item.analysis == {}
        with pytest.raises(change_requests.RequestConflict):
            change_requests.submit_analysis(item.pk, actor="analyzer", **data)
        assert ChangeRequestActivity.objects.get(revision=2).analysis == analysis


def test_api_queue_scope_filter_and_no_human_transitions(client, owner, org, item, analysis):
    headers = token(org)
    url = "/api/v1/requests"
    query = {"repository_external_id": "repo:orders", "ids": str(item.pk)}
    assert client.get(url, query, **headers).json()["count"] == 1
    assert client.get(url, {**query, "ids": str(uuid.uuid4())}, **headers).json()["count"] == 0
    assert client.get(url, {**query, "ids": "not-an-id"}, **headers).status_code == 400
    assert (
        client.get(url, {**query, "repository_external_id": "repo:other"}, **headers).json()[
            "count"
        ]
        == 0
    )
    denied = token(org, repository="repo:other")
    assert post(client, item, body(item, analysis), denied).status_code == 404
    for scopes in (
        ("requests:read",),
        ("requests:analyse",),
        ("findings:read", "findings:implement"),
    ):
        assert post(client, item, body(item, analysis), token(org, scopes)).status_code == 403
    assert (
        post(client, item, {**body(item, analysis), "status": "specified"}, headers).status_code
        == 400
    )
    other_org = create_workspace(owner, "Other tenant")
    assert post(client, item, body(item, analysis), token(other_org)).status_code == 404


@pytest.mark.parametrize(
    "field",
    ["project_kind", "specification", "new_interfaces", "changed_interfaces", "breaking_changes"],
)
def test_analysis_contract_requires_every_section(client, org, item, analysis, field):
    invalid = copy.deepcopy(analysis)
    del invalid[field]
    assert post(client, item, body(item, invalid), token(org)).status_code == 400
    with tenant_scope(org.id):
        item.refresh_from_db()
        assert item.status == "open" and item.revision == 1


def test_competing_analysis_and_conflicting_replay(client, org, item, analysis):
    headers, data = token(org), body(item, analysis)
    assert post(client, item, data, headers).status_code == 200
    assert (
        post(client, item, {**data, "submission_id": str(uuid.uuid4())}, headers).status_code == 409
    )
    assert (
        post(
            client, item, {**data, "analysis": {**analysis, "specification": "Override"}}, headers
        ).status_code
        == 409
    )


def test_manual_stage_bypass_and_unassigned_request_are_rejected(client, owner, org, analysis):
    with tenant_scope(org.id):
        item = change_requests.create(kind="bug", description="Unassigned", actor=owner.username)
        with pytest.raises(ValidationError, match="Submit an analysis"):
            change_requests.update(
                item.pk, revision=1, actor=owner.username, action="transition", status="analyzed"
            )
        with pytest.raises(change_requests.RequestConflict):
            change_requests.submit_analysis(item.pk, actor="analyzer", **body(item, analysis))
    client.force_login(owner)
    page = client.get(f"/w/{org.id}/requests/{item.pk}/")
    assert b"Not assigned" in page.content


def test_personal_token_uses_explicit_workspace_and_read_only_blocks_analysis(
    client, org, item, analysis
):
    lead = User.objects.create_user(
        username="lead@example.invalid", email_verified=True, is_tech_lead=True
    )
    raw = user_tokens.issue(
        lead.pk, name="Analysis", scopes=["requests:read", "requests:analyse"], days=30
    )
    headers = {"HTTP_AUTHORIZATION": "Bearer " + raw}
    data = body(item, analysis)
    assert post(client, item, data, headers).status_code == 400
    headers["HTTP_X_WORKSPACE_ID"] = str(org.pk)
    assert post(client, item, data, headers).status_code == 200
    with tenant_scope(org.id):
        Subscription.objects.update(status="canceled")
    assert post(client, item, data, headers).status_code == 403


def test_cancel_and_accept_require_csrf_and_readers_cannot_accept(
    client, owner, org, item, analysis
):
    from django.test import Client

    headers = token(org)
    assert post(client, item, body(item, analysis), headers).status_code == 200
    url = f"/w/{org.id}/requests/{item.pk}/"
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(owner)
    for action, status in (("accept", "specified"), ("cancel", "cancelled")):
        assert (
            strict.post(url, {"action": action, "status": status, "revision": 2}).status_code == 403
        )
    Membership.objects.filter(user=owner, organization=org).update(role="viewer")
    client.force_login(owner)
    assert (
        client.post(url, {"action": "accept", "status": "specified", "revision": 2}).status_code
        == 403
    )
    assert (
        client.post(url, {"action": "cancel", "status": "cancelled", "revision": 2}).status_code
        == 302
    )
