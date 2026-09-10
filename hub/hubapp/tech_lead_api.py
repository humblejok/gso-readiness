"""Narrow instance-wide management surface; tenant data still runs under one RLS scope."""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from . import finding_work
from .api import finding_data, paginated, permitted, repository_filter
from .models import Finding, Organization, User
from .services import audit, create_workspace, require_writes
from .tenancy import tenant_scope


def personal_only(request, scope):
    if not getattr(request.auth, "is_user_token", False):
        raise PermissionDenied("A tech-lead user token is required.")
    permitted(request, scope)


def workspace_data(row):
    return {"id": str(row.pk), "name": row.name, "revision": row.management_revision}


def fields(data, expected):
    if not isinstance(data, dict) or set(data) != set(expected):
        raise ValidationError("Expected exactly: " + ", ".join(sorted(expected)))


def name_value(data):
    value = data.get("name")
    if not isinstance(value, str) or not value.strip() or len(value) > 160:
        raise ValidationError("Name must contain 1–160 characters.")
    return value.strip()


class WorkspacesAPI(APIView):
    workspace_optional = True

    def get(self, request):
        personal_only(request, "workspaces:read")
        return paginated(request, Organization.objects.filter(suspended=False), workspace_data)

    @transaction.atomic
    def post(self, request):
        personal_only(request, "workspaces:write")
        fields(request.data, {"name"})
        # Serialize provisioning and preserve the normal owner/trial/workspace-count rules.
        user = User.objects.select_for_update().get(pk=request.auth.user_id)
        if not (user.is_active and user.email_verified and user.is_tech_lead):
            raise PermissionDenied("Tech-lead access is no longer active.")
        try:
            org = create_workspace(user, name_value(request.data))
        except DjangoValidationError as error:
            raise ValidationError(error.messages) from error
        with tenant_scope(org.pk):
            audit("user_token.workspace_created", f"user:{user.pk}/token:{request.auth.pk}", org.pk)
        return Response(workspace_data(org), status=201)


class WorkspaceDetailAPI(APIView):
    def get(self, request, workspace_id):
        personal_only(request, "workspaces:read")
        return Response(workspace_data(request.workspace))

    @transaction.atomic
    def patch(self, request, workspace_id):
        personal_only(request, "workspaces:write")
        fields(request.data, {"name", "revision"})
        org = get_object_or_404(
            Organization.objects.select_for_update(), pk=workspace_id, suspended=False
        )
        require_writes()
        if (
            type(request.data["revision"]) is not int
            or request.data["revision"] != org.management_revision
        ):
            return Response(
                {"error": "Workspace changed; reload its current revision."}, status=409
            )
        org.name = name_value(request.data)
        org.management_revision += 1
        org.save(update_fields=["name", "management_revision"])
        audit(
            "user_token.workspace_updated",
            f"user:{request.auth.user_id}/token:{request.auth.pk}",
            org.pk,
        )
        return Response(workspace_data(org))


class FindingManageAPI(APIView):
    def patch(self, request, pk):
        personal_only(request, "findings:write")
        action = request.data.get("action") if isinstance(request.data, dict) else None
        expected = (
            {"action", "revision", "remediation"} if action == "edit" else {"action", "revision"}
        )
        fields(request.data, expected)
        if action not in {"implement", "cancel", "edit"}:
            raise ValidationError(
                "Use implement, cancel or edit. Lifecycle outcomes require evidence-based revalidation."
            )
        finding = get_object_or_404(repository_filter(request, Finding.objects.all()), pk=pk)
        try:
            finding = finding_work.configure(
                finding.pk,
                action,
                request.data["revision"],
                request.auth.pk,
                request.data.get("remediation"),
            )
        except finding_work.WorkConflict as error:
            return Response({"error": str(error)}, status=409)
        except DjangoValidationError as error:
            raise ValidationError(error.messages) from error
        return Response(finding_data(finding))
