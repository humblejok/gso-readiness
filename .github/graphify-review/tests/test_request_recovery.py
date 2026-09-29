import argparse
import copy
import json
import uuid
from pathlib import Path
from unittest import TestCase, mock

import test_implement_requests as fixture

engine, requests = fixture.engine, fixture.requests


class RequestRecoveryTests(TestCase):
    setUp = fixture.RequestImplementationTests.setUp
    plan = fixture.RequestImplementationTests.plan
    report = fixture.RequestImplementationTests.report
    committed = fixture.RequestImplementationTests.committed

    def test_prompt_dispatches_to_request_recovery_and_fresh_independent_verifier(self):
        github = Path(__file__).resolve().parents[2]
        prompt = (github / "prompts/implement-requests.prompt.md").read_text()
        manager = (
            github / "agents/request-implementation-manager.agent.md"
        ).read_text()
        self.assertIn("retry=<expired-attempt-ID-or-state-path>", prompt)
        for instruction in (
            "mode=readiness",
            "implement_requests.py retry",
            "prepare-verification",
            "Request Implementation Verifier",
            "historical reports cannot be reused",
            "Do not edit source",
            "uncertain claim response",
            "same configured Hub token actor",
        ):
            self.assertIn(instruction, manager)

    def prepare(self, path, state, allow_dirty=True):
        if state.get("recovery"):
            return engine.prepare_verification(path, state)
        return fixture.RequestImplementationTests.prepare(
            self, path, state, allow_dirty
        )

    def expired(self):
        path, state, args = self.committed()
        self.claim_status = {
            "attempt_id": state["attempt_id"],
            "request_id": state["request_id"],
            "revision": state["item"]["revision"],
            "specification_digest": state["item"]["specification_digest"],
            "status": "running",
            "expired": True,
            "owned_by_caller": True,
        }
        engine.request_json.side_effect = lambda *a, **kw: {
            **self.item,
            "claim": copy.deepcopy(self.claim_status),
        }
        self.attempt = str(uuid.uuid4())
        prior = engine.api.side_effect
        engine.api.side_effect = lambda state, data: (
            {**self.item, "attempt_id": self.attempt}
            if data["action"] == "recover"
            else prior(state, data)
        )
        return path, state, args

    def successor(self, path, state):
        result = engine.retry(path, state)
        target = Path(result["state"])
        return target, json.loads(target.read_text())

    def test_recovers_expired_request_and_delivers_existing_commit_with_fresh_evidence(
        self,
    ):
        path, state, args = self.expired()
        original = path.read_bytes()
        baseline = (path.parent / "baseline-request.json").read_bytes()
        sha = state["commit_sha"]
        target, new = self.successor(path, state)
        self.assertNotEqual(new["request_id"], state["request_id"])
        self.assertEqual(engine.retry(path, state)["state"], str(target))
        self.assertEqual(engine.start(target, new)["stage"], "committed")
        self.assertNotEqual(new["attempt_id"], state["attempt_id"])
        with self.assertRaisesRegex(engine.WorkError, "fresh committed verification"):
            engine.deliver(target, new, args)
        args.report = self.report(target, new)
        engine.deliver(target, new, args)
        self.assertEqual(new["stage"], "completed")
        self.assertEqual(new["commit_sha"], sha)
        self.assertEqual(engine.remote_sha(self.root, "origin", new["branch"]), sha)
        self.assertEqual(self.pr_calls, 1)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual((path.parent / "baseline-request.json").read_bytes(), baseline)

    def test_rejects_active_cancelled_completed_foreign_or_mismatched_claim(self):
        path, state, _ = self.expired()
        original = copy.deepcopy(self.claim_status)
        for changes in (
            {"expired": False},
            {"status": "cancelled"},
            {"status": "failed"},
            {"status": "succeeded"},
            {"owned_by_caller": False},
            {"request_id": str(uuid.uuid4())},
            {"revision": 9},
            {"specification_digest": "sha256:" + "a" * 64},
        ):
            self.claim_status = {**original, **changes}
            with (
                self.subTest(changes=changes),
                self.assertRaisesRegex(engine.WorkError, "expired claim"),
            ):
                self.successor(path, state)

    def test_old_hub_without_status_blocks_without_claim(self):
        path, state, _ = self.expired()
        engine.request_json.side_effect = lambda *a, **kw: copy.deepcopy(self.item)
        with self.assertRaisesRegex(engine.WorkError, "Update the Hub"):
            self.successor(path, state)

    def test_changed_specification_blocks_recovery(self):
        path, state, _ = self.expired()
        self.item["analysis"]["specification"] = "Different objective"
        with self.assertRaisesRegex(engine.WorkError, "Approved specification"):
            self.successor(path, state)

    def test_dirty_retained_commit_blocks_without_discarding_edits(self):
        path, state, _ = self.expired()
        file = Path(state["worktree"]) / "app.py"
        file.write_text("Later user changes\n")
        with self.assertRaisesRegex(engine.WorkError, "clean retained worktree"):
            self.successor(path, state)
        self.assertEqual(file.read_text(), "Later user changes\n")

    def test_uncertain_claim_retries_same_successor_identity(self):
        path, state, _ = self.expired()
        target, new = self.successor(path, state)
        previous = engine.api.side_effect
        calls = []

        def uncertain(saved, data):
            calls.append(data)
            if len(calls) == 1:
                raise engine.PublishError("Uncertain response")
            return previous(saved, data)

        with mock.patch.object(engine, "api", side_effect=uncertain):
            with self.assertRaises(engine.PublishError):
                engine.start(target, new)
            new = json.loads(target.read_text())
            engine.start(target, new)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(calls[1]["predecessor_attempt_id"], state["attempt_id"])

    def test_missing_verifier_output_does_not_allow_delivery(self):
        path, state, args = self.expired()
        target, new = self.successor(path, state)
        engine.start(target, new)
        prepared = engine.prepare_verification(target, new)
        with self.assertRaises(FileNotFoundError):
            fixture.verify_request.build(
                argparse.Namespace(request=prepared["request"])
            )
        with self.assertRaisesRegex(engine.WorkError, "fresh committed verification"):
            engine.deliver(target, new, args)
        self.assertEqual(self.pr_calls, 0)

    def test_uncertain_completion_and_pr_submission_are_not_replaced(self):
        path, state, _ = self.expired()
        for changes in (
            {"stage": "completion_pending"},
            {"pr_submission_started": True},
            {"pull_request": "https://example.invalid/pr/1"},
        ):
            with (
                self.subTest(changes=changes),
                self.assertRaisesRegex(engine.WorkError, "no completion or PR"),
            ):
                self.successor(path, {**state, **changes})
