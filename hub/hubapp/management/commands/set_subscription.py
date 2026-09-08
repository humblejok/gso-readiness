from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from hubapp.models import Organization, Subscription
from hubapp.services import audit
from hubapp.tenancy import tenant_scope


class Command(BaseCommand):
    help = "Operator-only activation of a manually invoiced subscription; never charges a customer."

    def add_arguments(self, parser):
        parser.add_argument("organization_id")
        parser.add_argument("--plan", choices=settings.PLANS, required=True)
        parser.add_argument("--days", type=int, required=True)
        parser.add_argument("--operator", required=True)

    def handle(self, *args, **options):
        if not 1 <= options["days"] <= 1095:
            raise CommandError("Days must be between 1 and 1095.")
        with tenant_scope(options["organization_id"]), transaction.atomic():
            org = Organization.objects.select_for_update().get(pk=options["organization_id"])
            sub = Subscription.objects.get()
            if sub.stripe_subscription:
                raise CommandError(
                    "This subscription is provider-managed; change it through Stripe."
                )
            sub.plan, sub.status = options["plan"], "active"
            sub.valid_until = timezone.now() + timedelta(days=options["days"])
            sub.save()
            audit("operator.subscription_activated", options["operator"], org.id)
        self.stdout.write("Subscription activated. No payment was collected.")
