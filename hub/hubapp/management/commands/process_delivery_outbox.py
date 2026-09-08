import signal
import threading

from django.core.management.base import BaseCommand

from hubapp.jira import process_one
from hubapp.models import Organization
from hubapp.tenancy import tenant_scope


class Command(BaseCommand):
    help = "Process one delivery per workspace per sweep, or run continuously. SQLite supports one worker only."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        stopping = threading.Event()
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, lambda *_: stopping.set())
            signal.signal(signal.SIGINT, lambda *_: stopping.set())
        from django.conf import settings
        from django.db import connection

        if settings.PRODUCTION:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
                )
                if any(cursor.fetchone()):
                    from django.core.management.base import CommandError

                    raise CommandError("Worker requires a non-superuser without BYPASSRLS.")
        try:
            while not stopping.is_set():
                processed = 0
                for org in Organization.objects.filter(suspended=False).order_by("id").iterator():
                    if stopping.is_set():
                        break
                    with tenant_scope(org.id):
                        processed += int(process_one(org))
                if options["once"]:
                    self.stdout.write(f"Processed {processed} delivery events.")
                    return
                stopping.wait(2 if processed else 5)
        except KeyboardInterrupt:
            self.stdout.write("Worker stopped.")
