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
from request_protocol import require_protocol, validate_binding


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
    require_protocol(item)
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
        "binding": {"branch": "feature/" + item["display_id"], "base_branch": base,
                    "commit_sha": "", "pull_request": "", "predecessor_attempt_id": ""},
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


def failed_adoption_history(path, state):
    completion, _ = read_json(path.parent / "completion.json")
    receipt = state.get("hub_result", {})
    if (
        state.get("kind") != "request" or state.get("stage") != "completed"
        or completion.get("outcome") != "failed" or receipt.get("status") != "failed"
        or not state.get("attempt_id")
        or receipt.get("attempt_id") != state["attempt_id"]
        or receipt.get("request_id") != state["item"]["id"]
        or receipt.get("result") != {"status": "specified", "revision": state["item"]["revision"] + 1}
        or not re.fullmatch(r"[a-f0-9]{40,64}", state.get("commit_sha", ""))
        or completion.get("commit_sha") != state["commit_sha"]
        or completion.get("base_branch") != state["base_branch"]
        or completion.get("report") is not None
        or any(state.get(key) or completion.get(key) for key in ("pull_request", "pr_submission_started"))
    ):
        raise engine.WorkError("Adoption requires an acknowledged failed request with its recorded commit and no PR submission or pending completion.")
    frozen, _ = read_json(Path(state.get("baseline_path", str(path.parent / "baseline-request.json"))))
    if specification(state["item"]) != frozen or digest(frozen) != state["item"]["specification_digest"]:
        raise engine.WorkError("The failed attempt's frozen specification changed.")
    return frozen, completion


def adoption_context(state, frozen):
    root = Path(state["root"])
    engine.check_credential_context(state)
    if engine.configured_hub(root) != state["hub_url"]:
        raise engine.WorkError("Hub destination changed since the failed attempt.")
    identifier, attempt = str(uuid.UUID(state["item"]["id"])), str(uuid.UUID(state["attempt_id"]))
    current = copy.deepcopy(engine.request_json(root, "GET",
        f"/api/v1/requests/{identifier}/implementation?attempt_id={attempt}", expected_hub=state["hub_url"]))
    require_protocol(current)
    hub_requests.checked(current, hub_requests.project_id(root), [identifier], status="specified")
    claim = current.pop("claim", None)
    if not isinstance(claim, dict):
        raise engine.WorkError("Update the Hub before adoption: read-only failed-claim status is unavailable.")
    if (claim.get("status") != "failed" or claim.get("owned_by_caller") is not True
            or claim.get("attempt_id") != attempt or claim.get("request_id") != state["request_id"]
            or claim.get("revision") != frozen["revision"]
            or claim.get("specification_digest") != digest(frozen)):
        raise engine.WorkError("Adoption requires the same actor's confirmed failed claim, not an active, expired-only, cancelled or successful claim.")
    approved = specification(current)
    expected = {**frozen, "revision": frozen["revision"] + 1}
    if (approved != expected or current.get("specification_digest") != digest(approved)
            or current.get("clone_url") != state["item"].get("clone_url")
            or current.get("display_id") != state["item"]["display_id"]):
        raise engine.WorkError("Approved scope, project or revision changed beyond the recorded failure; adoption is blocked.")
    return current, approved


