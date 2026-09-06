#!/usr/bin/env python3
"""Build the normalized, evidence-first input for specialist review."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from profile_core import load_resolved_profile, matches_pattern, profile_summary, resolve_profile
from reproducibility import AUTHORED_SOURCE_SUFFIXES, source_snapshot
from review_core import COMPLETION_STATES, EXECUTION_STATES

SOURCE_SUFFIXES = AUTHORED_SOURCE_SUFFIXES
SKIP_DIRS = {".git", ".venv", ".graphify-review-venv", "venv", "node_modules", "vendor", "dist", "build", "bin", "obj", "target", ".gradle", "coverage", "__pycache__", "output", "graphify-out"}
CONFIG_NAMES = {"Dockerfile", "Makefile", "Procfile", "compose.yaml", "docker-compose.yml", "tox.ini", "pytest.ini", "mypy.ini", "ruff.toml", "tsconfig.json", "appsettings.json", "application.properties", "application.yml", "application.yaml", "Directory.Build.props", "Directory.Build.targets", ".editorconfig"}
CONFIG_SUFFIXES = {".toml", ".yaml", ".yml", ".ini", ".cfg", ".env", ".properties"}
MANIFEST_NAMES = {"pyproject.toml", "requirements.txt", "Pipfile", "poetry.lock", "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "gradle.lockfile", "Cargo.toml", "go.mod", "Gemfile", "composer.json", "packages.lock.json", "global.json", "Directory.Packages.props"}
MANIFEST_SUFFIXES = {".csproj", ".fsproj", ".vbproj", ".sln", ".slnx"}


def relative_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for current, directories, names in os.walk(root):
        directories[:] = sorted(item for item in directories if item not in SKIP_DIRS)
        current_path = Path(current)
        files.extend(current_path / name for name in sorted(names))
    return sorted(files)


def git_value(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)
        return result.stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def section(
    state: str,
    summary: str,
    items: list[Any] | None = None,
    completion: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    completion = completion or (
        "not_applicable" if state == "not_applicable"
        else "complete" if state in {"passed", "failed"}
        else "not_completed"
    )
    return {"state": state, "completion": completion, "summary": summary, "items": items or [], **extra}


def apply_result(evidence: dict[str, Any], result: dict[str, Any]) -> None:
    kind = result.get("kind")
    state = result.get("state")
    if state not in EXECUTION_STATES:
        raise ValueError(f"tool result {kind!r} has invalid state {state!r}")
    completion = result.get("completion")
    if completion not in COMPLETION_STATES:
        raise ValueError(f"tool result {kind!r} has invalid or missing completion {completion!r}")
    paths = {
        "tests_executed": ("tests",), "coverage": ("coverage",), "dependency_vulnerability_scan": ("security", "dependency_vulnerability_scan"),
        "secret_scan": ("security", "secret_scan"), "static_security_scan": ("security", "static_security_scan"),
        "configuration_analysis": ("configuration",), "source_analysis": ("source",), "graph_analysis": ("graph",), "dependencies": ("dependencies",),
    }
    if kind not in paths:
        raise ValueError(f"unknown tool result kind: {kind!r}")
    current = evidence
    for key in paths[kind][:-1]:
        current = current[key]
    target = dict(current[paths[kind][-1]])
    if kind in {"source_analysis", "graph_analysis", "configuration_analysis", "dependencies"} and target.get("completion") == "complete":
        audits = list(target.get("audit_results", []))
        audits.append({key: value for key, value in result.items() if key != "kind"})
        target["audit_results"] = audits
        if state == "failed":
            target["state"] = "failed"
    else:
        target.update({key: value for key, value in result.items() if key != "kind"})
    current[paths[kind][-1]] = target
    attempts = result.get("provider_attempts")
    tool_results = attempts if isinstance(attempts, list) and attempts else [result]
    for tool_result in tool_results:
        if not isinstance(tool_result, dict) or not tool_result.get("tool"):
            continue
        evidence["tools"].append({
            "name": tool_result["tool"], "version": tool_result.get("tool_version"),
            "command": tool_result.get("command"), "provider": tool_result.get("provider"),
            "state": tool_result.get("state"), "completion": tool_result.get("completion"),
            "exit_code": tool_result.get("exit_code"), "output_hash": tool_result.get("output_hash"),
        })


def matching_files(paths: list[str], patterns: list[str]) -> list[str]:
    return sorted({path for path in paths if any(matches_pattern(path, pattern) for pattern in patterns)})


def build(
    repository: Path,
    graph_path: Path | None,
    tool_results: list[dict[str, Any]],
    resolved_profile: dict[str, Any] | None = None,
    run_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_profile = resolved_profile or resolve_profile("generic", repository)
    profile = resolved_profile["profile"]
    files = relative_files(repository)
    relative_paths = [path.relative_to(repository).as_posix() for path in files]
    source_files = [path.relative_to(repository).as_posix() for path in files if path.suffix.lower() in SOURCE_SUFFIXES]
    excluded_inputs = [Path(value) for value in run_context.get("excluded_inputs", [])] if run_context else []
    snapshot = source_snapshot(repository, excluded_paths=excluded_inputs)
    profile_evidence = profile.get("evidence", {})
    profile_tests = matching_files(relative_paths, profile_evidence.get("test_patterns", []))
    test_files = sorted(set(profile_tests) | {path for path in source_files if "test" in Path(path).name.lower() or any(part in {"test", "tests", "spec", "specs"} for part in Path(path).parts)})
    profile_configs = matching_files(relative_paths, profile_evidence.get("configuration_patterns", []))
    config_files = sorted(set(profile_configs) | {path.relative_to(repository).as_posix() for path in files if path.name in CONFIG_NAMES or path.suffix.lower() in CONFIG_SUFFIXES})
    profile_manifests = matching_files(relative_paths, profile_evidence.get("manifest_patterns", []))
    manifests = sorted(set(profile_manifests) | {path.relative_to(repository).as_posix() for path in files if path.name in MANIFEST_NAMES or path.suffix.lower() in MANIFEST_SUFFIXES})
    startup_files = matching_files(relative_paths, profile_evidence.get("startup_patterns", []))
    applicable_audits = []
    for audit in profile.get("audits", []):
        applicable_files = matching_files(relative_paths, audit.get("when_any_files", []))
        if applicable_files:
            applicable_audits.append({**audit, "state": "not_run", "matched_files": applicable_files})
    commit = git_value(repository, "rev-parse", "HEAD")
    branch = git_value(repository, "branch", "--show-current")
    dirty_text = git_value(repository, "status", "--porcelain")
    graph_data: dict[str, Any] | None = None
    if graph_path and graph_path.exists():
        graph_data = json.loads(graph_path.read_text(encoding="utf-8"))
    graph_items = []
    graph_extra: dict[str, Any] = {}
    if graph_data:
        derived = graph_data.get("derived", {}) if isinstance(graph_data, dict) else {}
        graph_items = [{"source": str(graph_path), "schema_version": graph_data.get("schema_version")}]
        graph_extra = {"node_count": derived.get("node_count", len(graph_data.get("nodes", []))), "edge_count": derived.get("edge_count", len(graph_data.get("edges", []))), "cycles": derived.get("cycles", [])}

    evidence: dict[str, Any] = {
        "schema_version": "2.1",
        "repository": {
            "root": str(repository), "commit_sha": commit, "branch": branch,
            "dirty_worktree": bool(dirty_text) if commit else None,
            "source_snapshot_hash": snapshot["hash"], "source_file_count": snapshot["file_count"],
        },
        "profile": {
            **profile_summary(resolved_profile),
            "requested": resolved_profile.get("requested", profile["id"]),
            "detection": resolved_profile.get("detection", {}),
            "manifest_files": profile_manifests,
            "configuration_files": profile_configs,
            "startup_files": startup_files,
            "test_files": profile_tests,
            "applicable_audits": applicable_audits,
        },
        "graph": section("passed" if graph_data else "unavailable", "Normalized Graphify evidence loaded." if graph_data else "No normalized Graphify evidence was supplied.", graph_items, **graph_extra),
        "source": section("passed" if source_files else "unavailable", f"Inventoried {len(source_files)} source files.", source_files),
        "tests": section("not_run", f"Inventoried {len(test_files)} test files; execution was not established.", test_files),
        "coverage": section("not_run", "No coverage execution result was supplied."),
        "security": {
            "dependency_vulnerability_scan": section("not_run", "Dependency vulnerability status is unknown."),
            "secret_scan": section("not_run", "No dedicated secret scan result was supplied."),
            "static_security_scan": section("not_run", "No static security scan result was supplied."),
        },
        "dependencies": section("passed" if manifests else "unavailable", f"Inventoried {len(manifests)} dependency manifests.", manifests),
        "configuration": section("passed" if config_files else "unavailable", f"Inventoried {len(config_files)} configuration files.", config_files),
        "tools": ([{"name": "graphify", "version": None, "command": None, "state": "passed", "completion": "complete"}] if graph_data else []),
        "limitations": [],
    }
    for result in tool_results:
        apply_result(evidence, result)
    for name, item in evidence["security"].items():
        if item["state"] in {"not_run", "unavailable"}:
            evidence["limitations"].append(f"{name.replace('_', ' ').capitalize()} was not established.")
    if evidence["tests"]["state"] in {"not_run", "unavailable"}:
        evidence["limitations"].append("Test execution status was not established.")
    if profile["id"] != "generic" and resolved_profile.get("detection", {}).get("state") != "matched":
        evidence["limitations"].append(f"Selected profile {profile['id']} was not detected from repository markers; profile rules remain explicit but applicability requires reviewer confirmation.")
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default=".")
    parser.add_argument("--graph")
    parser.add_argument("--tool-results", help="JSON array of execution results")
    parser.add_argument("--profile", default="generic", help="profile id/alias used when --profile-file is omitted")
    parser.add_argument("--profile-file", help="resolved profile JSON produced by resolve_profile.py")
    parser.add_argument("--run-context", help="versioned run context used to reproduce source-snapshot exclusions")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    tool_results: list[dict[str, Any]] = []
    if args.tool_results:
        loaded = json.loads(Path(args.tool_results).read_text(encoding="utf-8"))
        if not isinstance(loaded, list) or not all(isinstance(item, dict) for item in loaded):
            raise SystemExit("--tool-results must contain a JSON array of objects")
        tool_results = loaded
    repository = Path(args.repository).resolve()
    resolved = load_resolved_profile(args.profile_file) if args.profile_file else resolve_profile(args.profile, repository)
    run_context = json.loads(Path(args.run_context).read_text(encoding="utf-8")) if args.run_context else None
    payload = build(repository, Path(args.graph) if args.graph else None, tool_results, resolved, run_context)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
