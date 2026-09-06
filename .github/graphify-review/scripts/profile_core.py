#!/usr/bin/env python3
"""Framework profile loading, validation, detection, and resolution helpers."""
from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path
from typing import Any

from review_core import CATEGORIES, QUALITY_IMPACTS, load_yaml, sha256_value

PROFILE_SCHEMA_VERSION = "1.0"
DEFAULT_PROFILE_ID = "generic"
PROFILE_FIELDS = {
    "version",
    "id",
    "aliases",
    "display_name",
    "description",
    "project_type",
    "detection",
    "evidence",
    "audits",
    "rules",
    "scoring",
}
DEFAULT_PROFILES_DIR = Path(__file__).resolve().parents[1] / "profiles"
PROFILE_SKIP_DIRS = {".git", ".venv", ".graphify-review-venv", "venv", "node_modules", "vendor", "dist", "build", "bin", "obj", "target", ".gradle", "coverage", "__pycache__", "output", "graphify-out"}
PROJECT_TYPES = {"generic", "web", "api", "service", "cli", "library", "desktop", "mobile", "data_pipeline"}
AUDIT_EVIDENCE_KINDS = {"source_analysis", "tests_executed", "dependency_vulnerability_scan", "secret_scan", "static_security_scan", "configuration_analysis", "graph_analysis", "dependencies", "coverage"}


class ProfileError(ValueError):
    """Raised when a profile is invalid or cannot be resolved unambiguously."""


def matches_pattern(relative_path: str, pattern: str) -> bool:
    """Match repository-relative paths, treating ``**/`` as zero-or-more dirs."""
    normalized = relative_path.replace("\\", "/")
    candidate_patterns = {pattern.replace("\\", "/")}
    if pattern.startswith("**/"):
        candidate_patterns.add(pattern[3:])
    return any(fnmatch.fnmatchcase(normalized, item) for item in candidate_patterns)


def repository_files(repository: Path) -> list[str]:
    files: list[str] = []
    for current, directories, names in os.walk(repository):
        directories[:] = sorted(item for item in directories if item not in PROFILE_SKIP_DIRS)
        current_path = Path(current)
        files.extend((current_path / name).relative_to(repository).as_posix() for name in sorted(names))
    return sorted(files)


