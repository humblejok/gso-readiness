import argparse
import copy
import json
import unittest
import uuid
from pathlib import Path
from unittest import mock

import test_implement_requests as fixtures
from test_implement_findings import git
import implement_requests as requests
import implement_findings as engine
import verify_request
from request_contract import specification
from request_protocol import PROTOCOL, ProtocolError
from targeted_contract import digest
from publish_review import PublishError


class AdoptionTests(unittest.TestCase):
    setUp = fixtures.RequestImplementationTests.setUp
    plan = fixtures.RequestImplementationTests.plan
    prepare = fixtures.RequestImplementationTests.prepare
    report = fixtures.RequestImplementationTests.report
    committed = fixtures.RequestImplementationTests.committed

    def correction(self):
        path, old, args = self.committed()
        body = {"outcome": "failed", "comment": "Retained correction needed.", "report": None,
                "pull_request": "", "commit_sha": old["commit_sha"], "base_branch": old["base_branch"]}
        engine.store_completion(path, old, body)
        old.update(stage="completed", hub_result={"status": "failed", "attempt_id": old["attempt_id"],
                   "request_id": self.item["id"], "result": {"status": "specified", "revision": self.item["revision"] + 1}})
        engine.save(path, old)
        prior_revision = self.item["revision"]
        self.item["revision"] += 1
        self.item["specification_digest"] = digest(specification(self.item))
        failed_status = {"status": "failed", "owned_by_caller": True, "attempt_id": old["attempt_id"],
                         "request_id": old["request_id"], "revision": prior_revision,
                         "specification_digest": old["item"]["specification_digest"]}
        engine.request_json.side_effect = lambda *a, **kw: {**self.item, "claim": failed_status}
        tree = path.parent / "correction-worktree"
        branch = "correction/REQ-" + self.item["id"]
        git(self.root, "worktree", "add", "-b", branch, str(tree), old["commit_sha"])
        (tree / "app.py").write_text("safe = True\ncorrected = True\n")
        git(tree, "add", "app.py")
        git(tree, "commit", "-m", "Correct retained implementation")
        sha = git(tree, "rev-parse", "HEAD")
        arguments = argparse.Namespace(request=self.item["id"], remote="origin", worktree=str(tree), branch=branch, commit_sha=sha)
        result = requests.adopt(path, old, arguments)
        target = Path(result["state"])
        state = json.loads(target.read_text())
        self.attempt = str(uuid.uuid4())
        previous_run = engine.run.side_effect
        def run(root, *command):
            result = previous_run(root, *command)
            if command[:3] == ("gh", "pr", "view"):
                result = json.dumps({**json.loads(result), "headRefName": branch})
            return result
        engine.run.side_effect = run
        return path, old, target, state

    def fresh_report(self, path, state):
        sample = json.loads(Path(self.report(path, state)).read_text())["result"]
        prepared = requests.prepare_verification(path, state)
        Path(prepared["verification"]).write_text(json.dumps(sample))
        return verify_request.build(argparse.Namespace(request=prepared["request"]))["envelope"]

    def test_new_registered_adoption_and_preflight_before_remote_writes(self):
        parent, old, path, state = self.correction()
        history = parent.read_bytes()
        requests.start_adoption(path, state)
        report = self.fresh_report(path, state)
        summary = path.parent / "summary.txt"
        summary.write_text("Implemented the approved correction and verified it.")
        calls = []
        previous = engine.api.side_effect
        def api(current, data):
            calls.append(data["action"])
            if data["action"] == "preflight":
                self.assertEqual(self.pr_calls, 0)
                self.assertIsNone(engine.remote_sha(self.root, "origin", state["branch"]))
            return previous(current, data)
        engine.api.side_effect = api
        engine.deliver(path, state, argparse.Namespace(report=report, summary_file=str(summary)))
        self.assertEqual(state["stage"], "completed")
        self.assertEqual(self.pr_calls, 1)
        self.assertEqual(calls, ["claim", "preflight", "complete"])
        self.assertEqual(parent.read_bytes(), history)
        self.assertEqual(self.completions[-1]["completion"]["report"]["source"]["branch"], state["branch"])

    def test_legacy_pending_adoption_resume_keeps_pr_and_history_and_is_idempotent(self):
        parent, old, path, state = self.correction()
        requests.start_adoption(path, state)
        report = self.fresh_report(path, state)
        summary = path.parent / "summary.txt"
        summary.write_text("Verified correction.")
        self.fail_sync = True
        with self.assertRaises(PublishError):
            engine.deliver(path, state, argparse.Namespace(report=report, summary_file=str(summary)))
        self.assertEqual(self.pr_calls, 1)
        # Model the supplied legacy helper: local binding was never registered on Hub.
        state.pop("binding")
        engine.save(path, state)
        history = path.read_bytes()
        completion = (path.parent / "completion.json").read_bytes()
        frozen = (path.parent / "baseline-request.json").read_bytes()
        current_claim = {"attempt_id": state["attempt_id"], "request_id": state["request_id"],
                         "owned_by_caller": True, "expired": True, "status": "running", "binding": None}
        engine.request_json.side_effect = lambda *a, **kw: {**self.item, "claim": current_claim}
        required_run = str(uuid.uuid4())
        successor_id = str(uuid.uuid4())
        previous = engine.api.side_effect
        resume_calls = []
        def api(current, data):
            if data["action"] == "resume":
                resume_calls.append(copy.deepcopy(data))
                return {**self.item, "attempt_id": successor_id, "implementation_binding": current["binding"], "required_run_id": required_run}
            return previous(current, data)
        engine.api.side_effect = api
        args = argparse.Namespace(request=None, remote=None)
        result = requests.resume(path, state, args)
        target = Path(result["state"])
        successor = json.loads(target.read_text())
        self.assertEqual(successor["stage"], "pr_created")
        self.assertEqual(requests.resume(path, state, args)["state"], str(target))
        self.assertEqual(len(resume_calls), 1)
        self.assertEqual(path.read_bytes(), history)
        self.assertEqual((path.parent / "completion.json").read_bytes(), completion)
        self.assertEqual((path.parent / "baseline-request.json").read_bytes(), frozen)
        with self.assertRaises(engine.WorkError):
            requests.verify_source(successor, report, clean=True)
        new_report = self.fresh_report(target, successor)
        self.assertEqual(json.loads(Path(new_report).read_text())["run_id"], required_run)
        self.fail_sync = False
        self.attempt = successor_id
        engine.deliver(target, successor, argparse.Namespace(report=new_report, summary_file=str(summary)))
        self.assertEqual(successor["stage"], "completed")
        self.assertEqual(self.pr_calls, 1)
        self.assertEqual(successor["commit_sha"], state["commit_sha"])
        self.assertEqual(successor["pull_request"], state["pull_request"])
        self.assertEqual(path.read_bytes(), history)

    def test_missing_protocol_stops_before_claim_or_remote_write(self):
        self.item.pop("implementation_protocol")
        with self.assertRaises(ProtocolError):
            self.plan()
        engine.api.assert_not_called()
        self.assertEqual(git(self.root, "branch", "--list").strip(), "* main")

    def test_protocol_contract_is_identical_in_hub_and_kit(self):
        root = Path(__file__).resolve().parents[3]
        self.assertEqual((root / ".github/graphify-review/scripts/request_protocol.py").read_bytes(),
                         (root / "hub/hubapp/request_protocol.py").read_bytes())
