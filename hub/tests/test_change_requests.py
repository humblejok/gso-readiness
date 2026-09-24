import copy
import json
import uuid
from datetime import timedelta
from io import StringIO

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db.models import Sum
from django.test import Client
from django.utils import timezone

from hubapp import change_requests
from hubapp.models import (
    AuditEvent,
    ChangeRequest,
    ChangeRequestActivity,
    Membership,
    Repository,
    Subscription,
)
from hubapp.services import create_workspace, usage
from hubapp.tenancy import tenant_scope


@pytest.fixture
def item(org, owner):
    with tenant_scope(org.id):
        Repository.objects.create(organization_id=org.id, external_id="repo:orders", name="Orders")
        return change_requests.create(
            kind="bug",
            description="Login fails after a timeout.",
            actor=owner.username,
            repository_external_id="repo:orders",
        )


def analyze(item, actor):
    return change_requests.submit_analysis(
        item.pk,
        revision=item.revision,
        actor=actor,
        repository_external_id=item.repository_external_id,
        submission_id=str(uuid.uuid4()),
        analysis={
            "project_kind": "backend",
            "specification": "Add regression coverage and correct session handling.",
            "new_interfaces": "None: existing login only.",
            "changed_interfaces": "Login behavior corrected.",
            "breaking_changes": "None: restores the existing contract.",
        },
    )


@pytest.mark.parametrize("kind", ["bug", "feature"])
def test_create_edit_and_history(client, owner, org, kind):
    client.force_login(owner)
    base = f"/w/{org.id}/requests/"
    assert client.get(base).status_code == 200
    assert client.get(base + "new/").status_code == 200
    response = client.post(
        base + "new/", {"kind": kind, "description": "  A useful request.  ", "status": "closed"}
    )
    assert response.status_code == 302
    with tenant_scope(org.id):
        item = ChangeRequest.objects.get()
        assert item.kind == kind and item.status == "open" and item.revision == 1
        assert item.description == "A useful request."
        assert item.created_by == owner.username
    url = base + f"{item.pk}/"
    assert response.url == url
    assert b"/analyse-requests" in client.get(url).content
    response = client.post(
        url,
        {
            "action": "edit",
            "kind": "feature",
            "description": "<script>alert(1)</script>\nUpdated request",
            "revision": 1,
        },
    )
    assert response.status_code == 302
    page = client.get(url)
    assert b"&lt;script&gt;" in page.content and b"<script>alert" not in page.content
    with tenant_scope(org.id):
        item.refresh_from_db()
        assert item.kind == "feature" and item.revision == 2 and item.status == "open"
        assert list(
            ChangeRequestActivity.objects.order_by("revision").values_list("action", flat=True)
        ) == ["created", "edited"]
        assert ChangeRequestActivity.objects.get(revision=1).description == "A useful request."
        assert AuditEvent.objects.filter(action__startswith="request.").count() == 2


def test_workflow_no_skips_backwards_or_closed_edits(client, owner, org, item, no_handoff):
    client.force_login(owner)
    url = f"/w/{org.id}/requests/{item.pk}/"
    response = client.post(url, {"action": "transition", "status": "implemented", "revision": 1})
    assert b"stages cannot be skipped" in response.content
    with tenant_scope(org.id):
        analyze(item, owner.username)
    for revision, status in enumerate(["specified", "implemented", "closed"], start=2):
        if status == "closed":
            with tenant_scope(org.id):
                change_requests.update(
                    item.pk,
                    revision=revision,
                    actor=owner.username,
                    action="edit_handoff",
                    handoff=no_handoff,
                )
            revision += 1
        assert (
            client.post(
                url,
                {
                    "action": "accept" if status == "specified" else "transition",
                    "status": status,
                    "revision": revision,
                    "handoff_reviewed": "on",
                },
            ).status_code
            == 302
        )
    assert b"closed and read-only" in client.get(url).content
    for data in (
        {"action": "edit", "kind": "feature", "description": "Changed", "revision": 6},
        {"action": "transition", "status": "open", "revision": 6, "handoff_reviewed": "on"},
    ):
        assert b"Closed requests are read-only" in client.post(url, data).content
    with tenant_scope(org.id):
        item.refresh_from_db()
        assert item.status == "closed" and item.revision == 6
        assert ChangeRequestActivity.objects.count() == 6
        assert ChangeRequestActivity.objects.get(revision=6).previous_status == "implemented"


