"""Hosting adapters. Git operations stay in implement_findings; only PR/auth differs."""
import json
import re
import uuid
from pathlib import Path

from azure_devops import AzureError, request
from git_host_contract import HostError, pull_request_url, repository_host, validate_pr_url
from review_settings import load_settings


def describe(root, remote, run):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", remote):
        raise HostError("Invalid remote name.")
    project = load_settings(root)["project"]
    host = repository_host(run(root, "git", "remote", "get-url", remote), project.get("git_provider") or "auto")
    if host["provider"] == "azure-devops":
        host["api_version"] = project.get("azure_api_version") or "6.0"
    return host


def provider_for(root, host, run):
    if host["provider"] == "github":
        return GitHub(root, host, run)
    if host["provider"] == "azure-devops":
        return AzureDevOps(root, host, run)
    raise HostError("Unsupported Git hosting provider.")


class GitHub:
    def __init__(self, root, host, run):
        self.root, self.host, self.run = Path(root), host, run

    def preflight(self):
        self.run(self.root, "gh", "auth", "status", "--hostname", self.host["github_repository"].split("/", 1)[0])

    def ensure_pr(self, state, path, text, checkpoint):
        tree, repo = Path(state["worktree"]), self.host["github_repository"]
        matches = json.loads(self.run(tree, "gh", "pr", "list", "--repo", repo, "--head", state["branch"], "--base", state["base_branch"], "--state", "all", "--json", "url,state,headRefOid"))
        if matches:
            if len(matches) != 1 or matches[0]["state"] != "OPEN" or matches[0]["headRefOid"] != state["commit_sha"]:
                raise HostError("An incompatible PR already exists. Inspect it; do not create a duplicate.")
            url = matches[0]["url"]
        else:
            body_path = path.parent / "pr-body.txt"
            if not body_path.exists():
                with body_path.open("x", encoding="utf-8") as handle:
                    handle.write(text + "\n\nTargeted revalidation passed for " + state["item"]["display_id"] + ". Other findings and whole-project grades were not reassessed.\n")
            url = self.run(tree, "gh", "pr", "create", "--repo", repo, "--head", state["branch"], "--base", state["base_branch"], "--title", "Implement " + state["item"]["display_id"], "--body-file", str(body_path))
        return validate_pr_url(url, self.host["clone_url"])

    def verify(self, state):
        pr = json.loads(self.run(Path(state["worktree"]), "gh", "pr", "view", state["pull_request"], "--repo", self.host["github_repository"], "--json", "url,state,headRefName,baseRefName,headRefOid"))
        if (pr["state"] != "OPEN" or pr["baseRefName"] != state["base_branch"] or pr["headRefName"] != state["branch"]
                or pr["headRefOid"] != state["commit_sha"] or pr["url"] != state["pull_request"]):
            raise HostError("PR no longer matches the validated source/target. Hub remains unchanged.")


class AzureDevOps:
    def __init__(self, root, host, run):
        self.root, self.host = Path(root), host

    def preflight(self):
        repo = request(self.root, self.host, "GET")
        try:
            identifier = str(uuid.UUID(repo["id"]))
            if repo.get("isDisabled") or repository_host(repo["remoteUrl"])["clone_url"].casefold() != self.host["clone_url"].casefold():
                raise ValueError
            if self.host.get("repository_uuid") and self.host["repository_uuid"] != identifier:
                raise ValueError
        except (KeyError, ValueError, TypeError) as exc:
            raise AzureError("Azure repository identity changed or does not match the selected remote.") from exc
        self.host["repository_uuid"] = identifier

    def validate(self, pr, state):
        try:
            if (pr["status"] != "active" or pr["sourceRefName"] != "refs/heads/" + state["branch"]
                    or pr["targetRefName"] != "refs/heads/" + state["base_branch"]
                    or pr["lastMergeSourceCommit"]["commitId"] != state["commit_sha"]
                    or str(uuid.UUID(pr["repository"]["id"])) != self.host["repository_uuid"]
                    or pr.get("forkSource") or pr.get("autoCompleteSetBy")):
                raise ValueError
            return pull_request_url(self.host["clone_url"], pr["pullRequestId"])
        except (KeyError, ValueError, TypeError) as exc:
            raise AzureError("Azure PR does not match the active validated source/target/repository, or auto-completion is enabled. Hub remains unchanged.") from exc

    def ensure_pr(self, state, path, text, checkpoint):
        # Request two matches: one is reusable, more than one is ambiguous. We
        # include closed PRs so retries cannot silently create duplicates.
        listing = request(self.root, self.host, "GET", "/pullrequests", {
            "searchCriteria.sourceRefName": "refs/heads/" + state["branch"],
            "searchCriteria.targetRefName": "refs/heads/" + state["base_branch"],
            "searchCriteria.status": "all", "$top": 2})
        matches = listing.get("value")
        if not isinstance(matches, list) or type(listing.get("count")) is not int or listing["count"] != len(matches):
            raise AzureError("Invalid Azure PR listing; no PR was created.")
        if matches:
            if len(matches) != 1:
                raise AzureError("Multiple Azure PRs match this branch; inspect them instead of creating a duplicate.")
            return self.validate(matches[0], state)
        if state.get("pr_submission_started"):
            raise AzureError("An earlier Azure PR submission had an uncertain result and no matching PR is visible yet. Inspect the server and retry this saved attempt later; do not create a duplicate.")
        state["pr_submission_started"] = True
        checkpoint(path, state)  # Persist before the non-idempotent REST creation.
        pr = request(self.root, self.host, "POST", "/pullrequests", data={
            "sourceRefName": "refs/heads/" + state["branch"], "targetRefName": "refs/heads/" + state["base_branch"],
            "title": "Implement " + state["item"]["display_id"],
            "description": text + "\n\nTargeted revalidation passed. Other findings and whole-project grades were not reassessed. Not merged or deployed."})
        return self.validate(pr, state)

    def verify(self, state):
        url = validate_pr_url(state["pull_request"], self.host["clone_url"])
        pr = request(self.root, self.host, "GET", "/pullrequests/" + url.rsplit("/", 1)[1])
        if self.validate(pr, state) != url:
            raise AzureError("Azure PR identity changed. Hub remains unchanged.")
