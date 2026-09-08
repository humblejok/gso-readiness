import copy
from datetime import timedelta
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ValidationError
from django.utils import timezone

from hubapp.billing import receive_webhook, reconcile_subscription
from hubapp.imports import import_review
from hubapp.jira import (
    DeliveryError,
    JiraClient,
    cipher,
    encrypt_secret,
    process_one,
    validate_url,
)
from hubapp.models import (
    BillingEvent,
    DeliveryAttempt,
    JiraAssociation,
    JiraBinding,
    JiraConnection,
    OutboxEvent,
    Repository,
    Subscription,
)
from hubapp.tenancy import tenant_scope


@pytest.fixture
def jira_setup(org, envelope, key, settings):
    settings.SECRET_ENCRYPTION_KEYS = [Fernet.generate_key().decode()]
    settings.OUTBOUND_INTEGRATIONS_ENABLED = True
    settings.JIRA_ALLOWED_HOSTS = {"jira.example.invalid"}
    with tenant_scope(org.id):
        import_review(envelope, key)
        connection = JiraConnection.objects.create(
            name="Corporate",
            base_url="https://jira.example.invalid",
            encrypted_token=encrypt_secret("test-pat"),
            enabled=True,
        )
        JiraBinding.objects.create(
            repository=Repository.objects.get(),
            connection=connection,
            project_key="DEMO",
        )
        next_envelope = copy.deepcopy(envelope)
        next_envelope["review"]["run"]["run_id"] = "with-jira"
        import_review(next_envelope, "repo:orders:with-jira")
        assert OutboxEvent.objects.count() == 2  # Supported findings only.
    return connection


def test_jira_create_then_only_managed_comment_updates(org, jira_setup, monkeypatch):
    calls = []

    def transport(self, method, path, payload=None):
        calls.append((method, path, payload))
        if path == "issue":
            return {"id": "101", "key": "DEMO-1"}
        if path.endswith("/comment"):
            return {"id": "201"}
        return {}

    monkeypatch.setattr(JiraClient, "request", transport)
    with tenant_scope(org.id):
        assert process_one(org)
        assert JiraAssociation.objects.count() == 1
        assert sum(method == "POST" and path == "issue" for method, path, _ in calls) == 1
        assert not any(method == "PUT" and path == "issue/101" for method, path, _ in calls)
        event = OutboxEvent.objects.filter(state="done").first()
        from hubapp.jira import deliver

        assert deliver(event) == "noop"
        event.observation.data = {
            **event.observation.data,
            "description": "Updated description",
        }
        deliver(event)
        assert any(method == "PUT" and path == "issue/101/comment/201" for method, path, _ in calls)
        assert not any(method == "PUT" and path == "issue/101" for method, path, _ in calls)


def test_ambiguous_create_is_not_repeated(org, jira_setup, monkeypatch):
    calls = []

    def transport(self, method, path, payload=None):
        calls.append((method, path))
        if method == "POST" and path == "issue":
            raise DeliveryError("network_error", retry=True)
        return {"issues": [], "total": 0}

    monkeypatch.setattr(JiraClient, "request", transport)
    with tenant_scope(org.id):
        process_one(org)
        event = OutboxEvent.objects.get(state="retry")
        # Isolate this event from the other supported finding.
        OutboxEvent.objects.exclude(pk=event.pk).update(state="cancelled")
        OutboxEvent.objects.filter(pk=event.pk).update(next_attempt_at=timezone.now())
        process_one(org)
        event.refresh_from_db()
        assert event.state == "failed"
        assert DeliveryAttempt.objects.filter(outcome="manual_creation_recovery_required").exists()
        assert calls.count(("POST", "issue")) == 1


