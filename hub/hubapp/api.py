"""Token API: workspace identity comes exclusively from authenticated token ownership."""

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import connection
from django.http import JsonResponse
from rest_framework.exceptions import PermissionDenied, Throttled
from rest_framework.response import Response
from rest_framework.views import APIView

from .contracts import render_remediation
from .imports import ImportConflict, import_review
from .models import Finding, Observation, OutboxEvent, Repository, ReviewImport
from .services import rate_limit


def permitted(request, scope):
    if scope not in request.auth.scopes:
        raise PermissionDenied("Token scope is insufficient.")


def repository_filter(request, query, field="repository__external_id"):
    restriction = request.auth.repository_external_id
    return query.filter(**{field: restriction}) if restriction else query


def paginated(request, query, serializer):
    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except ValueError:
        page = 1
    count = query.count()
    rows = query.order_by("created_at", "pk")[(page - 1) * 50 : page * 50]
    return Response(
        {
            "count": count,
            "page": page,
            "page_size": 50,
            "results": [serializer(row) for row in rows],
        }
    )


def finding_data(row):
    return {
        "id": str(row.id),
        "repository_id": str(row.repository_id),
        "fingerprint": row.fingerprint,
        "lifecycle": row.lifecycle,
        "last_seen": row.last_seen,
        "finding": row.data,
    }


class ImportsAPI(APIView):
    def post(self, request):
        permitted(request, "reviews:write")
        if not rate_limit("import:" + str(request.auth.organization_id), 30):
            raise Throttled()
        try:
            record, created = import_review(
                request.data,
                request.headers.get("Idempotency-Key", ""),
                request.auth.pk,
                request.auth.repository_external_id,
            )
        except DjangoValidationError as error:
            return Response({"errors": error.messages}, status=400)
        except ImportConflict:
            return Response({"error": "Run identity conflicts with existing content."}, status=409)
        except DjangoPermissionDenied as error:
            raise PermissionDenied(str(error)) from error
        return Response(record.result, status=201 if created else 200)


class ImportDetailAPI(APIView):
    def get(self, request, pk):
        permitted(request, "findings:read")
        from django.shortcuts import get_object_or_404

        row = get_object_or_404(repository_filter(request, ReviewImport.objects.all()), pk=pk)
        return Response({**row.result, "payload": row.payload})


class RepositoriesAPI(APIView):
    def get(self, request):
        permitted(request, "findings:read")
        return paginated(
            request,
            repository_filter(request, Repository.objects.all(), "external_id"),
            lambda r: {
                "id": str(r.id),
                "external_id": r.external_id,
                "name": r.name,
                "default_branch": r.default_branch,
            },
        )


class RepositoryDetailAPI(APIView):
    def get(self, request, pk):
        permitted(request, "findings:read")
        from django.shortcuts import get_object_or_404

        row = get_object_or_404(
            repository_filter(request, Repository.objects.all(), "external_id"), pk=pk
        )
        return Response(
            {
                "id": str(row.id),
                "external_id": row.external_id,
                "name": row.name,
                "default_branch": row.default_branch,
                "active": row.active,
            }
        )


class FindingsAPI(APIView):
    def get(self, request, repository_id=None):
        permitted(request, "findings:read")
        query = repository_filter(request, Finding.objects.all())
        if repository_id:
            query = query.filter(repository_id=repository_id)
        for field in ("lifecycle", "severity", "category", "verification_status"):
            if request.query_params.get(field):
                query = query.filter(**{field: request.query_params[field]})
        return paginated(request, query, finding_data)


class FindingDetailAPI(APIView):
    def get(self, request, pk, part=""):
        permitted(request, "findings:read")
        if part not in {"", "observations", "remediation", "deliveries"}:
            from django.http import Http404

            raise Http404
        from django.shortcuts import get_object_or_404

        finding = get_object_or_404(repository_filter(request, Finding.objects.all()), pk=pk)
        observations = Observation.objects.filter(finding=finding)
        if part == "observations":
            return paginated(
                request,
                observations,
                lambda r: {
                    "id": str(r.id),
                    "import_id": str(r.review_import_id),
                    "lifecycle": r.lifecycle,
                    "data": r.data,
                    "reconciliation": r.reconciliation,
                    "remediation": r.remediation,
                },
            )
        if part == "remediation":
            latest = observations.order_by("-created_at", "-pk").first()
            return Response(
                {
                    "remediation": latest.remediation if latest else None,
                    "markdown": render_remediation(latest.remediation if latest else None),
                }
            )
        if part == "deliveries":
            return paginated(
                request,
                OutboxEvent.objects.filter(observation__finding=finding),
                lambda r: {"id": str(r.id), "state": r.state, "attempts": r.attempts},
            )
        return Response(finding_data(finding))


def live(request):
    return JsonResponse({"status": "ok"})


def ready(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM hubapp_organization LIMIT 1")
            from django.conf import settings

            if settings.PRODUCTION:
                cursor.execute(
                    "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
                )
                if any(cursor.fetchone()):
                    return JsonResponse({"status": "unsafe_database_role"}, status=503)
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        if executor.migration_plan(executor.loader.graph.leaf_nodes()):
            return JsonResponse({"status": "migrations_pending"}, status=503)
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ready"})
