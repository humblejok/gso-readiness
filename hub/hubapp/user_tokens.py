"""Personal token lifecycle. Only an active, verified tech lead may issue a token."""

import secrets
from datetime import timedelta

from django.contrib.auth.hashers import make_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .models import User, UserApiToken, UserTokenEvent


@transaction.atomic
def issue(user_id, *, name, scopes, days):
    user = User.objects.select_for_update().get(pk=user_id)
    if not (user.is_active and user.email_verified and user.is_tech_lead):
        raise PermissionDenied("An active, verified tech-lead account is required.")
    if (
        not scopes
        or not isinstance(scopes, list)
        or any(scope not in dict(UserApiToken.SCOPES) for scope in scopes)
        or type(days) is not int
        or not 1 <= days <= 365
        or not isinstance(name, str)
        or not name.strip()
        or len(name) > 100
    ):
        raise ValidationError("Invalid personal token name, scopes or expiry.")
    if (
        UserApiToken.objects.filter(
            user=user, revoked_at__isnull=True, expires_at__gt=timezone.now()
        ).count()
        >= 20
    ):
        raise ValidationError(
            "Revoke an existing token before creating more than 20 active personal tokens."
        )
    secret = secrets.token_urlsafe(32)
    token = UserApiToken.objects.create(
        user=user,
        name=name.strip(),
        scopes=sorted(set(scopes)),
        token_hash=make_password(secret),
        expires_at=timezone.now() + timedelta(days=days),
    )
    UserTokenEvent.objects.create(token=token, action="created")
    return f"{token.pk}.{secret}"


@transaction.atomic
def revoke(user_id, token_id):
    User.objects.select_for_update().get(pk=user_id)
    token = UserApiToken.objects.select_for_update().get(pk=token_id, user_id=user_id)
    if not token.revoked_at:
        token.revoked_at = timezone.now()
        token.save(update_fields=["revoked_at"])
        UserTokenEvent.objects.create(token=token, action="revoked")
