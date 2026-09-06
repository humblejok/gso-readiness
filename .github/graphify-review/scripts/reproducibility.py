#!/usr/bin/env python3
"""Deterministic source snapshots and versioned review-run preparation."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from review_core import canonical_json, load_yaml, sha256_file, sha256_value
from validate_policy import validate_policy
from finding_identity import continuity_findings

RUN_MODES = {"fresh", "revalidate", "rescore"}
SKIP_PARTS = {
    ".git", ".venv", ".graphify-review-venv", "venv", "node_modules", "vendor", "dist", "build", "bin",
    "obj", "target", ".gradle", "coverage", "__pycache__", "output", "graphify-out", ".DS_Store",
}
REVIEW_INPUT_NAMES = {
    "Dockerfile", "Makefile", "Procfile", "compose.yaml", "docker-compose.yml", ".editorconfig", ".gitignore",
    "Pipfile", "Podfile", "go.mod", "gradle.lockfile",
}
AUTHORED_SOURCE_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go", ".rs", ".rb", ".php", ".cs", ".fs",
    ".vb", ".c", ".cc", ".cpp", ".h", ".hpp", ".swift", ".scala", ".sh", ".ps1", ".sql", ".proto",
    ".graphql", ".gql", ".cshtml", ".razor", ".html", ".css", ".scss", ".sass", ".vue", ".svelte",
    ".groovy", ".clj", ".cljs", ".ex", ".exs", ".dart", ".lua", ".pl", ".r",
}
REVIEW_INPUT_SUFFIXES = AUTHORED_SOURCE_SUFFIXES | {
    ".csproj", ".fsproj",
    ".vbproj", ".sln", ".slnx", ".props", ".targets", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".properties", ".xml", ".gradle", ".kts", ".lock", ".env", ".tf", ".tfvars", ".hcl", ".bicep",
    ".config", ".conf", ".md", ".rst", ".adoc",
}
REVIEW_KIT_PREFIXES = {
    (".github", "graphify-review"), (".github", "agents"), (".github", "skills"), (".github", "prompts"),
}


class ReproducibilityError(ValueError):
    """Raised when a strict review cannot establish stable inputs."""


def git_value(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)
        return result.stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def review_input_files(root: Path) -> list[Path]:
    """Return authored inputs that can influence a review, in stable order."""
    files: list[Path] = []
    for current, directories, names in os.walk(root):
        current_path = Path(current)
        relative_directory = current_path.relative_to(root)
        directories[:] = sorted(
            item for item in directories
            if item not in SKIP_PARTS and not any(part in SKIP_PARTS for part in (relative_directory / item).parts)
        )
        for name in sorted(names):
            path = current_path / name
            relative = path.relative_to(root)
            if any(part in SKIP_PARTS for part in relative.parts):
                continue
            if any(tuple(relative.parts[: len(prefix)]) == prefix for prefix in REVIEW_KIT_PREFIXES):
                continue
            if (
                name in REVIEW_INPUT_NAMES
                or name.startswith(("Dockerfile.", ".env."))
                or (name.lower().startswith("requirements") and path.suffix.lower() in {".txt", ".in"})
                or path.suffix.lower() in REVIEW_INPUT_SUFFIXES
            ):
                files.append(path)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def source_snapshot(
    root: Path,
    paths: Iterable[Path] | None = None,
    excluded_paths: Iterable[Path] | None = None,
) -> dict[str, Any]:
    """Hash path names and bytes so dirty or non-Git worktrees remain identifiable."""
    root = root.resolve()
    selected = list(paths) if paths is not None else review_input_files(root)
    excluded = {path.resolve() for path in (excluded_paths or [])}
    digest = hashlib.sha256()
    records: list[dict[str, Any]] = []
    for path in sorted(selected, key=lambda item: item.resolve().relative_to(root).as_posix()):
        absolute = path.resolve()
        if absolute in excluded:
            continue
        try:
            relative = absolute.relative_to(root).as_posix()
        except ValueError as exc:
            raise ReproducibilityError(f"snapshot path escapes repository: {path}") from exc
        if not absolute.is_file():
            continue
        path_bytes = relative.encode("utf-8")
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        file_digest = hashlib.sha256()
        size = 0
        with absolute.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                size += len(chunk)
                file_digest.update(chunk)
                digest.update(chunk)
        records.append({"path": relative, "size": size, "hash": "sha256:" + file_digest.hexdigest()})
    return {"hash": "sha256:" + digest.hexdigest(), "file_count": len(records), "files": records}


def sampling_identity(snapshot_hash: str, sampling_config: dict[str, Any], profile_hash: str | None = None) -> dict[str, str]:
    """Return seed inputs isolated from scoring, confidence, and model-policy changes."""
    config_hash = sha256_value(sampling_config)
    value = {"source_snapshot": snapshot_hash, "sampling_config": config_hash, "profile": profile_hash or "generic"}
    return {**value, "seed": hashlib.sha256(canonical_json(value)).hexdigest()}


def load_baseline(path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise ReproducibilityError(f"baseline does not exist: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReproducibilityError(f"baseline must be valid JSON: {path}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        raise ReproducibilityError("baseline must be a review.json object with findings[]")
    return data, sha256_file(path)


def prepare_run(
    repository: Path,
    policy: dict[str, Any],
    mode: str,
    output_root: Path,
    baseline_path: Path | None = None,
    allow_dirty: bool = False,
    now: datetime | None = None,
    excluded_inputs: Iterable[Path] | None = None,
) -> dict[str, Any]:
    repository = repository.resolve()
    if mode not in RUN_MODES:
        raise ReproducibilityError(f"mode must be one of {sorted(RUN_MODES)}")
    if mode in {"revalidate", "rescore"} and baseline_path is None:
        raise ReproducibilityError(f"mode={mode} requires baseline=<path-to-review.json>")
    if mode == "fresh" and baseline_path is not None:
        raise ReproducibilityError("mode=fresh does not accept a baseline; use mode=revalidate")

    commit = git_value(repository, "rev-parse", "HEAD")
    branch = git_value(repository, "branch", "--show-current")
    dirty_text = git_value(repository, "status", "--porcelain")
    dirty = bool(dirty_text) if commit else None
    strict = bool(policy.get("reproducibility", {}).get("require_clean_worktree", True))
    if strict and dirty and not allow_dirty:
        raise ReproducibilityError("strict reproducibility requires a clean worktree; commit/stash changes or pass --allow-dirty explicitly")

    exclusions = [path.resolve() for path in (excluded_inputs or [])]
    if baseline_path is not None:
        exclusions.append(baseline_path.resolve())
    snapshot = source_snapshot(repository, excluded_paths=exclusions)
    baseline: dict[str, Any] | None = None
    baseline_summary: dict[str, Any] | None = None
    if baseline_path is not None:
        baseline, baseline_hash = load_baseline(baseline_path.resolve())
        baseline_findings = continuity_findings(baseline)
        fingerprints = [item["fingerprint"] for item in baseline_findings]
        if len(fingerprints) != len(set(fingerprints)):
            raise ReproducibilityError("baseline contains duplicate supported/inconclusive finding fingerprints")
        baseline_summary = {
            "path": str(baseline_path.resolve()),
            "hash": baseline_hash,
            "review_id": baseline.get("review", {}).get("id"),
            "finding_count": len(baseline_findings),
            "verified_finding_count": len(baseline.get("findings", [])),
            "inconclusive_finding_count": len(baseline.get("inconclusive_findings", [])),
            "finding_fingerprints": sorted(fingerprints),
        }

    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
    identity = sha256_value({
        "mode": mode,
        "source_snapshot": snapshot["hash"],
        "policy": sha256_value(policy),
        "baseline": baseline_summary.get("hash") if baseline_summary else None,
    })
    run_id = f"{stamp}-{identity.split(':', 1)[1][:12]}"
    output_directory = output_root.resolve() / run_id
    if output_directory.exists():
        raise ReproducibilityError(f"refusing to overwrite existing run directory: {output_directory}")
    output_directory.mkdir(parents=True)
    (output_directory / "evidence").mkdir()

    context = {
        "schema_version": "1.0",
        "run_id": run_id,
        "mode": mode,
        "created_at_utc": now.isoformat(),
        "repository": {
            "root": str(repository), "commit_sha": commit, "branch": branch, "dirty_worktree": dirty,
            "dirty_override": bool(dirty and allow_dirty),
        },
        "source_snapshot": snapshot,
        "policy_hash": sha256_value(policy),
        "excluded_inputs": sorted(str(path) for path in exclusions),
        "baseline": baseline_summary,
        "output_directory": str(output_directory),
    }
    context_path = output_directory / "run-context.json"
    context_path.write_text(json.dumps(context, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if baseline is not None:
        claims = continuity_findings(baseline)
        (output_directory / "evidence" / "baseline-findings.json").write_text(
            json.dumps({"findings": claims}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        confidence = baseline.get("assessment_confidence", {})
        (output_directory / "evidence" / "baseline-confidence.json").write_text(
            json.dumps(confidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return context


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default=".")
    parser.add_argument("--policy", default=str(Path(__file__).resolve().parents[1] / "policy.yaml"))
    parser.add_argument("--mode", choices=sorted(RUN_MODES), default="fresh")
    parser.add_argument("--baseline")
    parser.add_argument("--output-root")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--exclude-input", action="append", default=[], help="generated/non-authored input to exclude from the source snapshot")
    args = parser.parse_args()
    policy = load_yaml(args.policy)
    errors = validate_policy(policy)
    if errors:
        raise SystemExit("Invalid policy:\n" + "\n".join(f"- {error}" for error in errors))
    configured_root = policy.get("reproducibility", {}).get("output_root", ".github/graphify-review/output/runs")
    output_root = Path(args.output_root or configured_root)
    if not output_root.is_absolute():
        output_root = Path(args.repository) / output_root
    try:
        repository = Path(args.repository).resolve()
        exclusions = [Path(value) if Path(value).is_absolute() else repository / value for value in args.exclude_input]
        context = prepare_run(
            repository, policy, args.mode, output_root,
            Path(args.baseline) if args.baseline else None, args.allow_dirty,
            excluded_inputs=exclusions,
        )
    except ReproducibilityError as exc:
        raise SystemExit(f"Unable to prepare review run: {exc}") from exc
    print(json.dumps({
        "run_id": context["run_id"], "mode": context["mode"],
        "output_directory": context["output_directory"],
        "source_snapshot_hash": context["source_snapshot"]["hash"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
