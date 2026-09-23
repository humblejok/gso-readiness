"""Portable approved-request and verification contracts; no finding lifecycle is inferred."""

import json
import re
import uuid

if __package__:
    from .targeted_contract import TargetedError, bounded, validate_targeted
else:
    from targeted_contract import TargetedError, bounded, validate_targeted


def display_id(identifier):
    return "REQ-" + str(uuid.UUID(str(identifier)))


def specification(item):
    result = {
        key: item[key]
        for key in (
            "id",
            "revision",
            "repository_external_id",
            "kind",
            "description",
            "analysis",
        )
    }
    validate_specification(result)
    return result


def validate_specification(value):
    try:
        if set(value) != {
            "id",
            "revision",
            "repository_external_id",
            "kind",
            "description",
            "analysis",
        }:
            raise ValueError
        if (
            str(uuid.UUID(value["id"])) != value["id"]
            or type(value["revision"]) is not int
            or value["revision"] < 1
        ):
            raise ValueError
        if (
            not bounded(value["repository_external_id"], 500)
            or value["kind"] not in {"bug", "feature"}
            or not bounded(value["description"], 20000)
        ):
            raise ValueError
        analysis = value["analysis"]
        if set(analysis) != {
            "project_kind",
            "specification",
            "new_interfaces",
            "changed_interfaces",
            "breaking_changes",
        }:
            raise ValueError
        if analysis["project_kind"] not in {"backend", "frontend", "fullstack"}:
            raise ValueError
        for key in (
            "specification",
            "new_interfaces",
            "changed_interfaces",
            "breaking_changes",
        ):
            if not bounded(analysis[key], 64000 if key == "specification" else 16000):
                raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise TargetedError(
            "A complete user-approved request specification is required."
        ) from exc
    return value


def validate_request_report(report):
    """Reuse strict source/evidence checks via an internal projection, never an exported finding."""
    try:
        if set(report) != {
            "schema_version",
            "kind",
            "run_id",
            "repository_external_id",
            "baseline",
            "source",
            "result",
        }:
            raise ValueError
        if (
            report["kind"] != "request-verification"
            or report["schema_version"] != "1.0"
        ):
            raise ValueError
        baseline, result = report["baseline"], report["result"]
        if (
            set(baseline) != {"request_id", "revision", "specification_digest"}
            or type(baseline["revision"]) is not int
            or baseline["revision"] < 1
        ):
            raise ValueError
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", baseline["specification_digest"]):
            raise ValueError
        if set(result) != {
            "status",
            "rationale",
            "reviewer",
            "evidence",
            "checks",
            "acceptance",
        }:
            raise ValueError
        statuses = {
            "satisfied": "resolved",
            "not_satisfied": "still_present",
            "inconclusive": "not_reproduced",
        }
        if (
            result["status"] not in statuses
            or not isinstance(result["acceptance"], list)
            or not 1 <= len(result["acceptance"]) <= 100
        ):
            raise ValueError
        for row in result["acceptance"]:
            if (
                set(row) != {"criterion", "status", "evidence"}
                or not bounded(row["criterion"])
                or not bounded(row["evidence"])
            ):
                raise ValueError
            if row["status"] not in {"passed", "failed", "unavailable"}:
                raise ValueError
            if result["status"] == "satisfied" and row["status"] != "passed":
                raise TargetedError(
                    "Success requires every acceptance criterion to pass."
                )
        projected = {
            key: report[key]
            for key in ("schema_version", "run_id", "repository_external_id", "source")
        }
        projected.update(
            kind="finding-revalidation",
            baseline={
                "run_id": baseline["request_id"],
                "finding_id": display_id(baseline["request_id"]),
                "review_digest": baseline["specification_digest"],
                "fingerprint": baseline["specification_digest"],
            },
            result={
                **{
                    key: result[key]
                    for key in ("rationale", "reviewer", "evidence", "checks")
                },
                "status": statuses[result["status"]],
            },
            not_revalidated=[],
        )
        validate_targeted(projected)
        raw = json.dumps(report, ensure_ascii=False, allow_nan=False)
        if len(raw.encode()) > 256 * 1024 or re.search(
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|(?:gh[pousr]_[A-Za-z0-9]{20,})",
            raw,
        ):
            raise ValueError
    except TargetedError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise TargetedError("Invalid request verification contract.") from exc
    return report
