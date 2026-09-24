from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from hubapp import models
from hubapp.tenancy import tenant_scope


class Command(BaseCommand):
    help = "Dry-run by default. Erase one confirmed workspace after a 7-day request window; never erases backups."

    def add_arguments(self, parser):
        parser.add_argument("organization_id")
        parser.add_argument("--execute", action="store_true")
        parser.add_argument("--confirm")
        parser.add_argument("--operator", required=True)

    def handle(self, *args, **options):
        with tenant_scope(options["organization_id"]), transaction.atomic():
            org = models.Organization.objects.select_for_update().get(pk=options["organization_id"])
            counts = {
                "imports": models.ReviewImport.objects.count(),
                "findings": models.Finding.objects.count(),
                "requests": models.ChangeRequest.objects.count(),
                "request_implementations": models.RequestImplementation.objects.count(),
                "members": models.Membership.objects.filter(organization=org).count(),
            }
            self.stdout.write(f"Workspace {org.id}: {counts}")
            if not options["execute"]:
                self.stdout.write("Dry run: no records deleted.")
                return
            if (
                options["confirm"] != str(org.id)
                or not org.deletion_requested_at
                or org.deletion_requested_at > timezone.now() - timedelta(days=7)
            ):
                raise CommandError(
                    "Exact UUID confirmation and a request at least seven days old are required."
                )
            sub = models.Subscription.objects.get()
            if sub.stripe_subscription and sub.status != "canceled":
                raise CommandError("Cancel and reconcile the provider subscription before erasure.")
            # Explicit ordering handles protected Jira relationships. The user's account is retained.
            for model in (
                models.RequestHandoff,
                models.ProjectRelationship,
                models.RequestImplementation,
                models.ChangeRequestActivity,
                models.ChangeRequest,
                models.DeliveryAttempt,
                models.OutboxEvent,
                models.JiraAssociation,
                models.JiraBinding,
                models.JiraConnection,
                models.Observation,
                models.Finding,
                models.ReviewImport,
                models.Repository,
                models.Invitation,
                models.ApiClient,
                models.Subscription,
                models.AuditEvent,
            ):
                model.objects.all().delete()
            models.Membership.objects.filter(organization=org).delete()
            org.delete()
        # Minimal receipt must be retained in the operator's external audit system, not the erased tenant.
        self.stdout.write(
            f"ERASURE RECEIPT: workspace={options['organization_id']} operator={options['operator']} at={timezone.now().isoformat()}. Live records deleted; coordinate backup expiry separately."
        )
