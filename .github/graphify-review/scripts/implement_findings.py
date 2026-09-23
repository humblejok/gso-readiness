"""Bounded Git/Hub workflow helpers. The coding agent edits code and verifies evidence separately."""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from azure_devops import AzureError
from export_review import KIT_ROOT, read_json, write_new_json
from git_host_contract import HostError, repository_host
from git_providers import describe, provider_for
from hub_findings import context, request_json
from publish_review import PublishError, configured_hub
from revalidate_finding import source
from review_settings import load_settings, process_environment
from targeted_contract import bounded, digest, validate_targeted


class WorkError(ValueError):
    pass


def run(root, *command):
    try:
        environment = process_environment(root)
        if environment.get("SSL_CERT_FILE") and not environment.get("GIT_SSL_CAINFO"):
            environment["GIT_SSL_CAINFO"] = environment["SSL_CERT_FILE"]
        environment.update(GIT_TERMINAL_PROMPT="0", GH_PROMPT_DISABLED="1")
        result = subprocess.run(command, cwd=root, env=environment, capture_output=True, timeout=60, check=False)
        if result.returncode:
            raise WorkError(f"{command[0]} operation failed. Check local Git/hosting authentication and branch permissions; raw output is suppressed.")
        output = result.stdout.decode("utf-8", errors="strict")
        return output if "-z" in command else output.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkError(f"{command[0]} did not complete. A remote write may have succeeded; inspect the saved state before retrying.") from exc


def github_repository(url):
    # Compatibility for existing callers; new workflow uses describe/provider_for.
    try:
        return repository_host(url, "github")["github_repository"]
    except HostError as exc:
        raise WorkError(str(exc)) from exc


def hosting(state):
    # Resume pre-adapter GitHub states without rewriting their identity.
    if "hosting" not in state:
        state["hosting"] = repository_host("https://" + state["github_repository"])
    return state["hosting"]


def credential_context(root):
    settings = load_settings(root)
    project = settings["project"]
    return {**{key: project.get(key, "") for key in ("repository_id", "hub_credential_ref", "azure_credential_ref")},
            "azure_auth": project.get("azure_auth") or settings["user"].get("azure_auth") or "auto",
            **({"hub_workspace_id": project["hub_workspace_id"]} if project.get("hub_workspace_id") else {})}


def check_credential_context(state):
    if "credential_context" in state and credential_context(Path(state["root"])) != state["credential_context"]:
        raise WorkError("Project identity or credential selection changed during this attempt. Restore the original selection; do not complete a claim with another project's credentials.")


def bound_provider(state):
    check_credential_context(state)
    root, host = Path(state["root"]), hosting(state)
    current = describe(root, state["remote"], run)
    if any(current.get(key) != value for key, value in host.items() if key != "repository_uuid"):
        raise WorkError("Git remote/provider settings changed since this attempt was planned. No remote write was made.")
    # Push URLs can differ from fetch URLs. Never send code to a different host/repo.
    for checkout in {root, Path(state["worktree"])}:
        if not checkout.exists():
            continue
        push_urls = run(checkout, "git", "remote", "get-url", "--push", "--all", state["remote"]).splitlines()
        if len(push_urls) != 1 or repository_host(push_urls[0])["clone_url"].casefold() != host["clone_url"].casefold():
            raise WorkError("Push destination differs from the selected repository or is ambiguous. Check Git remote configuration.")
    return provider_for(root, host, run)


def remote_sha(root, remote, branch):
    raw = run(root, "git", "ls-remote", "--heads", remote, "refs/heads/" + branch)
    if not raw:
        return None
    fields = raw.split()
    if len(fields) != 2 or fields[1] != "refs/heads/" + branch or not re.fullmatch(r"[a-f0-9]{40,64}", fields[0]):
        raise WorkError("Remote returned ambiguous branch metadata.")
    return fields[0]


