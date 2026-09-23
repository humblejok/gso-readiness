import json
import secrets
from datetime import timedelta
from functools import wraps
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import check_password, make_password
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404 as django_object_or_404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import forms
from .contracts import canonical
from .finding_work import WorkConflict, configure, remediation_text
from .imports import ImportConflict, import_review
from .models import (
    ApiClient,
    AuditEvent,
    ChangeRequest,
    ChangeRequestActivity,
    Finding,
    FindingActivity,
    Invitation,
    JiraBinding,
    JiraConnection,
    Membership,
    Observation,
    Organization,
    OutboxEvent,
    Repository,
    RequestImplementation,
    ReviewImport,
    User,
)
from .services import (
    audit,
    create_workspace,
    entitlement,
    issue_token,
    rate_limit,
    require_writes,
    usage,
)
from .tenancy import tenant_scope


def get_object_or_404(query, **kwargs):
    try:
        return django_object_or_404(query, **kwargs)
    except (ValidationError, ValueError, TypeError) as error:
        raise Http404 from error


def workspace_url(request, suffix=""):
    return f"/w/{request.workspace.id}/{suffix}"


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.membership or request.membership.role not in roles:
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


def page(request, template, **context):
    if getattr(request, "workspace", None):
        sub, limits, active = entitlement()
        context.update(
            subscription=sub,
            limits=limits,
            writable=active,
            brand_enabled=active and limits["branding"],
        )
    return render(request, template, context)


def signup(request):
    form = forms.SignupForm(request.POST or None)
    if request.method == "POST":
        if not rate_limit("signup:" + request.META.get("REMOTE_ADDR", ""), 10, 3600):
            return HttpResponse("Please try again later.", status=429)
        if form.is_valid():
            try:
                user = form.save()
            except IntegrityError:
                form.add_error(None, "Unable to register. Try account recovery.")
            else:
                send_verification(user)
                return page(
                    request,
                    "message.html",
                    title="Check your email",
                    detail="Confirm your email address before signing in. In local development the email appears in the server console.",
                )
    return page(
        request,
        "form.html",
        title="Create your account",
        form=form,
        submit="Create account",
    )


def send_verification(user):
    token = signing.dumps({"user": user.pk, "email": user.email}, salt="verify-email")
    send_mail(
        "Confirm your Finding Hub account",
        f"Confirm your email within 24 hours:\n{settings.PUBLIC_URL}/accounts/verify/{token}/",
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
    )


def resend_verification(request):
    from django import forms as django_forms

    class EmailForm(django_forms.Form):
        email = django_forms.EmailField()

    form = EmailForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if not rate_limit("verify-resend:" + request.META.get("REMOTE_ADDR", ""), 5, 3600):
            return HttpResponse("Please try again later.", status=429)
        user = User.objects.filter(
            email__iexact=form.cleaned_data["email"], email_verified=False
        ).first()
        if user:
            send_verification(user)
        return page(
            request,
            "message.html",
            title="Check your email",
            detail="If an unverified account matches, we sent a new confirmation link.",
        )
    return page(
        request,
        "form.html",
        title="Resend email confirmation",
        form=form,
        submit="Send confirmation",
    )


def verify(request, token):
    try:
        data = signing.loads(token, salt="verify-email", max_age=86400)
        user = User.objects.get(pk=data["user"], email=data["email"])
    except (signing.BadSignature, User.DoesNotExist, KeyError):
        return page(
            request,
            "message.html",
            title="Link unavailable",
            detail="This confirmation link is invalid or expired. Request another confirmation email.",
        )
    if request.method == "POST":
        if not user.email_verified:
            user.email_verified, user.is_active = True, True
            user.save(update_fields=["email_verified", "is_active"])
        return redirect("login")
    return page(request, "form.html", title="Confirm your email", submit="Confirm email address")


@login_required
def home(request):
    memberships = Membership.objects.filter(user=request.user).select_related("organization")
    return page(request, "home.html", memberships=memberships)


@login_required
def new_workspace(request):
    form = forms.WorkspaceForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            org = create_workspace(request.user, form.cleaned_data["name"])
        except ValidationError as error:
            form.add_error(None, error)
        else:
            return redirect(f"/w/{org.id}/")
    return page(
        request,
        "form.html",
        title="Create a workspace",
        form=form,
        submit="Create workspace",
    )


