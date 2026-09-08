#!/usr/bin/env python3
"""Execute profile audit recipes with deterministic provenance and completeness."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from profile_core import load_resolved_profile, matches_pattern
from reproducibility import review_input_files

PREREQUISITE_PATTERNS = re.compile(
    r"(assets file .* not found|nuget source .* does not exist|unable to load the service index|"
    r"could not find a part of the path|command not found|no such file or directory|sdk .* not found)",
    re.IGNORECASE,
)
NO_TESTS_PATTERNS = re.compile(
    r"(no test is available|no tests? (?:were found|to run)|0 tests? (?:found|discovered)|\bno-source\b)",
    re.IGNORECASE,
)
TEST_COMPILATION_PATTERNS = re.compile(
    r"(compilation (?:error|failure)|maven-compiler-plugin|execution failed for task [^\n]*compile|"
    r"\berror CS\d{4}\b|\berror [A-Z]{2,5}\d{3,5}:|could not compile)",
    re.IGNORECASE,
)
SECURITY_SCANNERS = {"auto", "native", "jfrog"}
JFROG_MANAGER_FLAGS = {
    "maven": "--mvn",
    "gradle": "--gradle",
    "nuget": "--nuget",
    "npm": "--npm",
    "yarn": "--yarn",
    "pnpm": "--pnpm",
    "pip": "--pip",
    "pipenv": "--pipenv",
    "poetry": "--poetry",
    "go": "--go",
    "cocoapods": "--cocoapods",
    "swift": "--swift",
}
PROFILE_JFROG_MANAGERS = {
    "aspnet-core-api": {"nuget"},
    "spring-boot": {"maven", "gradle"},
}
JFROG_SECRET_EXCLUSIONS = ";".join((
    "**/.git/**", "**/.venv/**", "**/.graphify-review-venv/**", "**/venv/**",
    "**/node_modules/**", "**/vendor/**", "**/dist/**", "**/build/**", "**/bin/**", "**/obj/**",
    "**/target/**", "**/.gradle/**", "**/coverage/**", "**/.github/graphify-review/output/**",
    "**/graphify-out/**",
))


def output_hash(stdout: str, stderr: str) -> str:
    return "sha256:" + hashlib.sha256((stdout + "\n---stderr---\n" + stderr).encode("utf-8")).hexdigest()


def tool_version(executable: str, cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            [executable, "--version"], cwd=cwd, capture_output=True,
            text=True, encoding="utf-8-sig", errors="replace", timeout=20,
        )
        return (result.stdout or result.stderr).strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        return None


def run_command(
    command: list[str],
    cwd: Path,
    timeout: int,
    environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command, cwd=cwd, capture_output=True, timeout=timeout, env=environment,
        )
        # JSON must never be parsed after lossy decoding of package/advisory identifiers.
        try:
            stdout = result.stdout.decode("utf-8-sig")
            decoding_error = False
        except UnicodeDecodeError:
            stdout = result.stdout.decode("utf-8-sig", errors="replace")
            decoding_error = True
        return {
            "exit_code": result.returncode, "stdout": stdout,
            "stderr": result.stderr.decode("utf-8-sig", errors="replace"),
            "timed_out": False, "decoding_error": decoding_error,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "exit_code": None,
            "stdout": exc.stdout.decode("utf-8-sig", errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout or "",
            "stderr": exc.stderr.decode("utf-8-sig", errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr or "",
            "timed_out": True,
        }


def classify(audit: dict[str, Any], execution: dict[str, Any]) -> tuple[str, str, str]:
    combined = execution["stdout"] + "\n" + execution["stderr"]
    if execution["timed_out"]:
        return "unavailable", "not_completed", "Audit timed out before producing complete evidence."
    if execution.get("decoding_error"):
        return "unavailable", "not_completed", "Audit output could not be decoded as UTF-8."
    if audit.get("evidence_kind") == "tests_executed" and NO_TESTS_PATTERNS.search(combined):
        return "unavailable", "not_completed", "The test runner completed without discovering executable tests."
    if execution["exit_code"] == 0:
        return "passed", "complete", "Audit completed for every declared target."
    if PREREQUISITE_PATTERNS.search(combined):
        return "unavailable", "not_completed", "Audit prerequisites were unavailable; no complete result was produced."
    if audit.get("evidence_kind") == "dependency_vulnerability_scan":
        return "unavailable", "not_completed", "Dependency scan did not complete successfully for every target."
    if audit.get("evidence_kind") == "tests_executed" and TEST_COMPILATION_PATTERNS.search(combined):
        return "unavailable", "not_completed", "Compilation failed before the test suite produced a complete result."
    if audit.get("evidence_kind") in {"secret_scan", "static_security_scan"} and execution["exit_code"] != 1:
        return "unavailable", "not_completed", "Security scanner failed before producing a trustworthy result."
    return "failed", "complete", "Audit completed and reported project/test/analyzer failures."


def dotnet_vulnerability_commands(audit: dict[str, Any], repository: Path) -> list[tuple[str, list[str]]]:
    projects = sorted(
        path for path in audit.get("matched_files", [])
        if Path(path).suffix.lower() in {".csproj", ".fsproj", ".vbproj"} and (repository / path).is_file()
    )
    base = shlex.split(audit["command"])
    if len(base) >= 3 and base[:3] == ["dotnet", "list", "package"]:
        tail = base[3:]
        return [(project, ["dotnet", "list", project, "package", *tail]) for project in projects]
    return []


def execute_audit(audit: dict[str, Any], repository: Path, timeout: int) -> dict[str, Any]:
    command = shlex.split(audit["command"])
    executable = command[0]
    base = {
        "kind": audit["evidence_kind"], "audit_id": audit["id"], "provider": "native", "tool": executable,
        "tool_version": tool_version(executable, repository), "command": audit["command"],
        "targets": sorted(audit.get("matched_files", [])),
    }
    if shutil.which(executable) is None:
        return {
            **base, "state": "unavailable", "completion": "not_completed", "exit_code": None,
            "output_hash": output_hash("", f"{executable} is not installed"), "items": [],
            "summary": f"Required executable {executable!r} is unavailable.",
        }

    commands = dotnet_vulnerability_commands(audit, repository)
    if audit["id"] == "dotnet-vulnerable-packages" and not commands:
        return {
            **base, "state": "unavailable", "completion": "not_completed", "exit_code": None,
            "output_hash": output_hash("", "No project targets were resolved"), "items": [],
            "summary": "No project targets were available for a complete dependency scan.",
        }
    commands = commands or [("repository", command)]
    base["targets"] = [target for target, _ in commands]
    items: list[dict[str, Any]] = []
    all_stdout: list[str] = []
    all_stderr: list[str] = []
    states: list[str] = []
    completions: list[str] = []
    exit_codes: list[int | None] = []
    for target, target_command in commands:
        execution = run_command(target_command, repository, timeout)
        state, completion, summary = classify(audit, execution)
        stdout, stderr = execution["stdout"], execution["stderr"]
        all_stdout.append(stdout)
        all_stderr.append(stderr)
        states.append(state)
        completions.append(completion)
        exit_codes.append(execution["exit_code"])
        items.append({
            "target": target, "command": shlex.join(target_command), "state": state, "completion": completion,
            "exit_code": execution["exit_code"], "output_hash": output_hash(stdout, stderr), "summary": summary,
        })
    complete = all(item == "complete" for item in completions)
    if not complete:
        state, completion = "unavailable", "partial" if any(item == "complete" for item in completions) else "not_completed"
    elif any(item == "failed" for item in states):
        state, completion = "failed", "complete"
    else:
        state, completion = "passed", "complete"
    return {
        **base, "state": state, "completion": completion,
        "exit_code": exit_codes[0] if len(exit_codes) == 1 else None,
        "output_hash": output_hash("\n".join(all_stdout), "\n".join(all_stderr)),
        "items": items, "covered_targets": [item["target"] for item in items if item["completion"] == "complete"],
        "summary": f"{len([item for item in items if item['completion'] == 'complete'])}/{len(items)} audit targets completed.",
    }


def parse_jfrog_simple_json(stdout: str) -> dict[str, Any] | None:
    """Parse the stable JFrog simple-json envelope, tolerating non-JSON log prefixes."""
    try:
        parsed = json.loads(stdout)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, character in enumerate(stdout):
            if character != "{":
                continue
            try:
                parsed, _ = decoder.raw_decode(stdout[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and "scansStatus" in parsed:
                return parsed
    return None


def normalized_repository_path(value: Any, repository: Path) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(repository.resolve()).as_posix()
        except (OSError, ValueError):
            return path.name
    normalized = path.as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized if normalized and not normalized.startswith("../") else path.name


def _source_collections(payload: dict[str, Any], names: tuple[str, ...]) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    for name in names:
        values = payload.get(name, [])
        if isinstance(values, list):
            rows.extend((name, value) for value in values if isinstance(value, dict))
    return rows


def extract_jfrog_sca_findings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    findings: dict[tuple[str, str, str], dict[str, Any]] = {}
    for name in ("vulnerabilities", "securityViolations"):
        rows = payload.get(name)
        if rows is not None and (not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows)):
            raise ValueError("Invalid JFrog finding collection")
    for index, (collection, row) in enumerate(_source_collections(payload, ("vulnerabilities", "securityViolations"))):
        cves = []
        for cve in row.get("cves", []) if isinstance(row.get("cves"), list) else []:
            if not isinstance(cve, dict) or not isinstance(cve.get("id"), str):
                continue
            identifier = cve["id"].strip()
            if not identifier:
                continue
            cves.append({"id": identifier, **{
                key: cve[key] for key in ("cvssV2", "cvssV3", "cwe") if cve.get(key)
            }})
        issue_id = row.get("issueId")
        issue_id = issue_id.strip() if isinstance(issue_id, str) else ""
        package = str(row.get("impactedPackageName") or "")
        version = str(row.get("impactedPackageVersion") or "")
        # Keep unidentified rows separate; callers must not use them for automatic upgrades.
        identity_missing = not issue_id and not cves
        identity = issue_id or ",".join(sorted({item["id"] for item in cves}))
        key = (identity or f"unidentified-row:{index}", package, version)
        normalized = findings.setdefault(key, {
            "type": "dependency_vulnerability",
            "issue_id": issue_id or None,
            "identity_missing": identity_missing,
            "advisory_ids": sorted({item["id"] for item in cves}),
            "package": package,
            "installed_version": version,
            "package_type": row.get("impactedPackageType"),
            "severity": str(row.get("severity") or "unknown").lower(),
            "summary": row.get("summary"),
            "applicability": row.get("applicable"),
            "fixed_versions": sorted(str(item) for item in (row.get("fixedVersions") or [])),
            "cves": cves,
            "components": [
                {key: component[key] for key in ("id", "name", "version") if component.get(key)}
                for component in (row.get("components") or []) if isinstance(component, dict)
            ],
            "impact_paths": [
                [
                    {key: component[key] for key in ("id", "name", "version") if component.get(key)}
                    for component in path if isinstance(component, dict)
                ]
                for path in (row.get("impactPaths") or []) if isinstance(path, list)
            ],
            "references": sorted(str(item) for item in (row.get("references") or [])),
            "result_types": [],
            "source": {"tool": "jfrog-xray"},
        })
        normalized["result_types"] = sorted(set(normalized["result_types"]) | {collection})
        for field in ("watch", "policies", "fail_build"):
            if row.get(field) not in (None, "", []):
                normalized[field] = row[field]
    return sorted(findings.values(), key=lambda item: (
        item.get("severity", ""), item.get("package", ""), item.get("installed_version", ""),
        item.get("issue_id") or "",
    ))


def extract_jfrog_secret_findings(payload: dict[str, Any], repository: Path) -> list[dict[str, Any]]:
    """Return secret metadata without persisting findings, snippets, or code-flow content."""
    findings: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    for collection, row in _source_collections(payload, ("secrets", "secretsViolations")):
        file_path = normalized_repository_path(row.get("file"), repository) or "unknown"
        line = row.get("startLine") if isinstance(row.get("startLine"), int) else 0
        rule_id = str(row.get("ruleId") or "unknown")
        fingerprint = str(row.get("fingerprint") or "")
        applicability = row.get("applicability") if isinstance(row.get("applicability"), dict) else {}
        applicability_status = str(applicability.get("status") or "unknown")
        key = (rule_id, fingerprint, file_path, line)
        normalized = findings.setdefault(key, {
            "type": "secret_detection",
            "rule_id": rule_id,
            "severity": str(row.get("severity") or "unknown").lower(),
            "path": file_path,
            "start_line": line or None,
            "start_column": row.get("startColumn") if isinstance(row.get("startColumn"), int) else None,
            "end_line": row.get("endLine") if isinstance(row.get("endLine"), int) else None,
            "end_column": row.get("endColumn") if isinstance(row.get("endColumn"), int) else None,
            "fingerprint": fingerprint or None,
            "applicability": applicability_status,
            "active": applicability_status.lower() not in {"inactive", "not applicable", "not_applicable"},
            "result_types": [],
            "source": {"tool": "jfrog-secrets"},
        })
        normalized["result_types"] = sorted(set(normalized["result_types"]) | {collection})
        for field in ("watch", "policies", "fail_build"):
            if row.get(field) not in (None, "", []):
                normalized[field] = row[field]
    return sorted(findings.values(), key=lambda item: (
        item.get("path", ""), item.get("start_line") or 0, item.get("rule_id", ""),
    ))


def jfrog_payload_state(
    payload: dict[str, Any] | None,
    scan_kind: str,
    execution: dict[str, Any],
    finding_count: int,
) -> tuple[str, str, str]:
    if execution["timed_out"]:
        return "unavailable", "not_completed", "JFrog audit timed out before producing complete evidence."
    if execution.get("decoding_error"):
        return "unavailable", "not_completed", "JFrog audit stdout is not valid UTF-8; no upgrade decision is safe."
    if execution["exit_code"] not in {0, 3}:
        return "unavailable", "not_completed", "JFrog audit failed before producing a trustworthy scan result."
    if payload is None:
        return "unavailable", "not_completed", "JFrog audit did not produce valid simple-json output."
    statuses = payload.get("scansStatus")
    status_key = "scaScanStatusCode" if scan_kind == "sca" else "secretsScanStatusCode"
    if not isinstance(statuses, dict) or not isinstance(statuses.get(status_key), int):
        return "unavailable", "not_completed", f"JFrog did not report a completed {scan_kind} sub-scan."
    if statuses[status_key] != 0:
        return "unavailable", "not_completed", f"JFrog reported a failed {scan_kind} sub-scan."
    errors = payload.get("errors", [])
    if isinstance(errors, list) and errors:
        return "unavailable", "partial", f"JFrog completed part of the {scan_kind} scan but reported target errors."
    if finding_count:
        return "failed", "complete", f"JFrog completed the {scan_kind} scan and reported findings."
    return "passed", "complete", f"JFrog completed the {scan_kind} scan without findings."


def jfrog_failure_hints(payload: dict[str, Any] | None, execution: dict[str, Any]) -> list[str]:
    """Classify setup failures without retaining potentially sensitive CLI text."""
    messages = [str(execution.get("stderr") or "")]
    errors = payload.get("errors", []) if isinstance(payload, dict) else []
    for error in errors if isinstance(errors, list) else []:
        if isinstance(error, dict):
            messages.append(str(error.get("errorMessage") or ""))
    text = "\n".join(messages).lower()
    patterns = {
        "authentication_or_authorization": ("unauthorized", "forbidden", "authentication", "401", "403"),
        "advanced_security_entitlement": ("entitlement", "not entitled", "advanced security", "license"),
        "network_proxy_or_tls": ("proxy", "connection refused", "certificate", "x509", "tls", "timed out"),
        "dependency_or_extractor_resolution": (
            "dependency resolution", "failed to resolve", "restore failed", "extractor", "service index",
        ),
        "jfrog_cli_version": ("unknown flag", "unrecognized option", "unsupported flag"),
        "server_configuration": ("no server", "server id", "server-id", "platform url"),
    }
    return sorted(name for name, markers in patterns.items() if any(marker in text for marker in markers))


def manifest_manager(relative_path: str, sibling_names: set[str]) -> str | None:
    path = Path(relative_path)
    name = path.name
    lower = name.lower()
    suffix = path.suffix.lower()
    if lower == "pom.xml":
        return "maven"
    if lower in {"settings.gradle", "settings.gradle.kts", "build.gradle", "build.gradle.kts", "gradle.lockfile"}:
        return "gradle"
    if suffix in {".csproj", ".fsproj", ".vbproj", ".sln", ".slnx"} or lower in {"packages.lock.json", "directory.packages.props"}:
        return "nuget"
    if lower == "pnpm-lock.yaml":
        return "pnpm"
    if lower == "yarn.lock":
        return "yarn"
    if lower in {"package-lock.json", "npm-shrinkwrap.json"}:
        return "npm"
    if lower == "package.json":
        if "pnpm-lock.yaml" in sibling_names:
            return "pnpm"
        if "yarn.lock" in sibling_names:
            return "yarn"
        return "npm"
    if lower == "pipfile":
        return "pipenv"
    if lower == "poetry.lock":
        return "poetry"
    if lower.startswith("requirements") and suffix in {".txt", ".in"}:
        return "pip"
    if lower == "go.mod":
        return "go"
    if lower == "podfile":
        return "cocoapods"
    if name == "Package.swift":
        return "swift"
    return None


def _is_within(directory: str, parent: str) -> bool:
    return parent == "." or directory == parent or directory.startswith(parent.rstrip("/") + "/")


def discover_jfrog_targets(repository: Path, paths: list[str], profile_id: str) -> list[dict[str, Any]]:
    names_by_directory: dict[str, set[str]] = {}
    for relative in paths:
        directory = Path(relative).parent.as_posix()
        names_by_directory.setdefault(directory, set()).add(Path(relative).name.lower())

    manifests_by_manager: dict[str, list[str]] = {}
    for relative in paths:
        directory = Path(relative).parent.as_posix()
        manager = manifest_manager(relative, names_by_directory.get(directory, set()))
        if manager:
            manifests_by_manager.setdefault(manager, []).append(relative)

    allowed = PROFILE_JFROG_MANAGERS.get(profile_id)
    targets: list[dict[str, Any]] = []
    for manager, manifests in sorted(manifests_by_manager.items()):
        if allowed is not None and manager not in allowed:
            continue
        directories = sorted({Path(path).parent.as_posix() for path in manifests}, key=lambda item: (len(Path(item).parts), item))
        if manager == "gradle":
            settings_dirs = {
                Path(path).parent.as_posix() for path in manifests
                if Path(path).name.lower() in {"settings.gradle", "settings.gradle.kts"}
            }
            directories = sorted(
                settings_dirs | {directory for directory in directories if not any(_is_within(directory, root) for root in settings_dirs)},
                key=lambda item: (len(Path(item).parts), item),
            )
        elif manager == "nuget":
            solution_dirs = {
                Path(path).parent.as_posix() for path in manifests if Path(path).suffix.lower() in {".sln", ".slnx"}
            }
            directories = sorted(
                solution_dirs | {directory for directory in directories if not any(_is_within(directory, root) for root in solution_dirs)},
                key=lambda item: (len(Path(item).parts), item),
            )
        roots: list[str] = []
        for directory in directories:
            if not any(_is_within(directory, root) for root in roots):
                roots.append(directory)
        for directory in roots:
            target_manifests = sorted(
                path for path in manifests if _is_within(Path(path).parent.as_posix(), directory)
            )
            targets.append({
                "id": f"{manager}:{directory}",
                "manager": manager,
                "directory": directory,
                "manifests": target_manifests,
            })
    return sorted(targets, key=lambda item: item["id"])


def jfrog_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.setdefault("CI", "true")
    environment.setdefault("JFROG_CLI_AVOID_NEW_VERSION_WARNING", "true")
    return environment


def jfrog_command(scan_kind: str, manager: str | None, server_id: str | None, use_wrapper: bool = False) -> list[str]:
    command = ["jf", "audit"]
    if manager:
        command.append(JFROG_MANAGER_FLAGS[manager])
    command.extend([f"--{scan_kind}", "--format=simple-json", "--fail=false"])
    if scan_kind == "sca":
        command.extend(["--without-contextual-analysis", "--vuln"])
    else:
        command.append(f"--exclusions={JFROG_SECRET_EXCLUSIONS}")
    if use_wrapper:
        command.append("--use-wrapper")
    if server_id:
        command.append(f"--server-id={server_id}")
    return command


def execute_jfrog_target(
    repository: Path,
    target: dict[str, Any],
    scan_kind: str,
    timeout: int,
    server_id: str | None,
) -> dict[str, Any]:
    directory = repository / target["directory"]
    manager = target.get("manager") if scan_kind == "sca" else None
    wrapper_names = {"maven": ("mvnw", "mvnw.cmd"), "gradle": ("gradlew", "gradlew.bat")}
    use_wrapper = manager in wrapper_names and any((directory / name).is_file() for name in wrapper_names[manager])
    command = jfrog_command(scan_kind, manager, server_id, use_wrapper)
    execution = run_command(command, directory, timeout, jfrog_environment())
    payload = None
    findings = []
    active_findings = []
    parsing_error = None
    if not execution.get("decoding_error") and not execution["timed_out"]:
        try:
            payload = parse_jfrog_simple_json(execution["stdout"])
            if scan_kind == "sca":
                findings = extract_jfrog_sca_findings(payload or {})
                active_findings = findings
            else:
                findings = extract_jfrog_secret_findings(payload or {}, repository)
                active_findings = [item for item in findings if item.get("active")]
        except (ValueError, TypeError, KeyError, AttributeError):
            # Exception text and raw CLI output can contain credentials or scanned secrets.
            parsing_error = "JFrog output could not be normalized; no complete scan evidence was produced."
    state, completion, summary = jfrog_payload_state(payload, scan_kind, execution, len(active_findings))
    if parsing_error:
        state, completion, summary = "unavailable", "not_completed", parsing_error
    elif any(item.get("identity_missing") for item in findings):
        state, completion, summary = (
            "unavailable", "not_completed",
            "JFrog findings include entries without an Xray issue ID or CVE ID; automatic upgrade decisions are blocked.",
        )
    setup_hints = jfrog_failure_hints(payload, execution) if completion != "complete" else []
    if setup_hints:
        summary += f" Setup checks: {', '.join(setup_hints)}."
    errors = payload.get("errors", []) if isinstance(payload, dict) else []
    sanitized_errors = []
    for error in errors if isinstance(errors, list) else []:
        if not isinstance(error, dict):
            continue
        message = str(error.get("errorMessage") or "")
        sanitized_errors.append({
            "path": normalized_repository_path(error.get("filePath"), repository),
            "message_hash": output_hash(message, ""),
        })
    return {
        "target": target["id"],
        "working_directory": target["directory"],
        "manager": manager,
        "manifests": target.get("manifests", []),
        "command": shlex.join(command),
        "state": state,
        "completion": completion,
        "exit_code": execution["exit_code"],
        "scan_status": (payload or {}).get("scansStatus"),
        "output_hash": output_hash(execution["stdout"], execution["stderr"]),
        "findings": findings,
        "errors": sanitized_errors,
        "setup_hints": setup_hints,
        "summary": summary,
    }


def aggregate_jfrog_results(
    scan_kind: str,
    target_results: list[dict[str, Any]],
    version: str | None,
    server_id: str | None,
) -> dict[str, Any]:
    evidence_kind = "dependency_vulnerability_scan" if scan_kind == "sca" else "secret_scan"
    targets = [item["target"] for item in target_results]
    covered = [item["target"] for item in target_results if item["completion"] == "complete"]
    complete = len(covered) == len(targets) and bool(targets)
    if complete:
        state = "failed" if any(item["state"] == "failed" for item in target_results) else "passed"
        completion = "complete"
    else:
        state = "unavailable"
        completion = "partial" if covered else "not_completed"
    findings = [finding for item in target_results for finding in item.get("findings", [])]
    setup_hints = sorted({hint for item in target_results for hint in item.get("setup_hints", [])})
    command = target_results[0]["command"] if len(target_results) == 1 else "jf audit (one invocation per declared target)"
    return {
        "kind": evidence_kind,
        "audit_id": f"jfrog-xray-{scan_kind}",
        "provider": "jfrog",
        "tool": "jfrog-xray" if scan_kind == "sca" else "jfrog-secrets",
        "executable": "jf",
        "tool_version": version,
        "server_id": server_id or "configured-default",
        "command": command,
        "targets": targets,
        "covered_targets": covered,
        "state": state,
        "completion": completion,
        "exit_code": target_results[0]["exit_code"] if len(target_results) == 1 else None,
        "output_hash": output_hash(json.dumps([item["output_hash"] for item in target_results]), ""),
        "items": target_results,
        "findings": findings,
        "setup_hints": setup_hints,
        "summary": (
            f"{len(covered)}/{len(targets)} JFrog {scan_kind} targets completed; {len(findings)} findings reported."
            + (f" Setup checks: {', '.join(setup_hints)}." if setup_hints else "")
        ),
    }


def unavailable_jfrog_result(
    scan_kind: str,
    targets: list[dict[str, Any]],
    reason: str,
    server_id: str | None,
) -> dict[str, Any]:
    evidence_kind = "dependency_vulnerability_scan" if scan_kind == "sca" else "secret_scan"
    target_ids = [item["id"] for item in targets]
    return {
        "kind": evidence_kind,
        "audit_id": f"jfrog-xray-{scan_kind}",
        "provider": "jfrog",
        "tool": "jfrog-xray" if scan_kind == "sca" else "jfrog-secrets",
        "executable": "jf",
        "tool_version": None,
        "server_id": server_id or "configured-default",
        "command": None,
        "targets": target_ids,
        "covered_targets": [],
        "state": "unavailable",
        "completion": "not_completed",
        "exit_code": None,
        "output_hash": output_hash("", reason),
        "items": [],
        "findings": [],
        "summary": reason,
    }


def run_jfrog_sca(
    repository: Path,
    paths: list[str],
    profile_id: str,
    timeout: int,
    server_id: str | None,
) -> dict[str, Any]:
    targets = discover_jfrog_targets(repository, paths, profile_id)
    if not targets:
        if profile_id == "generic":
            return {
                "kind": "dependency_vulnerability_scan", "audit_id": "jfrog-xray-sca", "provider": "jfrog",
                "tool": "jfrog-xray", "executable": "jf", "tool_version": tool_version("jf", repository),
                "server_id": server_id or "configured-default", "command": None, "targets": [],
                "covered_targets": [], "state": "not_applicable", "completion": "not_applicable",
                "exit_code": None, "output_hash": output_hash("", "No supported dependency manifests"),
                "items": [], "findings": [], "summary": "No JFrog-supported dependency manifests were detected.",
            }
        return unavailable_jfrog_result(
            "sca", targets, f"No supported dependency target was detected for profile {profile_id!r}.", server_id,
        )
    if shutil.which("jf") is None:
        return unavailable_jfrog_result("sca", targets, "Required executable 'jf' is unavailable.", server_id)
    version = tool_version("jf", repository)
    target_results = [
        execute_jfrog_target(repository, target, "sca", timeout, server_id) for target in targets
    ]
    return aggregate_jfrog_results("sca", target_results, version, server_id)


def run_jfrog_secrets(repository: Path, timeout: int, server_id: str | None) -> dict[str, Any]:
    target = {"id": "repository", "directory": ".", "manifests": []}
    if shutil.which("jf") is None:
        return unavailable_jfrog_result("secrets", [target], "Required executable 'jf' is unavailable.", server_id)
    version = tool_version("jf", repository)
    target_result = execute_jfrog_target(repository, target, "secrets", timeout, server_id)
    return aggregate_jfrog_results("secrets", [target_result], version, server_id)


def attempt_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: result.get(key) for key in (
            "audit_id", "provider", "tool", "tool_version", "command", "state", "completion", "exit_code",
            "output_hash", "summary",
        )
    }


def fallback_result(primary: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    selected = dict(fallback)
    selected["provider_attempts"] = [attempt_summary(primary), attempt_summary(fallback)]
    selected["summary"] = f"{fallback.get('summary', '')} JFrog was attempted first but did not complete."
    return selected


def run_audits(
    repository: Path,
    resolved_profile: dict[str, Any],
    timeout: int = 300,
    security_scanner: str = "auto",
    jfrog_server_id: str | None = None,
) -> list[dict[str, Any]]:
    if security_scanner not in SECURITY_SCANNERS:
        raise ValueError(f"security_scanner must be one of {sorted(SECURITY_SCANNERS)}")
    audits = resolved_profile.get("profile", {}).get("audits", [])
    paths = [path.relative_to(repository).as_posix() for path in review_input_files(repository)]
    results = []
    resolved_server_id = jfrog_server_id or os.environ.get("JFROG_CLI_SERVER_ID")
    use_jfrog = security_scanner == "jfrog" or (security_scanner == "auto" and shutil.which("jf") is not None)
    native_dependency_audits = []
    for definition in audits:
        matched = sorted({path for path in paths if any(matches_pattern(path, pattern) for pattern in definition.get("when_any_files", []))})
        if matched:
            audit = {**definition, "matched_files": matched}
            if audit.get("evidence_kind") == "dependency_vulnerability_scan":
                native_dependency_audits.append(audit)
                if use_jfrog:
                    continue
            results.append(execute_audit(audit, repository, timeout))

    if use_jfrog:
        jfrog_sca = run_jfrog_sca(
            repository, paths, resolved_profile.get("profile", {}).get("id", "generic"), timeout, resolved_server_id,
        )
        if jfrog_sca["completion"] == "complete" or jfrog_sca["state"] == "not_applicable" or security_scanner == "jfrog":
            results.append(jfrog_sca)
        elif native_dependency_audits:
            native_results = [execute_audit(audit, repository, timeout) for audit in native_dependency_audits]
            results.append(fallback_result(jfrog_sca, native_results[0]))
        else:
            results.append(jfrog_sca)

    gitleaks_audit = {
        "id": "gitleaks-secret-scan", "title": "Scan repository-authored content for secrets",
        "command": "gitleaks detect --source . --no-banner --redact --exit-code 1",
        "evidence_kind": "secret_scan", "matched_files": ["repository"],
    }
    if use_jfrog:
        jfrog_secrets = run_jfrog_secrets(repository, timeout, resolved_server_id)
        if jfrog_secrets["completion"] == "complete" or security_scanner == "jfrog":
            results.append(jfrog_secrets)
        else:
            results.append(fallback_result(jfrog_secrets, execute_audit(gitleaks_audit, repository, timeout)))
    else:
        results.append(execute_audit(gitleaks_audit, repository, timeout))

    semgrep_config = next((path for path in (".semgrep.yml", ".semgrep.yaml") if (repository / path).is_file()), None)
    if semgrep_config:
        results.append(execute_audit({
            "id": "semgrep-static-security", "title": "Run repository-configured Semgrep rules",
            "command": f"semgrep scan --config {semgrep_config} --metrics off --error .",
            "evidence_kind": "static_security_scan", "matched_files": [semgrep_config],
        }, repository, timeout))
    else:
        results.append({
            "kind": "static_security_scan", "audit_id": "semgrep-static-security", "provider": "native",
            "tool": "semgrep",
            "tool_version": None, "command": None, "targets": [], "covered_targets": [],
            "state": "unavailable", "completion": "not_completed", "exit_code": None,
            "output_hash": output_hash("", "No repository Semgrep configuration was found"), "items": [],
            "summary": "Static security scan is unavailable because no repository Semgrep configuration was found.",
        })
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default=".")
    parser.add_argument("--profile-file", required=True)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--security-scanner", choices=sorted(SECURITY_SCANNERS), default="auto")
    parser.add_argument("--jfrog-server-id", help="Configured JFrog CLI server ID; credentials are never accepted here")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    resolved = load_resolved_profile(args.profile_file)
    results = run_audits(
        Path(args.repository).resolve(), resolved, args.timeout, args.security_scanner, args.jfrog_server_id,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {output} ({len(results)} audits)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
