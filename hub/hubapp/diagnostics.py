"""Bounded rejection diagnostics: never serialize payloads or arbitrary exceptions."""

import json
import logging
import uuid
from datetime import datetime, timezone

from django.core.exceptions import ValidationError

logger = logging.getLogger("hub.diagnostics")

# Exact matches only. Unknown exceptions must not disclose their message/parameters.
SAFE_REASONS = {
    "Handoff must be an object.": "handoff_shape",
    "Provide the complete handoff proposal.": "handoff_fields",
    "Invalid contract availability.": "handoff_availability",
    "Select at most 20 affected target projects.": "handoff_targets_limit",
    "Provide target project, requirements and acceptance criteria.": "handoff_target_fields",
    "Invalid handoff target field.": "handoff_target_content",
    "Duplicate handoff target.": "handoff_duplicate_target",
    "Provide implementation commit and pull request.": "handoff_reference_fields",
    "Provide a full implementation commit SHA.": "handoff_commit_sha",
    "Invalid implementation pull request.": "handoff_pull_request",
    "Handoff exceeds 64 KiB.": "handoff_size",
    "Possible credential in handoff; remove secrets before sharing.": "handoff_credential",
    "The source project must be active before approving a handoff.": "handoff_source_inactive",
    "Every target must be an active configured consumer in this workspace.": "handoff_target_not_configured",
    "Handoff reference must match the verified implementation.": "handoff_reference_mismatch",
    "Downstream handoffs require the exact implementation commit and PR.": "handoff_reference_missing",
    "Handoff already published; do not republish or overwrite it.": "handoff_already_published",
    "Workspace storage allowance reached; no handoffs published.": "handoff_storage_limit",
    "Workspace storage allowance reached.": "storage_limit",
    "Invalid implementation completion.": "completion_shape_or_size",
    "Success requires the current approved specification, clean verified commit and feature-branch PR.": "completion_evidence_mismatch",
    "Implementation may propose a contract, not attest deployment availability.": "handoff_must_be_proposed",
    "A failed completion must not carry a success report.": "failed_completion_has_report",
    "A failed completion cannot propose a handoff.": "failed_completion_has_handoff",
    "Unknown request implementation action.": "implementation_action_or_fields",
    "Claim or approved specification changed, expired or was cancelled.": "claim_stale",
    "Request project changed.": "project_changed",
    "Invalid request verification contract.": "verification_contract",
    "Success requires every acceptance criterion to pass.": "verification_acceptance",
    "Resolution requires all recorded targeted checks to pass; unavailable evidence is not success.": "verification_checks_not_passed",
    "Use a credential-free HTTPS URL without ambiguous path segments, query or fragment.": "reference_url_invalid",
    "Invalid pull request ID.": "reference_pr_id",
    "Pull request URL is too long.": "reference_pr_length",
    "Use a GitHub or Azure DevOps HTTPS pull request URL.": "reference_pr_format",
    "Pull request URL does not match its hosting provider.": "reference_pr_provider",
    "Pull request belongs to a different repository than the configured clone URL.": "reference_pr_repository",
    "This request changed since you opened it. Reload the page and review the latest version before saving.": "request_revision_stale",
    "Save the handoff review first, including a reason when no targets are affected.": "handoff_review_not_saved",
    "Confirm human validation and review of downstream impacts before closing.": "handoff_confirmation_required",
    "Review handoffs after implementation, before closing.": "handoff_wrong_workflow_stage",
    "Move to the next workflow stage; stages cannot be skipped.": "request_invalid_transition",
    "Submit an analysis, then explicitly accept it after review.": "request_analysis_acceptance_required",
    "Only an analyzed request can be accepted.": "request_not_analyzed",
    "Only analyzed or specified requests have an editable analysis.": "request_analysis_not_editable",
    "Only the request creator can cancel it.": "request_cancel_creator_only",
    "Implemented or closed requests cannot be cancelled.": "request_not_cancellable",
    "Closed requests are read-only.": "request_closed",
    "Cancelled requests are read-only.": "request_cancelled",
    "Implemented requests can only be closed.": "request_already_implemented",
    "A published handoff stays assigned to its approved target project.": "handoff_target_immutable",
    "Select an active project in this workspace.": "request_project_inactive",
    "Provide a description.": "request_description_required",
    "Provide the complete structured implementation analysis.": "request_analysis_incomplete",
    "Select backend, frontend or fullstack project scope.": "request_project_scope_invalid",
}
for field in ("summary", "contract", "compatibility", "availability_details"):
    SAFE_REASONS[f"Provide bounded {field}; explain explicitly when none applies."] = (
        f"handoff_{field}_missing_or_too_long"
    )


