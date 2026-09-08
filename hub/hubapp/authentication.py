import uuid

from django.contrib.auth.hashers import check_password
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed, Throttled

from .models import ApiClient
from .services import rate_limit
from .tenancy import set_tenant


class TokenPrincipal:
    is_authenticated = True

    def __init__(self, token):
        self.pk = str(token.pk)


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
            raise AuthenticationFailed("A valid workspace token is required.")
        if not rate_limit("api-auth:" + str(token_id), 120):
            raise Throttled()
        # Unscoped token lookup is intentionally limited to authentication.
        token = (
            ApiClient.all_objects.select_related("organization")
            .filter(pk=token_id, revoked_at__isnull=True, expires_at__gt=timezone.now())
            .first()
        )
        if (
            not token
            or token.organization.suspended
            or not check_password(secret, token.token_hash)
        ):
            raise AuthenticationFailed("Invalid or expired token.")
        set_tenant(token.organization_id)
        request.workspace = token.organization
        return TokenPrincipal(token), token
