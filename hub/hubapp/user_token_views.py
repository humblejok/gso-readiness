from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods

from . import forms, user_tokens
from .models import UserApiToken
from .views import get_object_or_404, page


@login_required
@require_http_methods(["GET", "POST"])
def personal_tokens(request):
    if not request.user.email_verified:
        raise PermissionDenied
    form = forms.UserTokenForm(request.POST if request.method == "POST" else None)
    secret = None
    if request.method == "POST":
        if request.POST.get("revoke"):
            token = get_object_or_404(
                UserApiToken.objects.filter(user=request.user), pk=request.POST["revoke"]
            )
            try:
                user_tokens.revoke(request.user.pk, token.pk)
            except UserApiToken.DoesNotExist as error:
                raise Http404 from error
            return redirect("personal-tokens")
        if not request.user.is_tech_lead:
            raise PermissionDenied("Only designated tech leads can create personal API tokens.")
        if form.is_valid():
            data = dict(form.cleaned_data)
            data.pop("confirm")
            try:
                secret = user_tokens.issue(request.user.pk, **data)
            except ValidationError as error:
                form.add_error(None, error.messages)
    return page(
        request,
        "user_tokens.html",
        form=form,
        secret=secret,
        tokens=Paginator(
            UserApiToken.objects.filter(user=request.user).order_by("-created_at", "pk"), 50
        ).get_page(request.GET.get("page")),
    )
