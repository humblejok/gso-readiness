"""Operator console: explicit read-only views, never a tenant administrator bypass."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from .models import Organization, User, UserApiToken, UserTokenEvent


@admin.register(User)
class HubUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ("Verification and technical access", {"fields": ("email_verified", "is_tech_lead")}),
    )

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        return fields if request.user.is_superuser else (*fields, "is_tech_lead")

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        previous = User.objects.select_for_update().filter(pk=obj.pk).first() if change else None
        changed = obj.is_tech_lead != (previous.is_tech_lead if previous else False)
        if changed and not request.user.is_superuser:
            raise PermissionDenied(
                "Only a superuser operator may grant or remove tech-lead access."
            )
        super().save_model(request, obj, form, change)
        if not (obj.is_active and obj.email_verified and obj.is_tech_lead):
            for token in UserApiToken.objects.filter(user=obj, revoked_at__isnull=True):
                token.revoked_at = timezone.now()
                token.save(update_fields=["revoked_at"])
                UserTokenEvent.objects.create(token=token, action="operator_revoked")


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "id", "suspended", "deletion_requested_at")
    readonly_fields = (
        "id",
        "name",
        "management_revision",
        "created_at",
        "deletion_requested_at",
        "brand_name",
        "brand_color",
        "logo_url",
        "custom_domain",
        "domain_verified_at",
        "domain_challenge",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        from .services import audit
        from .tenancy import tenant_scope

        with tenant_scope(obj.pk):
            audit("operator.workspace_changed", request.user.pk, obj.pk)