def test_stale_forms_and_noop_save(org, owner, item):
    with tenant_scope(org.id):
        change_requests.update(
            item.pk,
            revision=1,
            actor=owner.username,
            action="edit",
            kind=item.kind,
            description=item.description,
        )
        assert ChangeRequestActivity.objects.count() == 1
        analyze(item, owner.username)
        with pytest.raises(change_requests.RequestConflict):
            change_requests.update(
                item.pk,
                revision=1,
                actor=owner.username,
                action="edit",
                kind="feature",
                description="Overwrite",
            )
        with pytest.raises(ValidationError, match="cannot be skipped"):
            change_requests.update(
                item.pk, revision=2, actor=owner.username, action="transition", status="open"
            )
        item.refresh_from_db()
        assert item.description == "Login fails after a timeout."
        assert ChangeRequestActivity.objects.count() == 2


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"kind": "invalid", "description": "Bad type"},
        {"kind": "bug", "description": "   "},
        {"kind": "feature", "description": "x" * 20001},
    ],
)
def test_create_validation(client, owner, org, data):
    client.force_login(owner)
    response = client.post(f"/w/{org.id}/requests/new/", data)
    assert response.status_code == 200 and response.context["form"].errors
    with tenant_scope(org.id):
        assert not ChangeRequest.objects.exists()
        assert not ChangeRequestActivity.objects.exists()


@pytest.mark.parametrize("role", ["owner", "admin", "reviewer", "viewer"])
def test_roles(client, owner, org, item, role):
    Membership.objects.filter(user=owner, organization=org).update(role=role)
    client.force_login(owner)
    base = f"/w/{org.id}/requests/"
    assert client.get(base).status_code == 200
    page = client.get(base + f"{item.pk}/")
    assert page.status_code == 200
    assert (b"Save changes" in page.content) == (role != "viewer")
    create = client.post(base + "new/", {"kind": "feature", "description": "New request"})
    edit = client.post(
        base + f"{item.pk}/",
        {"action": "edit", "kind": "bug", "description": "Edited", "revision": 1},
    )
    assert create.status_code == edit.status_code == (403 if role == "viewer" else 302)


def test_workspace_isolation_and_anonymous_access(client, owner, org, item):
    base = f"/w/{org.id}/requests/"
    assert client.get(base).status_code == 302
    other = create_workspace(owner, "Unrelated workspace")
    client.force_login(owner)
    assert client.get(f"/w/{other.id}/requests/{item.pk}/").status_code == 404
    assert (
        client.post(
            f"/w/{other.id}/requests/{item.pk}/",
            {"action": "transition", "status": "analyzed", "revision": 1},
        ).status_code
        == 404
    )
    assert b"Login fails" not in client.get(f"/w/{other.id}/requests/").content
    with tenant_scope(other.id):
        assert not ChangeRequest.objects.exists() and not ChangeRequestActivity.objects.exists()
    assert not ChangeRequest.objects.exists() and not ChangeRequestActivity.objects.exists()
    Membership.objects.filter(user=owner, organization=org).delete()
    assert client.get(base).status_code == 404


