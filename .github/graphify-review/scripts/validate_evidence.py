#!/usr/bin/env python3
"""Validate the normalized evidence envelope and execution-state claims."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from review_core import COMPLETION_STATES, EXECUTION_STATES

REQUIRED = {"schema_version", "repository", "profile", "graph", "source", "tests", "coverage", "security", "dependencies", "configuration", "tools", "limitations"}
SECTIONS = ("graph", "source", "tests", "coverage", "dependencies", "configuration")
SECURITY_SECTIONS = ("dependency_vulnerability_scan", "secret_scan", "static_security_scan")


def validate_evidence(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return ["evidence root must be an object"]
    errors: list[str] = []
    errors.extend(f"missing top-level field: {field}" for field in sorted(REQUIRED - set(data)))
    errors.extend(f"unknown top-level field: {field}" for field in sorted(set(data) - REQUIRED))
    if data.get("schema_version") != "2.1":
        errors.append("schema_version must be 2.1")
    profile = data.get("profile")
    if not isinstance(profile, dict):
        errors.append("profile must be an object")
    else:
        for field in ("id", "version", "display_name", "hash", "detection_state", "requested", "detection", "manifest_files", "configuration_files", "startup_files", "test_files", "applicable_audits"):
            if field not in profile:
                errors.append(f"profile.{field} is required")
        if not isinstance(profile.get("id"), str) or not profile.get("id"):
            errors.append("profile.id must be a non-empty string")
        if not isinstance(profile.get("hash"), str) or not profile.get("hash", "").startswith("sha256:"):
            errors.append("profile.hash must be a SHA-256 identifier")
        if profile.get("detection_state") not in {"matched", "not_detected", "not_applicable"}:
            errors.append("profile.detection_state is invalid")
    for name in SECTIONS:
        section = data.get(name)
        if not isinstance(section, dict):
            errors.append(f"{name} must be an object")
        elif section.get("state") not in EXECUTION_STATES:
            errors.append(f"{name}.state is invalid")
        elif section.get("completion") not in COMPLETION_STATES:
            errors.append(f"{name}.completion is invalid")
        elif section.get("state") in {"passed", "failed"} and section.get("completion") != "complete":
            errors.append(f"{name} cannot be {section.get('state')} unless completion is complete")
    security = data.get("security")
    if not isinstance(security, dict):
        errors.append("security must be an object")
    else:
        errors.extend(f"security.{field} is required" for field in SECURITY_SECTIONS if field not in security)
        errors.extend(f"security.{field} is unknown" for field in security if field not in SECURITY_SECTIONS)
        for name in SECURITY_SECTIONS:
            section = security.get(name)
            if not isinstance(section, dict):
                errors.append(f"security.{name} must be an object")
            elif section.get("state") not in EXECUTION_STATES:
                errors.append(f"security.{name}.state is invalid")
            elif section.get("completion") not in COMPLETION_STATES:
                errors.append(f"security.{name}.completion is invalid")
            elif section.get("state") in {"passed", "failed"} and section.get("completion") != "complete":
                errors.append(f"security.{name} cannot be {section.get('state')} unless completion is complete")
            elif section.get("state") in {"passed", "failed"} and not section.get("items"):
                errors.append(f"security.{name} completed state requires structured result items")
            if name == "dependency_vulnerability_scan" and isinstance(section, dict) and section.get("state") in {"passed", "failed"}:
                targets = section.get("targets")
                covered = section.get("covered_targets")
                if not isinstance(targets, list) or not targets or sorted(targets) != sorted(covered or []):
                    errors.append("security.dependency_vulnerability_scan must cover every declared target")
                if not isinstance(section.get("output_hash"), str) or not section.get("output_hash", "").startswith("sha256:"):
                    errors.append("security.dependency_vulnerability_scan requires an output_hash")
    tools = data.get("tools")
    if not isinstance(tools, list):
        errors.append("tools must be an array")
    else:
        for index, tool in enumerate(tools):
            if not isinstance(tool, dict) or not isinstance(tool.get("name"), str) or tool.get("state") not in EXECUTION_STATES or tool.get("completion") not in COMPLETION_STATES:
                errors.append(f"tools[{index}] must contain a name, valid state, and valid completion")
            elif tool.get("command") and (not isinstance(tool.get("output_hash"), str) or not tool.get("output_hash", "").startswith("sha256:")):
                errors.append(f"tools[{index}] executed command requires an output_hash")
    if not isinstance(data.get("limitations"), list) or not all(isinstance(item, str) for item in data.get("limitations", [])):
        errors.append("limitations must be a string array")
    return sorted(dict.fromkeys(errors))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence")
    args = parser.parse_args()
    try:
        data = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"ERROR: unable to load evidence: {exc}", file=sys.stderr)
        return 1
    errors = validate_evidence(data)
    try:
        import jsonschema  # type: ignore

        schema_path = Path(__file__).resolve().parents[1] / "schema" / "evidence.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
        errors.extend(f"schema: {error.message}" for error in validator.iter_errors(data))
    except ImportError:
        pass
    errors = sorted(dict.fromkeys(errors))
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Valid normalized evidence v2.1: {args.evidence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
