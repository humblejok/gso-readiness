"""Approved-request adapter for the shared safe Git/PR implementation engine."""

import argparse
import copy
import os
import re
import tempfile
import uuid
from pathlib import Path

import hub_requests
import implement_findings as engine
import verify_request
from export_review import read_json, write_new_json
from handoff_contract import validate_handoff
from request_contract import display_id, specification, validate_request_report
from targeted_contract import digest


def prepare_handoff(filename, current):
    if not filename:
        return None  # Human closure still requires an explicit review.
    if "related_projects" not in current:
        raise engine.WorkError(
            "Update the Hub through migration 0011 before submitting handoff proposals."
        )
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


def recovery_inputs(path, state):
    if (
        state.get("kind") != "request"
        or state["stage"] != "committed"
        or not re.fullmatch(r"[a-f0-9]{40,64}", state.get("commit_sha", ""))
        or not state.get("attempt_id")
        or state.get("pull_request")
        or state.get("pr_submission_started")
        or (path.parent / "completion.json").exists()
    ):
        raise engine.WorkError(
            "Request recovery requires a committed attempt with no completion or PR submission. Sync uncertain completions instead."
        )
    engine.retained_worktree_checks(state, state["commit_sha"])
    root = Path(state["root"])
    if engine.configured_hub(root) != state["hub_url"]:
        raise engine.WorkError("Hub destination changed since the attempt was created.")
    identifier = str(uuid.UUID(state["item"]["id"]))
    attempt = str(uuid.UUID(state["attempt_id"]))
    current = engine.request_json(
        root,
        "GET",
        f"/api/v1/requests/{identifier}/implementation?attempt_id={attempt}",
        expected_hub=state["hub_url"],
    )
    hub_requests.checked(
        current, hub_requests.project_id(root), [identifier], status="specified"
    )
    frozen, _ = read_json(
        Path(state.get("baseline_path", str(path.parent / "baseline-request.json")))
    )
    if (
        specification(current) != frozen
        or specification(state["item"]) != frozen
        or current.get("specification_digest") != digest(frozen)
        or current.get("clone_url") != state["item"].get("clone_url")
        or current.get("display_id") != state["item"]["display_id"]
    ):
        raise engine.WorkError(
            "Approved specification, revision or project changed; retained implementation cannot be recovered automatically."
        )
    claim = current.pop("claim", None)
    if not isinstance(claim, dict):
        raise engine.WorkError(
            "Update the Hub before request recovery: read-only claim status is unavailable."
        )
    if (
        claim.get("attempt_id") != attempt
        or claim.get("request_id") != state["request_id"]
        or claim.get("revision") != frozen["revision"]
        or claim.get("specification_digest") != digest(frozen)
        or claim.get("expired") is not True
        or claim.get("owned_by_caller") is not True
        or claim.get("status") not in {"running", "expired"}
    ):
        raise engine.WorkError(
            "Recovery requires the same actor's expired claim and original approved specification. Active, cancelled or completed claims cannot be replaced."
        )
    return current, frozen


def retry(path, state):
    directory = path.parent / "retry"
    target = directory / "state.json"
    if target.exists():
        saved, _ = read_json(target)
        if saved.get("recovery", {}).get("predecessor_digest") != digest(state):
            raise engine.WorkError(
                "Historical attempt changed after recovery was planned."
            )
        return {
            "state": str(target),
            "stage": saved["stage"],
            "worktree": saved["worktree"],
            "message": "Use the existing successor; do not create another claim.",
        }
    current, frozen = recovery_inputs(path, state)
    new = {
        key: copy.deepcopy(state[key])
        for key in (
            "schema_version",
            "kind",
            "root",
            "hub_url",
            "base_branch",
            "base_commit",
            "branch",
            "remote",
            "worktree",
            "commit_sha",
        )
    }
    new.update(
        item=current,
        hosting=engine.hosting(copy.deepcopy(state)),
        credential_context=engine.credential_context(Path(state["root"])),
        request_id=str(uuid.uuid4()),
        stage="planned",
        baseline_path=str(directory / "baseline-request.json"),
        recovery={
            "kind": "expired-request",
            "predecessor": str(path),
            "predecessor_digest": digest(state),
        },
    )
    temporary = Path(tempfile.mkdtemp(prefix=".retry-", dir=path.parent))
    write_new_json(temporary / "baseline-request.json", frozen)
    write_new_json(temporary / "state.json", new)
    os.rename(temporary, directory)
    return {
        "state": str(target),
        "stage": "planned",
        "worktree": state["worktree"],
        "commit_sha": state["commit_sha"],
        "message": "History preserved. Start this successor to acquire a new claim, then prepare fresh committed verification.",
    }


def start_recovery(path, state):
    if (
        state["stage"] not in {"planned", "committed"}
        or state["recovery"].get("kind") != "expired-request"
    ):
        raise engine.WorkError("This request recovery has advanced; do not restart it.")
    predecessor = Path(state["recovery"]["predecessor"])
    old, _ = read_json(predecessor)
    if digest(old) != state["recovery"]["predecessor_digest"] or state[
        "commit_sha"
    ] != old.get("commit_sha"):
        raise engine.WorkError("Historical request attempt or retained commit changed.")
    recovery_inputs(predecessor, old)
    claimed = engine.api(
        state,
        {
            "action": "recover",
            "revision": state["item"]["revision"],
            "request_id": state["request_id"],
            "predecessor_attempt_id": old["attempt_id"],
        },
    )
    if claimed["specification_digest"] != state["item"]["specification_digest"] or (
        state.get("attempt_id") and state["attempt_id"] != claimed["attempt_id"]
    ):
        raise engine.WorkError(
            "Successor claim differs from the saved request/specification."
        )
    state.update(attempt_id=claimed["attempt_id"], stage="committed")
    engine.save(path, state)
    return {
        "state": str(path),
        "stage": "committed",
        "worktree": state["worktree"],
        "baseline": state["baseline_path"],
        "commit_sha": state["commit_sha"],
        "request_id": state["item"]["id"],
        "branch": state["branch"],
        "base_branch": state["base_branch"],
    }


def prepare_verification(path, state):
    if state["stage"] != "committed" or not state.get("recovery"):
        raise engine.WorkError(
            "Request prepare-verification requires a committed recovery attempt."
        )
    result = verify_request.prepare(
        argparse.Namespace(
            repository=state["worktree"],
            baseline=state["baseline_path"],
            allow_dirty=False,
        )
    )
    prepared, _ = read_json(Path(result["request"]))
    if prepared["report"]["source"]["commit_sha"] != state["commit_sha"]:
        raise engine.WorkError(
            "Retained commit changed; recovery cannot verify a replacement commit."
        )
    state.setdefault("verification_runs", {})["committed"] = prepared["report"][
        "run_id"
    ]
    engine.save(path, state)
    return {**result, "state": str(path), "phase": "committed"}


def verify_source(state, report_path, clean=False):
    report, _ = read_json(Path(report_path))
    validate_request_report(report)
    tree = Path(state["worktree"])
    frozen, _ = read_json(
        Path(state.get("baseline_path", str(tree.parent / "baseline-request.json")))
    )
    if state.get("recovery") and report["run_id"] != state.get(
        "verification_runs", {}
    ).get("committed"):
        raise engine.WorkError(
            "Recovery requires fresh committed verification via prepare-verification and the independent Request Implementation Verifier; historical reports cannot be reused."
        )
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