def adoption_git_checks(state, predecessor, *, require_no_pr=True):
    engine.retained_worktree_checks(predecessor, predecessor["commit_sha"])
    root, tree = Path(state["root"]), Path(state["worktree"])
    if (state["root"] != predecessor["root"] or state["remote"] != predecessor["remote"]
            or state["base_branch"] != predecessor["base_branch"]
            or state["base_commit"] != predecessor["base_commit"]
            or not state["branch"].startswith("correction/")
            or state["branch"] in {state["base_branch"], predecessor["branch"]}
            or tree.resolve() in {root.resolve(), Path(predecessor["worktree"]).resolve()}
            or not re.fullmatch(r"[a-f0-9]{40,64}", state["commit_sha"])):
        raise engine.WorkError("Supply a separate correction worktree/branch and exact full commit SHA from the saved repository.")
    engine.run(root, "git", "check-ref-format", "refs/heads/" + state["branch"])
    if (Path(engine.run(tree, "git", "rev-parse", "--show-toplevel")).resolve() != tree.resolve()
            or (tree / engine.run(tree, "git", "rev-parse", "--git-common-dir")).resolve()
            != (root / engine.run(root, "git", "rev-parse", "--git-common-dir")).resolve()
            or engine.run(tree, "git", "symbolic-ref", "--quiet", "--short", "HEAD") != state["branch"]
            or engine.run(tree, "git", "rev-parse", "HEAD") != state["commit_sha"]
            or engine.run(tree, "git", "status", "--porcelain")):
        raise engine.WorkError("Correction worktree must be clean at the explicit branch/commit in the original repository.")
    engine.run(tree, "git", "merge-base", "--is-ancestor", predecessor["commit_sha"], state["commit_sha"])
    if (state["commit_sha"] == predecessor["commit_sha"]
            or engine.run(tree, "git", "rev-list", "--min-parents=2", state["base_commit"] + ".." + state["commit_sha"])):
        raise engine.WorkError("Correction must extend the failed commit without merge commits or history rewriting.")
    provider = engine.bound_provider(state)
    provider.preflight()
    if engine.remote_sha(root, state["remote"], state["branch"]) not in {None, state["commit_sha"]}:
        raise engine.WorkError("Correction remote branch differs from the explicit commit; it will not be overwritten.")
    if require_no_pr:
        provider.require_no_pr(state)


def check_adoption(path, state, *, require_no_pr=True):
    link = state["adoption"]
    old_path = Path(link["predecessor"])
    old, _ = read_json(old_path)
    frozen, completion = failed_adoption_history(old_path, old)
    if (link.get("kind") != "corrective-request" or digest(old) != link["predecessor_digest"]
            or digest(completion) != link["completion_digest"] or digest(frozen) != link["baseline_digest"]
            or state["commit_sha"] != link["source"]["commit_sha"]
            or state["branch"] != link["source"]["branch"]
            or str(Path(state["worktree"]).resolve()) != link["worktree"]):
        raise engine.WorkError("Historical inputs or the pinned correction identity changed; adoption cannot be relabelled.")
    approved, _ = read_json(Path(state["baseline_path"]))
    if approved != specification(state["item"]) or digest(approved) != state["item"]["specification_digest"]:
        raise engine.WorkError("The adopted current-revision baseline changed.")
    adoption_git_checks(state, old, require_no_pr=require_no_pr)
    if engine.source(Path(state["worktree"])) != link["source"]:
        raise engine.WorkError("Correction source changed after adoption; preserve it and inspect separately.")
    current, current_frozen = adoption_context(old, frozen)
    if current_frozen != approved:
        raise engine.WorkError("The current approved revision differs from the adopted baseline.")
    return current


