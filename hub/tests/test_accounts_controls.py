import io
from datetime import timedelta

import pytest
from django.core import mail
from django.core.management import call_command
from django.utils import timezone

from hubapp.models import (
    Membership,
    Organization,
    User,
)
from hubapp.services import create_workspace, issue_token
from hubapp.tenancy import tenant_scope


def test_invitation_is_email_bound_single_use_and_does_not_elevate(client, owner, org, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    client.force_login(owner)
    from django.test import TestCase

    with TestCase.captureOnCommitCallbacks(execute=True):
        response = client.post(
            f"/w/{org.id}/members/",
            {"email": "colleague@example.invalid", "role": "reviewer"},
        )
    assert response.status_code == 302
    url = mail.outbox[0].body.splitlines()[-1].replace(settings.PUBLIC_URL, "")
    assert client.post(url).status_code == 403  # Owner isn't the invited address.
    colleague = User.objects.create_user(
        username="colleague@example.invalid",
        email="colleague@example.invalid",
        email_verified=True,
    )
    client.force_login(colleague)
    assert client.get(url).status_code == 200
    assert client.post(url).status_code == 302
    assert Membership.objects.get(user=colleague, organization=org).role == "reviewer"
    assert client.post(url).status_code == 404


def test_member_edit_and_ownership_transfer_are_owner_only(client, owner, org):
    colleague = User.objects.create_user(
        username="colleague", email="colleague@example.invalid", email_verified=True
    )
    member = Membership.objects.create(user=colleague, organization=org, role="viewer")
    client.force_login(owner)
    response = client.post(
        f"/w/{org.id}/members/",
        {"update_member": member.pk, "role": "reviewer", "billing_access": "on"},
    )
    assert response.status_code == 302
    member.refresh_from_db()
    assert member.role == "reviewer" and member.billing_access
    assert (
        client.post(
            f"/w/{org.id}/members/",
            {"transfer_owner": member.pk, "confirm_workspace": "wrong"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/w/{org.id}/members/",
            {"transfer_owner": member.pk, "confirm_workspace": org.name},
        ).status_code
        == 302
    )
    assert Membership.objects.get(user=colleague, organization=org).role == "owner"
    assert Membership.objects.get(user=owner, organization=org).role == "admin"


def test_verified_domain_cannot_open_another_workspace(client, owner, org, settings):
    settings.ALLOWED_HOSTS = ["testserver", "branded.example.invalid"]
    org.custom_domain = "branded.example.invalid"
    org.domain_verified_at = timezone.now()
    org.save()
    other = create_workspace(owner, "Other workspace")
    client.force_login(owner)
    assert client.get(f"/w/{org.id}/", HTTP_HOST=org.custom_domain).status_code == 200
    assert client.get(f"/w/{other.id}/", HTTP_HOST=org.custom_domain).status_code == 404
    assert client.get("/api/v1/findings", HTTP_HOST=org.custom_domain).status_code == 404


def test_erasure_is_dry_run_and_requires_delayed_request(org, owner):
    output = io.StringIO()
    call_command("erase_workspace", str(org.id), operator="test-operator", stdout=output)
    assert "Dry run" in output.getvalue()
    assert Organization.objects.filter(pk=org.pk).exists()
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        call_command(
            "erase_workspace",
            str(org.id),
            operator="test-operator",
            execute=True,
            confirm=str(org.id),
            stdout=output,
        )
    org.deletion_requested_at = timezone.now() - timedelta(days=8)
    org.save()
    call_command(
        "erase_workspace",
        str(org.id),
        operator="test-operator",
        execute=True,
        confirm=str(org.id),
        stdout=output,
    )
    assert not Organization.objects.filter(pk=org.pk).exists()
    assert User.objects.filter(pk=owner.pk).exists()


def test_bounded_json_rejects_duplicates_and_non_finite_numbers(client, org):
    with tenant_scope(org.id):
        token = issue_token("test", ["reviews:write"])
    for raw in ('{"x":1,"x":2}', '{"x":NaN}', "[" * 100 + "0" + "]" * 100):
        response = client.post(
            "/api/v1/review-imports",
            data=raw,
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + token,
        )
        assert response.status_code == 400


def test_password_reset_uses_canonical_origin_not_branded_host(client, owner, org, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.ALLOWED_HOSTS = ["testserver", "brand.example.invalid"]
    org.custom_domain = "brand.example.invalid"
    org.domain_verified_at = timezone.now()
    org.save()
    response = client.post(
        "/accounts/password-reset/", {"email": owner.email}, HTTP_HOST=org.custom_domain
    )
    assert response.status_code == 302
    assert settings.PUBLIC_URL + "/accounts/reset/" in mail.outbox[0].body
    assert "brand.example.invalid" not in mail.outbox[0].body


def test_liveness_does_not_require_database_access(client, monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("Database unavailable")

    monkeypatch.setattr("hubapp.tenancy.set_tenant", unavailable)
    assert client.get("/health/live").status_code == 200


def test_invalid_posted_identifiers_return_not_found_not_server_error(client, owner, org):
    client.force_login(owner)
    assert client.post(f"/w/{org.id}/tokens/", {"revoke": "not-a-uuid"}).status_code == 404
    assert client.post(f"/w/{org.id}/members/", {"remove": "not-an-integer"}).status_code == 404
    assert client.get(f"/w/{org.id}/audit/").status_code == 200
