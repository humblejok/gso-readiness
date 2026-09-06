#!/usr/bin/env python3
"""Shared, dependency-free policy and decision helpers for the review kit."""
from __future__ import annotations

import ast
import hashlib
import json
import re
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any

CATEGORIES = (
    "architecture",
    "correctness",
    "maintainability",
    "security",
    "deployability",
    "testing",
    "operations",
)
SEVERITIES = ("critical", "high", "medium", "low", "informational")
IMPACTS = ("blocker", "required_before_production", "post_release", "none")
QUALITY_IMPACTS = ("systemic", "material", "localized", "none")
VERIFICATION_STATES = ("supported", "partially_supported", "unsupported", "inconclusive")
EXECUTION_STATES = ("not_run", "passed", "failed", "unavailable", "not_applicable")
COMPLETION_STATES = ("complete", "partial", "not_completed", "not_applicable")
RELEASE_RECOMMENDATIONS = (
    "GO",
    "GO_WITH_ACTIONS",
    "CONDITIONAL",
    "NO_GO",
    "INSUFFICIENT_EVIDENCE",
)
READINESS_STATUS = {
    "GO": "ready",
    "GO_WITH_ACTIONS": "ready_with_actions",
    "CONDITIONAL": "conditional",
    "NO_GO": "not_ready",
    "INSUFFICIENT_EVIDENCE": "insufficient_evidence",
}


class PolicyError(ValueError):
    """Raised when a policy cannot be parsed or is internally inconsistent."""


