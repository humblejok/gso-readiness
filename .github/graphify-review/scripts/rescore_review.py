#!/usr/bin/env python3
"""Apply current deterministic scoring to an immutable baseline review."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from finding_identity import continuity_findings, ensure_identity
from profile_core import profile_summary, resolve_profile
from render_review import render
from review_core import (
    CATEGORIES, IMPACTS, QUALITY_IMPACTS, SEVERITIES, calculate_confidence, calculate_scores,
    load_yaml, risk_acceptance_for, sha256_value,
)
from validate_policy import validate_policy

EVIDENCE_KEYS = (
    "source_analysis", "graph_analysis", "tests_executed", "dependency_vulnerability_scan",
    "secret_scan", "static_security_scan", "configuration_analysis",
)


def conservative_confidence(baseline: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    confidence = copy.deepcopy(baseline.get("assessment_confidence", {}))
    states = confidence.get("states", {}) if isinstance(confidence.get("states"), dict) else {}
    completions = confidence.get("completions", {}) if isinstance(confidence.get("completions"), dict) else {}
    for key in EVIDENCE_KEYS:
        if key not in states:
            states[key] = "unavailable"
        if key not in completions:
            completions[key] = "complete" if key in {"source_analysis", "graph_analysis", "configuration_analysis"} and states.get(key) in {"passed", "failed"} else "not_completed"
    evidence = {
        "source": {"state": states["source_analysis"], "completion": completions["source_analysis"]},
        "graph": {"state": states["graph_analysis"], "completion": completions["graph_analysis"]},
        "tests": {"state": states["tests_executed"], "completion": completions["tests_executed"]},
        "security": {
            "dependency_vulnerability_scan": {"state": states["dependency_vulnerability_scan"], "completion": completions["dependency_vulnerability_scan"]},
            "secret_scan": {"state": states["secret_scan"], "completion": completions["secret_scan"]},
            "static_security_scan": {"state": states["static_security_scan"], "completion": completions["static_security_scan"]},
        },
        "configuration": {"state": states["configuration_analysis"], "completion": completions["configuration_analysis"]},
        "limitations": list(confidence.get("limitations", []) or []),
    }
    result = calculate_confidence(evidence, policy)
    result["limitations"] = sorted(dict.fromkeys([
        *result["limitations"],
        *(f"{key.replace('_', ' ').title()} lacks complete baseline provenance; rescore did not execute it."
          for key in result["unmet_decision_requirements"]),
    ]))
    return result


def statistics(review: dict[str, Any]) -> dict[str, Any]:
    findings = review.get("findings", [])
    return {
        "verified_findings": len(findings),
        "rejected_findings": len(review.get("rejected_findings", [])),
        "inconclusive_findings": len(review.get("inconclusive_findings", [])),
        "by_severity": {value: sum(item.get("severity") == value for item in findings) for value in SEVERITIES},
        "by_category": {value: sum(item.get("category") == value for item in findings) for value in CATEGORIES},
        "by_technical_quality_impact": {value: sum(item.get("technical_quality_impact") == value for item in findings) for value in QUALITY_IMPACTS},
        "by_production_impact": {value: sum(item.get("production_impact") == value for item in findings) for value in IMPACTS},
    }


def current_risks(findings: list[dict[str, Any]], policy: dict[str, Any]) -> list[dict[str, Any]]:
    for item in findings:
        item["risk_acceptance"] = risk_acceptance_for(item, policy)
    return [
        {**risk, "expired": bool(risk_acceptance_for({"id": risk.get("finding_id")}, policy).get("expired"))}
        for risk in policy.get("accepted_risks", [])
        if isinstance(risk, dict) and risk_acceptance_for({"id": risk.get("finding_id")}, policy)
    ]


def rescore(baseline: dict[str, Any], policy: dict[str, Any], run_context: dict[str, Any], repository: Path) -> dict[str, Any]:
    review = copy.deepcopy(baseline)
    findings = [ensure_identity(item) for item in review.get("findings", [])]
    review["findings"] = findings
    for group in ("rejected_findings", "inconclusive_findings"):
        review[group] = [ensure_identity(item) for item in review.get(group, [])]
    confidence = conservative_confidence(review, policy)
    profile_id = review.get("review", {}).get("profile", {}).get("id", "generic")
    profile = resolve_profile(profile_id, repository)
    selected_profile = profile_summary(profile)
    calculated = calculate_scores(
        findings, policy, confidence.get("score_percent"), profile=profile,
        confidence_below_threshold=confidence.get("below_decision_threshold"),
    )
    old_readiness = review.get("production_readiness", {})
    calculated["summary"] = old_readiness.get("summary", "Baseline findings were rescored without fresh discovery.")
    calculated["scoring_notes"] = "Rescore mode reused immutable baseline findings. No specialist discovery or finding removal occurred."
    review_meta = review.setdefault("review", {})
    baseline_review_id = review_meta.get("id")
    run_id = run_context["run_id"]
    review_meta["id"] = f"review-{hashlib.sha256(run_id.encode('utf-8')).hexdigest()[:16]}"
    review_meta["timestamp_utc"] = run_context.get("created_at_utc", review_meta.get("timestamp_utc"))
    review_meta["profile"] = selected_profile
    repository_context = run_context.get("repository", {})
    review_meta["repository"] = {
        **(review_meta.get("repository", {}) if isinstance(review_meta.get("repository"), dict) else {}),
        "root": repository_context.get("root", str(repository)),
        "commit_sha": repository_context.get("commit_sha"),
        "branch": repository_context.get("branch"),
        "dirty_worktree": repository_context.get("dirty_worktree"),
    }
    review_meta["manifest"] = {
        "path": str(Path(run_context["output_directory"]) / "run-context.json"),
        "input_hash": sha256_value({
            "run": run_id, "source_snapshot": run_context["source_snapshot"]["hash"],
            "policy": run_context.get("policy_hash"), "baseline": run_context.get("baseline", {}).get("hash"),
        }),
        "profile": selected_profile,
        "mode": "rescore",
        "baseline_review_id": baseline_review_id,
        "models": [],
    }
    review["schema_version"] = "2.1"
    baseline_claims = continuity_findings(baseline)
    review["run"] = {
        "run_id": run_id, "mode": "rescore",
        "source_snapshot_hash": run_context["source_snapshot"]["hash"],
        "output_directory": run_context["output_directory"], "baseline": run_context.get("baseline"),
        "finding_reconciliation": [
            {"baseline_id": item.get("id"), "baseline_fingerprint": item["fingerprint"], "status": "still_present", "current_id": item.get("id")}
            for item in baseline_claims
        ],
        "anchor_dispositions": [],
    }
    review["assessment_confidence"] = confidence
    review["production_readiness"] = calculated
    all_output_findings = [
        *findings,
        *review.get("rejected_findings", []),
        *review.get("inconclusive_findings", []),
    ]
    review["accepted_risks"] = current_risks(all_output_findings, policy)
    review["statistics"] = statistics(review)
    return review


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--run-context", required=True)
    parser.add_argument("--repository", default=".")
    parser.add_argument("--policy", default=str(Path(__file__).resolve().parents[1] / "policy.yaml"))
    parser.add_argument("--output", required=True)
    parser.add_argument("--markdown-output")
    args = parser.parse_args()
    policy = load_yaml(args.policy)
    errors = validate_policy(policy)
    if errors:
        raise SystemExit("Invalid policy:\n" + "\n".join(f"- {error}" for error in errors))
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    context = json.loads(Path(args.run_context).read_text(encoding="utf-8"))
    result = rescore(baseline, policy, context, Path(args.repository).resolve())
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown = Path(args.markdown_output) if args.markdown_output else output.with_name("REVIEW.md")
    markdown.write_text(render(result), encoding="utf-8", newline="\n")
    print(f"Wrote {output} and {markdown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
