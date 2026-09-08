"""Authentication views keep reset links on the canonical, operator-controlled origin."""

from urllib.parse import urlsplit

from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView, PasswordResetView
from django.http import HttpResponse

from .services import rate_limit


class EmailAuthenticationForm(AuthenticationForm):
    username = forms.EmailField(label="Email", max_length=150)

    def clean_username(self):
        return self.cleaned_data["username"].strip().lower()


class HubLoginView(LoginView):
    template_name = "registration/login.html"
    authentication_form = EmailAuthenticationForm


class HubPasswordResetView(PasswordResetView):
    extra_context = {"title": "Reset your password", "submit": "Send reset link"}

    def post(self, request, *args, **kwargs):
        if not rate_limit("password-reset:" + request.META.get("REMOTE_ADDR", ""), 5, 3600):
            return HttpResponse("Please try again later.", status=429)
        origin = urlsplit(settings.PUBLIC_URL)
        self.extra_email_context = {"domain": origin.netloc, "protocol": origin.scheme}
        return super().post(request, *args, **kwargs)