def save(path, state):
    fd, temporary = tempfile.mkstemp(prefix=".work-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, ensure_ascii=True, allow_nan=False)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def plan(args):
    root = Path(args.repository).resolve()
    if run(root, "git", "status", "--porcelain"):
        raise WorkError("Start from a clean checkout. Existing changes will not be staged, stashed or discarded.")
    base = run(root, "git", "symbolic-ref", "--quiet", "--short", "HEAD")
    commit = run(root, "git", "rev-parse", "HEAD")
    if (args.base_branch and args.base_branch != base) or (args.base_commit and args.base_commit != commit):
        raise WorkError("The original checkout changed during the batch. Stop instead of changing the target branch.")
    remote = args.remote
    if not re.fullmatch(r"[A-Za-z0-9_-]+", remote):
        raise WorkError("Invalid remote name.")
    host = describe(root, remote, run)
    hub = configured_hub(root)
    item = context(root, args.finding, hub)
    short_id = item["display_id"]
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,99}", short_id):
        raise WorkError("Finding ID cannot safely form a feature branch name.")
    if item.get("clone_url") and repository_host(item["clone_url"])["clone_url"].casefold() != host["clone_url"].casefold():
        raise WorkError("This checkout's Git remote differs from the Hub project's clone URL.")
    if item["lifecycle"] != "open" or not item["to_implement"]:
        raise WorkError("Finding is not open and marked to implement.")
    directory = KIT_ROOT / "output" / "implementations" / str(uuid.uuid4())
    directory.mkdir(parents=True, exist_ok=False)
    baseline = request_json(root, "GET", "/api/v1/review-imports/" + str(uuid.UUID(item["baseline_import_id"])), expected_hub=hub)
    write_new_json(directory / "baseline-review.json", baseline["payload"]["review"])
    state = {"schema_version": "1.0", "root": str(root), "hub_url": hub, "item": item,
             "credential_context": credential_context(root),
             "base_branch": base, "base_commit": commit, "branch": "feature/" + short_id,
             "remote": remote, "hosting": host, "request_id": str(uuid.uuid4()),
             "worktree": str(directory / "worktree"), "stage": "planned"}
    path = directory / "state.json"
    write_new_json(path, state)
    return {"state": str(path), "stage": "planned", "finding_id": short_id, "base_branch": base,
            "base_commit": commit, "branch": state["branch"], "worktree": state["worktree"]}


def api(state, data):
    check_credential_context(state)
    if state.get("kind") == "request":
        return request_json(Path(state["root"]), "POST", f"/api/v1/requests/{uuid.UUID(state['item']['id'])}/implementation",
                            {**data, "repository_external_id": state["item"]["repository_external_id"]}, state["hub_url"])
    return request_json(Path(state["root"]), "POST", f"/api/v1/findings/{uuid.UUID(state['item']['finding_id'])}/implementation", data, state["hub_url"])


def start(path, state):
    root, tree = Path(state["root"]), Path(state["worktree"])
    if state["stage"] not in {"planned", "claimed", "worktree_created", "started"}:
        raise WorkError("This attempt has advanced; do not start it again.")
    claimed = api(state, {"action": "claim", "revision": state["item"]["revision"], "request_id": state["request_id"]})
    state["attempt_id"] = claimed["attempt_id"]
    if state["stage"] == "planned":
        state["stage"] = "claimed"
    save(path, state)
    baseline_key = "specification_digest" if state.get("kind") == "request" else "baseline_observation_id"
    if claimed[baseline_key] != state["item"][baseline_key]:
        raise WorkError("Baseline changed before the claim. Do not implement the old proposal.")
    bound_provider(state).preflight()
    save(path, state)
    if remote_sha(root, state["remote"], state["base_branch"]) != state["base_commit"]:
        raise WorkError("Original checkout is not at the remote target branch tip. Update it manually and create a new attempt.")
    remote_head = remote_sha(root, state["remote"], state["branch"])
    if state["stage"] == "claimed":
        if remote_head or run(root, "git", "branch", "--list", state["branch"]):
            raise WorkError("The required feature branch already exists. It will not be reused or overwritten; resolve the collision manually.")
        run(root, "git", "worktree", "add", "-b", state["branch"], str(tree), state["base_commit"])
        state["stage"] = "worktree_created"
        save(path, state)
    if state["stage"] == "worktree_created":
        if remote_head and remote_head != state["base_commit"]:
            raise WorkError("Remote feature branch changed; stop without overwriting it.")
        if not remote_head:
            # Empty lease expectation means CREATE ONLY; it can never replace an existing remote ref.
            run(tree, "git", "push", "--force-with-lease=refs/heads/" + state["branch"] + ":", state["remote"], "HEAD:refs/heads/" + state["branch"])
        state["stage"] = "started"
        save(path, state)
    is_request = state.get("kind") == "request"
    return {"state": str(path), "stage": state["stage"], "worktree": str(tree),
            "baseline": str(path.parent / ("baseline-request.json" if is_request else "baseline-review.json")),
            "display_id": state["item"]["display_id"],
            **({"request_id": state["item"]["id"]} if is_request else {"finding_id": state["item"]["display_id"]}),
            "remediation": claimed["analysis"] if is_request else claimed["remediation"],
            "branch": state["branch"], "base_branch": state["base_branch"]}


