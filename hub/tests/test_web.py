import json
from datetime import timedelta

import pytest
from django.core import signing
from django.utils import timezone

from hubapp.imports import import_review
from hubapp.models import ApiClient, Finding, Membership, Subscription, User
from hubapp.services import create_workspace, issue_token
from hubapp.tenancy import tenant_scope


def test_api_scopes_revocation_and_tenant_isolation(client, org, owner, envelope, key):
    with tenant_scope(org.id):
        token = issue_token("CI", ["reviews:write", "findings:read"])
    headers = {"HTTP_AUTHORIZATION": "Bearer " + token, "HTTP_IDEMPOTENCY_KEY": key}
    response = client.post(
        "/api/v1/review-imports",
        data=json.dumps(envelope),
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 201, response.content
    assert (
        client.post(
            "/api/v1/review-imports",
            data=json.dumps(envelope),
            content_type="application/json",
            **headers,
        ).status_code
        == 200
    )
    items = client.get("/api/v1/findings", **headers).json()["results"]
    second = create_workspace(owner, "Other tenant")
    with tenant_scope(second.id):
        other = issue_token("reader", ["findings:read"])
    assert (
        client.get(
            "/api/v1/findings/" + items[0]["id"], HTTP_AUTHORIZATION="Bearer " + other
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/review-imports",
            data=json.dumps(envelope),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + other,
        ).status_code
        == 403
    )
    with tenant_scope(org.id):
        ApiClient.objects.update(revoked_at=timezone.now())
    assert client.get("/api/v1/findings", **headers).status_code == 401
    assert client.get("/api/v1/findings").status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "",
        "tokens/",
        "members/",
        "integrations/",
        "deliveries/",
        "billing/",
        "branding/",
        "erasure/",
        "upload/",
    ],
)
def test_dashboard_pages_render(client, owner, org, path):
    client.force_login(owner)
    response = client.get(f"/w/{org.id}/{path}")
    assert response.status_code == 200, response.content
    assert response["Cache-Control"] == "no-store, private"


def test_session_cannot_access_another_tenant(client, owner, org):
    stranger = User.objects.create_user(
        username="stranger", email="stranger@example.invalid", email_verified=True
    )
    other = create_workspace(stranger, "Private")
    client.force_login(owner)
    assert client.get(f"/w/{other.id}/").status_code == 404
    assert client.get(f"/w/{other.id}/export/").status_code == 404


def test_viewer_cannot_manage_tokens_or_import(client, owner, org):
    Membership.objects.filter(user=owner).update(role="viewer", billing_access=False)
    client.force_login(owner)
    for path in ("tokens/", "upload/", "members/", "billing/", "export/"):
        assert client.get(f"/w/{org.id}/{path}").status_code == 403
    assert client.get(f"/w/{org.id}/").status_code == 200


def test_export_scopes_stream_and_remains_available_after_expiry(client, org, owner, envelope, key):
    with tenant_scope(org.id):
        import_review(envelope, key)
        Subscription.objects.update(valid_until=timezone.now() - timedelta(days=1))
    client.force_login(owner)
    response = client.get(f"/w/{org.id}/export/")
    exported = json.loads(b"".join(response.streaming_content))
    assert len(exported["imports"]) == 1


def test_signup_verification_and_login(client, db, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    response = client.post(
        "/accounts/signup/",
        {
            "email": "new@example.invalid",
            "password1": "new-strong-password-4239",
            "password2": "new-strong-password-4239",
        },
    )
    assert response.status_code == 200
    user = User.objects.get(email="new@example.invalid")
    assert not user.is_active and not user.email_verified
    token = signing.dumps({"user": user.pk, "email": user.email}, salt="verify-email")
    assert client.get(f"/accounts/verify/{token}/").status_code == 200
    user.refresh_from_db()
    assert not user.email_verified  # GET cannot consume verification.
    assert client.post(f"/accounts/verify/{token}/").status_code == 302
    user.refresh_from_db()
    assert user.email_verified and user.is_active
    assert (
        client.post(
            "/accounts/login/",
            {"username": user.email, "password": "new-strong-password-4239"},
        ).status_code
        == 302
    )


def test_csrf_protects_customer_writes(owner, org):
    from django.test import Client

    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    assert client.post(f"/w/{org.id}/tokens/", {}).status_code == 403


def test_finding_content_is_escaped(client, org, owner, envelope, key):
    envelope["review"]["findings"][0]["description"] = "<script>alert(1)</script>"
    with tenant_scope(org.id):
        import_review(envelope, key)
        finding = Finding.objects.get(display_id="DEPLOY-001")
    client.force_login(owner)
    response = client.get(f"/w/{org.id}/findings/{finding.id}/")
    assert b"<script>alert(1)</script>" not in response.content
    assert b"&lt;script&gt;" in response.content


def test_bad_webhook_signature_does_not_change_subscription(client, org, settings):
    settings.BILLING_PROVIDER = "stripe"
    settings.STRIPE_WEBHOOK_SECRET = "test-webhook-secret"
    response = client.post(
        "/billing/webhook/",
        data="{}",
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE="bad",
    )
    assert response.status_code == 400
    with tenant_scope(org.id):
        assert Subscription.objects.get().plan == "trial"
