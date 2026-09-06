#!/usr/bin/env python3
"""Resolve a requested framework profile to a validated, hashed envelope."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from profile_core import DEFAULT_PROFILES_DIR, ProfileError, resolve_profile


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="generic", help="profile id/alias, generic, or auto")
    parser.add_argument("--repository", default=".")
    parser.add_argument("--profiles-dir", default=str(DEFAULT_PROFILES_DIR))
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        result = resolve_profile(args.profile, args.repository, args.profiles_dir)
    except (OSError, ValueError, ProfileError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    payload = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
        print(f"Resolved profile {result['profile']['id']} to {output}")
        if result["profile"]["id"] != "generic" and result["detection"]["state"] != "matched":
            print("WARNING: the explicitly selected framework profile was not detected in repository evidence", file=sys.stderr)
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

