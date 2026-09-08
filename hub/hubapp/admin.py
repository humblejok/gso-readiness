"""Operator console: explicit read-only views, never a tenant administrator bypass."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Organization, User


@admin.register(User)
class HubUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("Verification", {"fields": ("email_verified",)}),)


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "id", "suspended", "deletion_requested_at")
    readonly_fields = (
        "id",
        "name",
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
