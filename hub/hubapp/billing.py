"""Stripe adapter. Webhooks authenticate bytes, then reconcile current provider state."""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import stripe
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import BillingEvent, Organization, Subscription
from .services import audit
from .tenancy import tenant_scope


def client():
    if settings.BILLING_PROVIDER != "stripe" or not settings.STRIPE_SECRET_KEY:
        raise ValidationError("Online billing is not configured. Contact the operator.")
    return stripe.StripeClient(settings.STRIPE_SECRET_KEY, max_network_retries=2)


@transaction.atomic
def checkout(org, plan, interval):
    Organization.objects.select_for_update().get(pk=org.pk)
    price = settings.STRIPE_PRICES.get(f"{plan}:{interval}")
    if plan not in {"individual", "team"} or interval not in {"month", "year"} or not price:
        raise ValidationError("This price is not configured. Contact support.")
    api = client()
    sub = Subscription.objects.select_for_update().get()
    if sub.stripe_subscription and sub.status != "canceled":
        raise ValidationError("Manage your existing subscription through the billing portal.")
    if (
        sub.checkout_session
        and sub.checkout_expires_at
        and sub.checkout_expires_at > timezone.now()
    ):
        existing = api.v1.checkout.sessions.retrieve(sub.checkout_session)
        if existing.status == "open":
            return existing.url
        if existing.status == "complete":
            raise ValidationError(
                "Your payment is being reconciled. Refresh shortly; do not start a second checkout."
            )
    if not sub.stripe_customer:
        customer = api.v1.customers.create(
            {"name": org.name, "metadata": {"organization_id": str(org.id)}},
            options={"idempotency_key": f"hub-customer-{org.id}"},
        )
        sub.stripe_customer = customer.id
        sub.save(update_fields=["stripe_customer"])
    session = api.v1.checkout.sessions.create(
        {
            "customer": sub.stripe_customer,
            "mode": "subscription",
            "line_items": [{"price": price, "quantity": 1}],
            "success_url": f"{settings.PUBLIC_URL}/w/{org.id}/billing/?checkout=returned",
            "cancel_url": f"{settings.PUBLIC_URL}/w/{org.id}/billing/",
            "subscription_data": {"metadata": {"organization_id": str(org.id)}},
        },
        options={
            "idempotency_key": f"hub-checkout-{org.id}-{price}-{int(timezone.now().timestamp()) // 1800}"
        },
    )
    sub.checkout_session = session.id
    sub.checkout_expires_at = datetime.fromtimestamp(session.expires_at, dt_timezone.utc)
    sub.save(update_fields=["checkout_session", "checkout_expires_at"])
    audit("billing.checkout", target=plan)
    return session.url


def portal(org):
    sub = Subscription.objects.get()
    if not sub.stripe_customer:
        raise ValidationError("No billing account exists yet.")
    return (
        client()
        .v1.billing_portal.sessions.create(
            {
                "customer": sub.stripe_customer,
                "return_url": f"{settings.PUBLIC_URL}/w/{org.id}/billing/",
            }
        )
        .url
    )


@transaction.atomic
def reconcile_subscription(org, subscription_id):
    Organization.objects.select_for_update().get(pk=org.pk)
    sub = Subscription.objects.select_for_update().get()
    remote = client().v1.subscriptions.retrieve(subscription_id).to_dict_recursive()
    if remote.get("customer") != sub.stripe_customer or (
        sub.stripe_subscription
        and sub.stripe_subscription != subscription_id
        and sub.status != "canceled"
    ):
        raise ValidationError("Subscription ownership mismatch.")
    items = remote.get("items", {}).get("data", [])
    price = items[0].get("price", {}).get("id") if len(items) == 1 else None
    plans = {value: key.split(":")[0] for key, value in settings.STRIPE_PRICES.items()}
    if price not in plans or plans[price] not in settings.PLANS:
        raise ValidationError("Unrecognized subscription price.")
    status = remote.get("status", "incomplete")
    now = timezone.now()
    if status == "past_due" and sub.status != "past_due":
        sub.grace_until = now + timedelta(days=7)
    elif status != "past_due":
        sub.grace_until = None
    sub.plan, sub.status = plans[price], status
    # New Stripe versions expose period bounds on subscription items.
    end = remote.get("current_period_end") or items[0].get("current_period_end")
    sub.valid_until = datetime.fromtimestamp(end, dt_timezone.utc) if end else now
    sub.stripe_subscription = subscription_id
    sub.save()
    audit("billing.reconciled", target=subscription_id)


def receive_webhook(body, signature):
    if settings.BILLING_PROVIDER != "stripe" or not settings.STRIPE_WEBHOOK_SECRET:
        raise ValidationError("Billing webhook is disabled.")
    event = stripe.Webhook.construct_event(
        body, signature, settings.STRIPE_WEBHOOK_SECRET, tolerance=300
    )
    if not event.type.startswith("customer.subscription."):
        return
    remote = event.data.object
    # Customer ownership is looked up locally; never provision a tenant from event metadata.
    local = Subscription.all_objects.filter(stripe_customer=remote.customer).first()
    if not local:
        raise ValidationError("Unknown billing customer.")
    with tenant_scope(local.organization_id), transaction.atomic():
        org = Organization.objects.select_for_update().get(pk=local.organization_id)
        if BillingEvent.objects.filter(pk=event.id).exists():
            return
        local = Subscription.objects.get()
        if (
            local.stripe_subscription
            and local.stripe_subscription != remote.id
            and local.status != "canceled"
        ):
            audit("billing.other_subscription_event", target=remote.id)
            BillingEvent.objects.create(event_id=event.id)
            return
        # Fetch current state under the organization lock, so delayed events cannot restore stale access.
        reconcile_subscription(org, remote.id)
        BillingEvent.objects.create(event_id=event.id)
