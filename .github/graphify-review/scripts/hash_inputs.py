#!/usr/bin/env python3
"""Create the reproducibility manifest with SHA-256 input hashes."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from profile_core import load_resolved_profile, profile_summary, resolve_profile
from review_core import load_yaml, sha256_file, sha256_value

SKIP_PARTS = {".git", "__pycache__", "output", ".DS_Store"}


def directory_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for file_path in sorted(item for item in path.rglob("*") if item.is_file() and not any(part in SKIP_PARTS for part in item.relative_to(path).parts)):
        relative = file_path.relative_to(path).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        with file_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def git(root: Path, *args: str) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def load_records(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ValueError(f"{path} must contain a JSON array of objects")
    return data


def package_version() -> str | None:
    for name in ("graphifyy", "graphify"):
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    return None


def build_manifest(repository: Path, graph: Path, policy_path: Path, review_kit: Path, models: list[dict[str, Any]], tools: list[dict[str, Any]], profile: dict[str, Any] | None = None, run_context: dict[str, Any] | None = None) -> dict[str, Any]:
    commit = git(repository, "rev-parse", "HEAD")
    branch = git(repository, "branch", "--show-current")
    dirty = git(repository, "status", "--porcelain")
    policy = load_yaml(policy_path)
    graph_hash = sha256_file(graph)
    policy_hash = sha256_value(policy)
    profile = profile or resolve_profile("generic", repository)
    selected_profile = profile_summary(profile)
    kit_hash = directory_hash(review_kit)
    source_snapshot_hash = (run_context or {}).get("source_snapshot", {}).get("hash")
    identity = sha256_value({
        "commit": commit, "source_snapshot": source_snapshot_hash, "graph": graph_hash,
        "policy": policy_hash, "profile": selected_profile, "kit": kit_hash,
        "mode": (run_context or {}).get("mode"), "baseline": (run_context or {}).get("baseline", {}).get("hash") if isinstance((run_context or {}).get("baseline"), dict) else None,
        "models": models, "tools": tools,
    })
    return {
        "review_id": f"review-{identity.split(':', 1)[1][:16]}",
        "timestamp_utc": (run_context or {}).get("created_at_utc") or datetime.now(timezone.utc).isoformat(),
        "repository": {"commit_sha": commit, "branch": branch, "dirty_worktree": bool(dirty) if commit else None},
        "run": {
            "run_id": (run_context or {}).get("run_id"), "mode": (run_context or {}).get("mode"),
            "source_snapshot_hash": source_snapshot_hash, "baseline": (run_context or {}).get("baseline"),
        },
        "graphify": {"version": package_version(), "input_hash": graph_hash},
        "policy": {"version": policy.get("version"), "hash": policy_hash},
        "profile": selected_profile,
        "review_kit": {"version": (review_kit / "VERSION").read_text(encoding="utf-8").strip() if (review_kit / "VERSION").exists() else None, "hash": kit_hash},
        "models": models,
        "tools": tools,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default=".")
    parser.add_argument("--graph", required=True)
    parser.add_argument("--policy", default=str(Path(__file__).resolve().parents[1] / "policy.yaml"))
    parser.add_argument("--review-kit", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--models")
    parser.add_argument("--tools")
    parser.add_argument("--profile", default="generic", help="profile id/alias used when --profile-file is omitted")
    parser.add_argument("--profile-file", help="resolved profile JSON produced by resolve_profile.py")
    parser.add_argument("--run-context", help="versioned run-context.json produced by reproducibility.py")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    repository = Path(args.repository).resolve()
    profile = load_resolved_profile(args.profile_file) if args.profile_file else resolve_profile(args.profile, repository)
    run_context = json.loads(Path(args.run_context).read_text(encoding="utf-8")) if args.run_context else None
    result = build_manifest(repository, Path(args.graph), Path(args.policy), Path(args.review_kit), load_records(args.models), load_records(args.tools), profile, run_context)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
