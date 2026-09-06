#!/usr/bin/env python3
"""Select reproducible risk-directed and random control samples."""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from profile_core import load_resolved_profile, profile_summary
from reproducibility import AUTHORED_SOURCE_SUFFIXES, sampling_identity, source_snapshot
from review_core import load_yaml

SOURCE_SUFFIXES = AUTHORED_SOURCE_SUFFIXES
SKIP = {".git", ".venv", ".graphify-review-venv", "venv", "node_modules", "vendor", "dist", "build", "bin", "obj", "target", ".gradle", "__pycache__", "output", "graphify-out"}


def source_files(root: Path) -> list[str]:
    files: list[str] = []
    for current, directories, names in os.walk(root):
        directories[:] = sorted(item for item in directories if item not in SKIP)
        current_path = Path(current)
        files.extend(
            (current_path / name).relative_to(root).as_posix()
            for name in sorted(names)
            if (current_path / name).suffix.lower() in SOURCE_SUFFIXES
        )
    return sorted(files)


def git_commit(root: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "NO_COMMIT"


def risk_samples(graph: dict[str, Any], limit: int = 20) -> list[dict[str, str]]:
    nodes = {str(item.get("id")): item for item in graph.get("nodes", []) if isinstance(item, dict)}
    degrees: Counter[str] = Counter()
    for edge in graph.get("edges", []):
        if isinstance(edge, dict):
            degrees[str(edge.get("source"))] += 1
            degrees[str(edge.get("target"))] += 1
    selected: dict[str, dict[str, str]] = {}
    for cycle in graph.get("derived", {}).get("cycles", []):
        for node_id in cycle.get("nodes", []):
            node = nodes.get(str(node_id), {})
            target = node.get("path") or node.get("name") or str(node_id)
            selected[str(target)] = {"target": str(target), "selection_method": "risk_directed", "reason": f"Graphify cycle {cycle.get('id', '')}".strip()}
    for node_id, degree in sorted(degrees.items(), key=lambda pair: (-pair[1], pair[0])):
        node = nodes.get(node_id, {})
        target = node.get("path") or node.get("name") or node_id
        selected.setdefault(str(target), {"target": str(target), "selection_method": "risk_directed", "reason": f"Graph connectivity degree {degree}"})
        if len(selected) >= limit:
            break
    return list(selected.values())[:limit]


def select(
    root: Path,
    graph: dict[str, Any],
    policy: dict[str, Any],
    commit: str | None = None,
    profile_hash: str | None = None,
) -> dict[str, Any]:
    files = source_files(root)
    config = policy["review"]
    percent_count = math.ceil(len(files) * float(config["random_sampling_percent"]) / 100.0)
    count = max(int(config["random_sampling_min_files"]), percent_count) if files else 0
    count = min(count, int(config["random_sampling_max_files"]), len(files))
    snapshot = source_snapshot(root, [root / path for path in files])
    sampling_config = {
        "random_sampling_percent": config["random_sampling_percent"],
        "random_sampling_min_files": config["random_sampling_min_files"],
        "random_sampling_max_files": config["random_sampling_max_files"],
    }
    identity = sampling_identity(snapshot["hash"], sampling_config, profile_hash)
    seed = identity["seed"]
    rng = random.Random(int(seed, 16))
    random_selected = sorted(rng.sample(files, count)) if count else []
    return {
        "risk_selected": risk_samples(graph),
        "random_selected": [{"target": item, "selection_method": "random_control_sample", "reason": "Reproducible policy sample"} for item in random_selected],
        "seed": seed,
        "seed_inputs": {
            "source_snapshot_hash": snapshot["hash"],
            "source_file_count": snapshot["file_count"],
            "sampling_config_hash": identity["sampling_config"],
            "profile_hash": identity["profile"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default=".")
    parser.add_argument("--graph", required=True)
    parser.add_argument("--policy", default=str(Path(__file__).resolve().parents[1] / "policy.yaml"))
    parser.add_argument("--commit")
    parser.add_argument("--profile-file", help="resolved profile JSON; only its stable profile hash affects sampling")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    profile_hash = None
    if args.profile_file:
        profile_hash = profile_summary(load_resolved_profile(args.profile_file))["hash"]
    result = select(
        Path(args.repository).resolve(), json.loads(Path(args.graph).read_text(encoding="utf-8")),
        load_yaml(args.policy), args.commit, profile_hash,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
