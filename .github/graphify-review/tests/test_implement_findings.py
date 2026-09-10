import argparse
import io
import json
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "scripts"))
import implement_findings as work  # noqa: E402
from publish_review import PublishError  # noqa: E402
from revalidate_finding import source  # noqa: E402
from targeted_contract import digest  # noqa: E402
from git_host_contract import HostError  # noqa: E402


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


class ImplementationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.temp = Path(temporary.name).resolve()
        self.root = self.temp / "repo"
        self.root.mkdir()
        self.remote = self.temp / "remote.git"
        self.remote.mkdir()
        git(self.remote, "init", "--bare")
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.invalid")
        (self.root / "app.py").write_text("safe = False\n")
        git(self.root, "add", "app.py")
        git(self.root, "commit", "-m", "Baseline")
        git(self.root, "remote", "add", "origin", str(self.remote))
        git(self.root, "push", "-u", "origin", "main")
        self.original_commit = git(self.root, "rev-parse", "HEAD")
        self.baseline = json.loads((KIT / "examples/review.example.json").read_text())
        finding = self.baseline["findings"][0]
        self.item = {"finding_id": str(uuid.uuid4()), "display_id": finding["id"], "fingerprint": finding["fingerprint"],
                     "repository_external_id": "repo:orders", "clone_url": "", "lifecycle": "open", "to_implement": True,
                     "baseline_observation_id": str(uuid.uuid4()), "baseline_import_id": str(uuid.uuid4()), "revision": 1, "remediation": "Correct app.py and verify."}
        self.attempt = str(uuid.uuid4())
        self.pr_calls = 0
        self.completions = []
        self.fail_sync = False
        original_run = work.run
        def fake_run(root, *command):
            if command[:3] == ("git", "remote", "get-url"):
                return "git@github.example.invalid:team/orders.git"
            if command[:3] == ("gh", "auth", "status"):
                return ""
            if command[:3] == ("gh", "pr", "list"):
                return "[]"
            if command[:3] == ("gh", "pr", "create"):
                self.pr_calls += 1
                self.assertIn("--base", command)
                self.assertEqual(command[command.index("--base") + 1], "main")
                return "https://github.example.invalid/team/orders/pull/1"
            if command[:3] == ("gh", "pr", "view"):
                return json.dumps({"url": "https://github.example.invalid/team/orders/pull/1", "state": "OPEN", "baseRefName": "main", "headRefName": "feature/" + finding["id"], "headRefOid": git(root, "rev-parse", "HEAD")})
            self.assertNotEqual(command[0], "gh", "Tests must not call a real GitHub service")
            return original_run(root, *command)
        def fake_api(state, data):
            if data["action"] == "claim":
                return {**self.item, "attempt_id": self.attempt}
            self.completions.append(data)
            if self.fail_sync:
                raise PublishError("Uncertain response")
            return {"status": data["completion"]["outcome"], "result": {"lifecycle": "resolved" if data["completion"]["outcome"] == "succeeded" else "open"}}
        patches = [mock.patch.object(work, "KIT_ROOT", self.temp / "kit"),
                   mock.patch.object(work, "configured_hub", return_value="https://hub.example.invalid"),
                   mock.patch.object(work, "context", return_value=self.item),
                   mock.patch.object(work, "request_json", return_value={"payload": {"review": self.baseline}}),
                   mock.patch.object(work, "process_environment", return_value={}),
                   mock.patch.object(work, "run", side_effect=fake_run), mock.patch.object(work, "api", side_effect=fake_api)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def plan(self):
        result = work.plan(argparse.Namespace(repository=str(self.root), finding=self.item["finding_id"], remote="origin", base_branch=None, base_commit=None))
        path = Path(result["state"])
        return path, json.loads(path.read_text())

    def report(self, path, state):
        finding = self.baseline["findings"][0]
        report = {"schema_version": "1.0", "kind": "finding-revalidation", "run_id": str(uuid.uuid4()), "repository_external_id": "repo:orders",
                  "baseline": {"run_id": self.baseline["run"]["run_id"], "review_digest": digest(self.baseline), "finding_id": finding["id"], "fingerprint": finding["fingerprint"]},
                  "source": source(Path(state["worktree"])),
                  "result": {"status": "resolved", "rationale": "Corrected the static setting.", "reviewer": "independent-test-verifier",
                             "evidence": [{"path": "app.py", "fact": "safe is enabled"}], "checks": [{"command": "Inspect app.py", "status": "passed", "summary": "Confirmed effective configuration."}]},
                  "not_revalidated": sorted(f["fingerprint"] for group in ("findings", "inconclusive_findings", "rejected_findings") for f in self.baseline[group] if f["fingerprint"] != finding["fingerprint"])}
        target = path.parent / (str(uuid.uuid4()) + ".json")
        target.write_text(json.dumps(report))
        return str(target)

    def committed(self):
        path, state = self.plan()
        work.start(path, state)
        tree = Path(state["worktree"])
        (tree / "app.py").write_text("safe = True\n")
        provisional = self.report(path, state)
        work.commit(path, state, argparse.Namespace(report=provisional, paths=["app.py"]))
        report = self.report(path, state)
        summary = path.parent / "summary.txt"
        summary.write_text("Corrected the effective static setting and checked the targeted configuration.")
        return path, state, argparse.Namespace(report=report, summary_file=str(summary))

    def test_success_creates_remote_branch_and_pr_without_touching_original_checkout(self):
        path, state, args = self.committed()
        self.assertEqual(state["branch"], "feature/" + self.item["display_id"])
        result = work.deliver(path, state, args)
        self.assertEqual(result["stage"], "completed")
        self.assertEqual(self.pr_calls, 1)
        self.assertEqual(git(self.root, "branch", "--show-current"), "main")
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), self.original_commit)
        self.assertEqual((self.root / "app.py").read_text(), "safe = False\n")
        self.assertEqual(work.remote_sha(self.root, "origin", state["branch"]), state["commit_sha"])
        self.assertEqual(self.completions[0]["completion"]["outcome"], "succeeded")

    def test_existing_branch_and_dirty_checkout_are_never_overwritten(self):
        (self.root / "unrelated.txt").write_text("User changes")
        with self.assertRaisesRegex(work.WorkError, "clean checkout"):
            self.plan()
        (self.root / "unrelated.txt").unlink()
        path, state = self.plan()
        git(self.root, "branch", state["branch"])
        with self.assertRaisesRegex(work.WorkError, "already exists"):
            work.start(path, state)
        self.assertEqual(git(self.root, "rev-parse", state["branch"]), self.original_commit)

    def test_remote_branch_exists_before_edits_and_staging_is_explicit(self):
        path, state = self.plan()
        work.start(path, state)
        self.assertEqual(work.remote_sha(self.root, "origin", state["branch"]), self.original_commit)
        tree = Path(state["worktree"])
        (tree / "app.py").write_text("safe = True\n")
        (tree / "other.py").write_text("unexpected = True\n")
        with self.assertRaisesRegex(work.WorkError, "explicit commit list"):
            work.commit(path, state, argparse.Namespace(report=self.report(path, state), paths=["app.py"]))
        self.assertEqual(git(tree, "rev-parse", "HEAD"), self.original_commit)

    def test_uncertain_hub_completion_retries_same_body_without_duplicate_pr(self):
        path, state, args = self.committed()
        self.fail_sync = True
        with self.assertRaises(PublishError):
            work.deliver(path, state, args)
        self.assertEqual(state["stage"], "completion_pending")
        original = (path.parent / "completion.json").read_bytes()
        self.fail_sync = False
        work.sync(path, state)
        self.assertEqual(self.pr_calls, 1)
        self.assertEqual(self.completions[0], self.completions[1])
        self.assertEqual((path.parent / "completion.json").read_bytes(), original)

    def test_existing_worker_lock_is_not_removed_by_a_second_process(self):
        path, _ = self.plan()
        lock = path.parent / ".worker.lock"
        lock.write_text("Owned by another process")
        with mock.patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(work.main(["start", "--state", str(path), "--repository", str(self.root)]), 2)
        self.assertEqual(lock.read_text(), "Owned by another process")

    def test_remote_parser_rejects_credentials_and_other_transports(self):
        self.assertEqual(work.github_repository("https://github.example.invalid/team/orders.git"), "github.example.invalid/team/orders")
        for remote in ("https://secret@github.com/team/repo", "file:///tmp/repo", "http://github.com/a/b", "https://github.com/a/b?token=secret"):
            with self.assertRaises(work.WorkError):
                work.github_repository(remote)

    def test_failed_verification_prevents_commit_and_failure_posts_reason_without_pr(self):
        path, state = self.plan()
        work.start(path, state)
        tree = Path(state["worktree"])
        (tree / "app.py").write_text("safe = True\n")
        report_path = Path(self.report(path, state))
        report = json.loads(report_path.read_text())
        report["result"]["status"] = "still_present"
        report_path.write_text(json.dumps(report))
        with self.assertRaises(work.WorkError):
            work.commit(path, state, argparse.Namespace(report=str(report_path), paths=["app.py"]))
        self.assertEqual(git(tree, "rev-parse", "HEAD"), self.original_commit)
        reason = path.parent / "failure.txt"
        reason.write_text("The targeted check still finds the defect; changes retained for inspection.")
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(work.main(["fail", "--repository", str(self.root), "--state", str(path), "--summary-file", str(reason)]), 0)
        self.assertEqual(self.pr_calls, 0)
        self.assertEqual(self.completions[0]["completion"]["outcome"], "failed")
        self.assertIn("still finds", self.completions[0]["completion"]["comment"])
        self.assertTrue(tree.exists())

    def test_mismatched_pr_head_blocks_hub_resolution(self):
        path, state, args = self.committed()
        original_run = work.run
        def altered(root, *command):
            result = original_run(root, *command)
            if command[:3] == ("gh", "pr", "view"):
                result = json.dumps({**json.loads(result), "headRefOid": "f" * 40})
            return result
        with mock.patch.object(work, "run", side_effect=altered):
            with self.assertRaisesRegex(HostError, "PR no longer matches"):
                work.deliver(path, state, args)
        self.assertEqual(self.completions, [])
        self.assertFalse((path.parent / "completion.json").exists())

    def test_azure_end_to_end_uses_git_not_gh_and_original_base(self):
        import git_providers
        clone = "https://ado.example.invalid/Collection/Project/_git/orders"
        repo_id = str(uuid.uuid4())
        previous_run = work.run
        requests = []
        def git_only(root, *command):
            self.assertNotEqual(command[0], "gh", "Azure must never require gh")
            if command[:3] == ("git", "remote", "get-url"):
                return clone
            return previous_run(root, *command)
        def azure_api(root, host, method, suffix="", query=None, data=None):
            requests.append((method, suffix, data))
            self.assertEqual(host["api_version"], "6.0")
            if suffix == "":
                return {"id": repo_id, "remoteUrl": clone}
            if method == "GET" and suffix == "/pullrequests":
                return {"count": 0, "value": []}
            if method == "POST":
                self.assertEqual(data["targetRefName"], "refs/heads/main")
                self.assertEqual(data["sourceRefName"], "refs/heads/" + state["branch"])
            return {"pullRequestId": 19, "status": "active", "sourceRefName": "refs/heads/" + state["branch"],
                    "targetRefName": "refs/heads/main", "repository": {"id": repo_id},
                    "lastMergeSourceCommit": {"commitId": state["commit_sha"]}}
        with mock.patch.object(work, "run", side_effect=git_only), mock.patch.object(git_providers, "request", side_effect=azure_api):
            path, state, args = self.committed()
            self.assertEqual(work.remote_sha(self.root, "origin", state["branch"]), self.original_commit)
            result = work.deliver(path, state, args)
        self.assertEqual(result["stage"], "completed")
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), self.original_commit)
        self.assertEqual(git(self.root, "branch", "--show-current"), "main")
        self.assertEqual(self.completions[0]["completion"]["pull_request"], clone + "/pullrequest/19")
        self.assertEqual(sum(method == "POST" for method, _, _ in requests), 1)
        self.assertEqual(self.pr_calls, 0)

    def test_changed_push_destination_blocks_before_branch_creation(self):
        path, state = self.plan()
        previous_run = work.run
        def changed_push(root, *command):
            if "--push" in command:
                return "https://other.example.invalid/org/repo"
            return previous_run(root, *command)
        with mock.patch.object(work, "run", side_effect=changed_push):
            with self.assertRaisesRegex(work.WorkError, "Push destination"):
                work.start(path, state)
        self.assertFalse(Path(state["worktree"]).exists())

    def test_legacy_github_attempt_can_resume(self):
        path, state = self.plan()
        state["github_repository"] = state.pop("hosting")["github_repository"]
        work.save(path, state)
        result = work.start(path, state)
        self.assertEqual(result["stage"], "started")
        self.assertEqual(state["hosting"]["provider"], "github")

    def test_uncertain_azure_pr_cannot_be_replaced_by_failure(self):
        path, state = self.plan()
        work.start(path, state)
        state["pr_submission_started"] = True
        state["stage"] = "pushed"
        work.save(path, state)
        with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(work.main(["fail", "--repository", str(self.root), "--state", str(path), "--summary-file", "unused"]), 2)
        self.assertIn("outcome is uncertain", stderr.getvalue())
        self.assertFalse((path.parent / "completion.json").exists())
        self.assertFalse(self.completions)

    def test_changed_project_credential_selection_blocks_resuming_attempt(self):
        path, state = self.plan()
        changed = {**state["credential_context"], "hub_credential_ref": "another-workspace"}
        with mock.patch.object(work, "credential_context", return_value=changed):
            with self.assertRaisesRegex(work.WorkError, "credential selection changed"):
                work.bound_provider(state)


if __name__ == "__main__":
    unittest.main()