def verify_source(state, report_path, clean=False):
    if state.get("kind") == "request":
        from implement_requests import verify_source as verify_request
        return verify_request(state, report_path, clean)
    report, _ = read_json(Path(report_path))
    validate_targeted(report)
    tree = Path(state["worktree"])
    baseline, _ = read_json(Path(state["worktree"]).parent / "baseline-review.json")
    if (report["baseline"]["fingerprint"] != state["item"]["fingerprint"]
            or report["baseline"]["finding_id"] != state["item"]["display_id"]
            or report["baseline"]["review_digest"] != digest(baseline)
            or report["repository_external_id"] != state["item"]["repository_external_id"]
            or report["result"]["status"] != "resolved" or source(tree) != report["source"]
            or report["source"]["branch"] != state["branch"] or (clean and report["source"]["dirty"])):
        raise WorkError("A successful targeted result for this exact finding and current source is required.")
    return report


def commit(path, state, args):
    if state["stage"] != "started" or not args.report or not args.paths:
        raise WorkError("Commit requires a started attempt, a passed provisional targeted result and explicit changed file paths.")
    verify_source(state, args.report)
    tree = Path(state["worktree"])
    if run(tree, "git", "rev-parse", "HEAD") != state["base_commit"]:
        raise WorkError("Unexpected commits exist on the feature branch; do not include them automatically.")
    selected = set(args.paths)
    for name in selected:
        candidate = tree / name
        if name.startswith("-") or Path(name).is_absolute() or ".." in Path(name).parts or not name or any(ord(c) < 32 for c in name):
            raise WorkError("Commit paths must be explicit relative file names.")
        if candidate.is_dir() or candidate.is_symlink() or not candidate.resolve().is_relative_to(tree.resolve()):
            raise WorkError("Directory, symlink or outside-worktree staging is refused.")
    changed = set(filter(None, run(tree, "git", "diff", "--no-renames", "--name-only", "-z", "HEAD").split("\0")))
    changed.update(filter(None, run(tree, "git", "ls-files", "--others", "--exclude-standard", "-z").split("\0")))
    if changed != selected:
        raise WorkError("Changed files differ from the explicit commit list. Review all changes; do not stage unrelated files.")
    run(tree, "git", "add", "--", *sorted(selected))
    run(tree, "git", "commit", "-m", "Implement " + state["item"]["display_id"])
    state["commit_sha"] = run(tree, "git", "rev-parse", "HEAD")
    state["stage"] = "committed"
    save(path, state)
    return {"state": str(path), "stage": "committed", "commit_sha": state["commit_sha"],
            "message": "Revalidate this clean committed revision before deliver; no PR or Hub resolution yet."}


def summary(path):
    value = Path(path).read_text(encoding="utf-8").strip()
    if not bounded(value, 8000):
        raise WorkError("Supply a factual summary/failure reason of 1–8,000 characters.")
    return value


def store_completion(path, state, body):
    target = path.parent / "completion.json"
    if target.exists():
        previous, _ = read_json(target)
        if previous != body:
            raise WorkError("Completion request already exists. Retry sync unchanged instead of changing the outcome.")
    else:
        write_new_json(target, body)
    state["stage"] = "completion_pending"
    save(path, state)


def sync(path, state):
    body, _ = read_json(path.parent / "completion.json")
    result = api(state, {"action": "complete", "attempt_id": state["attempt_id"], "completion": body})
    state["stage"] = "completed"
    state["hub_result"] = result
    save(path, state)
    return {"state": str(path), "stage": state["stage"], "hub_result": result}


