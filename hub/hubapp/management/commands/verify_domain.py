from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from hubapp.models import Organization
from hubapp.services import audit
from hubapp.tenancy import tenant_scope


class Command(BaseCommand):
    help = "Verify customer DNS TXT ownership; ingress TLS and allowed-host configuration remain operator-controlled."

    def add_arguments(self, parser):
        parser.add_argument("organization_id")
        parser.add_argument("--operator", required=True)

    def handle(self, *args, **options):
        import dns.resolver

        with tenant_scope(options["organization_id"]), transaction.atomic():
            org = Organization.objects.select_for_update().get(pk=options["organization_id"])
            if not org.custom_domain or not org.domain_challenge:
                raise CommandError("Workspace has no pending domain challenge.")
            from hubapp.services import entitlement

            _, limits, active = entitlement()
            if not active or not limits["branding"]:
                raise CommandError("Active enterprise branding entitlement is required.")
            if (
                Organization.objects.exclude(pk=org.pk)
                .filter(custom_domain=org.custom_domain, domain_verified_at__isnull=False)
                .exists()
            ):
                raise CommandError("Domain already belongs to another workspace.")
            answers = dns.resolver.resolve("_finding-hub." + org.custom_domain, "TXT", lifetime=5)
            if org.domain_challenge not in [
                b"".join(record.strings).decode() for record in answers
            ]:
                raise CommandError("DNS TXT ownership challenge did not match.")
            org.domain_verified_at = timezone.now()
            org.save(update_fields=["domain_verified_at"])
            audit("operator.domain_verified", options["operator"], org.pk)
        self.stdout.write(
            "DNS ownership verified. Configure ingress certificate/routing and explicit allowed host before use."
        )
