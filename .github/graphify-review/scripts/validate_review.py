#!/usr/bin/env python3
"""Validate the v2 review contract and deterministic governance invariants."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

from profile_core import load_resolved_profile, profile_summary, resolve_profile
from finding_identity import fingerprint as identity_fingerprint, normalize_identity
from review_core import (
    CATEGORIES,
    COMPLETION_STATES,
    EXECUTION_STATES,
    IMPACTS,
    QUALITY_IMPACTS,
    RELEASE_RECOMMENDATIONS,
    SEVERITIES,
    VERIFICATION_STATES,
    calculate_confidence,
    calculate_scores,
    load_yaml,
    risk_acceptance_for,
)
from validate_policy import validate_policy

LIKELIHOODS = {"high", "medium", "low", "unknown", "not_applicable"}
PRIORITIES = {"P0", "P1", "P2", "P3", "P4"}
EVIDENCE_TYPES = {"graphify", "source", "tool", "test", "configuration", "documentation", "dependency_vulnerability"}
IMMUTABLE_FIELDS = (
    "id", "fingerprint", "identity", "category", "title", "severity", "technical_quality_impact", "production_impact", "profile_rule_id", "likelihood",
    "remediation_priority", "verification_status", "evidence",
)
CVE_PATTERN = re.compile(r"\bCVE-\d{4}-\d{4,}\b", re.IGNORECASE)
STANDARD_PATTERNS = {
    "CWE": re.compile(r"^CWE-\d+$"),
    "OWASP Top 10": re.compile(r"^A\d{2}(?::\d{4})?$"),
    "OWASP ASVS": re.compile(r"^V\d+(?:\.\d+){1,3}$"),
    "NIST SSDF": re.compile(r"^[A-Z]{2}\.\d+(?:\.\d+)?$"),
}
TOP_LEVEL_FIELDS = {"schema_version", "run", "review", "assessment_confidence", "production_readiness", "findings", "accepted_risks", "rejected_findings", "inconclusive_findings", "strengths", "coverage", "tooling", "statistics"}
FINDING_FIELDS = {"id", "candidate_id", "fingerprint", "identity", "category", "type", "title", "severity", "technical_quality_impact", "production_impact", "profile_rule_id", "likelihood", "remediation_priority", "verification_status", "verification", "confidence", "description", "impact", "recommendation", "evidence", "standards", "risk_acceptance", "hard_gate_tags"}


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def finding_groups(data: dict[str, Any]) -> list[tuple[str, list[dict[str, Any]]]]:
    result: list[tuple[str, list[dict[str, Any]]]] = []
    for field in ("findings", "rejected_findings", "inconclusive_findings"):
        value = data.get(field, [])
        result.append((field, value if isinstance(value, list) else []))
    return result


def structured_cve_evidence(finding: dict[str, Any], cve: str) -> bool:
    for item in finding.get("evidence", []):
        if not isinstance(item, dict) or item.get("type") != "dependency_vulnerability":
            continue
        if str(item.get("advisory_id", "")).upper() != cve.upper():
            continue
        source = item.get("source", {})
        if item.get("tool") or (isinstance(source, dict) and (source.get("tool") or source.get("advisory"))):
            return True
    return False


def validate_finding(finding: Any, path: str, policy: dict[str, Any], root: Path, expected_group: str, profile: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(finding, dict):
        return [f"{path} must be an object"]
    errors.extend(f"{path}: unknown field {field}" for field in sorted(set(finding) - FINDING_FIELDS))
    required = ("id", "fingerprint", "identity", "category", "type", "title", "severity", "technical_quality_impact", "production_impact", "likelihood", "remediation_priority", "verification_status", "verification", "confidence", "description", "impact", "recommendation", "evidence", "standards", "risk_acceptance")
    for field in required:
        if field not in finding:
            errors.append(f"{path}.{field} is required")
    identity = finding.get("identity")
    if not isinstance(identity, dict):
        errors.append(f"{path}.identity must be an object")
    else:
        normalized = normalize_identity(identity)
        if identity != normalized:
            errors.append(f"{path}.identity is not normalized")
        if finding.get("fingerprint") != identity_fingerprint(identity):
            errors.append(f"{path}.fingerprint does not match identity")
        if identity.get("profile_rule_id") != str(finding.get("profile_rule_id") or ""):
            errors.append(f"{path}.identity.profile_rule_id differs from finding.profile_rule_id")
    if finding.get("category") not in CATEGORIES:
        errors.append(f"{path}.category is invalid")
    if finding.get("type") != "finding":
        errors.append(f"{path}.type must be finding")
    if finding.get("severity") not in SEVERITIES:
        errors.append(f"{path}.severity is invalid")
    if finding.get("technical_quality_impact") not in QUALITY_IMPACTS:
        errors.append(f"{path}.technical_quality_impact is invalid")
    if finding.get("production_impact") not in IMPACTS:
        errors.append(f"{path}.production_impact is invalid")
    if finding.get("likelihood") not in LIKELIHOODS:
        errors.append(f"{path}.likelihood is invalid")
    if finding.get("remediation_priority") not in PRIORITIES:
        errors.append(f"{path}.remediation_priority is invalid")
    status = finding.get("verification_status")
    if status not in VERIFICATION_STATES:
        errors.append(f"{path}.verification_status is invalid")
    expected_status = {"findings": {"supported", "partially_supported"}, "rejected_findings": {"unsupported"}, "inconclusive_findings": {"inconclusive"}}[expected_group]
    if status not in expected_status:
        errors.append(f"{path}.verification_status {status!r} is not allowed in {expected_group}")
    if expected_group != "findings" and finding.get("production_impact") != "none":
        errors.append(f"{path}: rejected/inconclusive findings must have production_impact=none")
    if expected_group != "findings" and finding.get("technical_quality_impact") != "none":
        errors.append(f"{path}: rejected/inconclusive findings must have technical_quality_impact=none")
    if expected_group == "findings" and finding.get("severity") != "informational" and finding.get("technical_quality_impact") == "none":
        errors.append(f"{path}: supported non-informational findings must have a scoreable technical_quality_impact")
    profile_definition = profile.get("profile", {}) if "profile" in profile else profile
    profile_rules = {item.get("id"): item for item in profile_definition.get("rules", []) if isinstance(item, dict)}
    profile_rule_id = finding.get("profile_rule_id")
    if profile_rule_id is not None:
        if profile_rule_id not in profile_rules:
            errors.append(f"{path}.profile_rule_id does not exist in selected profile")
        elif finding.get("category") not in profile_rules[profile_rule_id].get("categories", []):
            errors.append(f"{path}.category is not allowed by profile rule {profile_rule_id}")
    confidence = finding.get("confidence")
    if not is_number(confidence) or not 0 <= confidence <= 1:
        errors.append(f"{path}.confidence must be a number from 0 to 1")
    for field in ("id", "title", "description", "impact", "recommendation"):
        if not isinstance(finding.get(field), str) or not finding.get(field):
            errors.append(f"{path}.{field} must be a non-empty string")

    evidence = finding.get("evidence")
    if not isinstance(evidence, list):
        errors.append(f"{path}.evidence must be an array")
        evidence = []
    if expected_group == "findings" and not evidence:
        errors.append(f"{path}: supported findings require evidence provenance")
    evidence_types: set[str] = set()
    for index, item in enumerate(evidence):
        item_path = f"{path}.evidence[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_path} must be an object")
            continue
        kind = item.get("type")
        if kind not in EVIDENCE_TYPES:
            errors.append(f"{item_path}.type is invalid")
            continue
        evidence_types.add(kind)
        if kind == "source":
            source_path = item.get("path")
            if not isinstance(source_path, str) or not source_path:
                errors.append(f"{item_path}.path is required for source evidence")
            start, end = item.get("start_line"), item.get("end_line")
            if (start is None) != (end is None) or (start is not None and (not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start)):
                errors.append(f"{item_path}: start_line/end_line must be a valid inclusive range")
            if policy.get("validation", {}).get("local_source_required") and isinstance(source_path, str):
                local = root / source_path
                if not local.is_file():
                    errors.append(f"{item_path}: local source does not exist: {source_path}")
                elif item.get("content_hash"):
                    lines = local.read_text(encoding="utf-8").splitlines(keepends=True)
                    selected = "".join(lines[(start or 1) - 1 : end]) if end else "".join(lines)
                    actual = "sha256:" + hashlib.sha256(selected.encode()).hexdigest()
                    if actual != item["content_hash"]:
                        errors.append(f"{item_path}.content_hash does not match local source")
    severity = finding.get("severity")
    verification = finding.get("verification")
    if not isinstance(verification, dict):
        errors.append(f"{path}.verification must be an object")
        verification = {}
    reviewers = verification.get("reviewers", [])
    if not isinstance(reviewers, list) or not all(isinstance(item, str) and item for item in reviewers):
        errors.append(f"{path}.verification.reviewers must be a string array")
        reviewers = []
    rule = policy.get("verification", {}).get(severity, {}) if severity in SEVERITIES else {}
    if expected_group == "findings" and len(set(reviewers)) < int(rule.get("reviewers_required", 1)):
        errors.append(f"{path}: {severity} findings require {rule.get('reviewers_required', 1)} reviewer(s)")
    independent_required = bool(rule.get("independent_verifier_required")) or (finding.get("production_impact") == "blocker" and bool(policy.get("verification", {}).get("production_blocker", {}).get("independent_verifier_required")))
    if expected_group == "findings" and independent_required and verification.get("independent_verifier") is not True:
        errors.append(f"{path}: independent verification is required")
    if expected_group == "findings" and severity in {"high", "critical"} and not ({"source", "tool", "test", "configuration", "dependency_vulnerability"} & evidence_types):
        errors.append(f"{path}: high/critical findings require source or deterministic tool evidence; Graphify-only evidence is insufficient")
    if expected_group == "findings" and severity == "critical" and rule.get("deterministic_evidence_required") and not ({"source", "tool", "test", "configuration", "dependency_vulnerability"} & evidence_types):
        errors.append(f"{path}: critical finding lacks required deterministic evidence")

    corpus = " ".join(str(finding.get(field, "")) for field in ("title", "description", "impact", "recommendation"))
    for cve in sorted({match.upper() for match in CVE_PATTERN.findall(corpus)}):
        if not structured_cve_evidence(finding, cve):
            errors.append(f"{path}: {cve} claim lacks structured dependency vulnerability evidence from a tool/advisory")

    standards = finding.get("standards")
    if not isinstance(standards, list):
        errors.append(f"{path}.standards must be an array")
    else:
        for index, standard in enumerate(standards):
            standard_path = f"{path}.standards[{index}]"
            if not isinstance(standard, dict):
                errors.append(f"{standard_path} must be an object")
                continue
            framework, identifier = standard.get("framework"), standard.get("id")
            if framework not in STANDARD_PATTERNS:
                errors.append(f"{standard_path}.framework is invalid")
            elif not isinstance(identifier, str) or not STANDARD_PATTERNS[framework].fullmatch(identifier):
                errors.append(f"{standard_path}.id has invalid {framework} format")
            if not isinstance(standard.get("title"), str) or not standard.get("title"):
                errors.append(f"{standard_path}.title must be a non-empty string")

    acceptance = risk_acceptance_for(finding, policy)
    declared = finding.get("risk_acceptance")
    if acceptance:
        if not isinstance(declared, dict):
            errors.append(f"{path}.risk_acceptance must expose the policy acceptance")
        elif bool(declared.get("expired")) != bool(acceptance.get("expired")):
            errors.append(f"{path}.risk_acceptance.expired is inconsistent with accepted_until")
    elif declared is not None:
        errors.append(f"{path}.risk_acceptance has no matching policy entry")
    return errors


def validate_review(
    data: dict[str, Any], policy: dict[str, Any], root: Path,
    verified: dict[str, Any] | list[Any] | None = None,
    evidence: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
    anchors: dict[str, Any] | None = None,
) -> list[str]:
    errors = validate_policy(policy)
    required = ("schema_version", "run", "review", "assessment_confidence", "production_readiness", "findings", "accepted_risks", "rejected_findings", "inconclusive_findings", "strengths", "coverage", "tooling", "statistics")
    for field in required:
        if field not in data:
            errors.append(f"missing top-level field: {field}")
    errors.extend(f"unknown top-level field: {field}" for field in sorted(set(data) - TOP_LEVEL_FIELDS))
    if data.get("schema_version") != "2.1":
        errors.append("schema_version must be 2.1; legacy reviews are not silently reinterpreted")
    run = data.get("run")
    if not isinstance(run, dict):
        errors.append("run must be an object")
    else:
        if run.get("mode") not in {"fresh", "revalidate", "rescore"}:
            errors.append("run.mode is invalid")
        if run.get("mode") in {"revalidate", "rescore"} and not isinstance(run.get("baseline"), dict):
            errors.append(f"run.baseline is required for mode={run.get('mode')}")
        if run.get("mode") == "fresh" and run.get("baseline") is not None:
            errors.append("run.baseline must be null for mode=fresh")
        if not isinstance(run.get("finding_reconciliation"), list):
            errors.append("run.finding_reconciliation must be an array")
        elif run.get("mode") == "fresh" and run.get("finding_reconciliation"):
            errors.append("run.finding_reconciliation must be empty for mode=fresh")
        elif run.get("mode") in {"revalidate", "rescore"}:
            reconciliation = run.get("finding_reconciliation", [])
            baseline = run.get("baseline", {}) if isinstance(run.get("baseline"), dict) else {}
            expected_count = baseline.get("finding_count")
            if isinstance(expected_count, int) and len(reconciliation) != expected_count:
                errors.append(f"run.finding_reconciliation must contain one disposition for each of {expected_count} baseline findings")
            fingerprints = [item.get("baseline_fingerprint") for item in reconciliation if isinstance(item, dict)]
            if len(fingerprints) != len(set(fingerprints)):
                errors.append("run.finding_reconciliation contains duplicate baseline fingerprints")
            expected_fingerprints = baseline.get("finding_fingerprints")
            if isinstance(expected_fingerprints, list) and sorted(fingerprints) != sorted(expected_fingerprints):
                errors.append("run.finding_reconciliation fingerprints differ from the immutable baseline inventory")
            current_claims = [
                item for group in ("findings", "inconclusive_findings")
                for item in (data.get(group, []) if isinstance(data.get(group), list) else [])
                if isinstance(item, dict)
            ]
            current_by_fingerprint = {item.get("fingerprint"): item for item in current_claims}
            for index, item in enumerate(reconciliation):
                if not isinstance(item, dict) or item.get("status") not in {"still_present", "resolved", "superseded", "not_reproduced"}:
                    errors.append(f"run.finding_reconciliation[{index}] has an invalid status")
                    continue
                path = f"run.finding_reconciliation[{index}]"
                fingerprint = item.get("baseline_fingerprint")
                status = item.get("status")
                if not isinstance(fingerprint, str) or not fingerprint.startswith("sha256:"):
                    errors.append(f"{path}.baseline_fingerprint must be a SHA-256 identifier")
                if status == "still_present":
                    current = current_by_fingerprint.get(fingerprint)
                    if current is None:
                        errors.append(f"{path} claims still_present but no current claim has the baseline fingerprint")
                    elif item.get("current_id") != current.get("id"):
                        errors.append(f"{path}.current_id differs from the fingerprint-matched current claim")
                else:
                    if not isinstance(item.get("rationale"), str) or not item.get("rationale"):
                        errors.append(f"{path}.rationale is required for {status}")
                    if not isinstance(item.get("evidence"), list) or not item.get("evidence"):
                        errors.append(f"{path}.evidence is required for {status}")
                if status == "resolved" and fingerprint in current_by_fingerprint:
                    errors.append(f"{path} claims resolved but the baseline fingerprint is still present")
                if status == "superseded" and item.get("replacement_fingerprint") not in current_by_fingerprint:
                    errors.append(f"{path}.replacement_fingerprint must identify a current claim")
                if status == "not_reproduced":
                    carried = current_by_fingerprint.get(fingerprint)
                    if not carried or carried.get("verification_status") != "inconclusive":
                        errors.append(f"{path} must retain the baseline claim as inconclusive")
        anchor_dispositions = run.get("anchor_dispositions")
        if not isinstance(anchor_dispositions, list):
            errors.append("run.anchor_dispositions must be an array")
        else:
            disposition_ids: list[str] = []
            final_by_id = {
                item.get("id"): item for group in ("findings", "inconclusive_findings")
                for item in (data.get(group, []) if isinstance(data.get(group), list) else [])
                if isinstance(item, dict)
            }
            for index, disposition in enumerate(anchor_dispositions):
                path = f"run.anchor_dispositions[{index}]"
                if not isinstance(disposition, dict):
                    errors.append(f"{path} must be an object")
                    continue
                anchor_id = disposition.get("anchor_id")
                disposition_ids.append(anchor_id)
                status = disposition.get("status")
                if not isinstance(anchor_id, str) or not anchor_id:
                    errors.append(f"{path}.anchor_id must be a non-empty string")
                if status not in {"promoted", "dismissed", "inconclusive"}:
                    errors.append(f"{path}.status is invalid")
                if not isinstance(disposition.get("rationale"), str) or not disposition.get("rationale"):
                    errors.append(f"{path}.rationale is required")
                if not isinstance(disposition.get("evidence"), list) or not disposition.get("evidence"):
                    errors.append(f"{path}.evidence is required")
                if status in {"promoted", "inconclusive"}:
                    finding = final_by_id.get(disposition.get("finding_id"))
                    expected_statuses = {"supported", "partially_supported"} if status == "promoted" else {"inconclusive"}
                    if not finding or finding.get("verification_status") not in expected_statuses:
                        errors.append(f"{path}.finding_id does not identify a matching final {status} claim")
                    elif finding.get("identity", {}).get("anchor_id") != anchor_id:
                        errors.append(f"{path}.finding_id does not preserve the deterministic anchor identity")
            if len(disposition_ids) != len(set(disposition_ids)):
                errors.append("run.anchor_dispositions contains duplicate anchor IDs")
            if run.get("mode") == "rescore" and anchor_dispositions:
                errors.append("run.anchor_dispositions must be empty for model-free rescore mode")
            if anchors is not None:
                if not isinstance(anchors, dict) or not isinstance(anchors.get("anchors"), list):
                    errors.append("deterministic anchors input must be an object with anchors[]")
                else:
                    expected_anchor_ids = sorted(
                        item.get("id") for item in anchors["anchors"]
                        if isinstance(item, dict) and isinstance(item.get("id"), str)
                    )
                    actual_anchor_ids = sorted(item for item in disposition_ids if isinstance(item, str))
                    if actual_anchor_ids != expected_anchor_ids:
                        errors.append("run.anchor_dispositions must contain exactly one disposition for every deterministic anchor")
    review = data.get("review", {})
    if not isinstance(review, dict) or not all(key in review for key in ("id", "timestamp_utc", "repository", "profile", "manifest")):
        errors.append("review must contain id, timestamp_utc, repository, profile, and manifest")
    declared_profile = review.get("profile", {}) if isinstance(review, dict) else {}
    if profile is None:
        try:
            profile = resolve_profile(declared_profile.get("id", "generic") if isinstance(declared_profile, dict) else "generic", root)
        except ValueError as exc:
            errors.append(f"unable to resolve review profile: {exc}")
            profile = resolve_profile("generic", root)
    expected_profile = profile_summary(profile)
    if declared_profile != expected_profile:
        errors.append(f"review.profile differs from resolved profile: expected {expected_profile!r}")
    manifest_profile = review.get("manifest", {}).get("profile") if isinstance(review, dict) and isinstance(review.get("manifest"), dict) else None
    if manifest_profile != expected_profile:
        errors.append("review.manifest.profile differs from resolved profile")
    if evidence is not None and isinstance(evidence.get("profile"), dict):
        evidence_profile = {key: evidence["profile"].get(key) for key in expected_profile}
        if evidence_profile != expected_profile:
            errors.append("normalized evidence profile differs from resolved profile")
    if evidence is not None and isinstance(run, dict):
        evidence_snapshot = evidence.get("repository", {}).get("source_snapshot_hash")
        if evidence_snapshot and run.get("source_snapshot_hash") != evidence_snapshot:
            errors.append("run.source_snapshot_hash differs from normalized evidence")

    seen_ids: set[str] = set()
    seen_fingerprints: set[str] = set()
    for group, findings in finding_groups(data):
        if not isinstance(data.get(group), list):
            errors.append(f"{group} must be an array")
        for index, finding in enumerate(findings):
            errors.extend(validate_finding(finding, f"{group}[{index}]", policy, root, group, profile))
            if isinstance(finding, dict):
                finding_id = finding.get("id")
                if isinstance(finding_id, str):
                    if finding_id in seen_ids:
                        errors.append(f"duplicate finding id: {finding_id}")
                    seen_ids.add(finding_id)
                finding_fingerprint = finding.get("fingerprint")
                if isinstance(finding_fingerprint, str):
                    if finding_fingerprint in seen_fingerprints:
                        errors.append(f"duplicate finding fingerprint: {finding_fingerprint}")
                    seen_fingerprints.add(finding_fingerprint)

    confidence = data.get("assessment_confidence", {})
    confidence_score = confidence.get("score_percent") if isinstance(confidence, dict) else None
    if not isinstance(confidence_score, int) or isinstance(confidence_score, bool) or not 0 <= confidence_score <= 100:
        errors.append("assessment_confidence.score_percent must be an integer from 0 to 100")
    if isinstance(confidence, dict):
        states = confidence.get("states", {})
        completions = confidence.get("completions", {})
        coverage = confidence.get("coverage", {})
        if states and not isinstance(states, dict):
            errors.append("assessment_confidence.states must be an object")
        elif isinstance(states, dict):
            for key, state in states.items():
                if state not in EXECUTION_STATES:
                    errors.append(f"assessment_confidence.states.{key} is invalid")
                if isinstance(coverage, dict) and coverage.get(key) is True and state in {"not_run", "unavailable"}:
                    errors.append(f"assessment_confidence.coverage.{key} claims success while state is {state}")
        if not isinstance(completions, dict):
            errors.append("assessment_confidence.completions must be an object")
        else:
            for key, completion in completions.items():
                if completion not in COMPLETION_STATES:
                    errors.append(f"assessment_confidence.completions.{key} is invalid")
                if isinstance(coverage, dict) and coverage.get(key) is True and completion not in {"complete", "not_applicable"}:
                    errors.append(f"assessment_confidence.coverage.{key} claims success while completion is {completion}")
    if evidence is not None:
        expected_confidence = calculate_confidence(evidence, policy)
        for field in ("score_percent", "level", "coverage", "states", "completions", "limitations", "unmet_decision_requirements", "below_decision_threshold"):
            if field in confidence and confidence.get(field) != expected_confidence.get(field):
                errors.append(f"assessment_confidence.{field} differs from deterministic calculation")

    verified_findings = data.get("findings", []) if isinstance(data.get("findings"), list) else []
    try:
        expected = calculate_scores(
            verified_findings, policy, confidence_score if isinstance(confidence_score, int) else None,
            profile=profile,
            confidence_below_threshold=confidence.get("below_decision_threshold") if isinstance(confidence, dict) else None,
        )
    except ValueError as exc:
        errors.append(f"deterministic scoring input is invalid: {exc}")
        expected = {}
    readiness = data.get("production_readiness", {})
    if not isinstance(readiness, dict):
        errors.append("production_readiness must be an object")
        readiness = {}
    for field in ("score_10", "score_breakdown", "category_scores", "status", "release_recommendation", "release_blockers", "required_before_production", "recommended_after_release", "applied_category_caps", "applied_hard_gates"):
        if readiness.get(field) != expected.get(field):
            errors.append(f"production_readiness.{field} differs from deterministic calculation: expected {expected.get(field)!r}")
    if readiness.get("release_recommendation") not in RELEASE_RECOMMENDATIONS:
        errors.append("production_readiness.release_recommendation is invalid")

    statistics = data.get("statistics", {})
    if isinstance(statistics, dict):
        expected_counts = {
            "verified_findings": len(verified_findings),
            "rejected_findings": len(data.get("rejected_findings", [])) if isinstance(data.get("rejected_findings"), list) else 0,
            "inconclusive_findings": len(data.get("inconclusive_findings", [])) if isinstance(data.get("inconclusive_findings"), list) else 0,
        }
        for field, count in expected_counts.items():
            if statistics.get(field) != count:
                errors.append(f"statistics.{field} must be {count}")
        dimensions = {
            "by_severity": SEVERITIES,
            "by_category": CATEGORIES,
            "by_technical_quality_impact": QUALITY_IMPACTS,
            "by_production_impact": IMPACTS,
        }
        for field, values in dimensions.items():
            actual = {value: sum(1 for finding in verified_findings if finding.get(field.removeprefix("by_")) == value) for value in values}
            if statistics.get(field) != actual:
                errors.append(f"statistics.{field} differs from verified findings: expected {actual!r}")
    else:
        errors.append("statistics must be an object")

    output_risks = data.get("accepted_risks", [])
    if not isinstance(output_risks, list):
        errors.append("accepted_risks must be an array")
    else:
        expected_risk_ids = {risk.get("finding_id") for risk in policy.get("accepted_risks", [])}
        output_risk_ids = {risk.get("finding_id") for risk in output_risks if isinstance(risk, dict)}
        if output_risk_ids != expected_risk_ids:
            errors.append("accepted_risks must expose every policy risk acceptance, including expired entries")
        for index, risk in enumerate(output_risks):
            if not isinstance(risk, dict):
                errors.append(f"accepted_risks[{index}] must be an object")
                continue
            policy_risk = next((item for item in policy.get("accepted_risks", []) if item.get("finding_id") == risk.get("finding_id")), None)
            if policy_risk:
                for field in ("status", "owner", "rationale", "accepted_until", "production_impact_override"):
                    if risk.get(field) != policy_risk.get(field):
                        errors.append(f"accepted_risks[{index}].{field} differs from policy")
                try:
                    expired = date.today() > date.fromisoformat(str(policy_risk.get("accepted_until")))
                except ValueError:
                    expired = True
                if risk.get("expired") is not expired:
                    errors.append(f"accepted_risks[{index}].expired is inconsistent with accepted_until")

    tooling = data.get("tooling", {})
    if not isinstance(tooling, dict):
        errors.append("tooling must be an object")
    else:
        for name, item in tooling.items():
            if not isinstance(item, dict) or item.get("state") not in EXECUTION_STATES:
                errors.append(f"tooling.{name}.state must be a valid execution state")

    if verified is not None:
        source = verified.get("findings", []) if isinstance(verified, dict) else verified
        if not isinstance(source, list):
            errors.append("verified-findings input must be an array or object with findings[]")
        else:
            source_by_id = {item.get("id"): item for item in source if isinstance(item, dict)}
            final_by_id = {item.get("id"): item for item in verified_findings if isinstance(item, dict)}
            if set(source_by_id) != set(final_by_id):
                errors.append("final findings IDs differ from immutable verified-findings input")
            for finding_id in sorted(set(source_by_id) & set(final_by_id)):
                for field in IMMUTABLE_FIELDS:
                    if source_by_id[finding_id].get(field) != final_by_id[finding_id].get(field):
                        errors.append(f"finding {finding_id}: immutable field {field} changed during synthesis")
    return sorted(dict.fromkeys(errors))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("review")
    parser.add_argument("--policy", default=str(Path(__file__).resolve().parents[1] / "policy.yaml"))
    parser.add_argument("--verified-findings")
    parser.add_argument("--evidence")
    parser.add_argument("--profile-file", help="resolved profile JSON produced by resolve_profile.py")
    parser.add_argument("--anchors", help="deterministic framework-anchor JSON produced for the selected profile")
    parser.add_argument("--repository", default=".")
    args = parser.parse_args()
    try:
        data = json.loads(Path(args.review).read_text(encoding="utf-8"))
        policy = load_yaml(args.policy)
        verified = json.loads(Path(args.verified_findings).read_text(encoding="utf-8")) if args.verified_findings else None
        evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8")) if args.evidence else None
        profile = load_resolved_profile(args.profile_file) if args.profile_file else None
        anchors = json.loads(Path(args.anchors).read_text(encoding="utf-8")) if args.anchors else None
    except Exception as exc:
        print(f"ERROR: unable to load input: {exc}", file=sys.stderr)
        return 1
    if not isinstance(data, dict):
        print("ERROR: review root must be an object", file=sys.stderr)
        return 1
    errors = validate_review(data, policy, Path(args.repository).resolve(), verified, evidence, profile, anchors)
    try:
        import jsonschema  # type: ignore

        schema_dir = Path(__file__).resolve().parents[1] / "schema"
        schema = json.loads((schema_dir / "review.schema.json").read_text(encoding="utf-8"))
        finding_schema = json.loads((schema_dir / "finding.schema.json").read_text(encoding="utf-8"))
        resolver = jsonschema.RefResolver(
            base_uri=(schema_dir / "review.schema.json").resolve().as_uri(),
            referrer=schema,
            store={
                "finding.schema.json": finding_schema,
                (schema_dir / "finding.schema.json").resolve().as_uri(): finding_schema,
                finding_schema["$id"]: finding_schema,
            },
        )
        validator = jsonschema.Draft202012Validator(schema, resolver=resolver, format_checker=jsonschema.FormatChecker())
        errors.extend(f"schema: {error.message}" for error in validator.iter_errors(data))
    except ImportError:
        pass
    errors = sorted(dict.fromkeys(errors))
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(f"Validation failed with {len(errors)} error(s).", file=sys.stderr)
        return 1
    print(f"Valid Graphify production review v2.1: {args.review}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
