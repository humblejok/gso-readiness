"""Versioned request implementation agreement shared by the kit and Hub."""
import re
import uuid

PROTOCOL = {"version": 2, "capabilities": ["bound-claims", "corrective-adoption", "resume-existing-pr", "completion-preflight"]}
CHECKS = {
    "baseline_request": "Report request ID must match this request.",
    "baseline_revision": "Report revision must match the claimed approved revision.",
    "baseline_digest": "Report specification digest must match the claim.",
    "repository": "Report repository must match the request project.",
    "verification_status": "Independent verification must be satisfied.",
    "source_dirty": "Verify a clean committed worktree.",
    "commit": "Report commit must match the delivered commit.",
    "branch": "Verify the exact source branch registered on the claim.",
    "base_branch": "Use the registered nonempty target branch, distinct from the source branch.",
    "pull_request": "Provide the registered PR in the configured repository.",
    "pinned_commit": "The delivered commit must match the retained/adopted commit.",
    "fresh_verification": "Prepare fresh committed verification for the successor claim.",
    "clone_url": "The project Git destination changed since claiming.",
}

class ProtocolError(ValueError):
    code = "request_protocol_incompatible"


def require_protocol(context):
    protocol = context.get("implementation_protocol", {})
    if (not isinstance(protocol, dict) or protocol.get("version") != 2
            or not isinstance(protocol.get("capabilities"), list)
            or not all(isinstance(value, str) for value in protocol["capabilities"])
            or not set(PROTOCOL["capabilities"]) <= set(protocol["capabilities"])):
        raise ProtocolError("Update the Hub and kit together: request implementation protocol v2 is required. No new claim, branch or PR should be created.")

def valid_branch(value):
    return (isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", value)
            and ".." not in value and "//" not in value
            and all(not part.startswith(".") and not part.endswith((".", ".lock")) for part in value.split("/"))
            and not value.endswith("/"))

def validate_binding(value, identifier):
    keys = {"branch", "base_branch", "commit_sha", "pull_request", "predecessor_attempt_id"}
    if not isinstance(value, dict) or set(value) != keys or not all(isinstance(v, str) for v in value.values()):
        raise ValueError("Invalid implementation binding fields.")
    if not valid_branch(value["branch"]) or not valid_branch(value["base_branch"]) or value["branch"] == value["base_branch"]:
        raise ValueError("Invalid implementation source or target branch.")
    if value["commit_sha"] and not re.fullmatch(r"[a-f0-9]{40,64}", value["commit_sha"]):
        raise ValueError("Invalid pinned implementation commit.")
    canonical = "feature/REQ-" + str(uuid.UUID(str(identifier)))
    if value["branch"] != canonical:
        if not value["branch"].startswith("correction/") or not value["commit_sha"] or not value["predecessor_attempt_id"]:
            raise ValueError("Correction adoption requires a pinned commit and failed predecessor.")
        uuid.UUID(value["predecessor_attempt_id"])
    elif value["predecessor_attempt_id"]:
        raise ValueError("Normal feature claims cannot declare a corrective predecessor.")
    if len(value["pull_request"]) > 1000:
        raise ValueError("Invalid implementation PR reference.")
    return value

def completion_checks(item, attempt_revision, specification_digest, body, binding, clone_matches=True, required_run_id=""):
    report = body["report"]
    expected_branch = binding.get("branch", "feature/REQ-" + str(item.pk))
    tests = {
        "baseline_request": report["baseline"]["request_id"] == str(item.pk),
        "baseline_revision": report["baseline"]["revision"] == attempt_revision,
        "baseline_digest": report["baseline"]["specification_digest"] == specification_digest,
        "repository": report["repository_external_id"] == item.repository_external_id,
        "verification_status": report["result"]["status"] == "satisfied",
        "source_dirty": report["source"]["dirty"] is False,
        "commit": report["source"]["commit_sha"] == body["commit_sha"],
        "branch": report["source"]["branch"] == expected_branch,
        "base_branch": bool(body["base_branch"].strip()) and len(body["base_branch"]) <= 200
                       and body["base_branch"] != report["source"]["branch"]
                       and (not binding.get("base_branch") or body["base_branch"] == binding["base_branch"]),
        "pull_request": bool(body["pull_request"]) and (not binding.get("pull_request") or body["pull_request"] == binding["pull_request"]),
        "pinned_commit": not binding.get("commit_sha") or body["commit_sha"] == binding["commit_sha"],
        "fresh_verification": not required_run_id or report["run_id"] == required_run_id,
        "clone_url": clone_matches,
    }
    return [name for name, passed in tests.items() if not passed]
