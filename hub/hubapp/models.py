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
    is_tech_lead = models.BooleanField(
        default=False,
        help_text="Instance-wide API privilege across workspaces. Does not grant Django staff or superuser access.",
    )

    class Meta:
        constraints = [models.UniqueConstraint(Lower("email"), name="user_email_case_unique")]


class Organization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=160)
    management_revision = models.PositiveIntegerField(default=0)
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


class UserApiToken(models.Model):
    """Global authentication control record; never a tenant-data manager bypass."""

    SCOPES = [
        ("workspaces:read", "List and read all workspaces"),
        ("workspaces:write", "Create workspaces and edit workspace names"),
        ("reviews:write", "Import reviews and submit evidence-based revalidation"),
        ("findings:read", "Read findings and remediation"),
        ("findings:write", "Queue/cancel implementation and edit remediation drafts"),
        ("findings:implement", "Claim and complete queued implementations"),
        ("requests:read", "Read project requests"),
        ("requests:analyse", "Submit analysis for open requests (not accept it)"),
        ("requests:implement", "Claim and implement user-specified requests"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    token_hash = models.CharField(max_length=128)
    scopes = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True)


class UserTokenEvent(models.Model):
    token = models.ForeignKey(UserApiToken, on_delete=models.CASCADE, related_name="events")
    created_at = models.DateTimeField(auto_now_add=True)
    action = models.CharField(max_length=30)


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
    implementation_requested = models.BooleanField(default=False)
    implementation_text = models.TextField(blank=True)
    implementation_revision = models.PositiveIntegerField(default=0)

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


class FindingActivity(TenantRecord):
    """Immutable completion/comment history and optimistic, expiring implementation claims."""

    finding = models.ForeignKey(Finding, on_delete=models.CASCADE, related_name="activities")
    baseline_observation = models.ForeignKey(Observation, on_delete=models.CASCADE)
    request_id = models.UUIDField()
    kind = models.CharField(max_length=20)
    status = models.CharField(max_length=20)
    actor = models.CharField(max_length=100)
    revision = models.PositiveIntegerField()
    proposal = models.TextField(blank=True)
    comment = models.TextField(blank=True)
    data = models.JSONField(default=dict)
    expires_at = models.DateTimeField(null=True)
    payload_bytes = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "request_id"], name="finding_activity_request"
            ),
            models.UniqueConstraint(
                fields=["finding"],
                condition=models.Q(status="running"),
                name="one_running_implementation",
            ),
        ]


class ChangeRequest(TenantRecord):
    class Kind(models.TextChoices):
        BUG = "bug", "Bug fix"
        FEATURE = "feature", "Feature request"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ANALYZED = "analyzed", "Analyzed"
        SPECIFIED = "specified", "Specified"
        IMPLEMENTED = "implemented", "Implemented"
        CLOSED = "closed", "Closed"
        CANCELLED = "cancelled", "Cancelled"

    kind = models.CharField(max_length=10, choices=Kind.choices)
    description = models.TextField(max_length=20000)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    created_by = models.CharField(max_length=254)
    repository_external_id = models.CharField(max_length=500, blank=True)
    analysis = models.JSONField(default=dict, blank=True)
    analysis_submission_id = models.UUIDField(null=True, blank=True)
    analysis_submission_digest = models.CharField(max_length=64, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    revision = models.PositiveIntegerField(default=1)
    payload_bytes = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(kind__in=["bug", "feature"]), name="request_kind_valid"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=[
                        "open",
                        "analyzed",
                        "specified",
                        "implemented",
                        "closed",
                        "cancelled",
                    ]
                ),
                name="request_status_valid",
            ),
        ]
        indexes = [models.Index(fields=["organization", "status", "-updated_at"])]

    @property
    def next_status(self):
        states = ["open", "analyzed", "specified", "implemented", "closed"]
        if self.status not in states:
            return None
        index = states.index(self.status)
        return states[index + 1] if index + 1 < len(states) else None


class ChangeRequestActivity(TenantRecord):
    """Append-only snapshots of user edits and workflow transitions."""

    change_request = models.ForeignKey(
        ChangeRequest, on_delete=models.CASCADE, related_name="activities"
    )
    actor = models.CharField(max_length=254)
    action = models.CharField(
        max_length=16,
        choices=[("created", "Created"), ("edited", "Edited"), ("transitioned", "Status changed")],
    )
    revision = models.PositiveIntegerField()
    kind = models.CharField(max_length=10, choices=ChangeRequest.Kind.choices)
    description = models.TextField()
    repository_external_id = models.CharField(max_length=500, blank=True)
    analysis = models.JSONField(default=dict, blank=True)
    previous_status = models.CharField(max_length=16, blank=True)
    status = models.CharField(max_length=16, choices=ChangeRequest.Status.choices)
    payload_bytes = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["change_request", "revision"], name="request_revision_unique"
            )
        ]


class RequestImplementation(TenantRecord):
    """Frozen approved specification, exclusive lease and immutable completion receipt."""

    change_request = models.ForeignKey(
        ChangeRequest, on_delete=models.CASCADE, related_name="implementations"
    )
    request_id = models.UUIDField()
    actor = models.CharField(max_length=100)
    revision = models.PositiveIntegerField()
    specification = models.JSONField()
    expires_at = models.DateTimeField()
    status = models.CharField(
        max_length=16,
        default="running",
        choices=[
            (s, s.title()) for s in ("running", "succeeded", "failed", "cancelled", "expired")
        ],
    )
    comment = models.TextField(blank=True)
    data = models.JSONField(default=dict)
    payload_bytes = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "request_id"], name="request_implementation_identity"
            ),
            models.UniqueConstraint(
                fields=["change_request"],
                condition=models.Q(status="running"),
                name="one_running_request_implementation",
            ),
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
