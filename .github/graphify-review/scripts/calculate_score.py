#!/usr/bin/env python3
"""Deterministically calculate category scores and the release decision."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from profile_core import load_resolved_profile, resolve_profile
from review_core import calculate_scores, load_yaml
from validate_policy import validate_policy


def load_findings(path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("findings"), list):
        return data["findings"]
    raise ValueError("findings input must be an array or an object with findings[]")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--findings", required=True)
    parser.add_argument("--policy", default=str(Path(__file__).resolve().parents[1] / "policy.yaml"))
    parser.add_argument("--confidence", type=float, help="assessment confidence percent")
    parser.add_argument("--confidence-file", help="JSON containing score_percent")
    parser.add_argument("--profile-file", help="resolved profile JSON produced by resolve_profile.py")
    parser.add_argument("--output")
    args = parser.parse_args()
    policy = load_yaml(args.policy)
    errors = validate_policy(policy)
    if errors:
        raise SystemExit("Invalid policy:\n" + "\n".join(f"- {error}" for error in errors))
    confidence = args.confidence
    confidence_below_threshold = None
    if args.confidence_file:
        confidence_data = json.loads(Path(args.confidence_file).read_text(encoding="utf-8"))
        confidence = confidence_data.get("score_percent", confidence_data.get("assessment_confidence", {}).get("score_percent"))
        confidence_below_threshold = confidence_data.get(
            "below_decision_threshold",
            confidence_data.get("assessment_confidence", {}).get("below_decision_threshold"),
        )
    profile = load_resolved_profile(args.profile_file) if args.profile_file else resolve_profile("generic", ".")
    try:
        result = calculate_scores(
            load_findings(args.findings), policy, confidence, profile=profile,
            confidence_below_threshold=confidence_below_threshold,
        )
    except ValueError as exc:
        raise SystemExit(f"Invalid scoreable finding: {exc}") from exc
    payload = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
        print(f"Wrote {output}")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
