"""Session-only, workspace-scoped bug and feature request pages."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods

from . import change_requests, forms
from .models import ChangeRequest, ChangeRequestActivity
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
        }
    )
    transition_form = forms.ChangeRequestTransitionForm(
        initial={
            "status": item.next_status,
            "revision": item.revision,
        }
    )
    if request.method == "POST":
        if request.membership.role not in {"owner", "admin", "reviewer"}:
            raise PermissionDenied
        require_writes()
        action = request.POST.get("action")
        if action == "edit":
            form = edit_form = forms.ChangeRequestEditForm(request.POST)
        elif action == "transition":
            form = transition_form = forms.ChangeRequestTransitionForm(request.POST)
        else:
            raise PermissionDenied("Unknown request action.")
        if form.is_valid():
            try:
                change_requests.update(
                    pk, action=action, actor=request.user.get_username(), **form.cleaned_data
                )
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
        activities=Paginator(
            ChangeRequestActivity.objects.filter(change_request=item).order_by("-revision"), 20
        ).get_page(request.GET.get("page")),
        statuses=ChangeRequest.Status.choices,
    )