def validate_profile(profile: Any) -> list[str]:
    if not isinstance(profile, dict):
        return ["profile root must be a mapping"]
    errors: list[str] = []
    missing = PROFILE_FIELDS - set(profile)
    errors.extend(f"missing profile key: {key}" for key in sorted(missing))
    errors.extend(f"unknown profile key: {key}" for key in sorted(set(profile) - PROFILE_FIELDS))
    if profile.get("version") != 1:
        errors.append("profile version must be 1")
    profile_id = profile.get("id")
    if not isinstance(profile_id, str) or not profile_id:
        errors.append("profile id must be a non-empty string")
    for field in ("display_name", "description", "project_type"):
        if not isinstance(profile.get(field), str) or not profile.get(field):
            errors.append(f"profile {field} must be a non-empty string")
    if profile.get("project_type") not in PROJECT_TYPES:
        errors.append(f"profile project_type must be one of: {', '.join(sorted(PROJECT_TYPES))}")

    aliases = profile.get("aliases")
    if not isinstance(aliases, list) or not all(isinstance(item, str) and item for item in aliases):
        errors.append("profile aliases must be a string array")
        aliases = []
    normalized_names = [str(profile_id).lower(), *(item.lower() for item in aliases)]
    if len(normalized_names) != len(set(normalized_names)):
        errors.append("profile id and aliases must be unique ignoring case")

    detection = profile.get("detection")
    if not isinstance(detection, dict):
        errors.append("profile detection must be a mapping")
        detection = {}
    allowed_detection = {"file_patterns", "content_markers"}
    errors.extend(f"unknown detection key: {key}" for key in sorted(set(detection) - allowed_detection))
    file_patterns = detection.get("file_patterns")
    if not isinstance(file_patterns, list) or not all(isinstance(item, str) and item for item in file_patterns):
        errors.append("profile detection.file_patterns must be a string array")
    markers = detection.get("content_markers")
    if not isinstance(markers, list):
        errors.append("profile detection.content_markers must be an array")
        markers = []
    for index, marker in enumerate(markers):
        path = f"profile detection.content_markers[{index}]"
        if not isinstance(marker, dict):
            errors.append(f"{path} must be a mapping")
            continue
        if set(marker) != {"file_patterns", "contains_any"}:
            errors.append(f"{path} must contain exactly file_patterns and contains_any")
        for key in ("file_patterns", "contains_any"):
            value = marker.get(key)
            if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
                errors.append(f"{path}.{key} must be a non-empty string array")

    evidence = profile.get("evidence")
    evidence_keys = {"manifest_patterns", "configuration_patterns", "startup_patterns", "test_patterns"}
    if not isinstance(evidence, dict):
        errors.append("profile evidence must be a mapping")
        evidence = {}
    if set(evidence) != evidence_keys:
        errors.append(f"profile evidence must contain exactly: {', '.join(sorted(evidence_keys))}")
    for key in evidence_keys:
        value = evidence.get(key)
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            errors.append(f"profile evidence.{key} must be a string array")

    audits = profile.get("audits")
    if not isinstance(audits, list):
        errors.append("profile audits must be an array")
        audits = []
    audit_ids: set[str] = set()
    for index, audit in enumerate(audits):
        path = f"profile audits[{index}]"
        if not isinstance(audit, dict):
            errors.append(f"{path} must be a mapping")
            continue
        expected = {"id", "title", "command", "when_any_files", "evidence_kind", "required"}
        if set(audit) != expected:
            errors.append(f"{path} must contain exactly: {', '.join(sorted(expected))}")
        audit_id = audit.get("id")
        if not isinstance(audit_id, str) or not audit_id:
            errors.append(f"{path}.id must be a non-empty string")
        elif audit_id in audit_ids:
            errors.append(f"duplicate audit id: {audit_id}")
        else:
            audit_ids.add(audit_id)
        for key in ("title", "command", "evidence_kind"):
            if not isinstance(audit.get(key), str) or not audit.get(key):
                errors.append(f"{path}.{key} must be a non-empty string")
        if audit.get("evidence_kind") not in AUDIT_EVIDENCE_KINDS:
            errors.append(f"{path}.evidence_kind is invalid")
        patterns = audit.get("when_any_files")
        if not isinstance(patterns, list) or not patterns or not all(isinstance(item, str) and item for item in patterns):
            errors.append(f"{path}.when_any_files must be a non-empty string array")
        if not isinstance(audit.get("required"), bool):
            errors.append(f"{path}.required must be boolean")

    rules = profile.get("rules")
    if not isinstance(rules, list):
        errors.append("profile rules must be an array")
        rules = []
    rule_ids: set[str] = set()
    for index, rule in enumerate(rules):
        path = f"profile rules[{index}]"
        if not isinstance(rule, dict):
            errors.append(f"{path} must be a mapping")
            continue
        expected = {"id", "title", "categories", "default_technical_quality_impact", "guidance", "evidence_required"}
        if set(rule) != expected:
            errors.append(f"{path} must contain exactly: {', '.join(sorted(expected))}")
        rule_id = rule.get("id")
        if not isinstance(rule_id, str) or not rule_id:
            errors.append(f"{path}.id must be a non-empty string")
        elif rule_id in rule_ids:
            errors.append(f"duplicate profile rule id: {rule_id}")
        else:
            rule_ids.add(rule_id)
        categories = rule.get("categories")
        if not isinstance(categories, list) or not categories or any(item not in CATEGORIES for item in categories):
            errors.append(f"{path}.categories must contain review categories")
        if rule.get("default_technical_quality_impact") not in QUALITY_IMPACTS:
            errors.append(f"{path}.default_technical_quality_impact is invalid")
        if not isinstance(rule.get("guidance"), str) or not rule.get("guidance"):
            errors.append(f"{path}.guidance must be a non-empty string")
        required_evidence = rule.get("evidence_required")
        if not isinstance(required_evidence, list) or not required_evidence or not all(isinstance(item, str) and item for item in required_evidence):
            errors.append(f"{path}.evidence_required must be a non-empty string array")

    scoring = profile.get("scoring")
    if not isinstance(scoring, dict) or set(scoring) != {"category_caps"}:
        errors.append("profile scoring must contain exactly category_caps")
        scoring = {}
    caps = scoring.get("category_caps")
    if not isinstance(caps, list):
        errors.append("profile scoring.category_caps must be an array")
        caps = []
    cap_ids: set[str] = set()
    for index, cap in enumerate(caps):
        path = f"profile scoring.category_caps[{index}]"
        if not isinstance(cap, dict):
            errors.append(f"{path} must be a mapping")
            continue
        expected = {"id", "category", "max_score", "trigger_rule_ids", "minimum_findings", "minimum_technical_quality_impact"}
        if set(cap) != expected:
            errors.append(f"{path} must contain exactly: {', '.join(sorted(expected))}")
        cap_id = cap.get("id")
        if not isinstance(cap_id, str) or not cap_id:
            errors.append(f"{path}.id must be a non-empty string")
        elif cap_id in cap_ids:
            errors.append(f"duplicate category cap id: {cap_id}")
        else:
            cap_ids.add(cap_id)
        if cap.get("category") not in CATEGORIES:
            errors.append(f"{path}.category is invalid")
        max_score = cap.get("max_score")
        if not isinstance(max_score, (int, float)) or isinstance(max_score, bool) or not 0 <= max_score <= 10:
            errors.append(f"{path}.max_score must be a number from 0 to 10")
        triggers = cap.get("trigger_rule_ids")
        if not isinstance(triggers, list) or not triggers or not all(item in rule_ids for item in triggers):
            errors.append(f"{path}.trigger_rule_ids must reference profile rules")
        elif len(triggers) != len(set(triggers)):
            errors.append(f"{path}.trigger_rule_ids must be unique")
        minimum = cap.get("minimum_findings")
        if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 1:
            errors.append(f"{path}.minimum_findings must be a positive integer")
        if cap.get("minimum_technical_quality_impact") not in QUALITY_IMPACTS:
            errors.append(f"{path}.minimum_technical_quality_impact is invalid")
    return sorted(dict.fromkeys(errors))


