import uuid
from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponseForbidden, HttpResponseNotFound

from .models import Membership, Organization
from .tenancy import set_tenant, tenant_scope


class WorkspaceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == "/health/live":
            # Liveness must not open a database connection or inspect a session.
            return self.get_response(request)
        from django.http import HttpResponse

        try:
            if (
                int(request.META.get("CONTENT_LENGTH") or 0)
                > settings.DATA_UPLOAD_MAX_MEMORY_SIZE + 65536
            ):
                return HttpResponse("Request body too large.", status=413)
        except ValueError:
            return HttpResponse("Invalid content length.", status=400)
        if settings.PRODUCTION and request.path != "/health/live":
            from django.db import connection
            from django.http import JsonResponse

            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
                    )
                    if any(cursor.fetchone()):
                        return JsonResponse({"error": "Unsafe runtime database role."}, status=503)
            except Exception:
                return JsonResponse({"error": "Database unavailable."}, status=503)
        with tenant_scope(None):
            request.workspace = None
            request.membership = None
            host = request.get_host().split(":")[0].lower()
            domain_org = None
            if host not in {
                urlsplit(settings.PUBLIC_URL).hostname,
                "testserver",
            } and not (settings.DEBUG and host in {"localhost", "127.0.0.1"}):
                domain_org = Organization.objects.filter(
                    custom_domain=host,
                    domain_verified_at__isnull=False,
                    suspended=False,
                ).first()
                if not domain_org:
                    return HttpResponseNotFound()
                if request.path.startswith("/api/") or request.path.startswith("/operator/"):
                    return (
                        HttpResponseNotFound()
                    )  # APIs/operators use the canonical service origin.
                if request.path == "/" and request.user.is_authenticated:
                    from django.shortcuts import redirect

                    return redirect(f"/w/{domain_org.id}/")
            if request.path.startswith("/w/"):
                try:
                    organization_id = uuid.UUID(request.path.split("/")[2])
                except (ValueError, IndexError):
                    return HttpResponseNotFound()
                if domain_org and domain_org.id != organization_id:
                    return HttpResponseNotFound()
                if not request.user.is_authenticated:
                    from django.contrib.auth.views import redirect_to_login

                    return redirect_to_login(request.get_full_path())
                membership = (
                    Membership.objects.select_related("organization")
                    .filter(user=request.user, organization_id=organization_id)
                    .first()
                )
                if not membership or not request.user.email_verified:
                    return HttpResponseNotFound()
                if membership.organization.suspended:
                    return HttpResponseForbidden(
                        "Workspace suspended. Contact support for data access."
                    )
                request.membership, request.workspace = (
                    membership,
                    membership.organization,
                )
                set_tenant(organization_id)
            response = self.get_response(request)
            if getattr(request.user, "is_authenticated", False) or request.path.startswith("/api/"):
                response["Cache-Control"] = "no-store, private"
            response["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https: data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
            )
            return response
