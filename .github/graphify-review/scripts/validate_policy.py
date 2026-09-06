#!/usr/bin/env python3
"""Validate policy.yaml without mandatory third-party dependencies."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

from review_core import CATEGORIES, IMPACTS, QUALITY_IMPACTS, SEVERITIES, load_yaml

PROJECT_TYPES = {"generic", "web", "api", "service", "cli", "library", "desktop", "mobile", "data_pipeline"}
DEPLOYMENT_TYPES = {"conventional_server", "container", "serverless", "desktop", "mobile", "library"}
COMPLEXITIES = {"trivial", "normal", "complex", "critical"}
EVIDENCE_KEYS = {"source_analysis", "graph_analysis", "tests_executed", "dependency_vulnerability_scan", "secret_scan", "static_security_scan", "configuration_analysis"}
TOP_LEVEL = {"version", "runtime", "reproducibility", "project", "deployment", "security", "testing", "scoring", "confidence", "review", "verification", "models", "validation", "accepted_risks"}


def _mapping(value: Any, path: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{path} must be a mapping")
        return {}
    return value


def validate_policy(policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    missing = sorted((TOP_LEVEL - {"validation"}) - set(policy))
    errors.extend(f"missing mandatory key: {key}" for key in missing)
    errors.extend(f"unknown top-level key: {key}" for key in sorted(set(policy) - TOP_LEVEL))
    if policy.get("version") != 1:
        errors.append("version must be 1")

    runtime = _mapping(policy.get("runtime"), "runtime", errors)
    if not isinstance(runtime.get("auto_bootstrap"), bool):
        errors.append("runtime.auto_bootstrap must be boolean")
    for key in ("venv_path", "requirements_file"):
        value = runtime.get(key)
        if not isinstance(value, str) or not value:
            errors.append(f"runtime.{key} must be a non-empty workspace-relative path")
        elif Path(value).is_absolute() or ".." in Path(value).parts:
            errors.append(f"runtime.{key} must remain inside the workspace")
    minimum_python = runtime.get("minimum_python")
    if not isinstance(minimum_python, str) or not re.fullmatch(r"\d+\.\d+", minimum_python):
        errors.append("runtime.minimum_python must use major.minor format, for example 3.10")

    reproducibility = _mapping(policy.get("reproducibility"), "reproducibility", errors)
    for key in ("require_clean_worktree", "versioned_outputs", "require_baseline_disposition"):
        if not isinstance(reproducibility.get(key), bool):
            errors.append(f"reproducibility.{key} must be boolean")
    for key in ("versioned_outputs", "require_baseline_disposition"):
        if reproducibility.get(key) is not True:
            errors.append(f"reproducibility.{key} must be true")
    output_root = reproducibility.get("output_root")
    if not isinstance(output_root, str) or not output_root:
        errors.append("reproducibility.output_root must be a non-empty workspace-relative path")
    elif Path(output_root).is_absolute() or ".." in Path(output_root).parts:
        errors.append("reproducibility.output_root must remain inside the workspace")

    project = _mapping(policy.get("project"), "project", errors)
    if project.get("type") not in PROJECT_TYPES:
        errors.append(f"project.type must be one of {sorted(PROJECT_TYPES)}")
    deployment = _mapping(policy.get("deployment"), "deployment", errors)
    if deployment.get("type") not in DEPLOYMENT_TYPES:
        errors.append(f"deployment.type must be one of {sorted(DEPLOYMENT_TYPES)}")
    if not isinstance(deployment.get("containers_required"), bool):
        errors.append("deployment.containers_required must be boolean")

    security = _mapping(policy.get("security"), "security", errors)
    for key in ("require_sca", "require_sast", "require_secret_scan"):
        if not isinstance(security.get(key), bool):
            errors.append(f"security.{key} must be boolean")
    testing = _mapping(policy.get("testing"), "testing", errors)
    coverage = testing.get("minimum_coverage_percent")
    if not isinstance(coverage, (int, float)) or isinstance(coverage, bool) or not 0 <= coverage <= 100:
        errors.append("testing.minimum_coverage_percent must be a number from 0 to 100")

    scoring = _mapping(policy.get("scoring"), "scoring", errors)
    weights = _mapping(scoring.get("category_weights"), "scoring.category_weights", errors)
    if set(weights) != set(CATEGORIES):
        errors.append(f"scoring.category_weights must contain exactly: {', '.join(CATEGORIES)}")
    numeric_weights = [value for value in weights.values() if isinstance(value, (int, float)) and not isinstance(value, bool)]
    if len(numeric_weights) != len(weights) or any(value < 0 or value > 1 for value in numeric_weights):
        errors.append("all category weights must be numbers from 0 to 1")
    elif abs(sum(numeric_weights) - 1.0) > 1e-9:
        errors.append(f"scoring.category_weights must sum to 1.0 (actual {sum(numeric_weights):.12g})")
    penalties = _mapping(scoring.get("penalties"), "scoring.penalties", errors)
    if set(penalties) != set(SEVERITIES):
        errors.append(f"scoring.penalties must contain exactly: {', '.join(SEVERITIES)}")
    for severity in SEVERITIES:
        table = _mapping(penalties.get(severity), f"scoring.penalties.{severity}", errors)
        if set(table) != set(QUALITY_IMPACTS):
            errors.append(f"scoring.penalties.{severity} must contain exactly: {', '.join(QUALITY_IMPACTS)}")
        for impact, value in table.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                errors.append(f"scoring.penalties.{severity}.{impact} must be a non-negative number")
    aggregation = _mapping(scoring.get("overall_aggregation"), "scoring.overall_aggregation", errors)
    if set(aggregation) != {"weighted_average_weight", "weakest_category_weight"}:
        errors.append("scoring.overall_aggregation must contain exactly weighted_average_weight and weakest_category_weight")
    aggregation_values = [aggregation.get("weighted_average_weight"), aggregation.get("weakest_category_weight")]
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1 for value in aggregation_values):
        errors.append("scoring.overall_aggregation weights must be numbers from 0 to 1")
    elif abs(sum(aggregation_values) - 1.0) > 1e-9:
        errors.append("scoring.overall_aggregation weights must sum to 1.0")
    gates = _mapping(scoring.get("hard_gates"), "scoring.hard_gates", errors)
    for name, gate_value in gates.items():
        gate = _mapping(gate_value, f"scoring.hard_gates.{name}", errors)
        score = gate.get("max_score")
        if not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= score <= 10:
            errors.append(f"scoring.hard_gates.{name}.max_score must be a number from 0 to 10")
        if gate.get("release_recommendation") not in {"GO", "GO_WITH_ACTIONS", "CONDITIONAL", "NO_GO", "INSUFFICIENT_EVIDENCE"}:
            errors.append(f"scoring.hard_gates.{name}.release_recommendation is invalid")

    confidence = _mapping(policy.get("confidence"), "confidence", errors)
    minimum = confidence.get("minimum_for_decision_percent")
    if not isinstance(minimum, int) or isinstance(minimum, bool) or not 0 <= minimum <= 100:
        errors.append("confidence.minimum_for_decision_percent must be an integer from 0 to 100")
    evidence_weights = _mapping(confidence.get("evidence_weights"), "confidence.evidence_weights", errors)
    expected_evidence = EVIDENCE_KEYS
    if set(evidence_weights) != expected_evidence:
        errors.append("confidence.evidence_weights has missing or unknown evidence keys")
    confidence_values = [value for value in evidence_weights.values() if isinstance(value, (int, float)) and not isinstance(value, bool)]
    if len(confidence_values) != len(evidence_weights) or any(value < 0 for value in confidence_values):
        errors.append("confidence evidence weights must be non-negative numbers")
    elif abs(sum(confidence_values) - 100) > 1e-9:
        errors.append(f"confidence.evidence_weights must sum to 100 (actual {sum(confidence_values):.12g})")
    levels = _mapping(confidence.get("levels"), "confidence.levels", errors)
    if not all(isinstance(levels.get(key), int) and 0 <= levels[key] <= 100 for key in ("high", "medium")):
        errors.append("confidence.levels.high and .medium must be integers from 0 to 100")
    elif levels["high"] < levels["medium"]:
        errors.append("confidence.levels.high must be greater than or equal to .medium")
    requirements = confidence.get("decision_requirements")
    if not isinstance(requirements, list) or not requirements:
        errors.append("confidence.decision_requirements must be a non-empty list")
    elif len(requirements) != len(set(requirements)) or any(item not in EVIDENCE_KEYS for item in requirements):
        errors.append("confidence.decision_requirements contains duplicates or unknown evidence keys")

    review = _mapping(policy.get("review"), "review", errors)
    sample_percent = review.get("random_sampling_percent")
    if not isinstance(sample_percent, (int, float)) or isinstance(sample_percent, bool) or not 0 <= sample_percent <= 100:
        errors.append("review.random_sampling_percent must be a number from 0 to 100")
    sample_min, sample_max = review.get("random_sampling_min_files"), review.get("random_sampling_max_files")
    if not isinstance(sample_min, int) or isinstance(sample_min, bool) or sample_min < 0:
        errors.append("review.random_sampling_min_files must be a non-negative integer")
    if not isinstance(sample_max, int) or isinstance(sample_max, bool) or sample_max < 0:
        errors.append("review.random_sampling_max_files must be a non-negative integer")
    if isinstance(sample_min, int) and isinstance(sample_max, int) and sample_min > sample_max:
        errors.append("review.random_sampling_min_files cannot exceed random_sampling_max_files")

    verification = _mapping(policy.get("verification"), "verification", errors)
    for severity in SEVERITIES:
        rule = _mapping(verification.get(severity), f"verification.{severity}", errors)
        if not isinstance(rule.get("reviewers_required"), int) or isinstance(rule.get("reviewers_required"), bool) or rule.get("reviewers_required", 0) < 1:
            errors.append(f"verification.{severity}.reviewers_required must be a positive integer")
        if not isinstance(rule.get("independent_verifier_required"), bool):
            errors.append(f"verification.{severity}.independent_verifier_required must be boolean")
    blocker_rule = _mapping(verification.get("production_blocker"), "verification.production_blocker", errors)
    if not isinstance(blocker_rule.get("independent_verifier_required"), bool):
        errors.append("verification.production_blocker.independent_verifier_required must be boolean")

    models = _mapping(policy.get("models"), "models", errors)
    if not isinstance(models.get("exact_model_required"), bool):
        errors.append("models.exact_model_required must be boolean")
    routing = _mapping(models.get("routing"), "models.routing", errors)
    if set(routing) != COMPLEXITIES:
        errors.append("models.routing must contain exactly trivial, normal, complex, critical")
    for complexity, route_value in routing.items():
        route = _mapping(route_value, f"models.routing.{complexity}", errors)
        preferred = route.get("preferred")
        if not isinstance(preferred, list) or not preferred or not all(isinstance(item, str) and item for item in preferred):
            errors.append(f"models.routing.{complexity}.preferred must be a non-empty string list")
        elif models.get("exact_model_required") and len(preferred) != 1:
            errors.append(f"models.routing.{complexity}.preferred must contain exactly one model when exact_model_required is true")
    defaults = _mapping(models.get("agent_defaults"), "models.agent_defaults", errors)
    for agent, complexity in defaults.items():
        if complexity not in COMPLEXITIES:
            errors.append(f"models.agent_defaults.{agent} must be one of {sorted(COMPLEXITIES)}")

    risks = policy.get("accepted_risks")
    if not isinstance(risks, list):
        errors.append("accepted_risks must be a list")
    else:
        seen: set[str] = set()
        for index, risk_value in enumerate(risks):
            path = f"accepted_risks[{index}]"
            risk = _mapping(risk_value, path, errors)
            finding_id = risk.get("finding_id")
            if not isinstance(finding_id, str) or not finding_id:
                errors.append(f"{path}.finding_id must be a non-empty string")
            elif finding_id in seen:
                errors.append(f"{path}.finding_id duplicates {finding_id}")
            else:
                seen.add(finding_id)
            if risk.get("status") != "accepted":
                errors.append(f"{path}.status must be accepted")
            for key in ("owner", "rationale"):
                if not isinstance(risk.get(key), str) or not risk.get(key):
                    errors.append(f"{path}.{key} must be a non-empty string")
            try:
                date.fromisoformat(str(risk.get("accepted_until")))
            except ValueError:
                errors.append(f"{path}.accepted_until must be an ISO date")
            if risk.get("production_impact_override") not in IMPACTS:
                errors.append(f"{path}.production_impact_override is invalid")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("policy", nargs="?", default=str(Path(__file__).resolve().parents[1] / "policy.yaml"))
    args = parser.parse_args()
    try:
        policy = load_yaml(args.policy)
    except Exception as exc:
        print(f"Policy parse failed: {exc}", file=sys.stderr)
        return 1
    errors = validate_policy(policy)
    schema_path = Path(__file__).resolve().parents[1] / "policy.schema.json"
    try:
        import jsonschema  # type: ignore

        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        errors.extend(f"schema: {error.message}" for error in jsonschema.Draft202012Validator(schema).iter_errors(policy))
    except ImportError:
        pass
    if errors:
        for error in sorted(dict.fromkeys(errors)):
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Policy valid: {args.policy}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
