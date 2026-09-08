"""Django adapter for the shared offline import contract."""

from django.conf import settings
from django.core.exceptions import ValidationError

from . import import_contract
from .import_contract import canonical as canonical
from .import_contract import digest as digest
from .import_contract import render_remediation as render_remediation


def validate_envelope(data, idempotency_key):
    try:
        return import_contract.validate_envelope(
            data, idempotency_key, settings.BASE_DIR / "contracts"
        )
    except import_contract.ValidationError as exc:
        raise ValidationError(str(exc)) from exc
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ValidationError("Invalid import envelope structure.") from exc