def test_jira_private_address_is_blocked(org, jira_setup, monkeypatch, settings):
    monkeypatch.setattr(
        "hubapp.jira.socket.getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(DeliveryError, match="blocked_destination"):
        JiraClient(jira_setup).request("GET", "myself")


@pytest.mark.parametrize(
    "url",
    [
        "http://jira.example.invalid",
        "https://jira.example.invalid:8443",
        "https://user:pass@jira.example.invalid",
        "https://other.invalid",
        "https://jira.example.invalid/../admin",
        "https://jira.example.invalid?token=x",
    ],
)
def test_jira_urls_fail_closed(url, settings):
    settings.JIRA_ALLOWED_HOSTS = {"jira.example.invalid"}
    with pytest.raises(ValidationError):
        validate_url(url)


def test_secret_rotation_can_read_old_key(settings):
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    settings.SECRET_ENCRYPTION_KEYS = [old]
    encrypted = encrypt_secret("test-pat")
    assert "test-pat" not in encrypted
    settings.SECRET_ENCRYPTION_KEYS = [new, old]
    rotated = cipher().rotate(encrypted.encode())
    assert Fernet(new.encode()).decrypt(rotated) == b"test-pat"


def test_billing_reads_current_state_and_preserves_grace(org, monkeypatch, settings):
    settings.STRIPE_PRICES = {"team:month": "price_team"}
    remote = {
        "id": "sub_demo",
        "customer": "cus_demo",
        "status": "past_due",
        "items": {
            "data": [
                {
                    "price": {"id": "price_team"},
                    "current_period_end": int((timezone.now() + timedelta(days=30)).timestamp()),
                }
            ]
        },
    }
    provider = Mock()
    provider.v1.subscriptions.retrieve.return_value.to_dict_recursive.return_value = remote
    monkeypatch.setattr("hubapp.billing.client", lambda: provider)
    with tenant_scope(org.id):
        Subscription.objects.update(stripe_customer="cus_demo")
        reconcile_subscription(org, "sub_demo")
        sub = Subscription.objects.get()
        assert sub.plan == "team" and sub.grace_until > timezone.now()
        grace = sub.grace_until
        reconcile_subscription(org, "sub_demo")
        assert Subscription.objects.get().grace_until == grace
        remote["status"] = "canceled"
        reconcile_subscription(org, "sub_demo")
        from hubapp.services import entitlement

        assert not entitlement()[2]


def test_webhook_duplicate_processed_once(org, monkeypatch, settings):
    settings.BILLING_PROVIDER = "stripe"
    settings.STRIPE_WEBHOOK_SECRET = "whsec_test"
    event = Mock(id="evt_one", type="customer.subscription.updated")
    event.data.object.customer = "cus_demo"
    event.data.object.id = "sub_demo"
    monkeypatch.setattr("hubapp.billing.stripe.Webhook.construct_event", lambda *a, **k: event)
    reconcile = Mock()
    monkeypatch.setattr("hubapp.billing.reconcile_subscription", reconcile)
    with tenant_scope(org.id):
        Subscription.objects.update(stripe_customer="cus_demo")
    receive_webhook(b"{}", "signature")
    receive_webhook(b"{}", "signature")
    assert reconcile.call_count == 1
    assert BillingEvent.objects.count() == 1


def test_real_webhook_signature_accepts_valid_bytes_and_rejects_changes(org, monkeypatch, settings):
    import hashlib
    import hmac
    import json
    import time

    import stripe

    settings.BILLING_PROVIDER = "stripe"
    settings.STRIPE_WEBHOOK_SECRET = "synthetic-test-signing-key"
    with tenant_scope(org.id):
        Subscription.objects.update(stripe_customer="cus_signed")
    body = json.dumps(
        {
            "id": "evt_signed",
            "object": "event",
            "type": "customer.subscription.updated",
            "data": {
                "object": {"id": "sub_signed", "object": "subscription", "customer": "cus_signed"}
            },
        }
    ).encode()
    timestamp = int(time.time())
    signature = hmac.new(
        settings.STRIPE_WEBHOOK_SECRET.encode(),
        str(timestamp).encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    header = f"t={timestamp},v1={signature}"
    reconcile = Mock()
    monkeypatch.setattr("hubapp.billing.reconcile_subscription", reconcile)
    receive_webhook(body, header)
    assert reconcile.call_count == 1
    with pytest.raises(stripe.SignatureVerificationError):
        receive_webhook(body + b" ", header)


def test_checkout_reuses_pending_session_across_plan_changes(org, monkeypatch, settings):
    from hubapp.billing import checkout

    settings.STRIPE_PRICES = {"individual:month": "price_individual", "team:year": "price_team"}
    provider = Mock()
    provider.v1.customers.create.return_value.id = "cus_checkout"
    session = Mock(
        id="cs_checkout",
        expires_at=int((timezone.now() + timedelta(hours=24)).timestamp()),
        url="https://checkout.stripe.com/test",
        status="open",
    )
    provider.v1.checkout.sessions.create.return_value = session
    provider.v1.checkout.sessions.retrieve.return_value = session
    monkeypatch.setattr("hubapp.billing.client", lambda: provider)
    with tenant_scope(org.id):
        assert checkout(org, "individual", "month") == session.url
        assert checkout(org, "team", "year") == session.url
    assert provider.v1.checkout.sessions.create.call_count == 1
