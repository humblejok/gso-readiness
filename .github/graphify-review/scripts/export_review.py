#!/usr/bin/env python3
"""Prepare and build an offline Finding Hub envelope from an immutable past review."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

KIT_ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 10 * 1024 * 1024


class ExportError(ValueError):
    """A safe, user-facing export error (never include submitted prose)."""


def read_json(path: Path) -> tuple[Any, str]:
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ExportError("Input exceeds 10 MiB.")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ExportError("Input contains duplicate JSON keys.")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ExportError("Non-finite JSON numbers are not supported.")

    try:
        data = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_object, parse_constant=invalid_constant)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ExportError("Input must be valid UTF-8 JSON, not a Markdown report.") from exc
    return data, "sha256:" + hashlib.sha256(raw).hexdigest()


def load_review(path: Path) -> tuple[dict, str, dict]:
    from import_contract import identity_fingerprint, review_validator

    review, checksum = read_json(path)
    error = next(review_validator(KIT_ROOT / "schema").iter_errors(review), None)
    if error:
        raise ExportError("Expected a complete schema-2.1 review.json; invalid review schema.")
    fingerprints = set()
    for collection in ("findings", "inconclusive_findings", "rejected_findings"):
        for finding in review[collection]:
            fp = finding["fingerprint"]
            if fp in fingerprints or fp != identity_fingerprint(finding["identity"]):
                raise ExportError("Duplicate or invalid finding fingerprint; the original review must not be rewritten by export.")
            fingerprints.add(fp)
    context = {}
    context_path = path.parent / "run-context.json"
    if context_path.exists():
        context, _ = read_json(context_path)
        if not isinstance(context, dict) or context.get("run_id") != review["run"]["run_id"]:
            raise ExportError("The adjacent run context belongs to a different run.")
        snapshot = context.get("source_snapshot", {}).get("hash")
        if snapshot and snapshot != review["run"]["source_snapshot_hash"]:
            raise ExportError("Source snapshot conflicts with the run context.")
    return review, checksum, context


def source_metadata(review: dict, context: dict, commit: str | None, branch: str | None) -> dict:
    recorded = review["review"]["repository"]
    context_repo = context.get("repository", {})
    result = {}
    for field, override in (("commit_sha", commit), ("branch", branch)):
        values = [value for value in (recorded.get(field), context_repo.get(field), override) if value not in (None, "")]
        if not values or any(not isinstance(value, str) or not value.strip() for value in values):
            raise ExportError(f"Original {field} is unavailable; explicitly supply the reviewed source metadata, never current HEAD.")
        compared = [value.lower() if field == "commit_sha" else value for value in values]
        if len(set(compared)) != 1:
            raise ExportError(f"Conflicting original {field}; export cannot relabel a historical review.")
        result[field] = values[0]
    if not re.fullmatch(r"[a-fA-F0-9]{7,64}", result["commit_sha"]):
        raise ExportError("Original commit must be a 7-to-64-character hexadecimal revision.")
    if len(result["branch"]) > 200:
        raise ExportError("Original branch exceeds 200 characters.")
    return result


def write_new_json(path: Path, data: Any) -> None:
    encoded = (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    if len(encoded) > MAX_BYTES:
        raise ExportError("Output exceeds the Hub's 10 MiB upload limit.")
    # Exclusive creation: repeated exports must never replace an earlier payload.
    with path.open("xb") as stream:
        stream.write(encoded)


def preparation_request(args: argparse.Namespace) -> dict:
    from import_contract import text
    from review_settings import load_settings

    settings = load_settings(Path(args.repository))
    saved = settings["project"]
    path = Path(args.review).resolve()
    if path.is_dir():
        path /= "review.json"
    review, checksum, context = load_review(path)
    source = source_metadata(review, context, args.source_commit, args.source_branch)
    name = args.repository_name or saved.get("repository_name") or review["review"]["repository"].get("name")
    repository = {"external_id": args.repository_id or saved.get("repository_id"), "name": name,
                  "default_branch": args.default_branch or saved.get("default_branch")}
    if not all(text(repository[key], limit) for key, limit in (("external_id", 500), ("name", 200), ("default_branch", 200))):
        raise ExportError("Supply a stable repository ID, repository name and default branch.")
    if args.clone_url:
        from urllib.parse import urlsplit

        url = urlsplit(args.clone_url)
        if (url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment
                or len(args.clone_url) > 1000):
            raise ExportError("Clone URL must be credential-free HTTPS, without a query or fragment.")
        repository["clone_url"] = args.clone_url
    warnings = [
        "Export validates the Hub contract; it does not rerun historical policy/evidence validation or verify remediation safety.",
        "Remediation plans are proposals for the recorded source revision; commands are not executed.",
    ]
    if review["review"]["repository"].get("dirty_worktree") or context.get("repository", {}).get("dirty_worktree"):
        warnings.append("The original review included uncommitted changes. The recorded commit alone cannot reproduce its source snapshot.")
    request = {
        "schema_version": "1.0", "review_path": str(path), "review_sha256": checksum,
        "repository": repository, "source": source,
        "run_id": review["run"]["run_id"], "required_findings": review["findings"],
        "warnings": warnings,
        "hub_url": settings["user"].get("hub_url"),
    }
    return request


def prepare(args: argparse.Namespace) -> dict:
    request = preparation_request(args)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = Path(args.output_directory).resolve() if args.output_directory else KIT_ROOT / "output" / "exports" / f"{stamp}-{uuid.uuid4().hex[:8]}"
    directory.mkdir(parents=True, exist_ok=False)
    request_path = directory / "export-request.json"
    write_new_json(request_path, request)
    if not request["required_findings"]:
        write_new_json(directory / "remediations.json", [])
    return {
        "status": "prepared", "request": str(request_path), "remediations": str(directory / "remediations.json"),
        "required_plan_count": len(request["required_findings"]), "warnings": request["warnings"],
    }


def build(args: argparse.Namespace) -> dict:
    from import_contract import digest, validate_envelope

    request_path = Path(args.request).resolve()
    request, _ = read_json(request_path)
    if request.get("schema_version") != "1.0":
        raise ExportError("Unsupported export request version.")
    path = Path(request["review_path"])
    review, checksum, context = load_review(path)
    if checksum != request["review_sha256"] or review["run"]["run_id"] != request["run_id"]:
        raise ExportError("The source review changed after export preparation; no envelope was written.")
    source = source_metadata(review, context, request["source"]["commit_sha"], request["source"]["branch"])
    remediation_path = Path(args.remediations).resolve() if args.remediations else request_path.parent / "remediations.json"
    if not remediation_path.is_file():
        raise ExportError("Provide remediations.json: one source-bound plan for every supported finding.")
    plans, _ = read_json(remediation_path)
    envelope = {
        "schema_version": "1.0", "repository": request["repository"], "source": source,
        "review": review, "remediations": plans,
    }
    key = f"{envelope['repository']['external_id']}:{review['run']['run_id']}"
    validate_envelope(envelope, key, KIT_ROOT / "schema")
    # Review is embedded unchanged. Do not change timestamps, run IDs, scores or fingerprints.
    output = request_path.parent / "import-envelope.json"
    write_new_json(output, envelope)
    return {
        "status": "exported", "envelope": str(output), "idempotency_key": key,
        "run_id": review["run"]["run_id"],
        "payload_sha256": "sha256:" + digest(envelope), "review_sha256": checksum,
        "remediation_count": len(plans), "warnings": request.get("warnings", []),
        "hub_url": request.get("hub_url"),
    }


def ensure(args: argparse.Namespace) -> dict:
    """Resume a run-local export or verify it is current, without replacing any envelope."""
    from import_contract import digest, validate_envelope
    from review_settings import load_settings

    if args.if_hub_configured and not load_settings(Path(args.repository))["user"].get("hub_url"):
        return {"status": "not_configured", "message": "No Hub configured; automatic local export is not required. Nothing was uploaded."}
    expected = preparation_request(args)
    directory = Path(args.output_directory).resolve() if args.output_directory else Path(expected["review_path"]).parent / "hub"
    request_path = directory / "export-request.json"
    output = directory / "import-envelope.json"
    if directory.exists():
        if not request_path.is_file():
            raise ExportError("Export directory exists without its source-bound request. Do not overwrite it; select a new export directory explicitly.")
        stored, _ = read_json(request_path)
        fields = ("schema_version", "review_path", "review_sha256", "repository", "source", "run_id", "required_findings")
        if not isinstance(stored, dict) or any(stored.get(field) != expected[field] for field in fields):
            raise ExportError("Existing export belongs to a different or changed review/project. It was not updated. Use the new run's own hub directory; never overwrite a baseline envelope.")
    else:
        options = argparse.Namespace(**vars(args))
        options.output_directory = str(directory)
        prepare(options)
    if output.exists():
        envelope, _ = read_json(output)
        review, checksum, _ = load_review(Path(expected["review_path"]))
        key = f"{expected['repository']['external_id']}:{expected['run_id']}"
        validate_envelope(envelope, key, KIT_ROOT / "schema")
        if (checksum != expected["review_sha256"] or envelope["review"] != review
                or envelope["repository"] != expected["repository"] or envelope["source"] != expected["source"]):
            raise ExportError("Existing envelope is stale or belongs to another review/project. Nothing was overwritten; do not publish it for this run.")
        if args.remediations:
            plans, _ = read_json(Path(args.remediations))
            if plans != envelope["remediations"]:
                raise ExportError("An envelope already exists with different remediation plans. Reuse the original unchanged; do not replace published history.")
        return {
            "status": "exported", "reused": True, "envelope": str(output), "run_id": expected["run_id"],
            "idempotency_key": key, "review_sha256": checksum, "payload_sha256": "sha256:" + digest(envelope),
            "remediation_count": len(envelope["remediations"]), "warnings": expected["warnings"],
            "hub_url": expected["hub_url"],
        }
    plans_path = Path(args.remediations).resolve() if args.remediations else directory / "remediations.json"
    if not plans_path.exists() and not expected["required_findings"] and not args.remediations:
        write_new_json(plans_path, [])
    if not plans_path.exists():
        return {
            "status": "needs_remediations", "request": str(request_path), "remediations": str(plans_path),
            "run_id": expected["run_id"], "review_sha256": expected["review_sha256"],
            "required_plan_count": len(expected["required_findings"]), "warnings": expected["warnings"],
            "message": "The review exists but its Hub envelope is not ready. Generate source-bound plans for this run, then rerun ensure. Nothing was uploaded.",
        }
    return build(argparse.Namespace(request=str(request_path), remediations=str(plans_path)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    preparation = argparse.ArgumentParser(add_help=False)
    preparation.add_argument("--review", required=True, help="Explicit review.json or run directory; never auto-select latest")
    preparation.add_argument("--repository", default=".", help="Project whose saved defaults should be used")
    preparation.add_argument("--repository-id", help="Overrides saved project repository ID")
    preparation.add_argument("--repository-name")
    preparation.add_argument("--default-branch", help="Overrides saved project default branch (never historical source branch)")
    preparation.add_argument("--clone-url")
    preparation.add_argument("--source-commit", help="Original revision, only to fill missing metadata; conflicts are rejected")
    preparation.add_argument("--source-branch", help="Original branch, only to fill missing metadata; conflicts are rejected")
    preparation.add_argument("--output-directory", help="Export directory: prepare requires a new one; ensure can resume/verify a matching one")
    commands.add_parser("prepare", parents=[preparation], help="Read a historical run and produce a remediation request")
    ensuring = commands.add_parser("ensure", parents=[preparation], help="Create/resume a run-local hub export, or verify and reuse its matching envelope")
    ensuring.add_argument("--remediations", help="Optional source-bound plans; an existing envelope cannot be replaced")
    ensuring.add_argument("--if-hub-configured", action="store_true", help="Skip automatic local export when no user Hub URL is configured")
    building = commands.add_parser("build", help="Validate plans and write the import envelope without uploading")
    building.add_argument("--request", required=True)
    building.add_argument("--remediations", help="JSON array of plans; defaults to remediations.json beside the request")
    args = parser.parse_args(argv)
    try:
        result = {"prepare": prepare, "build": build, "ensure": ensure}[args.action](args)
    except ImportError:
        print(json.dumps({"status": "blocked", "error": "Export dependencies are missing. Run bootstrap_environment.py --first-call --json (or install the kit requirements in its dedicated environment)."}), file=sys.stderr)
        return 2
    except FileExistsError:
        print(json.dumps({"status": "blocked", "error": "Export output already exists; use a new directory or reuse the existing envelope unchanged."}), file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        from import_contract import ValidationError

        message = str(exc) if isinstance(exc, (ExportError, ValidationError)) else "Unable to read inputs or write a valid export; check file access and input structure."
        print(json.dumps({"status": "blocked", "error": message}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 3 if result["status"] == "needs_remediations" else 0


if __name__ == "__main__":
    raise SystemExit(main())
