import argparse
import copy
import io
import json
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import git_providers
import revalidate_finding
import test_implement_findings as fixture
from publish_review import PublishError
from targeted_contract import TargetedError

git, work = fixture.git, fixture.work


class RecoveryTests(unittest.TestCase):
    setUp = fixture.ImplementationTests.setUp
    plan = fixture.ImplementationTests.plan
    report = fixture.ImplementationTests.report

    def failed(self):
        path, state = self.plan()
        work.start(path, state)
        tree = Path(state["worktree"])
        (tree / "app.py").write_text("safe = True\n")
        historical = Path(self.report(path, state))
        body = {"outcome": "failed", "comment": "Optional Sonar was unavailable; report rejected.",
                "report": None, "pull_request": "", "commit_sha": "", "base_branch": "main"}
        work.store_completion(path, state, body)
        work.sync(path, state)
        self.item = copy.deepcopy(self.item)
        self.item["revision"] += 1
        work.context.return_value = self.item
        self.attempt = str(uuid.uuid4())
        return path, state, historical

    def successor(self, path, state):
        result = work.retry(path, state)
        successor = Path(result["state"])
        return successor, json.loads(successor.read_text())

    def fresh_report(self, path, state):
        with mock.patch.object(revalidate_finding, "KIT_ROOT", self.temp / "kit"):
            prepared = work.prepare_verification(path, state)
        # Simulate the independent verifier returning data, then use the real builder.
        specimen = json.loads(Path(self.report(path, state)).read_text())
        specimen["result"]["rationale"] += " Optional Sonar unavailable; full Quality Gate not verified."
        Path(prepared["verification"]).write_text(json.dumps(specimen["result"]))
        built = revalidate_finding.build(argparse.Namespace(request=prepared["request"], verification=None))
        return built["envelope"]

    def test_recovery_preserves_history_code_and_branches_requires_two_fresh_runs(self):
        path, state, historical = self.failed()
        originals = {p: p.read_bytes() for p in (path, historical, path.parent / "completion.json", path.parent / "baseline-review.json")}
        tree = Path(state["worktree"])
        before = (tree / "app.py").read_bytes()
        successor, new = self.successor(path, state)
        self.assertNotEqual(new["request_id"], state["request_id"])
        self.assertEqual(work.retry(path, state)["state"], str(successor))
        self.assertEqual((tree / "app.py").read_bytes(), before)
        work.start(successor, new)
        self.assertNotEqual(new["attempt_id"], state["attempt_id"])
        self.assertEqual(work.remote_sha(self.root, "origin", new["branch"]), self.original_commit)
        with self.assertRaisesRegex(work.WorkError, "fresh verification"):
            work.commit(successor, new, argparse.Namespace(report=str(historical), paths=["app.py"]))
        provisional = self.fresh_report(successor, new)
        # Having prepared a new run does not authorize reusing the old one.
        with self.assertRaisesRegex(work.WorkError, "fresh verification"):
            work.commit(successor, new, argparse.Namespace(report=str(historical), paths=["app.py"]))
        work.commit(successor, new, argparse.Namespace(report=provisional, paths=["app.py"]))
        summary = successor.parent / "summary.txt"
        summary.write_text("Corrected setting. Optional Sonar unavailable; full gate not verified.")
        with self.assertRaisesRegex(work.WorkError, "fresh verification"):
            work.deliver(successor, new, argparse.Namespace(report=provisional, summary_file=str(summary)))
        committed = self.fresh_report(successor, new)
        self.assertNotEqual(new["verification_runs"]["provisional"], new["verification_runs"]["committed"])
        result = work.deliver(successor, new, argparse.Namespace(report=committed, summary_file=str(summary)))
        self.assertEqual(result["stage"], "completed")
        self.assertEqual(self.pr_calls, 1)
        for artifact, data in originals.items():
            self.assertEqual(artifact.read_bytes(), data)
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), self.original_commit)
        self.assertEqual((self.root / "app.py").read_text(), "safe = False\n")
        self.assertIn("Sonar unavailable", self.completions[-1]["completion"]["report"]["result"]["rationale"])

    def test_uncertain_claim_reuses_successor_and_client_identity(self):
        path, state, _ = self.failed()
        successor, new = self.successor(path, state)
        api = work.api.side_effect
        calls = []

        def uncertain(saved, data):
            calls.append(data)
            if len(calls) == 1:
                raise PublishError("Uncertain claim response")
            return api(saved, data)

        with mock.patch.object(work, "api", side_effect=uncertain):
            with self.assertRaises(PublishError):
                work.start(successor, new)
            reread = json.loads(successor.read_text())
            work.start(successor, reread)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(work.retry(path, state)["state"], str(successor))

    def test_scope_revision_queue_changes_block_before_claim(self):
        path, state, _ = self.failed()
        for field, value in (("revision", 9), ("remediation", "Different task"),
                             ("baseline_observation_id", str(uuid.uuid4())), ("to_implement", False),
                             ("lifecycle", "resolved"), ("repository_external_id", "another-project")):
            changed = {**self.item, field: value}
            with self.subTest(field=field), mock.patch.object(work, "context", return_value=changed), self.assertRaisesRegex(work.WorkError, "Hub baseline"):
                work.retry(path, state)
        self.assertFalse((path.parent / "retry").exists())

    def test_committed_pending_uncertain_and_wrong_workflow_attempts_block(self):
        path, state, _ = self.failed()
        for change in ({"stage": "completion_pending"}, {"commit_sha": "a" * 40},
                       {"pr_submission_started": True}, {"pull_request": "https://example.invalid/pr/1"},
                       {"kind": "request"}):
            with self.subTest(change=change), self.assertRaisesRegex(work.WorkError, "confirmed failed"):
                work.retry(path, {**state, **change})

    def test_existing_pr_even_closed_or_other_target_blocks(self):
        path, state, _ = self.failed()
        runner = work.run.side_effect

        def existing(root, *command):
            if command[:3] == ("gh", "pr", "list"):
                self.assertNotIn("--base", command)
                self.assertIn("all", command)
                return '[{"url":"https://github.example.invalid/team/orders/pull/2"}]'
            return runner(root, *command)

        with mock.patch.object(work, "run", side_effect=existing), self.assertRaisesRegex(ValueError, "no existing PR"):
            work.retry(path, state)

    def test_remote_divergence_blocks_without_overwrite(self):
        path, state, _ = self.failed()
        with mock.patch.object(work, "remote_sha", return_value="f" * 40), self.assertRaisesRegex(work.WorkError, "Remote base"):
            work.retry(path, state)
        self.assertEqual(work.remote_sha(self.root, "origin", state["branch"]), self.original_commit)

    def test_dirty_original_and_foreign_worktree_block_without_changing_code(self):
        path, state, _ = self.failed()
        (self.root / "unrelated.txt").write_text("User work")
        with self.assertRaisesRegex(work.WorkError, "Original checkout"):
            work.retry(path, state)
        self.assertEqual((self.root / "unrelated.txt").read_text(), "User work")
        (self.root / "unrelated.txt").unlink()
        with self.assertRaisesRegex(work.WorkError, "Retained worktree identity"):
            work.retry(path, {**state, "worktree": str(self.root)})
        self.assertEqual((Path(state["worktree"]) / "app.py").read_text(), "safe = True\n")

    def test_uncertain_failure_receipt_creates_no_successor(self):
        path, state, _ = self.failed()
        self.fail_sync = True
        with self.assertRaises(PublishError):
            work.retry(path, state)
        self.assertFalse((path.parent / "retry").exists())
        self.assertEqual(json.loads(path.read_text())["stage"], "completed")

    def test_changed_source_invalidates_new_provisional_evidence(self):
        path, state, _ = self.failed()
        successor, new = self.successor(path, state)
        work.start(successor, new)
        report = self.fresh_report(successor, new)
        tree = Path(new["worktree"])
        (tree / "app.py").write_text("safe = False\n")
        with self.assertRaisesRegex(work.WorkError, "current source"):
            work.commit(successor, new, argparse.Namespace(report=report, paths=["app.py"]))
        self.assertEqual(git(tree, "rev-parse", "HEAD"), self.original_commit)

    def test_retry_after_successor_failure_keeps_both_histories(self):
        path, state, _ = self.failed()
        successor, new = self.successor(path, state)
        work.start(successor, new)
        completion = json.loads((path.parent / "completion.json").read_text())
        work.store_completion(successor, new, completion)
        work.sync(successor, new)
        self.item = {**self.item, "revision": self.item["revision"] + 1}
        work.context.return_value = self.item
        self.attempt = str(uuid.uuid4())
        next_path, next_state = self.successor(successor, new)
        work.start(next_path, next_state)
        self.assertEqual(work.retry(path, state)["state"], str(successor))
        self.assertEqual(next_state["worktree"], state["worktree"])
        self.assertEqual(json.loads(path.read_text())["stage"], "completed")
        self.assertEqual(json.loads(successor.read_text())["stage"], "completed")

    def test_unrecorded_local_commit_blocks(self):
        path, state, _ = self.failed()
        tree = Path(state["worktree"])
        git(tree, "add", "app.py")
        git(tree, "commit", "-m", "Unexpected commit")
        with self.assertRaisesRegex(work.WorkError, "Retained worktree identity"):
            work.retry(path, state)

    def test_changed_proposal_after_planning_blocks_start(self):
        path, state, _ = self.failed()
        successor, new = self.successor(path, state)
        self.item["remediation"] = "Changed after planning"
        with self.assertRaisesRegex(work.WorkError, "Hub baseline"):
            work.start(successor, new)
        self.assertEqual(json.loads(successor.read_text())["stage"], "planned")

    def test_original_worker_lock_blocks_retry_without_removing_lock(self):
        path, _, _ = self.failed()
        lock = path.parent / ".worker.lock"
        lock.write_text("Existing worker")
        with mock.patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(work.main(["retry", "--repository", str(self.root), "--state", str(path)]), 2)
        self.assertEqual(lock.read_text(), "Existing worker")

    def test_optional_limitations_are_documented_without_weakening_required_checks(self):
        path, state, _ = self.failed()
        successor, new = self.successor(path, state)
        work.start(successor, new)
        report = Path(self.fresh_report(successor, new))
        body = json.loads(report.read_text())
        body["result"]["checks"].append({"command": "Required Sonar gate", "status": "unavailable", "summary": "Not accessible"})
        report.write_text(json.dumps(body))
        with self.assertRaises(TargetedError):
            work.commit(successor, new, argparse.Namespace(report=str(report), paths=["app.py"]))

    def test_azure_pr_check_is_read_only_and_includes_all_targets_and_states(self):
        provider = git_providers.AzureDevOps(self.root, {}, work.run)
        for response in ({"count": 0, "value": []}, {"count": 1, "value": [{"status": "completed"}]}, {}):
            with mock.patch.object(git_providers, "request", return_value=response) as request:
                if response == {"count": 0, "value": []}:
                    provider.require_no_pr({"branch": "feature/ARCH-C002"})
                else:
                    with self.assertRaises(git_providers.AzureError):
                        provider.require_no_pr({"branch": "feature/ARCH-C002"})
                args = request.call_args.args
                self.assertEqual(args[2], "GET")
                self.assertEqual(args[4]["searchCriteria.status"], "all")
                self.assertNotIn("searchCriteria.targetRefName", args[4])
