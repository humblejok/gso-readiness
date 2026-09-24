"""Session-only, workspace-scoped bug and feature request pages."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods

from . import change_requests, forms, handoffs
from .models import (
    ChangeRequest,
    ChangeRequestActivity,
    ProjectRelationship,
    Repository,
    RequestHandoff,
    RequestImplementation,
)
from .services import require_writes
from .views import get_object_or_404, page, role_required, workspace_url


@require_http_methods(["GET"])
def index(request, organization_id):
    query = ChangeRequest.objects.all()
    search = request.GET.get("q", "").strip()[:200]
    if search:
        query = query.filter(description__icontains=search)
    for field, choices in (
        ("kind", ChangeRequest.Kind.values),
        ("status", ChangeRequest.Status.values),
    ):
        value = request.GET.get(field, "")
        if value in choices:
            query = query.filter(**{field: value})
    return page(
        request,
        "requests.html",
        rows=Paginator(query.order_by("-updated_at", "pk"), 25).get_page(request.GET.get("page")),
        kinds=ChangeRequest.Kind.choices,
        statuses=ChangeRequest.Status.choices,
    )


@role_required("owner", "admin", "reviewer")
@require_http_methods(["GET", "POST"])
def new(request, organization_id):
    require_writes()
    form = forms.ChangeRequestForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            item = change_requests.create(**form.cleaned_data, actor=request.user.get_username())
        except ValidationError as error:
            form.add_error(None, error.messages)
        else:
            messages.success(request, "Request created with Open status.")
            return redirect(workspace_url(request, f"requests/{item.pk}/"))
    return page(
        request,
        "form.html",
        title="New bug or feature request",
        form=form,
        submit="Create request",
        detail="Saved in this workspace. New requests start in Open status.",
    )


@require_http_methods(["GET", "POST"])
def detail(request, organization_id, pk):
    item = get_object_or_404(ChangeRequest.objects, pk=pk)
    edit_form = forms.ChangeRequestEditForm(
        initial={
            "kind": item.kind,
            "description": item.description,
            "revision": item.revision,
            "repository_external_id": item.repository_external_id,
        }
    )
    transition_class = (
        forms.RequestClosureForm
        if item.status in {"implemented", "closed"}
        else forms.ChangeRequestTransitionForm
    )
    transition_form = transition_class(
        initial={
            "status": item.next_status,
            "revision": item.revision,
        }
    )
    analysis_form = forms.RequestAnalysisForm(initial={**item.analysis, "revision": item.revision})
    related = handoffs.relationships(item.repository_external_id)
    draft_targets = {row["repository_external_id"]: row for row in item.handoff.get("targets", [])}
    choices = {row["repository_external_id"]: row["name"] for row in related}
    choices.update(
        {key: choices.get(key, key + " (relationship unavailable)") for key in draft_targets}
    )
    target_initial = [
        {
            "repository_external_id": key,
            "selected": key in draft_targets,
            **{
                field: draft_targets.get(key, {}).get(field, "")
                for field in ("requirements", "acceptance_criteria")
            },
        }
        for key in choices
    ]
    reference = item.handoff.get("implementation", {})
    attempt = (
        RequestImplementation.objects.filter(change_request=item, status="succeeded")
        .order_by("-created_at")
        .first()
    )
    if attempt:
        reference = {key: attempt.data["completion"][key] for key in ("commit_sha", "pull_request")}
    handoff_form = forms.HandoffForm(
        initial={"revision": item.revision, "availability": "unknown", **item.handoff, **reference}
    )
    target_forms = forms.HandoffTargetFormSet(
        initial=target_initial, prefix="targets", form_kwargs={"choices": list(choices.items())}
    )
    can_cancel = item.created_by == request.user.get_username() and item.status in {
        "open",
        "analyzed",
        "specified",
    }
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "cancel" and not can_cancel:
            raise PermissionDenied("Only the creator can cancel before implementation.")
        if action != "cancel" and request.membership.role not in {"owner", "admin", "reviewer"}:
            raise PermissionDenied
        require_writes()
        if action == "edit":
            form = edit_form = forms.ChangeRequestEditForm(request.POST)
        elif action in {"transition", "accept", "cancel"}:
            form = transition_form = transition_class(request.POST)
        elif action == "edit_analysis":
            form = analysis_form = forms.RequestAnalysisForm(request.POST)
        elif action == "edit_handoff":
            form = handoff_form = forms.HandoffForm(request.POST)
            target_forms = forms.HandoffTargetFormSet(
                request.POST, prefix="targets", form_kwargs={"choices": list(choices.items())}
            )
        else:
            raise PermissionDenied("Unknown request action.")
        if form.is_valid() and (action != "edit_handoff" or target_forms.is_valid()):
            data = dict(form.cleaned_data)
            if action == "edit_analysis":
                data = {"revision": data.pop("revision"), "analysis": data}
            if action == "edit_handoff":
                revision = data.pop("revision")
                data["implementation"] = {
                    key: data.pop(key) for key in ("commit_sha", "pull_request")
                }
                data["targets"] = [
                    {
                        key: row[key]
                        for key in ("repository_external_id", "requirements", "acceptance_criteria")
                    }
                    for row in target_forms.cleaned_data
                    if row.get("selected")
                ]
                data = {"revision": revision, "handoff": data}
            try:
                change_requests.update(pk, action=action, actor=request.user.get_username(), **data)
            except ChangeRequest.DoesNotExist as error:
                raise Http404 from error
            except ValidationError as error:
                form.add_error(None, error.messages)
            else:
                messages.success(request, "Request saved.")
                return redirect(workspace_url(request, f"requests/{pk}/"))
    return page(
        request,
        "request_detail.html",
        item=item,
        edit_form=edit_form,
        transition_form=transition_form,
        analysis_form=analysis_form,
        handoff_form=handoff_form,
        target_forms=target_forms,
        upstream_handoff=handoffs.incoming(item),
        downstream_handoffs=RequestHandoff.objects.filter(source_request=item).select_related(
            "target", "downstream_request"
        ),
        implementations=RequestImplementation.objects.filter(change_request=item).order_by(
            "-created_at"
        )[:25],
        can_cancel=can_cancel,
        activities=Paginator(
            ChangeRequestActivity.objects.filter(change_request=item).order_by("-revision"), 20
        ).get_page(request.GET.get("page")),
        statuses=[
            (value, label) for value, label in ChangeRequest.Status.choices if value != "cancelled"
        ],
    )


@role_required("owner", "admin")
@require_http_methods(["GET", "POST"])
def project_relationships(request, organization_id):
    form = forms.ProjectRelationshipForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            handoffs.configure_relationship(
                form.cleaned_data["source"].pk,
                form.cleaned_data["target"].pk,
                form.cleaned_data["description"],
                request.user.get_username(),
                remove=request.POST.get("action") == "remove",
            )
        except (ValidationError, Repository.DoesNotExist) as error:
            form.add_error(
                None,
                error.messages
                if isinstance(error, ValidationError)
                else "Select projects in this workspace.",
            )
        else:
            messages.success(
                request, "Project relationship saved. Existing published handoffs are unchanged."
            )
            return redirect(workspace_url(request, "project-relationships/"))
    return page(
        request,
        "project_relationships.html",
        form=form,
        relationships=ProjectRelationship.objects.select_related("source", "target").order_by(
            "source__name", "target__name"
        ),
    )
