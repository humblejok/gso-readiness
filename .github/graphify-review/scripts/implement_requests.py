"""Approved-request adapter for the shared safe Git/PR implementation engine."""

import re
import uuid
from pathlib import Path

import hub_requests
import implement_findings as engine
from export_review import read_json, write_new_json
from handoff_contract import validate_handoff
from request_contract import display_id, specification, validate_request_report
from targeted_contract import digest


def prepare_handoff(filename, current):
    if not filename:
        return None  # Human closure still requires an explicit review.
    if "related_projects" not in current:
        raise engine.WorkError("Update the Hub through migration 0011 before submitting handoff proposals.")
    proposal, _ = read_json(Path(filename))
    try:
        validate_handoff(proposal)
    except ValueError as exc:
        raise engine.WorkError(str(exc)) from exc
    allowed = {
        row["repository_external_id"] for row in current.get("related_projects", [])
    }
    if any(row["repository_external_id"] not in allowed for row in proposal["targets"]):
        raise engine.WorkError(
            "Handoff targets must be currently configured consumer projects."
        )
    if proposal["availability"] != "proposed":
        raise engine.WorkError(
            "An unmerged implementation handoff must use proposed availability. Humans can update it before closing."
        )
    return proposal


def plan(args):
    root = Path(args.repository).resolve()
    if engine.run(root, "git", "status", "--porcelain"):
        raise engine.WorkError(
            "Start from a clean checkout; existing changes will not be stashed or discarded."
        )
    base = engine.run(root, "git", "symbolic-ref", "--quiet", "--short", "HEAD")
    commit = engine.run(root, "git", "rev-parse", "HEAD")
    if (args.base_branch and args.base_branch != base) or (
        args.base_commit and args.base_commit != commit
    ):
        raise engine.WorkError(
            "Original checkout changed during the batch; keep the original base."
        )
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.remote):
        raise engine.WorkError("Invalid remote name.")
    host = engine.describe(root, args.remote, engine.run)
    hub = engine.configured_hub(root)
    identifier = str(uuid.UUID(args.request))
    item = engine.request_json(
        root, "GET", f"/api/v1/requests/{identifier}/implementation", expected_hub=hub
    )
    hub_requests.checked(
        item, hub_requests.project_id(root), [identifier], status="specified"
    )
    frozen = specification(item)
    if item.get("display_id") != display_id(identifier) or item.get(
        "specification_digest"
    ) != digest(frozen):
        raise engine.WorkError(
            "Invalid approved request identity or specification digest."
        )
    if (
        item.get("clone_url")
        and engine.repository_host(item["clone_url"])["clone_url"].casefold()
        != host["clone_url"].casefold()
    ):
        raise engine.WorkError("Checkout remote differs from the Hub request project.")
    directory = (
        engine.KIT_ROOT / "output" / "request-implementations" / str(uuid.uuid4())
    )
    directory.mkdir(parents=True, exist_ok=False)
    write_new_json(directory / "baseline-request.json", frozen)
    state = {
        "schema_version": "1.0",
        "kind": "request",
        "root": str(root),
        "hub_url": hub,
        "item": item,
        "credential_context": engine.credential_context(root),
        "base_branch": base,
        "base_commit": commit,
        "branch": "feature/" + item["display_id"],
        "remote": args.remote,
        "hosting": host,
        "request_id": str(uuid.uuid4()),
        "worktree": str(directory / "worktree"),
        "stage": "planned",
    }
    path = directory / "state.json"
    write_new_json(path, state)
    return {
        "state": str(path),
        "stage": "planned",
        "request_id": identifier,
        "base_branch": base,
        "base_commit": commit,
        "branch": state["branch"],
        "worktree": state["worktree"],
    }


def verify_source(state, report_path, clean=False):
    report, _ = read_json(Path(report_path))
    validate_request_report(report)
    tree = Path(state["worktree"])
    frozen, _ = read_json(tree.parent / "baseline-request.json")
    if (
        digest(frozen) != state["item"]["specification_digest"]
        or specification(state["item"]) != frozen
        or report["baseline"]
        != {
            "request_id": state["item"]["id"],
            "revision": state["item"]["revision"],
            "specification_digest": digest(frozen),
        }
        or report["repository_external_id"] != frozen["repository_external_id"]
        or report["result"]["status"] != "satisfied"
        or engine.source(tree) != report["source"]
        or report["source"]["branch"] != state["branch"]
        or (clean and report["source"]["dirty"])
    ):
        raise engine.WorkError(
            "A passing verification of this exact approved request and current source is required."
        )
    return report


if __name__ == "__main__":
    raise SystemExit(engine.main(kind="request"))
