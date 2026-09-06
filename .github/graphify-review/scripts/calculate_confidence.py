#!/usr/bin/env python3
"""Calculate evidence coverage independently from project quality."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from review_core import calculate_confidence, load_yaml
from validate_policy import validate_policy


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--policy", default=str(Path(__file__).resolve().parents[1] / "policy.yaml"))
    parser.add_argument("--output")
    args = parser.parse_args()
    evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
    policy = load_yaml(args.policy)
    errors = validate_policy(policy)
    if errors:
        raise SystemExit("Invalid policy:\n" + "\n".join(f"- {error}" for error in errors))
    payload = json.dumps(calculate_confidence(evidence, policy), indent=2, ensure_ascii=False) + "\n"
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