def load_profiles(profiles_dir: str | Path = DEFAULT_PROFILES_DIR) -> dict[str, dict[str, Any]]:
    root = Path(profiles_dir)
    profiles: dict[str, dict[str, Any]] = {}
    names: dict[str, str] = {}
    for path in sorted(root.glob("*.yaml")):
        profile = load_yaml(path)
        errors = validate_profile(profile)
        if errors:
            raise ProfileError(f"Invalid profile {path}:\n" + "\n".join(f"- {item}" for item in errors))
        profile_id = str(profile["id"])
        if profile_id in profiles:
            raise ProfileError(f"Duplicate profile id {profile_id!r}")
        profiles[profile_id] = {"definition": profile, "source_path": path, "hash": sha256_value(profile)}
        for name in [profile_id, *profile.get("aliases", [])]:
            normalized = str(name).strip().lower()
            if normalized in names:
                raise ProfileError(f"Profile name or alias {name!r} is shared by {names[normalized]!r} and {profile_id!r}")
            names[normalized] = profile_id
    if DEFAULT_PROFILE_ID not in profiles:
        raise ProfileError(f"Profiles directory must define {DEFAULT_PROFILE_ID!r}")
    return profiles


def detect_profile(repository: Path, profile: dict[str, Any], files: list[str] | None = None) -> dict[str, Any]:
    relative_files = files if files is not None else repository_files(repository)
    detection = profile.get("detection", {})
    matched_files = sorted({
        path for path in relative_files
        if any(matches_pattern(path, pattern) for pattern in detection.get("file_patterns", []))
    })
    matched_markers: list[dict[str, str]] = []
    for marker in detection.get("content_markers", []):
        for relative in relative_files:
            if not any(matches_pattern(relative, pattern) for pattern in marker.get("file_patterns", [])):
                continue
            try:
                content = (repository / relative).read_text(encoding="utf-8", errors="ignore")[:1_000_000]
            except OSError:
                continue
            for needle in marker.get("contains_any", []):
                if needle in content:
                    matched_markers.append({"path": relative, "marker": needle})
    matched_markers = sorted(matched_markers, key=lambda item: (item["path"], item["marker"]))
    content_markers = detection.get("content_markers", [])
    matched = bool(matched_markers) if content_markers else bool(matched_files)
    return {
        "state": "matched" if matched else "not_detected",
        "matched_files": matched_files,
        "matched_markers": matched_markers,
    }