def test_csrf_and_methods(client, owner, org, item):
    csrf = Client(enforce_csrf_checks=True)
    csrf.force_login(owner)
    base = f"/w/{org.id}/requests/"
    assert csrf.post(base + "new/", {"kind": "bug", "description": "Bad"}).status_code == 403
    assert (
        csrf.post(
            base + f"{item.pk}/", {"action": "transition", "status": "analyzed", "revision": 1}
        ).status_code
        == 403
    )
    client.force_login(owner)
    assert (
        client.get(base + f"{item.pk}/?action=transition&status=analyzed&revision=1").status_code
        == 200
    )
    assert client.delete(base + f"{item.pk}/").status_code == 405
    with tenant_scope(org.id):
        item.refresh_from_db()
        assert item.status == "open"


def test_read_only_workspace_and_export(client, owner, org, item):
    other = create_workspace(owner, "Other export")
    with tenant_scope(other.id):
        change_requests.create(kind="feature", description="Foreign secret", actor=owner.username)
    with tenant_scope(org.id):
        Subscription.objects.update(status="canceled")
        with pytest.raises(PermissionDenied):
            change_requests.update(
                item.pk, revision=1, actor=owner.username, action="transition", status="analyzed"
            )
    client.force_login(owner)
    base = f"/w/{org.id}/requests/"
    assert b"New request" not in client.get(base).content
    assert b"Mark analyzed" not in client.get(base + f"{item.pk}/").content
    assert client.post(base + "new/", {"kind": "bug", "description": "New"}).status_code == 403
    assert (
        client.post(
            base + f"{item.pk}/", {"action": "transition", "status": "analyzed", "revision": 1}
        ).status_code
        == 403
    )
    payload = json.loads(b"".join(client.get(f"/w/{org.id}/export/").streaming_content))
    assert len(payload["change_requests"]) == len(payload["change_request_activities"]) == 1
    assert payload["change_requests"][0]["id"] == str(item.pk)
    assert payload["change_request_activities"][0]["description"] == item.description


def test_storage_limit_rolls_back(settings, owner, org, item):
    with tenant_scope(org.id):
        expected = (
            item.payload_bytes
            + ChangeRequestActivity.objects.aggregate(n=Sum("payload_bytes"))["n"]
        )
        assert usage()["storage_bytes"] == expected
        plans = copy.deepcopy(settings.PLANS)
        plans["trial"]["storage_mb"] = 0
        settings.PLANS = plans
        with pytest.raises(ValidationError, match="storage allowance"):
            change_requests.create(kind="bug", description="New", actor=owner.username)
        with pytest.raises(ValidationError, match="storage allowance"):
            analyze(item, owner.username)
        item.refresh_from_db()
        assert item.status == "open" and item.revision == 1
        assert ChangeRequestActivity.objects.count() == ChangeRequest.objects.count() == 1


def test_search_filters_pagination(client, owner, org, item):
    with tenant_scope(org.id):
        for index in range(26):
            change_requests.create(
                kind="feature", description=f"Unicode café {index}", actor=owner.username
            )
    client.force_login(owner)
    base = f"/w/{org.id}/requests/"
    response = client.get(base, {"q": "café", "kind": "feature", "status": "open"})
    assert response.context["rows"].paginator.count == 26
    assert b"kind=feature&status=open" in response.content
    assert len(client.get(base + "?kind=feature&status=open&page=2").context["rows"]) == 1
    assert len(client.get(base + "?kind=bug").context["rows"]) == 1
    assert len(client.get(base + "?status=closed").context["rows"]) == 0


def test_erasure_includes_requests(owner, org, item):
    org.deletion_requested_at = timezone.now() - timedelta(days=8)
    org.save()
    output = StringIO()
    call_command("erase_workspace", str(org.id), operator="test", stdout=output)
    assert "'requests': 1" in output.getvalue()
    call_command(
        "erase_workspace",
        str(org.id),
        operator="test",
        execute=True,
        confirm=str(org.id),
        stdout=output,
    )
    with tenant_scope(org.id):
        assert not ChangeRequest.objects.exists() and not ChangeRequestActivity.objects.exists()