def dashboard(request, organization_id):
    query = Finding.objects.select_related("repository")
    show_all = request.GET.get("show_all") == "1" or request.GET.get("lifecycle") in {
        "inconclusive",
        "rejected",
    }
    if not show_all:
        query = query.exclude(
            Q(lifecycle__in=["inconclusive", "rejected"])
            | Q(verification_status__in=["inconclusive", "rejected"])
        )
    for key in ("severity", "lifecycle", "category"):
        if request.GET.get(key):
            query = query.filter(**{key: request.GET[key]})
    if request.GET.get("q"):
        query = query.filter(
            Q(title__icontains=request.GET["q"][:200])
            | Q(display_id__icontains=request.GET["q"][:200])
        )
    if request.GET.get("to_implement") == "1":
        query = query.filter(implementation_requested=True, lifecycle="open")
    rows = Paginator(query.order_by("-last_seen", "pk"), 50).get_page(request.GET.get("page"))
    return page(
        request,
        "dashboard.html",
        findings=rows,
        show_all=show_all,
        repositories=Repository.objects.order_by("name"),
        total=Finding.objects.count(),
        open_count=Finding.objects.filter(lifecycle="open").count(),
        critical_count=Finding.objects.filter(
            lifecycle="open", severity__in=["critical", "high"]
        ).count(),
        review_count=ReviewImport.objects.count(),
        recent=ReviewImport.objects.order_by("-created_at", "pk")[:5],
    )


def finding_detail(request, organization_id, pk):
    finding = get_object_or_404(Finding.objects, pk=pk)
    observations = (
        Observation.objects.filter(finding=finding)
        .select_related("review_import")
        .order_by("-created_at", "pk")
    )
    latest = observations.first()
    remediation = remediation_text(finding, latest)
    form = forms.ImplementationForm(
        request.POST if request.method == "POST" else None,
        initial={"revision": finding.implementation_revision, "remediation": remediation},
    )
    if request.method == "POST":
        if request.membership.role not in {"owner", "admin", "reviewer"}:
            raise PermissionDenied
        if form.is_valid():
            try:
                configure(
                    finding.id,
                    "edit",
                    form.cleaned_data["revision"],
                    request.user.pk,
                    form.cleaned_data["remediation"],
                )
            except (WorkConflict, ValidationError) as error:
                form.add_error(None, str(error))
            else:
                messages.success(
                    request, "Implementation proposal saved. Any older worker claim was cancelled."
                )
                return redirect(workspace_url(request, f"findings/{finding.id}/"))
    return page(
        request,
        "finding.html",
        finding=finding,
        observations=Paginator(observations, 25).get_page(request.GET.get("page")),
        remediation=remediation,
        implementation_form=form,
        activities=finding.activities.order_by("-created_at", "-pk")[:50],
        deliveries=OutboxEvent.objects.filter(observation__finding=finding).order_by("-created_at")[
            :20
        ],
    )


@require_POST
@role_required("owner", "admin", "reviewer")
def finding_implementation(request, organization_id, pk):
    finding = get_object_or_404(Finding.objects, pk=pk)
    try:
        revision = int(request.POST.get("revision", ""))
        configure(finding.id, request.POST.get("action"), revision, request.user.pk)
    except (ValueError, ValidationError):
        return HttpResponse(
            "Implementation request could not be applied. Refresh the finding and try again.",
            status=409,
        )
    return redirect(
        workspace_url(
            request, f"findings/{finding.id}/" if request.POST.get("return") == "detail" else ""
        )
    )


