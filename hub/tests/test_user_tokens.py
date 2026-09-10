import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, RequestFactory
from django.utils import timezone

from hubapp import user_tokens
from hubapp.admin import HubUserAdmin
from hubapp.models import (
    AuditEvent,
    Finding,
    Membership,
    Subscription,
    User,
    UserApiToken,
    UserTokenEvent,
)
from hubapp.services import issue_token
from hubapp.tenancy import current_tenant, tenant_scope


@pytest.fixture
def lead(db):
    return User.objects.create_user(
        username="lead@example.invalid",
        email="lead@example.invalid",
        password="test-lead-password",
        email_verified=True,
        is_tech_lead=True,
    )


@pytest.fixture
def personal(lead):
    return user_tokens.issue(
        lead.pk, name="Architecture", scopes=list(dict(UserApiToken.SCOPES)), days=30
    )


def api(client, token, method, path, data=None, workspace=None):
    headers = {"HTTP_AUTHORIZATION": "Bearer " + token}
    if workspace is not None:
        headers["HTTP_X_WORKSPACE_ID"] = str(workspace)
    return getattr(client, method)(
        path,
        data=json.dumps(data) if data is not None else None,
        content_type="application/json",
        **headers,
    )


def test_personal_token_ui_hash_scopes_and_revoke(client, lead):
    client.force_login(lead)
    response = client.post(
        "/accounts/tokens/",
        {"name": "My lead token", "scopes": ["findings:read"], "days": 2, "confirm": "on"},
    )
    assert response.status_code == 200
    secret = response.context["secret"]
    token = UserApiToken.objects.get()
    assert secret.startswith(str(token.pk) + ".")
    assert token.token_hash != secret.split(".")[1]
    assert token.scopes == ["findings:read"]
    assert secret.encode() not in client.get("/accounts/tokens/").content
    assert UserTokenEvent.objects.get().action == "created"
    assert client.post("/accounts/tokens/", {"revoke": token.pk}).status_code == 302
    token.refresh_from_db()
    assert token.revoked_at
    assert UserTokenEvent.objects.filter(action="revoked").count() == 1
    assert api(client, secret, "get", "/api/v1/workspaces").status_code == 401


def test_non_lead_cannot_issue_or_self_promote(client, owner):
    client.force_login(owner)
    assert b"Create token" not in client.get("/accounts/tokens/").content
    assert (
        client.post(
            "/accounts/tokens/",
            {
                "name": "Bad",
                "scopes": ["workspaces:write"],
                "days": 30,
                "confirm": "on",
                "is_tech_lead": "on",
            },
        ).status_code
        == 403
    )
    with pytest.raises(PermissionDenied):
        user_tokens.issue(owner.pk, name="Bad", scopes=["workspaces:read"], days=30)
    owner.refresh_from_db()
    assert not owner.is_tech_lead and not UserApiToken.objects.exists()


def test_token_ownership_csrf_and_confirmation(client, lead, owner, personal):
    csrf = Client(enforce_csrf_checks=True)
    csrf.force_login(lead)
    assert csrf.post("/accounts/tokens/", {"revoke": personal.split(".")[0]}).status_code == 403
    client.force_login(lead)
    response = client.post(
        "/accounts/tokens/",
        {"name": "Missing confirmation", "scopes": ["workspaces:read"], "days": 30},
    )
    assert response.context["form"].errors
    client.force_login(owner)
    assert client.post("/accounts/tokens/", {"revoke": personal.split(".")[0]}).status_code == 404
    assert not UserApiToken.objects.get().revoked_at


@pytest.mark.parametrize("field", ["is_tech_lead", "is_active", "email_verified"])
def test_account_eligibility_is_checked_on_every_request(client, lead, personal, field):
    assert api(client, personal, "get", "/api/v1/workspaces").status_code == 200
    User.objects.filter(pk=lead.pk).update(**{field: False})
    assert api(client, personal, "get", "/api/v1/workspaces").status_code == 401


