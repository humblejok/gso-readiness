"""Account, entitlement and token use cases. All workspace mutations serialize on its row."""

import hashlib
import secrets
import time
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import (
    ApiClient,
    AuditEvent,
    FindingActivity,
    Membership,
    Organization,
    RateBucket,
    ReviewImport,
    Subscription,
)
from .tenancy import tenant_scope


def audit(action, actor="", target=""):
    AuditEvent.objects.create(action=action, actor=str(actor)[:100], target=str(target)[:200])


@transaction.atomic
def create_workspace(user, name):
    if not user.email_verified:
        raise PermissionDenied("Verify your email first.")
    if Membership.objects.filter(user=user, role="owner").count() >= 10:
        raise ValidationError("Workspace limit reached. Contact support.")
    org = Organization.objects.create(name=name)
    Membership.objects.create(organization=org, user=user, role="owner", billing_access=True)
    with tenant_scope(org.id):
        Subscription.objects.create(
            valid_until=timezone.now() + timedelta(days=settings.TRIAL_DAYS)
        )
        audit("workspace.created", user.pk, org.pk)
    return org


def entitlement():
    sub = Subscription.objects.get()
    now = timezone.now()
    active = (
        sub.status in {"active", "trialing"}
        and sub.valid_until is not None
        and sub.valid_until > now
    )
    grace = sub.status == "past_due" and sub.grace_until is not None and sub.grace_until > now
    return sub, settings.PLANS.get(sub.plan, settings.PLANS["trial"]), active or grace


def require_writes():
    sub, limits, active = entitlement()
    if not active:
        raise PermissionDenied(
            "Subscription is read-only. Renew to resume changes; existing data remains exportable."
        )
    return limits


def usage():
    now = timezone.now()
    return {
        "imports_month": ReviewImport.objects.filter(
            created_at__year=now.year, created_at__month=now.month
        ).count(),
        "storage_bytes": (ReviewImport.objects.aggregate(total=Sum("payload_bytes"))["total"] or 0)
        + (FindingActivity.objects.aggregate(total=Sum("payload_bytes"))["total"] or 0),
    }


def issue_token(name, scopes, repository_external_id="", days=90):
    if not set(scopes) <= {"reviews:write", "findings:read", "findings:implement"} or not scopes:
        raise ValidationError("Invalid token scopes.")
    secret = secrets.token_urlsafe(32)
    token = ApiClient.objects.create(
        name=name,
        scopes=scopes,
        repository_external_id=repository_external_id,
        token_hash=make_password(secret),
        expires_at=timezone.now() + timedelta(days=days),
    )
    audit("token.created", target=token.pk)
    return f"{token.pk}.{secret}"


@transaction.atomic
def rate_limit(key, limit=30, seconds=60):
    digest = hashlib.sha256(key.encode()).hexdigest()
    bucket, _ = RateBucket.objects.get_or_create(
        key=digest, defaults={"window": int(time.time()) // seconds}
    )
    bucket = RateBucket.objects.select_for_update().get(pk=bucket.pk)
    window = int(time.time()) // seconds
    bucket.count = bucket.count + 1 if bucket.window == window else 1
    bucket.window = window
    bucket.save()
    return bucket.count <= limit
