from django.core.management.base import BaseCommand

from hubapp.billing import client, reconcile_subscription
from hubapp.models import Organization, Subscription
from hubapp.tenancy import tenant_scope


class Command(BaseCommand):
    help = "Reconcile current provider state after missed webhooks; schedule hourly when Stripe is enabled."

    def handle(self, *args, **options):
        failures = 0
        for org in Organization.objects.order_by("id").iterator():
            with tenant_scope(org.id):
                sub = Subscription.objects.get()
                if sub.stripe_subscription or sub.checkout_session:
                    try:
                        subscription_id = sub.stripe_subscription
                        if not subscription_id and sub.checkout_session:
                            session = client().v1.checkout.sessions.retrieve(sub.checkout_session)
                            if session.status == "complete":
                                subscription_id = session.subscription
                        if subscription_id:
                            reconcile_subscription(org, subscription_id)
                    except Exception:
                        failures += 1
                        self.stderr.write(
                            f"Reconciliation failed for workspace {org.id}; inspect provider configuration."
                        )
        if failures:
            from django.core.management.base import CommandError

            raise CommandError(f"{failures} workspaces could not be reconciled.")
