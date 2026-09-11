"""Project-restricted request reads and analysis submission; never human acceptance."""

import uuid

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied, Throttled
from rest_framework.response import Response
from rest_framework.views import APIView

from . import change_requests
from .api import paginated, permitted, repository_filter
from .models import ChangeRequest, Repository
from .services import rate_limit


def request_data(item):
    return {
        "id": str(item.pk),
        "repository_external_id": item.repository_external_id,
        "kind": item.kind,
        "description": item.description,
        "status": item.status,
        "revision": item.revision,
        "analysis": item.analysis,
        "analysis_submission_id": str(item.analysis_submission_id)
        if item.analysis_submission_id
        else None,
    }


def visible(request):
    return repository_filter(request, ChangeRequest.objects.all(), "repository_external_id")


class RequestsAPI(APIView):
    def get(self, request):
        permitted(request, "requests:read")
        repository = request.query_params.get("repository_external_id", "")
        if not repository:
            return Response({"error": "Select a repository_external_id."}, status=400)
        query = visible(request).filter(repository_external_id=repository)
        status = request.query_params.get("status", "open")
        if status not in ChangeRequest.Status.values:
            return Response({"error": "Invalid request status."}, status=400)
        query = query.filter(status=status)
        if not Repository.objects.filter(external_id=repository, active=True).exists():
            query = query.none()
        if request.query_params.get("ids"):
            try:
                ids = request.query_params["ids"].split(",")
                if len(ids) > 100:
                    raise ValueError
                ids = [uuid.UUID(value.strip()) for value in ids]
            except ValueError:
                return Response({"error": "Provide at most 100 request UUIDs."}, status=400)
            query = query.filter(pk__in=ids)
        return paginated(request, query, request_data)


class RequestAnalysisAPI(APIView):
    def get(self, request, pk):
        permitted(request, "requests:read")
        return Response(request_data(get_object_or_404(visible(request), pk=pk)))

    def post(self, request, pk):
        permitted(request, "requests:read")
        permitted(request, "requests:analyse")
        item = get_object_or_404(visible(request), pk=pk)
        if not rate_limit("request-analysis:" + str(request.auth.organization_id), 30):
            raise Throttled()
        try:
            if not isinstance(request.data, dict) or set(request.data) != {
                "revision",
                "repository_external_id",
                "submission_id",
                "analysis",
            }:
                raise ValidationError(
                    "Expected revision, repository_external_id, submission_id and analysis."
                )
            # Recheck the authorization binding under the service lock if the project was edited.
            if request.data["repository_external_id"] != item.repository_external_id:
                raise change_requests.RequestConflict("Request project changed.")
            result = change_requests.submit_analysis(
                item.pk, actor="token:" + str(request.auth.pk), **request.data
            )
        except change_requests.RequestConflict as error:
            return Response({"errors": error.messages}, status=409)
        except DjangoPermissionDenied as error:
            raise PermissionDenied(str(error)) from error
        except (ValidationError, ValueError, TypeError, AttributeError):
            return Response({"error": "Invalid request analysis."}, status=400)
        return Response(request_data(result))
