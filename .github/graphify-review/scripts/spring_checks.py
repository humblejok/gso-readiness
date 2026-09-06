#!/usr/bin/env python3
"""Produce deterministic Spring Boot review anchors; anchors are signals, not findings."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from reproducibility import source_snapshot

SKIP_PARTS = {
    ".git", ".venv", ".graphify-review-venv", "venv", "build", "target", "out", "node_modules",
    "output", "graphify-out",
}
SOURCE_SUFFIXES = {".java", ".kt"}
CONFIG_NAMES = {"application.properties", "application.yml", "application.yaml"}
FIELD_OR_METHOD_INJECTION = re.compile(r"@(?:Autowired|Inject)\b")
SERVICE_LOCATOR = re.compile(r"\b(?:ApplicationContext|BeanFactory)\b[^;\n]*\.getBean\s*\(")
BLOCKING_ASYNC = re.compile(r"\.(?:get|join)\s*\(\s*\)|\bThread\.sleep\s*\(")
DISABLED_TEST = re.compile(r"@(?:Disabled|Ignore)\b")
RAW_EXCEPTION_MESSAGE = re.compile(
    r"(?:ResponseEntity|body\s*\(|ProblemDetail|ResponseStatusException)[^;\n]*(?:ex|exception|throwable)\.getMessage\s*\(",
    re.IGNORECASE,
)
WILDCARD_CORS = re.compile(r"(?:allowedOrigins|setAllowedOrigins|addAllowedOriginPattern)\s*\([^;\n]*[\"']\*[\"']")
SENSITIVE_LOGGING = re.compile(
    r"\b(?:log|logger)\.(?:trace|debug|info|warn|error)\s*\([^;\n]*(?:url|uri|query|token)", re.IGNORECASE,
)
ACTUATOR_WILDCARD = re.compile(r"management\.endpoints\.web\.exposure\.include\s*[:=]\s*[\"']?\*[\"']?", re.IGNORECASE)


def source_files(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in SOURCE_SUFFIXES
        and not any(part in SKIP_PARTS for part in path.relative_to(root).parts)
    )


def configuration_files(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*")
        if path.is_file()
        and (
            path.name in CONFIG_NAMES
            or path.name.startswith("application-") and path.suffix.lower() in {".properties", ".yml", ".yaml"}
        )
        and not any(part in SKIP_PARTS for part in path.relative_to(root).parts)
    )


def normalized_excerpt(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())[:240]


def stable_anchor_id(rule_id: str, path: str, signal: str, excerpt: str, ordinal: int) -> str:
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
    lines = text.splitlines()
    for ordinal, match in enumerate(pattern.finditer(text), 1):
        line = text.count("\n", 0, match.start()) + 1
        excerpt = lines[line - 1] if lines else match.group(0)
        anchors.append({
            "id": stable_anchor_id(rule_id, relative, signal, excerpt, ordinal),
            "profile_rule_id": rule_id,
            "categories": categories,
            "signal": signal,
            "path": relative,
            "start_line": line,
            "excerpt_hash": "sha256:" + hashlib.sha256(normalized_excerpt(excerpt).encode("utf-8")).hexdigest(),
            "fact": normalized_excerpt(excerpt),
            "guidance": guidance,
            "requires_source_verification": True,
        })


def application_summaries(root: Path, files: list[Path]) -> list[dict[str, Any]]:
    entries: list[tuple[Path, str]] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        if any(marker in text for marker in ("@SpringBootApplication", "@RestController", "SecurityFilterChain", "@ConfigurationProperties", "@Async")):
            entries.append((path, text))
    if not entries:
        return []
    combined = "\n".join(text for _, text in entries)
    first_path = entries[0][0]
    relative = first_path.relative_to(root).as_posix()
    anchors: list[dict[str, Any]] = []

    def add_summary(rule_id: str, categories: list[str], signal: str, fact: str, guidance: str) -> None:
        anchors.append({
            "id": stable_anchor_id(rule_id, relative, signal, signal, 1),
            "profile_rule_id": rule_id,
            "categories": categories,
            "signal": signal,
            "path": relative,
            "start_line": 1,
            "excerpt_hash": "sha256:" + hashlib.sha256(signal.encode("utf-8")).hexdigest(),
            "fact": fact,
            "guidance": guidance,
            "requires_source_verification": True,
        })

    if "@RestController" in combined and not any(marker in combined for marker in ("SecurityFilterChain", "@EnableWebSecurity", "@EnableMethodSecurity")):
        add_summary(
            "SPRING-SEC-001", ["security"], "no-in-process-spring-security-marker",
            "REST controllers were found, but no SecurityFilterChain or method/web security marker was found in authored source.",
            "Establish dependencies and the upstream trust boundary before promoting this signal to a finding.",
        )
    if "@ConfigurationProperties" in combined and not any(marker in combined for marker in ("@Validated", "@Valid")):
        add_summary(
            "SPRING-CONFIG-001", ["correctness", "deployability"], "configuration-properties-without-visible-validation",
            "ConfigurationProperties binding was found, but no validation marker was found in the inspected source.",
            "Inspect constraints, registration, and consumers; report only required configuration that can fail after startup.",
        )
    if "@Async" in combined and not any(marker in combined for marker in ("TaskExecutor", "AsyncConfigurer", "ThreadPoolTaskExecutor")):
        add_summary(
            "SPRING-ASYNC-001", ["correctness", "operations"], "async-usage-without-visible-executor-ownership",
            "@Async usage was found without a visible authored executor configuration.",
            "Confirm the effective executor, queue limits, exception handling, context propagation, and deployment load.",
        )
    return anchors


def analyze(repository: Path) -> dict[str, Any]:
    repository = repository.resolve()
    files = source_files(repository)
    configs = configuration_files(repository)
    anchors: list[dict[str, Any]] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        add_matches(anchors, repository, path, text, FIELD_OR_METHOD_INJECTION, "SPRING-DI-001", ["architecture", "maintainability"], "annotation-driven-member-injection", "Determine whether this is field/method injection and whether it hides required collaborators or prevents invariant construction and focused tests.")
        add_matches(anchors, repository, path, text, SERVICE_LOCATOR, "SPRING-DI-001", ["architecture", "maintainability"], "application-context-service-location", "Trace whether dynamic bean lookup is framework integration or hides required application dependencies.")
        add_matches(anchors, repository, path, text, BLOCKING_ASYNC, "SPRING-ASYNC-001", ["correctness", "operations"], "blocking-or-waiting-on-async-work", "Trace the execution context and establish thread starvation, latency, deadlock, or failure-observation consequences.")
        add_matches(anchors, repository, path, text, DISABLED_TEST, "SPRING-QUALITY-001", ["testing"], "disabled-test", "Inventory the disabled scenario and determine whether it removes meaningful regression coverage.")
        add_matches(anchors, repository, path, text, RAW_EXCEPTION_MESSAGE, "SPRING-API-001", ["security", "correctness"], "raw-exception-message-in-response", "Verify whether internal or downstream exception text can reach an API caller.")
        add_matches(anchors, repository, path, text, WILDCARD_CORS, "SPRING-SEC-001", ["security"], "wildcard-cors-origin", "Confirm credentials mode, endpoint sensitivity, browser clients, and any proxy-enforced origin boundary.")
        add_matches(anchors, repository, path, text, SENSITIVE_LOGGING, "SPRING-API-001", ["security", "operations"], "potential-sensitive-url-or-query-logging", "Inspect the log template and arguments for tokens, identifiers, or sensitive query criteria.")
    for path in configs:
        text = path.read_text(encoding="utf-8", errors="replace")
        add_matches(anchors, repository, path, text, ACTUATOR_WILDCARD, "SPRING-OPS-001", ["operations", "security"], "wildcard-actuator-web-exposure", "Inspect management port/bind address and Spring Security rules before deciding whether sensitive endpoints are externally reachable.")
    anchors.extend(application_summaries(repository, files))
    anchors.sort(key=lambda item: (item["path"], item["start_line"], item["signal"], item["id"]))
    snapshot = source_snapshot(repository, files + configs)
    return {
        "schema_version": "1.0",
        "profile": "spring-boot",
        "source_snapshot_hash": snapshot["hash"],
        "anchors": anchors,
        "statistics": {
            "anchors": len(anchors),
            "by_signal": {signal: sum(item["signal"] == signal for item in anchors) for signal in sorted({item["signal"] for item in anchors})},
        },
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
