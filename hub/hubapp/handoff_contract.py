"""Portable cross-project handoff proposal validation; never creates target requests."""

import json
import re

if __package__:
    from .git_host_contract import validate_pr_url
else:
    from git_host_contract import validate_pr_url


def validate_handoff(value):
    if not isinstance(value, dict):
        raise ValueError("Handoff must be an object.")  # noqa: TRY004 - portable validation boundary
    required = {
        "summary",
        "contract",
        "compatibility",
        "availability",
        "availability_details",
        "targets",
    }
    if set(value) not in (required, required | {"implementation"}):
        raise ValueError("Provide the complete handoff proposal.")
    for field, maximum in (
        ("summary", 4000),
        ("contract", 16000),
        ("compatibility", 8000),
        ("availability_details", 2000),
    ):
        if (
            not isinstance(value[field], str)
            or not value[field].strip()
            or len(value[field]) > maximum
        ):
            raise ValueError(
                f"Provide bounded {field}; explain explicitly when none applies."
            )
    if value["availability"] not in (
        "unknown",
        "proposed",
        "merged",
        "test_available",
        "production_available",
    ):
        raise ValueError("Invalid contract availability.")
    if not isinstance(value["targets"], list) or len(value["targets"]) > 20:
        raise ValueError("Select at most 20 affected target projects.")
    seen = set()
    for target in value["targets"]:
        if not isinstance(target, dict) or set(target) != {
            "repository_external_id",
            "requirements",
            "acceptance_criteria",
        }:
            raise ValueError(
                "Provide target project, requirements and acceptance criteria."
            )
        for field, maximum in (
            ("repository_external_id", 500),
            ("requirements", 8000),
            ("acceptance_criteria", 8000),
        ):
            if (
                not isinstance(target[field], str)
                or not target[field].strip()
                or len(target[field]) > maximum
            ):
                raise ValueError("Invalid handoff target field.")
        if target["repository_external_id"] in seen:
            raise ValueError("Duplicate handoff target.")
        seen.add(target["repository_external_id"])
    if "implementation" in value:
        reference = value["implementation"]
        if not isinstance(reference, dict) or set(reference) != {
            "commit_sha",
            "pull_request",
        }:
            raise ValueError("Provide implementation commit and pull request.")
        if not isinstance(reference["commit_sha"], str) or (
            reference["commit_sha"]
            and not re.fullmatch(r"[a-f0-9]{40,64}", reference["commit_sha"])
        ):
            raise ValueError("Provide a full implementation commit SHA.")
        if not isinstance(reference["pull_request"], str):
            raise ValueError("Invalid implementation pull request.")
        if reference["pull_request"]:
            validate_pr_url(reference["pull_request"])
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(raw.encode()) > 64 * 1024:
        raise ValueError("Handoff exceeds 64 KiB.")
    if re.search(
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{20,}",
        raw,
    ):
        raise ValueError(
            "Possible credential in handoff; remove secrets before sharing."
        )
    return value
