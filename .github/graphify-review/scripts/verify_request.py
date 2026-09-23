"""Prepare/package independent approved-request verification; no Hub or Git mutations."""

import argparse
import json
import sys
import uuid
from pathlib import Path

from export_review import KIT_ROOT, read_json, write_new_json
from request_contract import validate_request_report, validate_specification
from revalidate_finding import source
from targeted_contract import TargetedError, digest


def prepare(args):
    root, baseline = Path(args.repository).resolve(), Path(args.baseline).resolve()
    approved, checksum = read_json(baseline)
    validate_specification(approved)
    current = source(root)
    if current["dirty"] and not args.allow_dirty:
        raise TargetedError(
            "Commit intended changes first, or explicitly allow a provisional dirty check."
        )
    run_id = str(uuid.uuid4())
    report = {
        "schema_version": "1.0",
        "kind": "request-verification",
        "run_id": run_id,
        "repository_external_id": approved["repository_external_id"],
        "baseline": {
            "request_id": approved["id"],
            "revision": approved["revision"],
            "specification_digest": digest(approved),
        },
        "source": current,
    }
    directory = KIT_ROOT / "output" / "request-verifications" / run_id
    directory.mkdir(parents=True, exist_ok=False)
    write_new_json(
        directory / "request.json",
        {
            "repository": str(root),
            "baseline_path": str(baseline),
            "baseline_sha256": checksum,
            "specification": approved,
            "report": report,
        },
    )
    return {
        "status": "needs_verification",
        "request": str(directory / "request.json"),
        "verification": str(directory / "verification.json"),
    }


def build(args):
    path = Path(args.request).resolve()
    request, _ = read_json(path)
    approved, checksum = read_json(Path(request["baseline_path"]))
    validate_specification(approved)
    report = request["report"]
    if (
        checksum != request["baseline_sha256"]
        or approved != request["specification"]
        or report["baseline"]
        != {
            "request_id": approved["id"],
            "revision": approved["revision"],
            "specification_digest": digest(approved),
        }
        or report["repository_external_id"] != approved["repository_external_id"]
    ):
        raise TargetedError(
            "Approved specification changed. Prepare anew; never rewrite a baseline."
        )
    if source(Path(request["repository"])) != report["source"]:
        raise TargetedError("Source changed. Prepare and independently verify again.")
    verification, _ = read_json(path.parent / "verification.json")
    report = {**report, "result": verification}
    validate_request_report(report)
    envelope = path.parent / "request-verification-envelope.json"
    if envelope.exists():
        previous, _ = read_json(envelope)
        if previous != report:
            raise TargetedError("Verification already exists with different content.")
    else:
        write_new_json(envelope, report)
    return {
        "status": "verified",
        "outcome": verification["status"],
        "envelope": str(envelope),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    preparation = actions.add_parser("prepare")
    preparation.add_argument("--repository", default=".")
    preparation.add_argument("--baseline", required=True)
    preparation.add_argument("--allow-dirty", action="store_true")
    building = actions.add_parser("build")
    building.add_argument("--request", required=True)
    args = parser.parse_args(argv)
    try:
        print(
            json.dumps(
                prepare(args) if args.action == "prepare" else build(args),
                ensure_ascii=True,
            )
        )
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "message": str(exc)
                    if isinstance(exc, TargetedError)
                    else "Request verification blocked; check inputs and evidence.",
                }
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