def deliver(path, state, args):
    if state["stage"] not in {"committed", "pushed", "pr_created"} or not args.report or not args.summary_file:
        raise WorkError("Deliver requires a committed attempt, clean targeted result and factual summary file.")
    report = verify_source(state, args.report, clean=True)
    if report["source"]["commit_sha"] != state["commit_sha"]:
        raise WorkError("Commit changed after revalidation.")
    tree = Path(state["worktree"])
    provider = bound_provider(state)
    provider.preflight()
    save(path, state)
    # Refresh claim before any further remote write; cancellation/edits invalidate it.
    claimed = api(state, {"action": "claim", "revision": state["item"]["revision"], "request_id": state["request_id"]})
    if claimed["attempt_id"] != state["attempt_id"]:
        raise WorkError("Unexpected implementation claim.")
    text = summary(args.summary_file)
    if state["stage"] == "committed":
        if remote_sha(tree, state["remote"], state["branch"]) not in {state["base_commit"], state["commit_sha"]}:
            raise WorkError("Remote feature branch diverged; no overwrite was attempted.")
        run(tree, "git", "push", state["remote"], "HEAD:refs/heads/" + state["branch"])
        state["stage"] = "pushed"
        save(path, state)
    if state["stage"] == "pushed":
        url = provider.ensure_pr(state, path, text, save)
        state["pull_request"] = url
        state["stage"] = "pr_created"
        save(path, state)
    provider.verify(state)
    store_completion(path, state, {"outcome": "succeeded", "comment": text, "report": report,
                                  "pull_request": state["pull_request"], "commit_sha": state["commit_sha"], "base_branch": state["base_branch"]})
    return sync(path, state)


def main(argv=None, *, kind="finding"):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "start", "commit", "deliver", "fail", "sync"))
    parser.add_argument("--repository", default=".")
    if kind == "request":
        parser.add_argument("--request", help="Hub request UUID displayed on its details page")
    else:
        parser.add_argument("--finding", help="Hub finding UUID (resolve the short ID through the queue)")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--base-branch")
    parser.add_argument("--base-commit")
    parser.add_argument("--state")
    parser.add_argument("--report")
    parser.add_argument("--summary-file")
    parser.add_argument("--paths", nargs="+")
    args = parser.parse_args(argv)
    lock = None
    lock_owned = False
    try:
        if args.action == "plan":
            if kind == "request":
                from implement_requests import plan as plan_request
                result = plan_request(args)
            else:
                result = plan(args)
        else:
            path = Path(args.state).resolve()
            lock = path.parent / ".worker.lock"
            with lock.open("x"):
                pass
            lock_owned = True
            state, _ = read_json(path)
            if state.get("kind", "finding") != kind:
                raise WorkError("State belongs to another workflow. Use its original implementation command.")
            if Path(state["root"]) != Path(args.repository).resolve():
                raise WorkError("State belongs to a different checkout. Use its original --repository.")
            if args.action == "start":
                result = start(path, state)
            elif args.action == "commit":
                result = commit(path, state, args)
            elif args.action == "deliver":
                result = deliver(path, state, args)
            elif args.action == "fail":
                if not state.get("attempt_id") or state["stage"] in {"completion_pending", "completed"}:
                    raise WorkError("No active claim, or completion already pending. Use sync for an uncertain completion.")
                if state.get("pr_submission_started") and not state.get("pull_request"):
                    raise WorkError("Azure PR creation outcome is uncertain. Retry deliver with the same state/report/summary or inspect the server; do not replace it with a failure.")
                store_completion(path, state, {"outcome": "failed", "comment": summary(args.summary_file), "report": None,
                                              "pull_request": state.get("pull_request", ""), "commit_sha": state.get("commit_sha", ""), "base_branch": state["base_branch"]})
                result = sync(path, state)
            else:
                result = sync(path, state)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except Exception as exc:  # noqa: BLE001 - Sanitized tool/credential boundary; preserve source and branches.
        message = str(exc) if isinstance(exc, (WorkError, PublishError, HostError, AzureError)) else "Implementation operation blocked. Inspect saved state and inputs; no destructive cleanup was attempted."
        print(json.dumps({"status": "blocked", "state": args.state, "message": message}), file=sys.stderr)
        return 2
    finally:
        if lock_owned and lock is not None and lock.exists():
            lock.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
