import json
import uuid
from io import StringIO
from unittest import mock

from django import forms
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import resolve

from hubapp.diagnostics import DiagnosticsMiddleware, form_rejection, logger, rejection


def test_unknown_errors_and_credential_paths_are_not_logged():
    request = RequestFactory().post(
        "/accounts/verify/SECRET-PATH/?token=SECRET-QUERY",
        {"secret": "SECRET-BODY"},
        HTTP_X_HUB_REQUEST_ID="SECRET-INBOUND-ID",
    )
    # Even exception messages and malformed attempt IDs are untrusted.
    rejection(
        request, "request.implementation.complete", ValueError("SECRET-EXCEPTION"), "SECRET-ID"
    )
    with mock.patch("hubapp.diagnostics.logger.log") as log:
        response = DiagnosticsMiddleware(lambda request: HttpResponse(status=400))(request)
    event = json.loads(log.call_args.args[1])
    assert "SECRET" not in log.call_args.args[1]
    assert event["reason"] == "validation_rejected"
    assert event["route"] == "unresolved"
    uuid.UUID(response["X-Hub-Request-ID"])


def test_success_is_quiet_and_does_not_reuse_incoming_id():
    identity = str(uuid.uuid4())
    request = RequestFactory().get("/health/live", HTTP_X_HUB_REQUEST_ID=identity)
    with mock.patch("hubapp.diagnostics.logger.log") as log:
        response = DiagnosticsMiddleware(lambda request: HttpResponse())(request)
    log.assert_not_called()
    assert response["X-Hub-Request-ID"] != identity


def test_form_rejection_is_logged_even_with_http_200():
    request = RequestFactory().post("/irrelevant")
    rejection(
        request, "request.edit_handoff", ValidationError("Provide the complete handoff proposal.")
    )
    with mock.patch("hubapp.diagnostics.logger.log") as log:
        response = DiagnosticsMiddleware(lambda request: HttpResponse())(request)
    event = json.loads(log.call_args.args[1])
    assert response.status_code == event["status"] == 200
    assert event["reason"] == "handoff_fields"


def test_generic_failure_uses_route_pattern_not_actual_path():
    identity = str(uuid.uuid4())
    request = RequestFactory().post(f"/api/v1/requests/{identity}/implementation?secret=SECRET")
    request.resolver_match = resolve(request.path)
    with mock.patch("hubapp.diagnostics.logger.log") as log:
        response = DiagnosticsMiddleware(lambda request: HttpResponse(status=403))(request)
    event = json.loads(log.call_args.args[1])
    assert event["status"] == response.status_code == 403
    assert "<uuid:pk>" in event["route"]
    assert event["pk"] == identity
    assert "SECRET" not in log.call_args.args[1]


def test_configured_handler_preserves_json_for_credential_route_patterns():
    request = RequestFactory().get("/accounts/verify/SECRET/")
    request.resolver_match = mock.Mock(
        route="accounts/verify/<str:token>/", kwargs={"token": "SECRET"}
    )
    stream = StringIO()
    handler = next(handler for handler in logger.handlers if handler.name == "diagnostics_console")
    with mock.patch.object(handler, "stream", stream):
        response = DiagnosticsMiddleware(lambda request: HttpResponse(status=400))(request)
    event = json.loads(stream.getvalue())
    assert event["diagnostic_id"] == response["X-Hub-Request-ID"]
    assert "SECRET" not in stream.getvalue()


def test_form_diagnostics_do_not_log_messages_parameters_values_or_unknown_codes():
    class ExampleForm(forms.Form):
        availability = forms.ChoiceField(choices=[("proposed", "Proposed")])
        summary = forms.CharField(max_length=4)

    form = ExampleForm({"availability": "SECRET-CHOICE", "summary": "SECRET-TEXT"})
    assert not form.is_valid()
    form.add_error(
        None,
        ValidationError(
            "SECRET-MESSAGE %(value)s", code="SECRET-CODE", params={"value": "SECRET-PARAM"}
        ),
    )
    form.errors["SECRET-FIELD"] = form.error_class(
        [ValidationError("SECRET-MESSAGE", code="required")]
    )
    request = RequestFactory().post("/", {"token": "SECRET-TOKEN"})
    form_rejection(request, "request.edit_handoff", form)
    raw = json.dumps(request.hub_rejection)
    assert "SECRET" not in raw
    rows = request.hub_rejection["validation_errors"]
    assert any(row["field"] == "availability" and row["code"] == "invalid_choice" for row in rows)
    assert any(row["field"] == "summary" and row["max_length"] == 4 for row in rows)
    assert any(row["code"] == "invalid_unspecified" for row in rows)
    assert any(row["field"] == "unrecognized_field" for row in rows)


def test_form_diagnostics_are_bounded():
    form = forms.Form({})
    for _ in range(205):
        form.add_error(None, ValidationError("SECRET", code="invalid"))
    request = RequestFactory().post("/")
    form_rejection(request, "request.edit_handoff", form)
    assert len(request.hub_rejection["validation_errors"]) == 200
    assert request.hub_rejection["errors_truncated"] is True