def adopt(path, state, args):
    if (not args.request or str(uuid.UUID(args.request)) != state["item"]["id"]
            or not args.worktree or not args.branch or not args.commit_sha
            or (args.remote is not None and args.remote != state["remote"])):
        raise engine.WorkError("Adopt requires the saved request UUID, correction worktree, branch and commit SHA; an explicit remote must match history.")
    tree = str(Path(args.worktree).resolve())
    directory = path.parent / "adoption"
    target = directory / "state.json"
    if target.exists():
        saved, _ = read_json(target)
        if (saved.get("adoption", {}).get("predecessor_digest") != digest(state)
                or (saved["worktree"], saved["branch"], saved["commit_sha"]) != (tree, args.branch, args.commit_sha)):
            raise engine.WorkError("An adoption already exists with different inputs; do not replace its identity.")
        return {"state": str(target), "stage": saved["stage"], "commit_sha": saved["commit_sha"],
                "message": "Use the existing adoption state; do not create another claim or reopen a terminal attempt."}
    frozen, completion = failed_adoption_history(path, state)
    new = {key: copy.deepcopy(state[key]) for key in (
        "schema_version", "kind", "root", "hub_url", "base_branch", "base_commit", "remote")}
    new.update(worktree=tree, branch=args.branch, commit_sha=args.commit_sha,
               hosting=engine.hosting(copy.deepcopy(state)), credential_context=engine.credential_context(Path(state["root"])))
    adoption_git_checks(new, state)
    current, approved = adoption_context(state, frozen)
    new.update(item=current, request_id=str(uuid.uuid4()), stage="planned",
               baseline_path=str(directory / "baseline-request.json"),
               adoption={"kind": "corrective-request", "predecessor": str(path),
                         "predecessor_digest": digest(state), "completion_digest": digest(completion),
                         "baseline_digest": digest(frozen), "worktree": tree,
                         "source": engine.source(Path(tree))})
    new["binding"] = {"branch": args.branch, "base_branch": state["base_branch"],
                      "commit_sha": args.commit_sha, "pull_request": "",
                      "predecessor_attempt_id": state["attempt_id"]}
    temporary = Path(tempfile.mkdtemp(prefix=".adoption-", dir=path.parent))
    write_new_json(temporary / "baseline-request.json", approved)
    write_new_json(temporary / "state.json", new)
    os.rename(temporary, directory)
    return {"state": str(target), "stage": "planned", "worktree": tree, "branch": new["branch"],
            "commit_sha": new["commit_sha"], "revision": current["revision"],
            "message": "Failed history preserved. Start this adoption for a fresh claim, then prepare-verification; no code or remote branch was changed."}


def validate_adoption_claim(state, claimed):
    if (claimed.get("specification_digest") != state["item"]["specification_digest"]
            or specification(claimed) != specification(state["item"])
            or claimed.get("status") != "specified"
            or (state.get("attempt_id") and claimed.get("attempt_id") != state["attempt_id"])
            or claimed.get("implementation_binding") != state.get("binding")):
        raise engine.WorkError("Fresh claim does not match the adopted approved revision or attempt.")
    return str(uuid.UUID(claimed["attempt_id"]))


def start_adoption(path, state):
    if state["stage"] not in {"planned", "committed"}:
        raise engine.WorkError("This corrective adoption has advanced; do not restart it.")
    check_adoption(path, state)
    claimed = engine.api(state, {"action": "claim", "revision": state["item"]["revision"], "request_id": state["request_id"]})
    attempt = validate_adoption_claim(state, claimed)
    old, _ = read_json(Path(state["adoption"]["predecessor"]))
    if attempt == old["attempt_id"]:
        raise engine.WorkError("Adoption requires a fresh claim, not the completed failed claim.")
    state.update(attempt_id=attempt, stage="committed")
    engine.save(path, state)
    return {"state": str(path), "stage": "committed", "worktree": state["worktree"],
            "commit_sha": state["commit_sha"], "baseline": state["baseline_path"],
            "request_id": state["item"]["id"], "revision": state["item"]["revision"],
            "message": "Fresh claim acquired. No commit or push performed; prepare fresh committed verification."}


def push_adoption(state):
    tree = Path(state["worktree"])
    head = engine.remote_sha(tree, state["remote"], state["branch"])
    if head == state["commit_sha"]:
        return
    if head is not None:
        raise engine.WorkError("Correction remote branch changed; no overwrite attempted.")
    ref = "refs/heads/" + state["branch"]
    # An empty lease creates only; the object ID cannot follow a concurrently moved HEAD.
    engine.run(tree, "git", "push", "--force-with-lease=" + ref + ":", state["remote"], state["commit_sha"] + ":" + ref)


