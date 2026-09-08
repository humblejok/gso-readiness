from django.core.management.base import BaseCommand
from django.db import transaction

from hubapp.jira import cipher
from hubapp.models import JiraConnection, Organization
from hubapp.services import audit
from hubapp.tenancy import tenant_scope


class Command(BaseCommand):
    help = "Re-encrypt stored PATs using the first configured encryption key; retain old keys until complete."

    def add_arguments(self, parser):
        parser.add_argument("--operator", required=True)

    def handle(self, *args, **options):
        encryption = cipher()
        for org in Organization.objects.order_by("id").iterator():
            with tenant_scope(org.pk), transaction.atomic():
                Organization.objects.select_for_update().get(pk=org.pk)
                for connection in JiraConnection.objects.all():
                    connection.encrypted_token = encryption.rotate(
                        connection.encrypted_token.encode()
                    ).decode()
                    connection.save(update_fields=["encrypted_token"])
                audit("operator.secrets_rotated", options["operator"], org.pk)
        self.stdout.write(
            "Rotation completed. Keep old backup keys according to the recovery policy."
        )
