import uuid

from django.contrib.auth.hashers import check_password
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import (
    AuthenticationFailed,
    PermissionDenied,
    Throttled,
    ValidationError,
)

from .models import ApiClient, Organization, UserApiToken
from .services import audit, rate_limit
from .tenancy import set_tenant


class TokenPrincipal:
    is_authenticated = True

    def __init__(self, token):
        self.pk = str(token.pk)


class UserTokenContext:
    """Per-request adapter: selecting a workspace never changes token ownership."""

    repository_external_id = ""
    is_user_token = True

    def __init__(self, token, workspace):
        self.pk, self.scopes, self.user_id = token.pk, token.scopes, token.user_id
        self.organization_id = workspace.pk if workspace else None


def selected_workspace(request):
    route = request.parser_context["kwargs"].get("workspace_id")
    header = request.headers.get("X-Workspace-ID")
    try:
        supplied = uuid.UUID(header) if header is not None else None
    except (ValueError, TypeError) as error:
        raise ValidationError("X-Workspace-ID must be a workspace UUID.") from error
    if route and supplied and route != supplied:
        raise PermissionDenied("Workspace header and URL do not match.")
    return route or supplied


class WorkspaceTokenAuthentication(BaseAuthentication):
    def authenticate_header(self, request):
        return "Bearer"

    def authenticate(self, request):
        if not rate_limit("api-ip:" + request.META.get("REMOTE_ADDR", ""), 240):
            raise Throttled()
        try:
            scheme, raw = get_authorization_header(request).decode().split(" ", 1)
            public_id, secret = raw.split(".", 1)
            if scheme.lower() != "bearer" or len(secret) > 200:
                raise ValueError
            token_id = uuid.UUID(public_id)
        except (ValueError, UnicodeError):
            raise AuthenticationFailed("A valid Hub API token is required.")
        if not rate_limit("api-auth:" + str(token_id), 120):
            raise Throttled()
        # Unscoped token lookup is intentionally limited to authentication.
        token = (
            ApiClient.all_objects.select_related("organization")
            .filter(pk=token_id, revoked_at__isnull=True, expires_at__gt=timezone.now())
            .first()
        )
        if token:
            if token.organization.suspended or not check_password(secret, token.token_hash):
                raise AuthenticationFailed("Invalid or expired token.")
            selected = selected_workspace(request)
            if selected and selected != token.organization_id:
                raise PermissionDenied("A workspace token cannot select another workspace.")
            set_tenant(token.organization_id)
            request.workspace = token.organization
            return TokenPrincipal(token), token
        personal = (
            UserApiToken.objects.select_related("user")
            .filter(pk=token_id, revoked_at__isnull=True, expires_at__gt=timezone.now())
            .first()
        )
        if (
            not personal
            or not personal.user.is_active
            or not personal.user.email_verified
            or not personal.user.is_tech_lead
            or not check_password(secret, personal.token_hash)
        ):
            raise AuthenticationFailed("Invalid or expired token.")
        if not rate_limit("api-user:" + str(personal.user_id), 240):
            raise Throttled()
        selected = selected_workspace(request)
        workspace = None
        if selected:
            workspace = Organization.objects.filter(pk=selected, suspended=False).first()
            if not workspace:
                raise PermissionDenied("Workspace unavailable.")
        elif not getattr(request.parser_context["view"], "workspace_optional", False):
            raise ValidationError(
                "User tokens require X-Workspace-ID for workspace data endpoints."
            )
        set_tenant(workspace.pk if workspace else None)
        request.workspace = workspace
        if workspace:
            audit("user_token.access", f"user:{personal.user_id}/token:{personal.pk}", request.path)
        return TokenPrincipal(personal), UserTokenContext(personal, workspace)