def retained_delivery_checks(state):
    """Read-only checks, including the existing PR; never rename, reset or repush here."""
    root, tree = Path(state["root"]), Path(state["worktree"])
    engine.check_credential_context(state)
    if engine.configured_hub(root) != state["hub_url"]:
        raise engine.WorkError("Hub destination changed. Restore the original project configuration.")
    if (not re.fullmatch(r"[a-f0-9]{40,64}", state.get("commit_sha", ""))
            or Path(engine.run(tree, "git", "rev-parse", "--show-toplevel")).resolve() != tree.resolve()
            or (tree / engine.run(tree, "git", "rev-parse", "--git-common-dir")).resolve()
            != (root / engine.run(root, "git", "rev-parse", "--git-common-dir")).resolve()
            or engine.run(tree, "git", "symbolic-ref", "--quiet", "--short", "HEAD") != state["branch"]
            or engine.run(tree, "git", "rev-parse", "HEAD") != state["commit_sha"]
            or engine.run(tree, "git", "status", "--porcelain")):
        raise engine.WorkError("Retained worktree must be clean at its recorded branch/commit in the original repository. Nothing was changed.")
    provider = engine.bound_provider(state)
    provider.preflight()
    head = engine.remote_sha(tree, state["remote"], state["branch"])
    known_pr = state.get("pull_request", "")
    if not known_pr and (state.get("pr_submission_started") or state["stage"] == "pushed"):
        known_pr = provider.find_pr(state)
    if known_pr:
        if head != state["commit_sha"]:
            raise engine.WorkError("Existing PR source branch moved or disappeared; do not overwrite it.")
        provider.verify({**state, "pull_request": known_pr})
    elif state.get("pr_submission_started"):
        raise engine.WorkError("PR creation outcome is uncertain. Reconcile that existing provider submission before resume; do not create another PR.")
    elif head not in {None, state["base_commit"], state["commit_sha"]}:
        raise engine.WorkError("Remote source branch diverged; resume will not overwrite it.")
    elif head is None and not state.get("adoption"):
        raise engine.WorkError("Retained feature branch disappeared. Resume will not recreate it.")
    return known_pr


