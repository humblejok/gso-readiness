#!/usr/bin/env python3
"""Stable finding identities and mandatory baseline reconciliation."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

DISPOSITIONS = {"resolved", "superseded", "not_reproduced"}


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:96] or "finding"


def inferred_identity(finding: dict[str, Any]) -> dict[str, str]:
    evidence = finding.get("evidence", []) if isinstance(finding.get("evidence"), list) else []
    primary = "repository"
    symbol = ""
    anchor_id = ""
    for item in evidence:
        if not isinstance(item, dict):
            continue
        primary = str(item.get("path") or item.get("source_id") or item.get("package") or primary)
        symbol = str(item.get("symbol") or "")
        anchor_id = str(item.get("anchor_id") or "")
        if primary != "repository":
            break
    return {
        "defect_key": slug(str(finding.get("profile_rule_id") or finding.get("title") or finding.get("id", "finding"))),
        "primary_location": primary.replace("\\", "/"),
        "symbol": symbol,
        "profile_rule_id": str(finding.get("profile_rule_id") or ""),
        "anchor_id": anchor_id,
    }


def normalize_identity(identity: dict[str, Any]) -> dict[str, str]:
    return {
        "defect_key": slug(str(identity.get("defect_key", "finding"))),
        "primary_location": str(identity.get("primary_location", "repository")).replace("\\", "/").strip(),
        "symbol": str(identity.get("symbol", "")).strip(),
        "profile_rule_id": str(identity.get("profile_rule_id", "")).strip(),
        "anchor_id": str(identity.get("anchor_id", "")).strip(),
    }


def fingerprint(identity: dict[str, Any]) -> str:
    normalized = normalize_identity(identity)
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def ensure_identity(finding: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(finding)
    identity = normalize_identity(result.get("identity") if isinstance(result.get("identity"), dict) else inferred_identity(result))
    result["identity"] = identity
    result["fingerprint"] = fingerprint(identity)
    return result


def continuity_findings(review: dict[str, Any]) -> list[dict[str, Any]]:
    """Return supported and inconclusive claims that must not silently disappear."""
    result: list[dict[str, Any]] = []
    for group in ("findings", "inconclusive_findings"):
        values = review.get(group, [])
        if isinstance(values, list):
            result.extend(ensure_identity(item) for item in values if isinstance(item, dict))
    return result


def load_findings(path: Path) -> tuple[Any, list[dict[str, Any]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data, data
    if isinstance(data, dict) and isinstance(data.get("findings"), list):
        return data, data["findings"]
    raise ValueError(f"{path} must be an array or object with findings[]")


def reconcile(
    baseline_findings: list[dict[str, Any]],
    current_findings: list[dict[str, Any]],
    dispositions: list[dict[str, Any]],
) -> dict[str, Any]:
    baseline = [ensure_identity(item) for item in baseline_findings]
    current = [ensure_identity(item) for item in current_findings]
    current_by_fingerprint = {item["fingerprint"]: item for item in current}
    disposition_by_fingerprint = {
        str(item.get("baseline_fingerprint")): item for item in dispositions if isinstance(item, dict)
    }
    records: list[dict[str, Any]] = []
    inconclusive: list[dict[str, Any]] = []
    errors: list[str] = []
    for old in baseline:
        old_fingerprint = old["fingerprint"]
        if old_fingerprint in current_by_fingerprint:
            records.append({
                "baseline_fingerprint": old_fingerprint, "baseline_id": old.get("id"), "status": "still_present",
                "current_id": current_by_fingerprint[old_fingerprint].get("id"),
            })
            continue
        disposition = disposition_by_fingerprint.get(old_fingerprint)
        if not disposition:
            errors.append(f"baseline finding {old.get('id')} ({old_fingerprint}) has no disposition")
            continue
        status = disposition.get("status")
        evidence = disposition.get("evidence")
        if status not in DISPOSITIONS:
            errors.append(f"baseline finding {old.get('id')} has invalid disposition {status!r}")
            continue
        if not isinstance(disposition.get("rationale"), str) or not disposition.get("rationale"):
            errors.append(f"baseline finding {old.get('id')} disposition requires a rationale")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"baseline finding {old.get('id')} disposition requires counter/change evidence")
        replacement = disposition.get("replacement_fingerprint")
        if status == "superseded" and replacement not in current_by_fingerprint:
            errors.append(f"baseline finding {old.get('id')} superseded disposition requires an existing replacement_fingerprint")
        record = {
            "baseline_fingerprint": old_fingerprint, "baseline_id": old.get("id"), "status": status,
            "rationale": disposition.get("rationale", ""), "evidence": evidence if isinstance(evidence, list) else [],
        }
        if replacement:
            record["replacement_fingerprint"] = replacement
            record["current_id"] = current_by_fingerprint.get(replacement, {}).get("id")
        records.append(record)
        if status == "not_reproduced":
            carried = copy.deepcopy(old)
            carried["verification_status"] = "inconclusive"
            carried["technical_quality_impact"] = "none"
            carried["production_impact"] = "none"
            carried["likelihood"] = "unknown"
            carried["risk_acceptance"] = None
            carried["verification"] = {
                "reviewers": ["baseline-revalidation"], "independent_verifier": True,
                "summary": f"Previously verified finding was not reproduced: {disposition.get('rationale', '')}",
            }
            carried["confidence"] = min(float(carried.get("confidence", 0.5)), 0.49)
            inconclusive.append(carried)
    if errors:
        raise ValueError("Baseline reconciliation failed:\n- " + "\n- ".join(errors))
    return {
        "schema_version": "1.0", "verified_findings": current,
        "baseline_inconclusive_findings": inconclusive, "reconciliation": records,
        "statistics": {
            "baseline": len(baseline), "current": len(current),
            "still_present": sum(item["status"] == "still_present" for item in records),
            "resolved": sum(item["status"] == "resolved" for item in records),
            "superseded": sum(item["status"] == "superseded" for item in records),
            "not_reproduced": sum(item["status"] == "not_reproduced" for item in records),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    assign = subparsers.add_parser("assign")
    assign.add_argument("--input", required=True)
    assign.add_argument("--output", required=True)
    reconcile_parser = subparsers.add_parser("reconcile")
    reconcile_parser.add_argument("--baseline", required=True)
    reconcile_parser.add_argument("--current", required=True)
    reconcile_parser.add_argument("--dispositions", required=True)
    reconcile_parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "assign":
        data, findings = load_findings(Path(args.input))
        assigned = [ensure_identity(item) for item in findings]
        if isinstance(data, list):
            payload = assigned
        else:
            payload = {**data, "findings": assigned}
            for group in ("rejected_findings", "inconclusive_findings"):
                if isinstance(data.get(group), list):
                    payload[group] = [ensure_identity(item) for item in data[group]]
    else:
        _, baseline = load_findings(Path(args.baseline))
        _, current = load_findings(Path(args.current))
        dispositions = json.loads(Path(args.dispositions).read_text(encoding="utf-8"))
        if isinstance(dispositions, dict):
            dispositions = dispositions.get("dispositions", [])
        if not isinstance(dispositions, list):
            raise SystemExit("dispositions must be an array or object with dispositions[]")
        try:
            payload = reconcile(baseline, current, dispositions)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
