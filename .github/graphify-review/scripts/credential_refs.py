"""Non-secret project credential selection. Never reads settings, secrets or the network."""
import hashlib
import json
import os

LOGGED_OUT = "__graphify_review_logged_out__"


class CredentialRefError(ValueError):
    pass


def slot(kind, destination, project, require_project=False):
    if kind not in {"hub", "azure"}:
        raise CredentialRefError("Unknown credential service.")
    repository_id = project.get("repository_id") or ""
    reference = project.get(kind + "_credential_ref") or ""
    if (reference or require_project) and not repository_id:
        raise CredentialRefError("Save this project's stable repository ID with /setup-review before selecting or storing a project credential.")
    legacy_service = "graphify-review-" + kind + ":" + destination
    account = "workspace-token" if kind == "hub" else "pat"
    service = legacy_service
    if repository_id:
        identity = json.dumps([destination, repository_id, reference or "default"], ensure_ascii=False, separators=(",", ":"))
        service = "graphify-review-" + kind + "-project:" + hashlib.sha256(identity.encode()).hexdigest()
    return {"service": service, "account": account, "legacy_service": legacy_service,
            "repository_id": repository_id, "reference": reference or "default", "explicit": bool(reference)}


def stored_token(backend, selected):
    value = backend.get_password(selected["service"], selected["account"])
    if value == LOGGED_OUT:
        return None
    # Compatibility for previously configured projects only. An explicit reference
    # never borrows a destination-wide credential if its project slot is missing.
    if not value and not selected["explicit"] and selected["service"] != selected["legacy_service"]:
        value = backend.get_password(selected["legacy_service"], selected["account"])
    return None if value == LOGGED_OUT else value


def check_environment_binding(prefix, selected):
    repository_id = os.environ.get(prefix + "_REPOSITORY_ID", "")
    reference = os.environ.get(prefix + "_CREDENTIAL_REF", "")
    if repository_id != selected["repository_id"]:
        raise CredentialRefError(prefix + "_REPOSITORY_ID must match this checkout's stable repository ID before an environment credential can be used.")
    if (selected["explicit"] or reference) and reference != selected["reference"]:
        raise CredentialRefError(prefix + "_CREDENTIAL_REF must match the selected project credential reference.")