def resolve_profile(
    requested: str | None,
    repository: str | Path,
    profiles_dir: str | Path = DEFAULT_PROFILES_DIR,
) -> dict[str, Any]:
    profiles = load_profiles(profiles_dir)
    aliases: dict[str, str] = {}
    for profile_id, entry in profiles.items():
        for name in [profile_id, *entry["definition"].get("aliases", [])]:
            aliases[str(name).strip().lower()] = profile_id
    requested_name = (requested or DEFAULT_PROFILE_ID).strip().lower()
    root = Path(repository).resolve()

    if requested_name == "auto":
        all_files = repository_files(root)
        matches = []
        detections: dict[str, dict[str, Any]] = {}
        for profile_id, entry in profiles.items():
            if profile_id == DEFAULT_PROFILE_ID:
                continue
            result = detect_profile(root, entry["definition"], all_files)
            detections[profile_id] = result
            if result["state"] == "matched":
                matches.append(profile_id)
        if len(matches) > 1:
            raise ProfileError(f"profile=auto is ambiguous; detected: {', '.join(sorted(matches))}. Select one explicitly.")
        selected = matches[0] if matches else DEFAULT_PROFILE_ID
        detection = detections.get(selected, {"state": "not_applicable", "matched_files": [], "matched_markers": []})
    else:
        selected = aliases.get(requested_name, "")
        if not selected:
            supported = sorted({DEFAULT_PROFILE_ID, "auto", *aliases})
            raise ProfileError(f"Unknown profile {requested!r}. Supported names: {', '.join(supported)}")
        detection = (
            {"state": "not_applicable", "matched_files": [], "matched_markers": []}
            if selected == DEFAULT_PROFILE_ID
            else detect_profile(root, profiles[selected]["definition"])
        )

    entry = profiles[selected]
    try:
        source_path = entry["source_path"].resolve().relative_to(root).as_posix()
    except ValueError:
        source_path = str(entry["source_path"].resolve())
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "requested": requested or DEFAULT_PROFILE_ID,
        "profile": entry["definition"],
        "hash": entry["hash"],
        "source_path": source_path,
        "detection": detection,
    }


def load_resolved_profile(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != PROFILE_SCHEMA_VERSION:
        raise ProfileError(f"{path}: invalid resolved profile envelope")
    required = {"schema_version", "requested", "profile", "hash", "source_path", "detection"}
    if set(data) != required:
        raise ProfileError(f"{path}: resolved profile must contain exactly: {', '.join(sorted(required))}")
    if not isinstance(data.get("requested"), str) or not isinstance(data.get("source_path"), str):
        raise ProfileError(f"{path}: requested and source_path must be strings")
    detection = data.get("detection")
    if not isinstance(detection, dict) or set(detection) != {"state", "matched_files", "matched_markers"}:
        raise ProfileError(f"{path}: detection must contain state, matched_files, and matched_markers")
    if detection.get("state") not in {"matched", "not_detected", "not_applicable"}:
        raise ProfileError(f"{path}: detection.state is invalid")
    if not isinstance(detection.get("matched_files"), list) or not isinstance(detection.get("matched_markers"), list):
        raise ProfileError(f"{path}: detection matches must be arrays")
    errors = validate_profile(data.get("profile"))
    if errors:
        raise ProfileError(f"{path}: invalid profile:\n" + "\n".join(f"- {item}" for item in errors))
    expected_hash = sha256_value(data["profile"])
    if data.get("hash") != expected_hash:
        raise ProfileError(f"{path}: profile hash does not match its definition")
    return data


def profile_summary(resolved: dict[str, Any]) -> dict[str, Any]:
    profile = resolved["profile"]
    return {
        "id": profile["id"],
        "version": profile["version"],
        "display_name": profile["display_name"],
        "hash": resolved["hash"],
        "detection_state": resolved.get("detection", {}).get("state", "not_applicable"),
    }