def test_expiry_bad_secret_and_invalid_scope(client, lead, personal):
    assert api(client, personal + "x", "get", "/api/v1/workspaces").status_code == 401
    UserApiToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert api(client, personal, "get", "/api/v1/workspaces").status_code == 401
    with pytest.raises(ValidationError):
        user_tokens.issue(lead.pk, name="Bad", scopes=["superuser"], days=30)
    with pytest.raises(ValidationError):
        user_tokens.issue(lead.pk, name="Bad", scopes=["findings:read"], days=366)


def test_tech_lead_manages_foreign_workspace_without_membership(client, lead, org, personal):
    assert not Membership.objects.filter(user=lead, organization=org).exists()
    response = api(client, personal, "get", "/api/v1/workspaces")
    assert response.status_code == 200 and response.json()["results"][0]["id"] == str(org.pk)
    path = f"/api/v1/workspaces/{org.pk}"
    assert api(client, personal, "get", path).json()["revision"] == 0
    response = api(client, personal, "patch", path, {"name": "Renamed workspace", "revision": 0})
    assert response.status_code == 200 and response.json()["revision"] == 1
    assert api(client, personal, "patch", path, {"name": "Stale", "revision": 0}).status_code == 409
    assert (
        api(
            client, personal, "patch", path, {"name": "Bad", "revision": 1, "suspended": False}
        ).status_code
        == 400
    )
    org.refresh_from_db()
    assert org.name == "Renamed workspace"
    assert not Membership.objects.filter(user=lead, organization=org).exists()
    assert not current_tenant()
    with tenant_scope(org.pk):
        assert AuditEvent.objects.filter(action="user_token.workspace_updated").exists()


def test_workspace_creation_and_scopes(client, lead, personal, org):
    response = api(
        client, personal, "post", "/api/v1/workspaces", {"name": "Lead-created workspace"}
    )
    assert response.status_code == 201
    new_id = response.json()["id"]
    assert Membership.objects.get(user=lead, organization_id=new_id).role == "owner"
    read = user_tokens.issue(
        lead.pk, name="Read only", scopes=["workspaces:read", "findings:read"], days=1
    )
    assert api(client, read, "post", "/api/v1/workspaces", {"name": "Denied"}).status_code == 403
    assert (
        api(
            client, read, "patch", f"/api/v1/workspaces/{org.pk}", {"name": "Denied", "revision": 0}
        ).status_code
        == 403
    )
    assert api(client, personal, "delete", f"/api/v1/workspaces/{org.pk}").status_code == 405
    assert (
        api(
            client, personal, "post", "/api/v1/workspaces", {"name": "Denied", "owner": lead.pk}
        ).status_code
        == 400
    )


def test_workspace_selection_and_existing_tokens_fail_closed(client, org, personal):
    assert api(client, personal, "get", "/api/v1/findings").status_code == 400
    assert (
        api(client, personal, "get", "/api/v1/findings", workspace="not-a-uuid").status_code == 400
    )
    assert (
        api(client, personal, "get", "/api/v1/findings", workspace=uuid.uuid4()).status_code == 403
    )
    assert (
        api(
            client, personal, "get", f"/api/v1/workspaces/{org.pk}", workspace=uuid.uuid4()
        ).status_code
        == 403
    )
    with tenant_scope(org.pk):
        token = issue_token("Workspace", ["findings:read"])
    assert api(client, token, "get", "/api/v1/findings").status_code == 200
    assert api(client, token, "get", "/api/v1/findings", workspace=org.pk).status_code == 200
    assert api(client, token, "get", "/api/v1/findings", workspace=uuid.uuid4()).status_code == 403
    assert api(client, token, "get", "/api/v1/workspaces").status_code == 403
    assert (
        api(
            client,
            token,
            "patch",
            f"/api/v1/workspaces/{org.pk}",
            {"name": "Denied", "revision": 0},
        ).status_code
        == 403
    )


