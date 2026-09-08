"""Prepare/build an immutable single-finding result; never rescore or revalidate other findings."""
import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path

from export_review import KIT_ROOT, ExportError, load_review, read_json, write_new_json
from reproducibility import source_snapshot
from review_settings import load_settings
from targeted_contract import TargetedError, digest, validate_targeted


def source(root):
    def git(*arguments):
        try:
            result = subprocess.run(["git", *arguments], cwd=root, check=True, capture_output=True)
            return result.stdout.decode("utf-8")
        except (OSError, subprocess.CalledProcessError, UnicodeError) as exc:
            raise TargetedError("Cannot read unambiguous UTF-8 Git/source metadata for this checkout.") from exc
    commit = git("rev-parse", "HEAD").strip()
    branch = git("symbolic-ref", "--quiet", "--short", "HEAD").strip()
    if not commit or not branch:
        raise TargetedError("Use a Git checkout on a named branch with a recorded commit.")
    files = git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
    paths = [root / name for name in set(files.split("\0")) if name]
    if any(path.is_dir() for path in paths):
        raise TargetedError("Targeted snapshots do not support Git submodule/directory entries. Revalidate the affected repository as its own checkout.")
    return {"commit_sha": commit, "branch": branch, "snapshot_hash": source_snapshot(root, paths=paths)["hash"],
            "dirty": bool(git("status", "--porcelain"))}


def prepare(args):
    root = Path(args.repository).resolve()
    path = Path(args.baseline).resolve()
    if path.is_dir():
        path /= "review.json"
    review, checksum, _ = load_review(path)
    findings = [f for group in ("findings", "inconclusive_findings", "rejected_findings") for f in review[group]]
    selected = [f for f in findings if args.finding in {f["id"], f["fingerprint"]}]
    if len(selected) != 1:
        raise TargetedError("Finding selector must match exactly one baseline ID or fingerprint.")
    finding = selected[0]
    repository_id = args.repository_id or load_settings(root)["project"].get("repository_id")
    if not repository_id:
        raise TargetedError("Configure the stable repository ID or supply --repository-id.")
    current = source(root)
    if current["dirty"] and not args.allow_dirty:
        raise TargetedError("Worktree is dirty. Commit intended changes first, or explicitly use --allow-dirty for a local provisional check.")
    run_id = str(uuid.uuid4())
    directory = KIT_ROOT / "output" / "revalidations" / run_id
    report = {
        "schema_version": "1.0", "kind": "finding-revalidation", "run_id": run_id,
        "repository_external_id": repository_id,
        "baseline": {"run_id": review["run"]["run_id"], "review_digest": digest(review),
                     "finding_id": finding["id"], "fingerprint": finding["fingerprint"]},
        "source": current, "not_revalidated": sorted(f["fingerprint"] for f in findings if f is not finding),
    }
    request = {"repository": str(root), "baseline_path": str(path), "baseline_sha256": checksum,
               "finding": finding, "report": report}
    directory.mkdir(parents=True, exist_ok=False)
    write_new_json(directory / "request.json", request)
    return {"status": "needs_verification", "request": str(directory / "request.json"),
            "verification": str(directory / "verification.json"), "finding_id": finding["id"], "run_id": run_id}


def build(args):
    request_path = Path(args.request).resolve()
    request, _ = read_json(request_path)
    review, checksum, _ = load_review(Path(request["baseline_path"]))
    report = request["report"]
    if checksum != request["baseline_sha256"] or digest(review) != report["baseline"]["review_digest"]:
        raise TargetedError("Baseline changed since preparation. Do not rewrite historical inputs.")
    findings = [f for group in ("findings", "inconclusive_findings", "rejected_findings") for f in review[group]]
    selected = [f for f in findings if f["fingerprint"] == report["baseline"]["fingerprint"]]
    if (len(selected) != 1 or selected[0] != request["finding"]
            or selected[0]["id"] != report["baseline"]["finding_id"]
            or review["run"]["run_id"] != report["baseline"]["run_id"]
            or report["not_revalidated"] != sorted(f["fingerprint"] for f in findings if f != selected[0])):
        raise TargetedError("Prepared selection no longer matches the immutable baseline. Prepare a new targeted request.")
    if source(Path(request["repository"])) != report["source"]:
        raise TargetedError("Source changed during verification. Prepare a new targeted run and recheck the current source.")
    verification, _ = read_json(Path(args.verification) if args.verification else request_path.parent / "verification.json")
    report = {**report, "result": verification}
    validate_targeted(report)
    output = request_path.parent / "revalidation-envelope.json"
    if output.exists():
        previous, _ = read_json(output)
        if previous != report:
            raise TargetedError("Targeted result already exists with different content; never overwrite it.")
    else:
        write_new_json(output, report)
    return {"status": "verified", "outcome": verification["status"], "envelope": str(output),
            "run_id": report["run_id"], "finding_id": report["baseline"]["finding_id"],
            "not_revalidated_count": len(report["not_revalidated"]),
            "message": "Single-finding result only. Project scores and all other findings are unchanged; nothing uploaded."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    preparation = commands.add_parser("prepare")
    preparation.add_argument("--repository", default=".")
    preparation.add_argument("--baseline", required=True)
    preparation.add_argument("--finding", required=True)
    preparation.add_argument("--repository-id")
    preparation.add_argument("--allow-dirty", action="store_true")
    building = commands.add_parser("build")
    building.add_argument("--request", required=True)
    building.add_argument("--verification")
    args = parser.parse_args(argv)
    try:
        result = prepare(args) if args.action == "prepare" else build(args)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, ImportError) as exc:
        message = str(exc) if isinstance(exc, (TargetedError, ExportError)) else "Targeted revalidation blocked; check setup, inputs and file permissions."
        print(json.dumps({"status": "blocked", "message": message}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
