import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from .tenancy import TenantManager


class User(AbstractUser):
    email = models.EmailField(unique=True)
    email_verified = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(Lower("email"), name="user_email_case_unique")]


class Organization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=160)
    created_at = models.DateTimeField(auto_now_add=True)
    suspended = models.BooleanField(default=False)
    deletion_requested_at = models.DateTimeField(null=True, blank=True)
    brand_name = models.CharField(max_length=100, blank=True)
    brand_color = models.CharField(max_length=7, default="#155eef")
    logo_url = models.URLField(blank=True)
    custom_domain = models.CharField(max_length=253, blank=True)
    domain_verified_at = models.DateTimeField(null=True, blank=True)
    domain_challenge = models.CharField(max_length=100, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["custom_domain"],
                condition=models.Q(domain_verified_at__isnull=False) & ~models.Q(custom_domain=""),
                name="verified_domain_unique",
            )
        ]

    def __str__(self):
        return self.name


class Membership(models.Model):
    ROLES = [(r, r.title()) for r in ("owner", "admin", "reviewer", "viewer")]
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(max_length=16, choices=ROLES, default="viewer")
    billing_access = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "user"], name="membership_unique")
        ]


class TenantRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = TenantManager()
    all_objects = models.Manager()

    class Meta:
        abstract = True


class Invitation(TenantRecord):
    email = models.EmailField()
    role = models.CharField(max_length=16, choices=Membership.ROLES, default="viewer")
    token_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True)


class Subscription(TenantRecord):
    plan = models.CharField(max_length=20, default="trial")
    status = models.CharField(max_length=20, default="trialing")
    valid_until = models.DateTimeField(null=True)
    grace_until = models.DateTimeField(null=True)
    stripe_customer = models.CharField(max_length=100, blank=True)
    stripe_subscription = models.CharField(max_length=100, blank=True)
    provider_event_at = models.BigIntegerField(default=0)
    checkout_session = models.CharField(max_length=255, blank=True)
    checkout_expires_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization"], name="one_subscription_per_org"),
            models.UniqueConstraint(
                fields=["stripe_customer"],
                condition=~models.Q(stripe_customer=""),
                name="stripe_customer_owner_unique",
            ),
            models.UniqueConstraint(
                fields=["stripe_subscription"],
                condition=~models.Q(stripe_subscription=""),
                name="stripe_subscription_owner_unique",
            ),
        ]


class ApiClient(TenantRecord):
    name = models.CharField(max_length=100)
    token_hash = models.CharField(max_length=128)
    scopes = models.JSONField(default=list)
    repository_external_id = models.CharField(max_length=500, blank=True)
    revoked_at = models.DateTimeField(null=True)
    expires_at = models.DateTimeField()


class Repository(TenantRecord):
    external_id = models.CharField(max_length=500)
    name = models.CharField(max_length=200)
    clone_url = models.CharField(max_length=1000, blank=True)
    default_branch = models.CharField(max_length=200, default="main")
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "external_id"],
                name="repository_tenant_identity",
            )
        ]


class ReviewImport(TenantRecord):
    repository = models.ForeignKey(Repository, on_delete=models.CASCADE)
    run_id = models.CharField(max_length=250)
    payload_hash = models.CharField(max_length=64)
    payload = models.JSONField()
    payload_bytes = models.PositiveIntegerField(default=0)
    source_commit = models.CharField(max_length=64)
    source_branch = models.CharField(max_length=200)
    mode = models.CharField(max_length=16)
    result = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["repository", "run_id"], name="review_run_identity")
        ]


class Finding(TenantRecord):
    repository = models.ForeignKey(Repository, on_delete=models.CASCADE)
    fingerprint = models.CharField(max_length=71)
    display_id = models.CharField(max_length=100)
    title = models.CharField(max_length=1000)
    severity = models.CharField(max_length=20)
    category = models.CharField(max_length=40)
    verification_status = models.CharField(max_length=30)
    lifecycle = models.CharField(max_length=30, default="open")
    data = models.JSONField()
    last_seen = models.DateTimeField(default=timezone.now)
    replacement_fingerprint = models.CharField(max_length=71, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["repository", "fingerprint"], name="finding_identity")
        ]
        indexes = [models.Index(fields=["organization", "lifecycle", "severity"])]


class Observation(TenantRecord):
    finding = models.ForeignKey(Finding, on_delete=models.CASCADE, related_name="observations")
    review_import = models.ForeignKey(ReviewImport, on_delete=models.CASCADE)
    data = models.JSONField()
    lifecycle = models.CharField(max_length=30)
    remediation = models.JSONField(null=True)
    reconciliation = models.JSONField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["finding", "review_import"], name="observation_once")
        ]


class JiraConnection(TenantRecord):
    name = models.CharField(max_length=100)
    base_url = models.URLField()
    encrypted_token = models.TextField()
    enabled = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "name"], name="jira_tenant_name")
        ]


class JiraBinding(TenantRecord):
    repository = models.OneToOneField(Repository, on_delete=models.CASCADE)
    connection = models.ForeignKey(JiraConnection, on_delete=models.PROTECT)
    project_key = models.CharField(max_length=100)
    issue_type = models.CharField(max_length=100, default="Task")
    enabled = models.BooleanField(default=True)
    resolved_transition = models.CharField(max_length=100, blank=True)


class JiraAssociation(TenantRecord):
    finding = models.ForeignKey(Finding, on_delete=models.CASCADE)
    connection = models.ForeignKey(JiraConnection, on_delete=models.PROTECT)
    issue_id = models.CharField(max_length=100)
    issue_key = models.CharField(max_length=100)
    managed_comment_id = models.CharField(max_length=100, blank=True)
    payload_hash = models.CharField(max_length=64, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["finding", "connection"], name="jira_association_once"),
            models.UniqueConstraint(fields=["connection", "issue_id"], name="jira_issue_once"),
        ]


class OutboxEvent(TenantRecord):
    observation = models.ForeignKey(Observation, on_delete=models.CASCADE)
    binding = models.ForeignKey(JiraBinding, on_delete=models.CASCADE)
    state = models.CharField(max_length=30, default="pending")
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["observation", "binding"], name="delivery_once")
        ]
        indexes = [models.Index(fields=["organization", "state", "next_attempt_at"])]


class DeliveryAttempt(TenantRecord):
    event = models.ForeignKey(OutboxEvent, on_delete=models.CASCADE)
    outcome = models.CharField(max_length=50)
    http_status = models.PositiveIntegerField(null=True)


class AuditEvent(TenantRecord):
    actor = models.CharField(max_length=100, blank=True)
    action = models.CharField(max_length=100)
    target = models.CharField(max_length=200, blank=True)


class BillingEvent(models.Model):
    event_id = models.CharField(max_length=100, primary_key=True)
    created_at = models.DateTimeField(auto_now_add=True)


class RateBucket(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    window = models.BigIntegerField()
    count = models.PositiveIntegerField(default=0)
