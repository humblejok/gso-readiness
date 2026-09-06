#!/usr/bin/env python3
"""Produce deterministic ASP.NET review anchors; anchors are signals, not findings."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from reproducibility import source_snapshot

SKIP_PARTS = {".git", ".venv", ".graphify-review-venv", "venv", "bin", "obj", "node_modules", "output", "graphify-out"}
WINDOWS_ABSOLUTE = re.compile(r"(?:[A-Za-z]:[\\/]|file:[/\\]{2,})")
SKIPPED_TEST = re.compile(r"\[(?:Fact|Theory)\s*\([^\]]*\bSkip\s*=|\.Skip\s*=", re.IGNORECASE)
SYNC_OVER_ASYNC = re.compile(r"\.(?:Result\b|Wait\s*\(|GetAwaiter\s*\(\s*\)\s*\.GetResult\s*\()")
BUILD_PROVIDER = re.compile(r"\.BuildServiceProvider\s*\(")
HTTP_CALL = re.compile(r"\.(?:GetAsync|PostAsync|PutAsync|DeleteAsync|SendAsync|GetFromJsonAsync|PostAsJsonAsync)\s*\(([^;]*)")
EXCEPTION_MESSAGE = re.compile(r"(?:return|Results\.|Problem\s*\(|GraphQLException).*\b(?:ex|exception)\.Message\b", re.IGNORECASE)
LOG_SENSITIVE = re.compile(r"Log(?:Trace|Debug|Information|Warning|Error|Critical)\s*\([^;]*(?:url|uri|query|token)", re.IGNORECASE)


def cs_files(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*.cs")
        if not any(part in SKIP_PARTS for part in path.relative_to(root).parts)
    )


def project_files(root: Path) -> list[Path]:
    suffixes = {".csproj", ".props", ".targets", ".config"}
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in suffixes
        and not any(part in SKIP_PARTS for part in path.relative_to(root).parts)
    )


def normalized_excerpt(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())[:240]


def anchor_id(rule_id: str, path: str, signal: str, excerpt: str, ordinal: int) -> str:
    value = json.dumps(
        {"rule": rule_id, "path": path, "signal": signal, "excerpt": normalized_excerpt(excerpt), "ordinal": ordinal},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return "anchor-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def add_matches(
    anchors: list[dict[str, Any]], root: Path, path: Path, text: str, pattern: re.Pattern[str],
    rule_id: str, categories: list[str], signal: str, guidance: str,
) -> None:
    relative = path.relative_to(root).as_posix()
    for ordinal, match in enumerate(pattern.finditer(text), 1):
        line = text.count("\n", 0, match.start()) + 1
        excerpt = text.splitlines()[line - 1] if text.splitlines() else match.group(0)
        anchors.append({
            "id": anchor_id(rule_id, relative, signal, excerpt, ordinal), "profile_rule_id": rule_id,
            "categories": categories, "signal": signal, "path": relative, "start_line": line,
            "excerpt_hash": "sha256:" + hashlib.sha256(normalized_excerpt(excerpt).encode("utf-8")).hexdigest(),
            "fact": normalized_excerpt(excerpt), "guidance": guidance, "requires_source_verification": True,
        })


def check_http_cancellation(anchors: list[dict[str, Any]], root: Path, path: Path, text: str) -> None:
    relative = path.relative_to(root).as_posix()
    for ordinal, match in enumerate(HTTP_CALL.finditer(text), 1):
        arguments = match.group(1)
        if re.search(r"CancellationToken|cancellationToken|stoppingToken|RequestAborted", arguments):
            continue
        line = text.count("\n", 0, match.start()) + 1
        excerpt = text.splitlines()[line - 1]
        anchors.append({
            "id": anchor_id("ASPNET-ASYNC-002", relative, "http-call-without-visible-cancellation", excerpt, ordinal),
            "profile_rule_id": "ASPNET-ASYNC-002", "categories": ["correctness", "operations"],
            "signal": "http-call-without-visible-cancellation", "path": relative, "start_line": line,
            "excerpt_hash": "sha256:" + hashlib.sha256(normalized_excerpt(excerpt).encode()).hexdigest(),
            "fact": normalized_excerpt(excerpt),
            "guidance": "Trace the request/host token through the caller before deciding whether cancellation is actually missing.",
            "requires_source_verification": True,
        })


def configuration_anchors(root: Path, paths: Iterable[Path]) -> list[dict[str, Any]]:
    anchors: list[dict[str, Any]] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        add_matches(
            anchors, root, path, text, WINDOWS_ABSOLUTE, "ASPNET-CONFIG-001", ["deployability", "correctness"],
            "machine-local-build-or-runtime-path",
            "Verify whether the absolute path is required by restore, build, test, or runtime execution on a clean host.",
        )
    return anchors


def startup_summary(root: Path, files: list[Path]) -> list[dict[str, Any]]:
    startup = [path for path in files if path.name in {"Program.cs", "Startup.cs"} or path.name.endswith("Extensions.cs")]
    combined = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in startup)
    if not startup or "WebApplication.CreateBuilder" not in combined:
        return []
    anchors: list[dict[str, Any]] = []
    path = startup[0]
    relative = path.relative_to(root).as_posix()
    auth_markers = ("AddAuthentication", "UseAuthentication", "AddAuthorization", "UseAuthorization", "RequireAuthorization")
    if not any(marker in combined for marker in auth_markers):
        signal = "no-in-process-authentication-or-authorization-marker"
        anchors.append({
            "id": anchor_id("ASPNET-MW-001", relative, signal, signal, 1), "profile_rule_id": "ASPNET-MW-001",
            "categories": ["security"], "signal": signal, "path": relative, "start_line": 1,
            "excerpt_hash": "sha256:" + hashlib.sha256(signal.encode()).hexdigest(),
            "fact": "No standard ASP.NET authentication/authorization registration or pipeline marker was found in startup files.",
            "guidance": "Establish the actual upstream trust boundary before promoting this signal to a finding.",
            "requires_source_verification": True,
        })
    options_used = bool(re.search(r"(?:AddOptions|Configure)\s*<", combined))
    if options_used and "ValidateOnStart" not in combined:
        signal = "options-binding-without-visible-validate-on-start"
        anchors.append({
            "id": anchor_id("ASPNET-CONFIG-001", relative, signal, signal, 1), "profile_rule_id": "ASPNET-CONFIG-001",
            "categories": ["correctness", "deployability"], "signal": signal, "path": relative, "start_line": 1,
            "excerpt_hash": "sha256:" + hashlib.sha256(signal.encode()).hexdigest(),
            "fact": "Typed options binding is present, but ValidateOnStart was not found in startup files.",
            "guidance": "Inspect validators and consumers; only report required settings that can fail after traffic starts.",
            "requires_source_verification": True,
        })
    return anchors


def analyze(repository: Path) -> dict[str, Any]:
    repository = repository.resolve()
    files = cs_files(repository)
    anchors = configuration_anchors(repository, project_files(repository))
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        add_matches(anchors, repository, path, text, BUILD_PROVIDER, "ASPNET-DI-003", ["architecture", "maintainability"], "container-built-during-registration", "Inspect ownership, duplicate singleton graphs, and whether resolution occurs before registration completes.")
        add_matches(anchors, repository, path, text, SYNC_OVER_ASYNC, "ASPNET-ASYNC-001", ["correctness", "operations"], "sync-over-async", "Trace the request/background execution path and establish starvation, deadlock, latency, or failure-observation consequences.")
        add_matches(anchors, repository, path, text, SKIPPED_TEST, "ASPNET-QUALITY-001", ["testing"], "skipped-test", "Inventory the skipped scenario and determine whether it removes meaningful regression coverage.")
        add_matches(anchors, repository, path, text, EXCEPTION_MESSAGE, "ASPNET-MW-001", ["security", "correctness"], "raw-exception-message-in-response", "Verify that internal/downstream exception text can reach an API caller.")
        add_matches(anchors, repository, path, text, LOG_SENSITIVE, "ASPNET-MW-001", ["security", "operations"], "potential-sensitive-url-or-query-logging", "Inspect the log template and arguments for tokens, identifiers, or sensitive query criteria.")
        check_http_cancellation(anchors, repository, path, text)
    anchors.extend(startup_summary(repository, files))
    anchors.sort(key=lambda item: (item["path"], item["start_line"], item["signal"], item["id"]))
    snapshot = source_snapshot(repository, files + project_files(repository))
    return {
        "schema_version": "1.0", "profile": "aspnet-core-api", "source_snapshot_hash": snapshot["hash"],
        "anchors": anchors, "statistics": {"anchors": len(anchors), "by_signal": {
            signal: sum(item["signal"] == signal for item in anchors) for signal in sorted({item["signal"] for item in anchors})
        }},
        "notice": "Deterministic anchors are mandatory investigation inputs and are not verified findings.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default=".")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = analyze(Path(args.repository))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {output} ({len(result['anchors'])} anchors)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
