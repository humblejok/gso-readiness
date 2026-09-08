import re

from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import User


class SignupForm(UserCreationForm):
    email = forms.EmailField(max_length=150)

    class Meta:
        model = User
        fields = ("email",)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "Unable to register this address. Try sign-in or account recovery."
            )
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = self.cleaned_data["email"]
        user.is_active = False
        if commit:
            user.save()
        return user


class WorkspaceForm(forms.Form):
    name = forms.CharField(max_length=160, label="Workspace name")


class TokenForm(forms.Form):
    name = forms.CharField(max_length=100)
    scopes = forms.MultipleChoiceField(
        choices=[
            ("reviews:write", "Upload reviews"),
            ("findings:read", "Read findings"),
        ],
        widget=forms.CheckboxSelectMultiple,
    )
    repository_external_id = forms.CharField(
        max_length=500,
        required=False,
        help_text="Optional: restrict the token to one stable repository external ID.",
    )
    days = forms.IntegerField(min_value=1, max_value=365, initial=90, label="Expires after days")


class InviteForm(forms.Form):
    email = forms.EmailField()
    role = forms.ChoiceField(choices=[(r, r.title()) for r in ("admin", "reviewer", "viewer")])


class BrandingForm(forms.Form):
    brand_name = forms.CharField(max_length=100)
    brand_color = forms.RegexField(r"^#[0-9a-fA-F]{6}$", initial="#155eef")
    logo_url = forms.URLField(
        required=False,
        assume_scheme="https",
        help_text="Optional HTTPS logo URL; visitors' browsers load it from this host.",
    )
    custom_domain = forms.CharField(
        max_length=253,
        required=False,
        help_text="Optional hostname. DNS verification and operator TLS activation are required.",
    )

    def clean_logo_url(self):
        from urllib.parse import urlsplit

        value = self.cleaned_data["logo_url"]
        parsed = urlsplit(value)
        if value and (parsed.scheme != "https" or parsed.username or parsed.password):
            raise forms.ValidationError("Use a credential-free HTTPS URL.")
        return value

    def clean_custom_domain(self):
        value = self.cleaned_data["custom_domain"].lower().rstrip(".")
        if value and not re.fullmatch(
            r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}",
            value,
        ):
            raise forms.ValidationError("Enter a hostname, without a scheme or path.")
        return value


class JiraForm(forms.Form):
    name = forms.CharField(max_length=100)
    base_url = forms.URLField(assume_scheme="https")
    token = forms.CharField(widget=forms.PasswordInput, max_length=4000)


class BindingForm(forms.Form):
    repository = forms.UUIDField()
    connection = forms.UUIDField()
    project_key = forms.RegexField(r"^[A-Z][A-Z0-9_]{0,99}$")
    issue_type = forms.CharField(max_length=100, initial="Task")
    resolved_transition = forms.CharField(max_length=100, required=False)


class UploadForm(forms.Form):
    envelope = forms.FileField(
        help_text="Import-envelope JSON, including the full review and remediation plans. Maximum 10 MiB."
    )