def resume(path, state, args):
    """One entry point for retained work. History stays immutable in the parent directory."""
    if (getattr(args, "request", None) and str(uuid.UUID(args.request)) != state["item"]["id"]
            or getattr(args, "remote", None) and args.remote != state["remote"]):
        raise engine.WorkError("Resume filter/remote differs from the retained attempt.")
    if state.get("stage") == "completed":
        return {"state": str(path), "stage": "completed", "message": "Already completed; no remote write performed."}
    # A persisted successor makes a lost Hub response safely retryable with the same identity.
    successor_path = path.parent / "resume" / "state.json"
    if successor_path.exists():
        successor, _ = read_json(successor_path)
        if successor.get("resumption", {}).get("predecessor_digest") != digest(state):
            raise engine.WorkError("Historical attempt changed after resume was planned.")
        if successor["stage"] != "planned":
            return {"state": str(successor_path), "stage": successor["stage"],
                    "next_action": "prepare-verification" if successor["stage"] in {"committed", "pr_created", "pushed"} else "resume",
                    "message": "Continue this existing successor. Do not create another claim."}
        return start_successor_locked(successor_path, successor)
    if state.get("resumption") and state["stage"] == "planned":
        return start_resumption(path, state)
    current = copy.deepcopy(engine.request_json(Path(state["root"]), "GET",
        f"/api/v1/requests/{uuid.UUID(state['item']['id'])}/implementation" +
        (f"?attempt_id={uuid.UUID(state['attempt_id'])}" if state.get("attempt_id") else ""), expected_hub=state["hub_url"]))
    require_protocol(current)
    engine.check_credential_context(state)
    if current["status"] in {"implemented", "closed", "cancelled"}:
        claim_info = current.get("claim", {})
        if claim_info.get("status") == "succeeded" and (path.parent / "completion.json").exists():
            body, _ = read_json(path.parent / "completion.json")
            if claim_info.get("owned_by_caller") and claim_info.get("completion_digest") == digest(body):
                return engine.sync(path, state)
        return {"state": str(path), "stage": "human_attention", "hub_status": current["status"],
                "message": "Request was completed/cancelled separately. Preserve this local history; do not sync or reopen it."}
    if specification(current) != specification(state["item"]) or current.get("clone_url") != state["item"].get("clone_url"):
        raise engine.WorkError("Approved scope or project changed; human review is required.")
    claim_info = current.get("claim", {})
    if not state.get("attempt_id"):
        if state["stage"] != "planned" or not state.get("binding"):
            raise engine.WorkError("No resumable claim or branch agreement. Preserve this attempt for review.")
        return engine.start(path, state)
    if not claim_info.get("owned_by_caller"):
        raise engine.WorkError("Resume requires the original claim's token actor.")
    if claim_info.get("status") not in {"running", "expired"}:
        raise engine.WorkError("Claim was cancelled or completed. Resume cannot override that decision.")
    if state["stage"] in {"claimed", "worktree_created", "started"}:
        if claim_info.get("expired") or claim_info.get("binding") != state.get("binding"):
            raise engine.WorkError("Uncommitted work needs human review after claim expiry or a changed agreement; it will not be discarded.")
        return engine.start(path, state)
    existing_pr = retained_delivery_checks(state)
    frozen, _ = read_json(Path(state.get("baseline_path", str(path.parent / "baseline-request.json"))))
    if frozen != specification(current) or digest(frozen) != current["specification_digest"]:
        raise engine.WorkError("Frozen specification changed; resume blocked.")
    pending = path.parent / "completion.json"
    previous_digest = ""
    if pending.exists():
        body, _ = read_json(pending)
        if (body.get("outcome") != "succeeded" or body.get("commit_sha") != state["commit_sha"]
                or body.get("pull_request") != state.get("pull_request", "")
                or body.get("base_branch") != state["base_branch"]):
            raise engine.WorkError("Pending completion is not this retained successful delivery; do not replace it.")
        previous_digest = digest(body)
        # A bound, live and valid completion can be retried unchanged without a new claim.
        if not claim_info.get("expired") and claim_info.get("binding") == state.get("binding") and state.get("binding"):
            from hub_findings import HubRequestError
            try:
                engine.api(state, {"action": "preflight", "attempt_id": state["attempt_id"], "completion": body})
            except HubRequestError as exc:
                if exc.status != 400:
                    raise
            else:
                return engine.sync(path, state)
    binding = {"branch": state["branch"], "base_branch": state["base_branch"],
               "commit_sha": state["commit_sha"], "pull_request": existing_pr,
               "predecessor_attempt_id": (state.get("binding") or {}).get("predecessor_attempt_id", "")}
    if state.get("adoption"):
        old_path = Path(state["adoption"]["predecessor"])
        old, _ = read_json(old_path)
        old_frozen, old_completion = failed_adoption_history(old_path, old)
        if (digest(old) != state["adoption"]["predecessor_digest"]
                or digest(old_frozen) != state["adoption"]["baseline_digest"]
                or digest(old_completion) != state["adoption"]["completion_digest"]
                or engine.source(Path(state["worktree"])) != state["adoption"]["source"]):
            raise engine.WorkError("Adoption provenance or exact source changed.")
        binding["predecessor_attempt_id"] = old["attempt_id"]
    validate_binding(binding, state["item"]["id"])
    new = copy.deepcopy(state)
    for key in ("attempt_id", "hub_result", "verification_runs", "recovery", "required_run_id"):
        new.pop(key, None)
    new.update(stage="planned", item=current, binding=binding, request_id=str(uuid.uuid4()), pull_request=existing_pr,
               baseline_path=str(successor_path.parent / "baseline-request.json"),
               resumption={"predecessor": str(path), "predecessor_digest": digest(state),
                           "attempt_id": state["attempt_id"], "completion_digest": previous_digest})
    preserved_handoff = None
    if pending.exists() and body.get("handoff") is not None:
        preserved_handoff = {key: value for key, value in body["handoff"].items() if key != "implementation"}
        validate_handoff(preserved_handoff)
        new["resume_handoff_path"] = str(successor_path.parent / "handoff-proposal.json")
    temporary = Path(tempfile.mkdtemp(prefix=".resume-", dir=path.parent))
    write_new_json(temporary / "state.json", new)
    write_new_json(temporary / "baseline-request.json", frozen)
    if preserved_handoff is not None:
        write_new_json(temporary / "handoff-proposal.json", preserved_handoff)
    os.rename(temporary, successor_path.parent)
    return start_successor_locked(successor_path, new)