@role_required("owner", "admin", "reviewer")
def upload(request, organization_id):
    form = forms.UploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        if not rate_limit("upload:" + str(organization_id), 30):
            return HttpResponse("Upload rate limit reached.", status=429)
        try:
            file = form.cleaned_data["envelope"]
            if file.size > settings.DATA_UPLOAD_MAX_MEMORY_SIZE:
                raise ValidationError("File exceeds 10 MiB.")
            data = json.loads(
                file.read(),
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
            key = f"{data['repository']['external_id']}:{data['review']['run']['run_id']}"
            record, created = import_review(data, key, request.user.pk)
        except (ValueError, KeyError, TypeError, RecursionError):
            form.add_error(None, "Invalid JSON import envelope.")
        except (ValidationError, ImportConflict) as error:
            form.add_error(None, str(error))
        else:
            messages.success(
                request,
                "Review imported." if created else "This identical review was already imported.",
            )
            return redirect(workspace_url(request))
    return page(
        request,
        "form.html",
        title="Import a review",
        form=form,
        submit="Validate and import",
    )


@role_required("owner", "admin")
def tokens(request, organization_id):
    form = forms.TokenForm(request.POST or None)
    secret = None
    if request.method == "POST":
        if request.POST.get("revoke"):
            token = get_object_or_404(ApiClient.objects, pk=request.POST["revoke"])
            token.revoked_at = timezone.now()
            token.save(update_fields=["revoked_at"])
            audit("token.revoked", request.user.pk, token.pk)
            return redirect(workspace_url(request, "tokens/"))
        if form.is_valid():
            require_writes()
            secret = issue_token(**form.cleaned_data)
    return page(
        request,
        "tokens.html",
        form=form,
        secret=secret,
        tokens=ApiClient.objects.order_by("-created_at")[:100],
    )


@role_required("owner", "admin")
def members(request, organization_id):
    form = forms.InviteForm(request.POST or None)
    if request.method == "POST" and request.POST.get("update_member"):
        if request.membership.role != "owner":
            raise PermissionDenied
        with transaction.atomic():
            Organization.objects.select_for_update().get(pk=organization_id)
            target = get_object_or_404(
                Membership.objects,
                pk=request.POST["update_member"],
                organization_id=organization_id,
            )
            new_role = request.POST.get("role")
            if target.role == "owner" or new_role not in {
                "admin",
                "reviewer",
                "viewer",
            }:
                raise PermissionDenied("Ownership cannot be removed using a role edit.")
            target.role = new_role
            target.billing_access = request.POST.get("billing_access") == "on"
            target.save(update_fields=["role", "billing_access"])
            audit("member.permissions_changed", request.user.pk, target.user_id)
        return redirect(workspace_url(request, "members/"))
    if request.method == "POST" and request.POST.get("transfer_owner"):
        if (
            request.membership.role != "owner"
            or request.POST.get("confirm_workspace") != request.workspace.name
        ):
            raise PermissionDenied(
                "Ownership transfer requires the owner and exact workspace-name confirmation."
            )
        with transaction.atomic():
            Organization.objects.select_for_update().get(pk=organization_id)
            target = get_object_or_404(
                Membership.objects,
                pk=request.POST["transfer_owner"],
                organization_id=organization_id,
            )
            target.role, target.billing_access = "owner", True
            target.save()
            if target.pk != request.membership.pk:
                request.membership.role = "admin"
                request.membership.save(update_fields=["role"])
            audit("workspace.ownership_transferred", request.user.pk, target.user_id)
        return redirect(workspace_url(request, "members/"))
    if request.method == "POST" and request.POST.get("remove"):
        with transaction.atomic():
            Organization.objects.select_for_update().get(pk=organization_id)
            member = get_object_or_404(
                Membership.objects,
                pk=request.POST["remove"],
                organization_id=organization_id,
            )
            if member.role == "owner" or (
                member.role == "admin" and request.membership.role != "owner"
            ):
                raise PermissionDenied(
                    "Only owners can remove administrators; transfer ownership first."
                )
            audit("member.removed", request.user.pk, member.user_id)
            member.delete()
        return redirect(workspace_url(request, "members/"))
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            Organization.objects.select_for_update().get(pk=organization_id)
            limits = require_writes()
            current = Membership.objects.filter(organization_id=organization_id).count()
            pending = Invitation.objects.filter(
                accepted_at__isnull=True, expires_at__gt=timezone.now()
            ).count()
            if current + pending >= limits["members"]:
                form.add_error(None, "Member allowance reached, including pending invitations.")
            elif form.cleaned_data["role"] == "admin" and request.membership.role != "owner":
                raise PermissionDenied("Only owners can invite administrators.")
            else:
                secret = secrets.token_urlsafe(32)
                invitation = Invitation.objects.create(
                    email=form.cleaned_data["email"].lower(),
                    role=form.cleaned_data["role"],
                    token_hash=make_password(secret),
                    expires_at=timezone.now() + timedelta(days=7),
                )
                signed = signing.dumps(
                    {
                        "org": str(organization_id),
                        "id": str(invitation.pk),
                        "secret": secret,
                    },
                    salt="workspace-invite",
                )
                url = f"{settings.PUBLIC_URL}/invitations/{signed}/"
                transaction.on_commit(
                    lambda: send_mail(
                        "Finding Hub workspace invitation",
                        f"You were invited to {request.workspace.name}. Create/verify an account with this email, then accept:\n{url}",
                        settings.DEFAULT_FROM_EMAIL,
                        [invitation.email],
                    )
                )
                audit("member.invited", request.user.pk, invitation.pk)
                messages.success(request, "Invitation sent. It expires in seven days.")
                return redirect(workspace_url(request, "members/"))
    return page(
        request,
        "members.html",
        form=form,
        members=Membership.objects.filter(organization_id=organization_id).select_related("user"),
        invitations=Invitation.objects.filter(
            accepted_at__isnull=True, expires_at__gt=timezone.now()
        ),
    )


@login_required
def accept_invitation(request, token):
    try:
        data = signing.loads(token, salt="workspace-invite", max_age=7 * 86400)
    except signing.BadSignature:
        raise PermissionDenied("Invitation expired or invalid.")
    with tenant_scope(data["org"]), transaction.atomic():
        org = get_object_or_404(
            Organization.objects.select_for_update(), pk=data["org"], suspended=False
        )
        invitation = get_object_or_404(
            Invitation.objects,
            pk=data["id"],
            accepted_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        if (
            not request.user.email_verified
            or invitation.email.lower() != request.user.email.lower()
            or not check_password(data["secret"], invitation.token_hash)
        ):
            raise PermissionDenied("Sign in with the verified email address that was invited.")
        if request.method == "POST":
            limits = require_writes()
            if Membership.objects.filter(organization=org).count() >= limits["members"]:
                raise PermissionDenied("Member allowance reached.")
            Membership.objects.get_or_create(
                organization=org, user=request.user, defaults={"role": invitation.role}
            )
            invitation.accepted_at = timezone.now()
            invitation.save(update_fields=["accepted_at"])
            audit("invitation.accepted", request.user.pk, invitation.pk)
            return redirect(f"/w/{org.id}/")
    return page(request, "form.html", title=f"Join {org.name}", submit="Accept invitation")


@role_required("owner")
def branding(request, organization_id):
    limits = require_writes()
    if not limits["branding"]:
        return page(
            request,
            "message.html",
            title="White-label settings",
            detail="Branding is included with an enterprise agreement. Contact the operator to enable it.",
        )
    org = request.workspace
    form = forms.BrandingForm(
        request.POST or None,
        initial={k: getattr(org, k) for k in forms.BrandingForm.base_fields},
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            org = Organization.objects.select_for_update().get(pk=org.pk)
            if form.cleaned_data["custom_domain"] != org.custom_domain:
                org.domain_verified_at = None
                org.domain_challenge = secrets.token_urlsafe(32)
            for key, value in form.cleaned_data.items():
                setattr(org, key, value)
            org.save()
            audit("branding.updated", request.user.pk, org.pk)
        messages.success(
            request,
            "Branding saved. A new domain stays inactive until DNS and TLS are verified by the operator.",
        )
        return redirect(workspace_url(request, "branding/"))
    return page(
        request,
        "form.html",
        title="White-label settings",
        form=form,
        submit="Save branding",
        detail=f"DNS challenge: _finding-hub.{org.custom_domain} TXT {org.domain_challenge}"
        if org.custom_domain
        else "Copyright and license notices remain visible in the Legal page.",
    )


def billing(request, organization_id):
    if request.membership.role != "owner" and not request.membership.billing_access:
        raise PermissionDenied
    from . import billing as adapter

    if request.method == "POST":
        try:
            if request.POST.get("action") == "portal":
                url = adapter.portal(request.workspace)
            else:
                url = adapter.checkout(
                    request.workspace,
                    request.POST.get("plan"),
                    request.POST.get("interval"),
                )
            if (
                urlsplit(url).hostname not in {"checkout.stripe.com", "billing.stripe.com"}
                or urlsplit(url).scheme != "https"
            ):
                raise ValidationError("Invalid payment-provider redirect.")
            return redirect(url)
        except ValidationError as error:
            messages.error(request, str(error))
        except Exception:
            messages.error(
                request,
                "Payment provider unavailable. No access change has been applied; try again later.",
            )
    return page(
        request,
        "billing.html",
        usage=usage(),
        online_billing=settings.BILLING_PROVIDER == "stripe",
    )


@csrf_exempt
@require_POST
def billing_webhook(request):
    import stripe

    from .billing import receive_webhook

    try:
        receive_webhook(request.body, request.headers.get("Stripe-Signature", ""))
    except (ValueError, stripe.SignatureVerificationError):
        return JsonResponse({"error": "Invalid signature or event."}, status=400)
    except ValidationError:
        return JsonResponse({"error": "Billing event requires operator attention."}, status=503)
    except Exception:
        return JsonResponse({"error": "Billing reconciliation unavailable."}, status=503)
    return JsonResponse({"received": True})


@role_required("owner", "admin")
def integrations(request, organization_id):
    from .jira import DeliveryError, JiraClient, encrypt_secret, validate_url

    form = forms.JiraForm(request.POST or None)
    if request.method == "POST":
        action = request.POST.get("action", "create")
        if action in {"enable", "disable", "test", "rotate"}:
            connection = get_object_or_404(
                JiraConnection.objects, pk=request.POST.get("connection")
            )
            if action == "disable":
                connection.enabled = False
                connection.save(update_fields=["enabled"])
            else:
                require_writes()
                if action == "rotate":
                    try:
                        connection.encrypted_token = encrypt_secret(
                            request.POST.get("replacement_token", "")
                        )
                        connection.save(update_fields=["encrypted_token"])
                        messages.success(
                            request,
                            "Jira credential replaced. The new PAT will not be shown again.",
                        )
                    except ValidationError as error:
                        messages.error(request, str(error))
                elif action == "enable":
                    validate_url(connection.base_url)
                    connection.enabled = True
                    connection.save(update_fields=["enabled"])
                else:
                    try:
                        JiraClient(connection).request("GET", "myself")
                        messages.success(request, "Jira connection succeeded.")
                    except (DeliveryError, ValidationError):
                        messages.error(
                            request,
                            "Jira connection failed. Check the operator egress/TLS policy and credentials.",
                        )
            audit("jira." + action, request.user.pk, connection.pk)
            return redirect(workspace_url(request, "integrations/"))
        if action == "bind":
            binding_form = forms.BindingForm(request.POST)
            if binding_form.is_valid():
                require_writes()
                data = binding_form.cleaned_data
                repo = get_object_or_404(Repository.objects, pk=data.pop("repository"))
                connection = get_object_or_404(JiraConnection.objects, pk=data.pop("connection"))
                JiraBinding.objects.update_or_create(
                    repository=repo,
                    defaults={
                        "organization_id": organization_id,
                        "connection": connection,
                        **data,
                    },
                )
                audit("jira.binding_changed", request.user.pk, repo.pk)
                return redirect(workspace_url(request, "integrations/"))
            messages.error(request, "Invalid repository binding.")
        elif form.is_valid():
            require_writes()
            try:
                validate_url(form.cleaned_data["base_url"])
                with transaction.atomic():
                    JiraConnection.objects.create(
                        name=form.cleaned_data["name"],
                        base_url=form.cleaned_data["base_url"].rstrip("/"),
                        encrypted_token=encrypt_secret(form.cleaned_data["token"]),
                    )
                audit("jira.created", request.user.pk)
            except (ValidationError, IntegrityError) as error:
                form.add_error(
                    None,
                    "Name already exists." if isinstance(error, IntegrityError) else error,
                )
            else:
                return redirect(workspace_url(request, "integrations/"))
    return page(
        request,
        "integrations.html",
        form=form,
        connections=JiraConnection.objects.order_by("name"),
        repositories=Repository.objects.order_by("name"),
        bindings=JiraBinding.objects.select_related("repository", "connection"),
        enabled=settings.OUTBOUND_INTEGRATIONS_ENABLED,
    )


@role_required("owner", "admin")
def deliveries(request, organization_id):
    if request.method == "POST":
        event = get_object_or_404(OutboxEvent.objects, pk=request.POST.get("event"))
        if event.state in {"failed", "retry", "pending"}:
            action = request.POST.get("action")
            if action == "cancel":
                event.state = "cancelled"
            elif action == "retry":
                require_writes()
                event.state, event.next_attempt_at = "retry", timezone.now()
            else:
                raise PermissionDenied
            event.save()
            audit("delivery." + action, request.user.pk, event.pk)
        return redirect(workspace_url(request, "deliveries/"))
    return page(
        request,
        "deliveries.html",
        events=Paginator(
            OutboxEvent.objects.select_related("observation__finding").order_by(
                "-created_at", "pk"
            ),
            50,
        ).get_page(request.GET.get("page")),
    )


@role_required("owner", "admin")
def export(request, organization_id):
    audit("workspace.exported", request.user.pk, organization_id)
    user_id = request.user.pk

    def chunks():
        with tenant_scope(organization_id):
            yield '{"schema_version":"1.0","imports":['
            first = True
            for record in ReviewImport.objects.order_by("created_at", "pk").iterator(chunk_size=10):
                if not Membership.objects.filter(
                    user_id=user_id,
                    organization_id=organization_id,
                    role__in=["owner", "admin"],
                ).exists():
                    return
                yield ("" if first else ",") + canonical(record.payload)
                first = False
            yield '],"finding_work":['
            first = True
            for finding in Finding.objects.order_by("pk").iterator(chunk_size=50):
                if not Membership.objects.filter(
                    user_id=user_id, organization_id=organization_id, role__in=["owner", "admin"]
                ).exists():
                    return
                record = {
                    "finding_id": str(finding.id),
                    "fingerprint": finding.fingerprint,
                    "repository_id": str(finding.repository_id),
                    "lifecycle": finding.lifecycle,
                    "to_implement": finding.implementation_requested,
                    "revision": finding.implementation_revision,
                    "remediation_draft": finding.implementation_text,
                }
                yield ("" if first else ",") + canonical(record)
                first = False
            yield '],"finding_activities":['
            first = True
            for activity in FindingActivity.objects.order_by("created_at", "pk").iterator(
                chunk_size=10
            ):
                if not Membership.objects.filter(
                    user_id=user_id, organization_id=organization_id, role__in=["owner", "admin"]
                ).exists():
                    return
                record = {
                    "id": str(activity.id),
                    "finding_id": str(activity.finding_id),
                    "baseline_observation_id": str(activity.baseline_observation_id),
                    "request_id": str(activity.request_id),
                    "created_at": activity.created_at.isoformat(),
                    "kind": activity.kind,
                    "status": activity.status,
                    "actor": activity.actor,
                    "revision": activity.revision,
                    "proposal": activity.proposal,
                    "comment": activity.comment,
                    "data": activity.data,
                }
                yield ("" if first else ",") + canonical(record)
                first = False
            for name, model, fields in (
                (
                    "request_implementations",
                    RequestImplementation,
                    (
                        "id",
                        "change_request_id",
                        "request_id",
                        "actor",
                        "revision",
                        "specification",
                        "created_at",
                        "expires_at",
                        "status",
                        "comment",
                        "data",
                    ),
                ),
                (
                    "change_requests",
                    ChangeRequest,
                    (
                        "id",
                        "created_at",
                        "updated_at",
                        "created_by",
                        "kind",
                        "description",
                        "repository_external_id",
                        "analysis",
                        "status",
                        "revision",
                    ),
                ),
                (
                    "change_request_activities",
                    ChangeRequestActivity,
                    (
                        "id",
                        "change_request_id",
                        "created_at",
                        "actor",
                        "action",
                        "revision",
                        "kind",
                        "description",
                        "repository_external_id",
                        "analysis",
                        "previous_status",
                        "status",
                    ),
                ),
            ):
                yield '],"' + name + '":['
                first = True
                for record in (
                    model.objects.order_by("created_at", "pk")
                    .values(*fields)
                    .iterator(chunk_size=20)
                ):
                    if not Membership.objects.filter(
                        user_id=user_id,
                        organization_id=organization_id,
                        role__in=["owner", "admin"],
                    ).exists():
                        return
                    record = {
                        key: value if isinstance(value, int) else str(value)
                        for key, value in record.items()
                    }
                    yield ("" if first else ",") + canonical(record)
                    first = False
            yield "]}"

    response = StreamingHttpResponse(chunks(), content_type="application/json")
    response["Content-Disposition"] = 'attachment; filename="finding-hub-export.json"'
    return response


@role_required("owner")
def deletion_request(request, organization_id):
    if request.method == "POST" and request.POST.get("confirm") == request.workspace.name:
        request.workspace.deletion_requested_at = timezone.now()
        request.workspace.save(update_fields=["deletion_requested_at"])
        audit("workspace.erasure_requested", request.user.pk, organization_id)
        return page(
            request,
            "message.html",
            title="Erasure requested",
            detail="An operator must verify and process this request, including retained backups. Nothing has been deleted yet.",
        )
    from django import forms as django_forms

    class Confirmation(django_forms.Form):
        confirm = django_forms.CharField(label="Type the workspace name to request erasure")

    return page(
        request,
        "form.html",
        title="Request workspace erasure",
        form=Confirmation(),
        submit="Request erasure",
        detail="Export your data first. This request is audited and requires operator processing.",
    )


def legal(request):
    return page(request, "legal.html", source_url=settings.SOURCE_URL)


@role_required("owner", "admin")
def audit_history(request, organization_id):
    return page(
        request,
        "audit.html",
        events=Paginator(AuditEvent.objects.order_by("-created_at", "pk"), 50).get_page(
            request.GET.get("page")
        ),
    )
