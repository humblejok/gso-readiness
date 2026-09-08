"""Fail-closed ORM scope. Global managers are restricted to authentication/operators."""

from contextlib import contextmanager
from contextvars import ContextVar

from django.core.exceptions import PermissionDenied
from django.db import connection, models

_tenant = ContextVar("finding_hub_tenant", default=None)


def current_tenant():
    return _tenant.get()


def set_tenant(organization_id):
    _tenant.set(organization_id)
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('hub.organization_id', %s, false)",
                [str(organization_id or "")],
            )


@contextmanager
def tenant_scope(organization_id):
    previous = current_tenant()
    set_tenant(organization_id)
    try:
        yield
    finally:
        set_tenant(previous)


class TenantManager(models.Manager):
    def get_queryset(self):
        query = super().get_queryset()
        tenant = current_tenant()
        return query.filter(organization_id=tenant) if tenant else query.none()

    def create(self, **kwargs):
        tenant = current_tenant()
        supplied = kwargs.get("organization_id") or getattr(kwargs.get("organization"), "pk", None)
        if not tenant or (supplied and str(supplied) != str(tenant)):
            raise PermissionDenied("Workspace context required.")
        kwargs.setdefault("organization_id", tenant)
        return super().create(**kwargs)