def start_successor_locked(path, state):
    lock = path.parent / ".worker.lock"
    with lock.open("x"):
        pass
    try:
        return start_resumption(path, state)
    finally:
        lock.unlink()


def start_resumption(path, state):
    parent, _ = read_json(Path(state["resumption"]["predecessor"]))
    if digest(parent) != state["resumption"]["predecessor_digest"]:
        raise engine.WorkError("Historical attempt changed; no claim acquired.")
    if state["resumption"]["completion_digest"]:
        previous, _ = read_json(Path(state["resumption"]["predecessor"]).parent / "completion.json")
        if digest(previous) != state["resumption"]["completion_digest"]:
            raise engine.WorkError("Historical completion changed; no claim acquired.")
    retained_delivery_checks(state)
    claimed = engine.api(state, {"action": "resume", "revision": state["item"]["revision"],
        "request_id": state["request_id"], "predecessor_attempt_id": state["resumption"]["attempt_id"],
        "binding": state["binding"], "previous_completion_digest": state["resumption"]["completion_digest"]})
    require_protocol(claimed)
    if (claimed.get("implementation_binding") != state["binding"]
            or claimed["specification_digest"] != state["item"]["specification_digest"]
            or not claimed.get("required_run_id")):
        raise engine.WorkError("Hub did not bind the successor agreement and fresh verification run.")
    state.update(attempt_id=str(uuid.UUID(claimed["attempt_id"])),
                 required_run_id=str(uuid.UUID(claimed["required_run_id"])),
                 stage="pr_created" if state.get("pull_request") else "committed")
    engine.save(path, state)
    return {"state": str(path), "stage": state["stage"], "next_action": "prepare-verification",
            "pull_request": state.get("pull_request", ""), "worktree": state["worktree"],
            "message": "Successor claim acquired. History and existing PR preserved. Obtain fresh independent committed verification before deliver; no Git write was performed."}


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
    if state.get("binding"):
        new["binding"] = copy.deepcopy(state["binding"])
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
    state["binding"] = claimed.get("implementation_binding") or state.get("binding")
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
    if state["stage"] not in {"committed", "pr_created", "pushed"}:
        raise engine.WorkError(
            "Request prepare-verification requires a committed recovery attempt."
        )
    if state.get("adoption") and not state.get("resumption"):
        check_adoption(path, state)
    if state.get("resumption"):
        retained_delivery_checks(state)
    result = verify_request.prepare(
        argparse.Namespace(
            repository=state["worktree"],
            baseline=state["baseline_path"],
            allow_dirty=False,
            run_id=state.get("required_run_id"),
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
    if (state.get("recovery") or state.get("adoption") or state.get("resumption")) and report["run_id"] != state.get(
        "verification_runs", {}
    ).get("committed"):
        raise engine.WorkError(
            "Recovery requires fresh committed verification via prepare-verification and the independent Request Implementation Verifier; historical reports cannot be reused."
        )
    if state.get("adoption") and (report["source"] != state["adoption"]["source"]
                                  or report["source"]["commit_sha"] != state["commit_sha"]):
        raise engine.WorkError("Verification must bind the exact adopted correction, not a replacement commit.")
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