def _strip_comment(value: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote:
            escaped = True
            continue
        if char in {"'", '"'}:
            quote = None if quote == char else char if quote is None else quote
        if char == "#" and quote is None and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value.rstrip()


def _scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return None
    lowered = value.lower()
    if lowered in {"null", "~"}:
        return None
    if lowered in {"true", "false"}:
        return lowered == "true"
    if value in {"[]", "{}"}:
        return [] if value == "[]" else {}
    if value[0:1] in {"[", "{"}:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            try:
                return ast.literal_eval(value)
            except (ValueError, SyntaxError):
                if value.startswith("[") and value.endswith("]"):
                    inner = value[1:-1].strip()
                    return [] if not inner else [_scalar(item) for item in inner.split(",")]
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return value[1:-1]
    if re.fullmatch(r"[-+]?\d+", value):
        return int(value)
    if re.fullmatch(r"[-+]?(?:\d+\.\d*|\.\d+)(?:[eE][-+]?\d+)?", value):
        return float(value)
    return value


def parse_simple_yaml(text: str) -> Any:
    """Parse the conservative YAML subset used by review policies.

    PyYAML is used when installed. This fallback supports nested mappings, lists,
    inline JSON-style values, quoted/unquoted scalars, and ``>``/``|`` blocks.
    """
    tokens: list[tuple[int, str, int]] = []
    for line_number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#") or raw.strip() == "---":
            continue
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise PolicyError(f"line {line_number}: tabs are not supported for indentation")
        indent = len(raw) - len(raw.lstrip(" "))
        content = _strip_comment(raw[indent:])
        if content:
            tokens.append((indent, content, line_number))

    def parse_block(index: int, indent: int) -> tuple[Any, int]:
        if index >= len(tokens) or tokens[index][0] != indent:
            line = tokens[index][2] if index < len(tokens) else "EOF"
            raise PolicyError(f"line {line}: invalid indentation")
        is_list = tokens[index][1].startswith("-")
        container: Any = [] if is_list else {}
        while index < len(tokens):
            current_indent, content, line_number = tokens[index]
            if current_indent < indent:
                break
            if current_indent > indent:
                raise PolicyError(f"line {line_number}: unexpected indentation")
            if is_list:
                if not content.startswith("-"):
                    break
                rest = content[1:].strip()
                index += 1
                if not rest:
                    if index < len(tokens) and tokens[index][0] > indent:
                        item, index = parse_block(index, tokens[index][0])
                    else:
                        item = None
                    container.append(item)
                    continue
                if ":" not in rest:
                    container.append(_scalar(rest))
                    continue
                key, raw_value = rest.split(":", 1)
                item = {key.strip(): _scalar(raw_value)}
                if not raw_value.strip() and index < len(tokens) and tokens[index][0] > indent:
                    nested, index = parse_block(index, tokens[index][0])
                    item[key.strip()] = nested
                if index < len(tokens) and tokens[index][0] > indent:
                    continuation, index = parse_block(index, tokens[index][0])
                    if not isinstance(continuation, dict):
                        raise PolicyError(f"line {line_number}: list mapping continuation must be a mapping")
                    item.update(continuation)
                container.append(item)
                continue

            if content.startswith("-") or ":" not in content:
                raise PolicyError(f"line {line_number}: expected 'key: value'")
            key, raw_value = content.split(":", 1)
            key = key.strip()
            raw_value = raw_value.strip()
            index += 1
            if raw_value in {">", "|"}:
                parts: list[str] = []
                while index < len(tokens) and tokens[index][0] > indent:
                    parts.append(tokens[index][1])
                    index += 1
                container[key] = (" " if raw_value == ">" else "\n").join(parts)
            elif not raw_value and index < len(tokens) and tokens[index][0] > indent:
                container[key], index = parse_block(index, tokens[index][0])
            else:
                container[key] = _scalar(raw_value)
        return container, index

    if not tokens:
        return {}
    parsed, next_index = parse_block(0, tokens[0][0])
    if next_index != len(tokens):
        raise PolicyError(f"line {tokens[next_index][2]}: unable to parse policy")
    return parsed


def load_yaml(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        result = yaml.safe_load(text)
    except ImportError:
        result = parse_simple_yaml(text)
    if not isinstance(result, dict):
        raise PolicyError(f"{path}: policy root must be a mapping")
    return result


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_value(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def verification_status(finding: dict[str, Any]) -> str:
    status = finding.get("verification_status", finding.get("status", ""))
    return {"verified": "supported", "rejected": "unsupported"}.get(status, status)


def is_scoreable(finding: dict[str, Any], policy: dict[str, Any]) -> bool:
    status = verification_status(finding)
    if status == "supported":
        return True
    return status == "partially_supported" and bool(
        policy.get("verification", {}).get("allow_partially_supported_for_scoring", True)
    )


def risk_acceptance_for(
    finding: dict[str, Any], policy: dict[str, Any], today: date | None = None
) -> dict[str, Any] | None:
    today = today or date.today()
    finding_id = finding.get("id")
    for risk in policy.get("accepted_risks", []) or []:
        if not isinstance(risk, dict) or risk.get("finding_id") != finding_id or risk.get("status") != "accepted":
            continue
        expires = risk.get("accepted_until")
        expired = False
        if expires:
            try:
                expired = today > date.fromisoformat(str(expires))
            except ValueError:
                expired = True
        result = deepcopy(risk)
        result["expired"] = expired
        return result
    return None


def effective_impact(
    finding: dict[str, Any], policy: dict[str, Any], today: date | None = None
) -> tuple[str, dict[str, Any] | None]:
    impact = finding.get("production_impact", "none")
    acceptance = risk_acceptance_for(finding, policy, today)
    if acceptance and not acceptance["expired"]:
        impact = acceptance.get("production_impact_override", impact)
    return impact, acceptance


def _hard_gate_tags(finding: dict[str, Any]) -> set[str]:
    raw = finding.get("hard_gate_tags", [])
    tags = {str(item) for item in raw} if isinstance(raw, list) else set()
    if finding.get("type") == "exposed_secret":
        tags.add("exposed_secret")
    return tags


def calculate_scores(
    findings: list[dict[str, Any]],
    policy: dict[str, Any],
    confidence_percent: int | float | None = None,
    today: date | None = None,
    profile: dict[str, Any] | None = None,
    confidence_below_threshold: bool | None = None,
) -> dict[str, Any]:
    """Calculate category scores, caps, and the release decision deterministically."""
    scoring = policy.get("scoring", {})
    weights = scoring.get("category_weights", {})
    penalties = scoring.get("penalties", {})
    category_scores = {category: 10.0 for category in CATEGORIES}
    scoreable: list[dict[str, Any]] = []
    effective: dict[str, str] = {}
    acceptances: list[dict[str, Any]] = []

    for finding in findings:
        if not isinstance(finding, dict) or not is_scoreable(finding, policy):
            continue
        severity = finding.get("severity")
        category = finding.get("category")
        technical_impact = finding.get("technical_quality_impact", "none")
        if severity not in SEVERITIES or category not in CATEGORIES or technical_impact not in QUALITY_IMPACTS:
            continue
        if severity != "informational" and technical_impact == "none":
            raise ValueError(
                f"scoreable finding {finding.get('id', '<missing id>')} must have technical_quality_impact "
                "systemic, material, or localized"
            )
        penalty = float(penalties.get(severity, {}).get(technical_impact, 0.0))
        category_scores[category] = max(0.0, category_scores[category] - penalty)
        scoreable.append(finding)
        impact, acceptance = effective_impact(finding, policy, today)
        effective[str(finding.get("id", ""))] = impact
        if acceptance:
            acceptances.append({"finding_id": finding.get("id"), **acceptance})

    category_scores = {key: round(min(10.0, max(0.0, value)), 1) for key, value in category_scores.items()}
    profile_definition = profile.get("profile", {}) if isinstance(profile, dict) and "profile" in profile else profile or {}
    quality_rank = {"none": 0, "localized": 1, "material": 2, "systemic": 3}
    category_caps_applied: list[dict[str, Any]] = []
    for cap in profile_definition.get("scoring", {}).get("category_caps", []) or []:
        if not isinstance(cap, dict):
            continue
        triggers = set(cap.get("trigger_rule_ids", []))
        threshold = quality_rank.get(cap.get("minimum_technical_quality_impact"), 99)
        matched = sorted(
            str(finding.get("id", ""))
            for finding in scoreable
            if finding.get("profile_rule_id") in triggers
            and quality_rank.get(finding.get("technical_quality_impact"), -1) >= threshold
        )
        if len(matched) < int(cap.get("minimum_findings", 1)):
            continue
        category = cap.get("category")
        if category not in category_scores:
            continue
        max_score = float(cap.get("max_score", 10.0))
        category_scores[category] = round(min(category_scores[category], max_score), 1)
        category_caps_applied.append(
            {"cap": cap.get("id"), "category": category, "finding_ids": matched, "max_score": max_score}
        )
    weighted = sum(category_scores[category] * float(weights.get(category, 0.0)) for category in CATEGORIES)
    weakest_score = min(category_scores.values())
    weakest_categories = sorted(category for category, score in category_scores.items() if score == weakest_score)
    aggregation = scoring.get("overall_aggregation", {})
    weighted_average_weight = float(aggregation.get("weighted_average_weight", 1.0))
    weakest_category_weight = float(aggregation.get("weakest_category_weight", 0.0))
    weighted_score = round(min(10.0, max(0.0, weighted)), 1)
    pre_gate_score = min(
        10.0,
        max(0.0, weighted_score * weighted_average_weight + weakest_score * weakest_category_weight),
    )
    score_10 = round(pre_gate_score, 1)
    score_breakdown = {
        "weighted_category_score_10": weighted_score,
        "weakest_category_score_10": round(weakest_score, 1),
        "weakest_categories": weakest_categories,
        "weighted_average_weight": weighted_average_weight,
        "weakest_category_weight": weakest_category_weight,
        "pre_gate_score_10": round(pre_gate_score, 1),
    }

    gates_applied: list[dict[str, Any]] = []
    for gate_name, gate in (scoring.get("hard_gates", {}) or {}).items():
        matched: list[str] = []
        release_waived: list[str] = []
        for finding in scoreable:
            finding_id = str(finding.get("id", ""))
            production_impact = finding.get("production_impact", "none")
            if gate_name == "critical_blocker":
                applies = finding.get("severity") == "critical" and production_impact == "blocker"
            elif gate_name == "exposed_secret":
                applies = "exposed_secret" in _hard_gate_tags(finding) and production_impact == "blocker"
            else:
                applies = gate_name in _hard_gate_tags(finding)
            if applies:
                matched.append(finding_id)
                if effective.get(finding_id, production_impact) != production_impact:
                    release_waived.append(finding_id)
        if matched:
            cap = float(gate.get("max_score", 10.0))
            score_10 = min(score_10, cap)
            gates_applied.append(
                {
                    "gate": gate_name,
                    "finding_ids": sorted(matched),
                    "max_score": cap,
                    "release_recommendation": gate.get("release_recommendation"),
                    "release_waived_finding_ids": sorted(release_waived),
                }
            )
    score_10 = round(score_10, 1)

    blockers = sorted(fid for fid, impact in effective.items() if impact == "blocker")
    required = sorted(fid for fid, impact in effective.items() if impact == "required_before_production")
    post_release = sorted(fid for fid, impact in effective.items() if impact == "post_release")
    forced_no_go = any(
        item.get("release_recommendation") == "NO_GO"
        and set(item.get("finding_ids", [])) - set(item.get("release_waived_finding_ids", []))
        for item in gates_applied
    )
    minimum_confidence = float(policy.get("confidence", {}).get("minimum_for_decision_percent", 60))
    if blockers or forced_no_go:
        recommendation = "NO_GO"
    elif confidence_below_threshold is True or (
        confidence_below_threshold is None
        and confidence_percent is not None
        and float(confidence_percent) < minimum_confidence
    ):
        recommendation = "INSUFFICIENT_EVIDENCE"
    elif required:
        recommendation = "CONDITIONAL"
    elif post_release:
        recommendation = "GO_WITH_ACTIONS"
    else:
        recommendation = "GO"

    return {
        "score_10": score_10,
        "score_breakdown": score_breakdown,
        "category_scores": category_scores,
        "status": READINESS_STATUS[recommendation],
        "release_recommendation": recommendation,
        "release_blockers": blockers,
        "required_before_production": required,
        "recommended_after_release": post_release,
        "applied_category_caps": category_caps_applied,
        "applied_hard_gates": gates_applied,
        "accepted_risks": acceptances,
    }


def evidence_state(evidence: dict[str, Any], key: str) -> str:
    paths = {
        "source_analysis": ("source",),
        "graph_analysis": ("graph",),
        "tests_executed": ("tests",),
        "dependency_vulnerability_scan": ("security", "dependency_vulnerability_scan"),
        "secret_scan": ("security", "secret_scan"),
        "static_security_scan": ("security", "static_security_scan"),
        "configuration_analysis": ("configuration",),
    }
    current: Any = evidence
    for part in paths[key]:
        current = current.get(part, {}) if isinstance(current, dict) else {}
    return current.get("state", "not_run") if isinstance(current, dict) else "not_run"


def evidence_completion(evidence: dict[str, Any], key: str) -> str:
    paths = {
        "source_analysis": ("source",),
        "graph_analysis": ("graph",),
        "tests_executed": ("tests",),
        "dependency_vulnerability_scan": ("security", "dependency_vulnerability_scan"),
        "secret_scan": ("security", "secret_scan"),
        "static_security_scan": ("security", "static_security_scan"),
        "configuration_analysis": ("configuration",),
    }
    current: Any = evidence
    for part in paths[key]:
        current = current.get(part, {}) if isinstance(current, dict) else {}
    return current.get("completion", "not_completed") if isinstance(current, dict) else "not_completed"


def calculate_confidence(evidence: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    config = policy.get("confidence", {})
    weights = config.get("evidence_weights", {})
    coverage: dict[str, bool] = {}
    states: dict[str, str] = {}
    completions: dict[str, str] = {}
    score = 0.0
    limitations = list(evidence.get("limitations", []) or [])
    labels = {
        "source_analysis": ("Source analysis", "source analysis"),
        "graph_analysis": ("Graph analysis", "graph analysis"),
        "tests_executed": ("Test execution", "test execution"),
        "dependency_vulnerability_scan": ("Dependency vulnerability scan", "dependency vulnerability scan"),
        "secret_scan": ("Secret scan", "secret scan"),
        "static_security_scan": ("Static security scan", "static security scan"),
        "configuration_analysis": ("Configuration analysis", "configuration analysis"),
    }
    for key, raw_weight in weights.items():
        state = evidence_state(evidence, key)
        completion = evidence_completion(evidence, key)
        states[key] = state
        completions[key] = completion
        available = state in {"passed", "failed", "not_applicable"} and completion in {"complete", "not_applicable"}
        coverage[key] = available
        if available:
            score += float(raw_weight)
        elif not any(labels[key][1] in str(item).lower() for item in limitations):
            limitations.append(f"{labels[key][0]} evidence is {state}.")
    score_percent = int(round(max(0.0, min(100.0, score))))
    levels = config.get("levels", {"high": 85, "medium": 60})
    if score_percent >= int(levels.get("high", 85)):
        level = "high"
    elif score_percent >= int(levels.get("medium", 60)):
        level = "medium"
    else:
        level = "low"
    minimum = int(config.get("minimum_for_decision_percent", 60))
    requirements = config.get("decision_requirements", []) or []
    unmet_requirements = sorted(key for key in requirements if not coverage.get(key, False))
    for key in unmet_requirements:
        label = labels.get(key, (key.replace("_", " ").title(), key.replace("_", " ")))[0]
        limitations.append(f"{label} is required for a release decision and is not complete.")
    return {
        "score_percent": score_percent,
        "level": level,
        "coverage": coverage,
        "states": states,
        "completions": completions,
        "limitations": sorted(dict.fromkeys(str(item) for item in limitations)),
        "unmet_decision_requirements": unmet_requirements,
        "below_decision_threshold": score_percent < minimum or bool(unmet_requirements),
    }
