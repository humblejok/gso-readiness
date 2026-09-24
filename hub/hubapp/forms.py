import re

from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import ChangeRequest, Repository, User, UserApiToken


class UserTokenForm(forms.Form):
    name = forms.CharField(max_length=100)
    scopes = forms.MultipleChoiceField(
        choices=UserApiToken.SCOPES, widget=forms.CheckboxSelectMultiple
    )
    days = forms.IntegerField(min_value=1, max_value=365, initial=30, label="Expires after days")
    confirm = forms.BooleanField(
        label="I understand these scopes apply to every workspace on this Hub, not just my memberships."
    )


class ChangeRequestForm(forms.Form):
    repository_external_id = forms.ChoiceField(
        required=False,
        label="Project",
        help_text="Select a project before using /analyse-requests.",
    )
    kind = forms.ChoiceField(choices=ChangeRequest.Kind.choices, label="Type")
    description = forms.CharField(
        max_length=20000,
        widget=forms.Textarea(attrs={"rows": 10}),
        help_text="Describe the bug and expected behavior, or the feature you would like. Maximum 20,000 characters.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["repository_external_id"].choices = [("", "Not assigned")] + [
            (r.external_id, r.name) for r in Repository.objects.order_by("name")
        ]


class ChangeRequestEditForm(ChangeRequestForm):
    revision = forms.IntegerField(
        min_value=1, widget=forms.HiddenInput(attrs={"id": "edit_revision"})
    )


class ChangeRequestTransitionForm(forms.Form):
    revision = forms.IntegerField(
        min_value=1, widget=forms.HiddenInput(attrs={"id": "transition_revision"})
    )
    status = forms.ChoiceField(choices=ChangeRequest.Status.choices, widget=forms.HiddenInput)


class RequestAnalysisForm(forms.Form):
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    project_kind = forms.ChoiceField(
        choices=[
            ("backend", "Backend only"),
            ("frontend", "Frontend only"),
            ("fullstack", "Backend and frontend"),
        ]
    )
    specification = forms.CharField(
        max_length=64000,
        widget=forms.Textarea(attrs={"rows": 22}),
        label="Implementation specification (Markdown)",
    )
    new_interfaces = forms.CharField(
        max_length=16000,
        widget=forms.Textarea(attrs={"rows": 6}),
        label="New routes / interoperability mechanisms (or None with a reason)",
    )
    changed_interfaces = forms.CharField(
        max_length=16000,
        widget=forms.Textarea(attrs={"rows": 6}),
        label="Changed existing interfaces (or None with a reason)",
    )
    breaking_changes = forms.CharField(
        max_length=16000,
        widget=forms.Textarea(attrs={"rows": 6}),
        label="Breaking changes and migration plan (or None with a reason)",
    )


class ProjectRelationshipForm(forms.Form):
    source = forms.ModelChoiceField(queryset=Repository.objects.none(), label="Producer project")
    target = forms.ModelChoiceField(queryset=Repository.objects.none(), label="Consumer project")
    description = forms.CharField(
        max_length=1000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="What does this consumer depend on?",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("source", "target"):
            self.fields[name].queryset = Repository.objects.order_by("name")


class RequestClosureForm(ChangeRequestTransitionForm):
    handoff_reviewed = forms.BooleanField(
        label="I validated the implementation and reviewed the downstream handoff and selected targets. Close and publish the selected Open requests."
    )


class HandoffForm(forms.Form):
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    summary = forms.CharField(
        max_length=4000,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Feature intent / reason no downstream work is needed",
    )
    contract = forms.CharField(
        max_length=16000,
        widget=forms.Textarea(attrs={"rows": 8}),
        label="Routes, contracts, authentication, errors and examples (or None with reason)",
    )
    compatibility = forms.CharField(
        max_length=8000,
        widget=forms.Textarea(attrs={"rows": 4}),
        label="Compatibility / breaking changes / migration",
    )
    availability = forms.ChoiceField(
        choices=[
            ("unknown", "Unknown"),
            ("proposed", "Proposed / PR only"),
            ("merged", "Merged, deployment unconfirmed"),
            ("test_available", "Available in test"),
            ("production_available", "Available in production"),
        ]
    )
    availability_details = forms.CharField(
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Availability evidence, environment and limitations",
    )
    commit_sha = forms.CharField(
        max_length=64,
        required=False,
        label="Exact implementation commit (required for publication)",
    )
    pull_request = forms.URLField(
        max_length=1000,
        required=False,
        assume_scheme="https",
        label="Implementation PR (required for publication)",
    )


class HandoffTargetForm(forms.Form):
    repository_external_id = forms.ChoiceField(label="Consumer")
    selected = forms.BooleanField(
        required=False, label="Create a downstream request for this consumer when closed"
    )
    requirements = forms.CharField(
        max_length=8000, required=False, widget=forms.Textarea(attrs={"rows": 4})
    )
    acceptance_criteria = forms.CharField(
        max_length=8000, required=False, widget=forms.Textarea(attrs={"rows": 4})
    )

    def __init__(self, *args, choices=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["repository_external_id"].choices = choices


HandoffTargetFormSet = forms.formset_factory(
    HandoffTargetForm, extra=0, max_num=40, validate_max=True, absolute_max=40
)


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
            ("findings:implement", "Claim and complete queued implementations"),
            ("requests:read", "Read project requests"),
            ("requests:analyse", "Submit request analysis (not accept it)"),
            ("requests:implement", "Claim and implement user-specified requests"),
        ],
        widget=forms.CheckboxSelectMultiple,
    )
    repository_external_id = forms.CharField(
        max_length=500,
        required=False,
        help_text="Optional: restrict the token to one stable repository external ID.",
    )
    days = forms.IntegerField(min_value=1, max_value=365, initial=90, label="Expires after days")


class ImplementationForm(forms.Form):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    remediation = forms.CharField(
        max_length=64000,
        widget=forms.Textarea(attrs={"rows": 16}),
        label="Implementation / remediation",
    )


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