def test_foreign_findings_import_read_queue_edit_cancel(client, lead, org, personal, envelope, key):
    response = client.post(
        "/api/v1/review-imports",
        data=json.dumps(envelope),
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer " + personal,
        HTTP_X_WORKSPACE_ID=str(org.pk),
        HTTP_IDEMPOTENCY_KEY=key,
    )
    assert response.status_code == 201
    with tenant_scope(org.pk):
        finding = Finding.objects.filter(lifecycle="open").first()
        revision = finding.implementation_revision
    assert api(client, personal, "get", "/api/v1/findings", workspace=org.pk).json()["count"] == 4
    path = f"/api/v1/findings/{finding.pk}/management"
    another = api(
        client, personal, "post", "/api/v1/workspaces", {"name": "Isolated workspace"}
    ).json()["id"]
    assert (
        api(
            client, personal, "get", f"/api/v1/findings/{finding.pk}", workspace=another
        ).status_code
        == 404
    )
    assert (
        api(
            client,
            personal,
            "patch",
            path,
            {"action": "implement", "revision": revision},
            workspace=another,
        ).status_code
        == 404
    )
    assert api(client, personal, "get", "/api/v1/findings", workspace=another).json()["count"] == 0
    result = api(
        client,
        personal,
        "patch",
        path,
        {"action": "implement", "revision": revision},
        workspace=org.pk,
    )
    assert result.status_code == 200 and result.json()["to_implement"]
    assert (
        api(
            client,
            personal,
            "patch",
            path,
            {
                "action": "edit",
                "revision": revision + 1,
                "remediation": "Add regression tests before changing the startup configuration.",
            },
            workspace=org.pk,
        ).status_code
        == 200
    )
    assert (
        api(
            client,
            personal,
            "patch",
            path,
            {"action": "cancel", "revision": revision + 1},
            workspace=org.pk,
        ).status_code
        == 409
    )
    assert (
        api(
            client,
            personal,
            "patch",
            path,
            {"action": "cancel", "revision": revision + 2},
            workspace=org.pk,
        ).status_code
        == 200
    )
    assert (
        api(
            client, personal, "patch", path, {"lifecycle": "resolved"}, workspace=org.pk
        ).status_code
        == 400
    )
    read = user_tokens.issue(lead.pk, name="Read", scopes=["findings:read"], days=1)
    assert (
        api(
            client, read, "patch", path, {"action": "implement", "revision": 3}, workspace=org.pk
        ).status_code
        == 403
    )
    client.force_login(lead)
    assert (
        client.get(f"/w/{org.pk}/").status_code == 404
    )  # No implicit session membership or admin grant.


def test_subscription_and_suspension_not_bypassed(client, org, personal):
    with tenant_scope(org.pk):
        Subscription.objects.update(status="canceled")
    assert (
        api(
            client,
            personal,
            "patch",
            f"/api/v1/workspaces/{org.pk}",
            {"name": "Denied", "revision": 0},
        ).status_code
        == 403
    )
    assert api(client, personal, "get", "/api/v1/findings", workspace=org.pk).status_code == 200
    org.suspended = True
    org.save()
    assert api(client, personal, "get", "/api/v1/findings", workspace=org.pk).status_code == 403
    assert api(client, personal, "get", "/api/v1/workspaces").json()["count"] == 0


def test_only_superuser_operator_can_grant_and_demotion_revokes(lead, personal, owner):
    admin = HubUserAdmin(User, AdminSite())
    request = RequestFactory().post("/operator/")
    request.user = owner
    assert "is_tech_lead" in admin.get_readonly_fields(request, lead)
    lead.is_tech_lead = False
    with pytest.raises(PermissionDenied):
        admin.save_model(request, lead, SimpleNamespace(), True)
    owner.is_superuser = True
    request.user = owner
    admin.save_model(request, lead, SimpleNamespace(), True)
    assert UserApiToken.objects.get().revoked_at
    assert UserTokenEvent.objects.filter(action="operator_revoked").exists()
