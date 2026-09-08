"""Portable contract for single-finding revalidation; no project-wide score or lifecycle inference."""
import hashlib
import json
import re
import uuid
from pathlib import PurePosixPath

MAX_BYTES = 256 * 1024


class TargetedError(ValueError):
    pass


def digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def bounded(value, limit=4000):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def validate_targeted(data):
    """Validate untrusted JSON with safe errors, without executing any proposed command."""
    try:
        required = {"schema_version", "kind", "run_id", "repository_external_id", "baseline", "source", "result", "not_revalidated"}
        if not isinstance(data, dict) or set(data) != required:
            raise ValueError
        if data["schema_version"] != "1.0" or data["kind"] != "finding-revalidation":
            raise ValueError
        uuid.UUID(data["run_id"])
        if not bounded(data["repository_external_id"], 500):
            raise ValueError
        baseline = data["baseline"]
        if set(baseline) != {"run_id", "review_digest", "finding_id", "fingerprint"}:
            raise ValueError
        if not bounded(baseline["run_id"], 250) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,99}", baseline["finding_id"]):
            raise ValueError
        for key in ("review_digest", "fingerprint"):
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", baseline[key]):
                raise ValueError
        source = data["source"]
        if set(source) != {"commit_sha", "branch", "snapshot_hash", "dirty"}:
            raise ValueError
        if not re.fullmatch(r"[a-f0-9]{40,64}", source["commit_sha"]) or not bounded(source["branch"], 200):
            raise ValueError
        if type(source["dirty"]) is not bool or not re.fullmatch(r"sha256:[a-f0-9]{64}", source["snapshot_hash"]):
            raise ValueError
        result = data["result"]
        if set(result) != {"status", "rationale", "evidence", "checks", "reviewer"}:
            raise ValueError
        if result["status"] not in {"resolved", "still_present", "not_reproduced"} or not bounded(result["rationale"]) or not bounded(result["reviewer"], 200):
            raise ValueError
        if not isinstance(result["evidence"], list) or not 1 <= len(result["evidence"]) <= 100:
            raise ValueError
        for evidence in result["evidence"]:
            if set(evidence) != {"path", "fact"} or not bounded(evidence["path"], 1000) or not bounded(evidence["fact"]):
                raise ValueError
            path = PurePosixPath(evidence["path"])
            if path.is_absolute() or ".." in path.parts or "\\" in evidence["path"] or ":" in evidence["path"]:
                raise ValueError
        if not isinstance(result["checks"], list) or not 1 <= len(result["checks"]) <= 100:
            raise ValueError
        for check in result["checks"]:
            if set(check) != {"command", "status", "summary"}:
                raise ValueError
            if not bounded(check["command"], 2000) or not bounded(check["summary"]):
                raise ValueError
            if check["status"] not in {"passed", "failed", "unavailable"}:
                raise ValueError
        if result["status"] == "resolved" and any(c["status"] != "passed" for c in result["checks"]):
            raise TargetedError("Resolution requires all recorded targeted checks to pass; unavailable evidence is not success.")
        untouched = data["not_revalidated"]
        if not isinstance(untouched, list) or len(untouched) > 1000 or len(untouched) != len(set(untouched)):
            raise ValueError
        if baseline["fingerprint"] in untouched or any(not re.fullmatch(r"sha256:[a-f0-9]{64}", fp) for fp in untouched):
            raise ValueError
        raw = json.dumps(data, ensure_ascii=False, allow_nan=False)
        if len(raw.encode()) > MAX_BYTES:
            raise ValueError
        if re.search(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|(?:gh[pousr]_[A-Za-z0-9]{20,})", raw):
            raise TargetedError("Possible raw credential detected; do not upload secret-bearing evidence.")
    except TargetedError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise TargetedError("Invalid single-finding revalidation contract.") from exc
    return data