def rejection(request, operation, error=None, attempt_id=None, *, stage="validation"):
    """Mark a handled rejection; middleware emits exactly one diagnostic event."""
    request = getattr(request, "_request", request)  # DRF wraps Django's request.
    detail = {"operation": operation, "stage": stage, "reason": "validation_rejected"}
    if error is not None:
        from .request_contract import RequestArtifactError
        from .targeted_contract import TargetedError

        if isinstance(error, TargetedError):
            detail["reason"] = "verification_contract"
        if isinstance(error, RequestArtifactError):
            detail["reason"] = "verification_artifact_mismatch"
        messages = error.messages if isinstance(error, ValidationError) else [str(error)]
        for message in messages:
            if message in SAFE_REASONS:
                detail.update(reason=SAFE_REASONS[message], explanation=message)
                break
    try:
        detail["attempt_id"] = str(uuid.UUID(str(attempt_id)))
    except ValueError:
        pass
    request.hub_rejection = detail


# Never log Django's interpolated messages or error.params: invalid choices/URLs can
# contain submitted text. Both field names and error codes must be known constants.
FORM_FIELDS = frozenset(
    {
        "revision",
        "status",
        "handoff_reviewed",
        "summary",
        "contract",
        "compatibility",
        "availability",
        "availability_details",
        "commit_sha",
        "pull_request",
        "repository_external_id",
        "selected",
        "requirements",
        "acceptance_criteria",
        "kind",
        "description",
        "project_kind",
        "specification",
        "new_interfaces",
        "changed_interfaces",
        "breaking_changes",
        "TOTAL_FORMS",
        "INITIAL_FORMS",
        "MIN_NUM_FORMS",
        "MAX_NUM_FORMS",
        "__all__",
    }
)
FORM_CODES = {
    "required": "This field is empty or missing; enter a value (or check the required confirmation).",
    "invalid": "The value has an invalid format; check the field type, for example URL or integer.",
    "invalid_choice": "The selected option is not allowed; reload the form and select an available option.",
    "max_length": "The value exceeds this field's maximum character count.",
    "min_length": "The value is shorter than this field's minimum character count.",
    "min_value": "The number is below the permitted minimum.",
    "max_value": "The number exceeds the permitted maximum.",
    "missing_management_form": "Target form bookkeeping is missing or invalid; reload the page before resubmitting.",
    "too_many_forms": "Too many consumer forms were submitted; reload and reduce the selection.",
    "too_few_forms": "Too few consumer forms were submitted; reload the page.",
    "null_characters_not_allowed": "Remove null characters from this field.",
}


def form_rejection(request, operation, form, formset=None):
    """Record every invalid field, including hidden fields and target management data."""
    rejection(request, operation, stage="form_validation")
    request = getattr(request, "_request", request)
    detail = request.hub_rejection
    detail.update(
        reason="form_invalid",
        explanation="Nothing saved. Correct the listed fields and submit again.",
    )
    rows = []
    truncated = False

    def collect(errors, location, fields=None):
        nonlocal truncated
        for name, errors_for_field in errors.items():
            safe_name = name if name in FORM_FIELDS else "unrecognized_field"
            for error in errors_for_field:
                if len(rows) >= 200:
                    truncated = True
                    return
                code = error.code if error.code in FORM_CODES else "invalid_unspecified"
                row = {
                    "form": location,
                    "field": safe_name,
                    "code": code,
                    "explanation": FORM_CODES.get(
                        code, "Validation failed; inspect the error beside this field."
                    ),
                }
                field = (fields or {}).get(name)
                for limit in ("max_length", "min_length", "max_value", "min_value"):
                    value = getattr(field, limit, None)
                    if type(value) is int:
                        row[limit] = value
                rows.append(row)

    collect(form.errors.as_data(), "request", form.fields)
    if formset is not None:
        collect(
            formset.management_form.errors.as_data(),
            "targets.management",
            formset.management_form.fields,
        )
        collect({"__all__": formset.non_form_errors().as_data()}, "targets")
        # The request formset is independently bounded by absolute_max=40.
        for index, target in enumerate(formset.forms[:40]):
            collect(target.errors.as_data(), f"targets[{index}]", target.fields)
    detail["validation_errors"] = rows
    detail["errors_truncated"] = truncated


class DiagnosticsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Never trust an incoming ID: it may contain credentials or log injection.
        request_id = str(uuid.uuid4())
        response = self.get_response(request)
        response["X-Hub-Request-ID"] = request_id
        detail = getattr(request, "hub_rejection", None)
        if response.status_code >= 400 or detail:
            match = getattr(request, "resolver_match", None)
            event = {
                "event": "hub.request_rejected",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "diagnostic_id": request_id,
                "status": response.status_code,
                "method": request.method
                if request.method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
                else "OTHER",
                # Pattern, not actual path/query: credential-bearing URLs stay private.
                "route": match.route if match else "unresolved",
                "reason": "http_error",
                **(detail or {}),
            }
            if match:
                for name in ("pk", "organization_id"):
                    value = match.kwargs.get(name)
                    if isinstance(value, uuid.UUID):
                        event[name] = str(value)
            logger.log(
                logging.ERROR if response.status_code >= 500 else logging.WARNING,
                json.dumps(event, sort_keys=True),
            )
        return response
