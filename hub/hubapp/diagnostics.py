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
}
for field in ("summary", "contract", "compatibility", "availability_details"):
    SAFE_REASONS[f"Provide bounded {field}; explain explicitly when none applies."] = (
        f"handoff_{field}_missing_or_too_long"
    )


def rejection(request, operation, error=None, attempt_id=None):
    """Mark a handled rejection; middleware emits exactly one diagnostic event."""
    request = getattr(request, "_request", request)  # DRF wraps Django's request.
    detail = {"operation": operation, "reason": "validation_rejected"}
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
