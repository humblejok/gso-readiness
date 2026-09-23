import argparse
import json
import sys
import uuid
from pathlib import Path
from unittest import TestCase, mock

import test_implement_findings as fixtures

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "scripts"))
import hub_requests
import implement_findings as engine
import implement_requests as requests
import verify_request
from request_contract import display_id, specification, validate_request_report
from targeted_contract import TargetedError, digest


class RequestImplementationTests(TestCase):
    def setUp(self):
        fixtures.ImplementationTests.setUp(self)
        identifier = str(uuid.uuid4())
        self.item = {
            "id": identifier,
            "revision": 3,
            "status": "specified",
            "repository_external_id": "repo:orders",
            "kind": "feature",
            "description": "Enable safe mode",
            "clone_url": "",
            "display_id": display_id(identifier),
            "analysis": {
                "project_kind": "backend",
                "specification": "Enable safe mode with regression coverage.",
                "new_interfaces": "None: internal setting only.",
                "changed_interfaces": "None: existing contract preserved.",
                "breaking_changes": "None: compatible behavior.",
            },
        }
        self.item["specification_digest"] = digest(specification(self.item))
        previous = engine.run

        def run(root, *command):
            result = previous(root, *command)
            if command[:3] == ("gh", "pr", "view"):
                result = json.dumps(
                    {
                        **json.loads(result),
                        "headRefName": "feature/" + self.item["display_id"],
                    }
                )
            return result

        for patch in (
            mock.patch.object(
                engine, "request_json", side_effect=lambda *a, **kw: self.item
            ),
            mock.patch.object(hub_requests, "project_id", return_value="repo:orders"),
            mock.patch.object(engine, "run", side_effect=run),
            mock.patch.object(verify_request, "KIT_ROOT", self.temp / "kit"),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def plan(self):
        result = requests.plan(
            argparse.Namespace(
                repository=str(self.root),
                request=self.item["id"],
                remote="origin",
                base_branch=None,
                base_commit=None,
            )
        )
        path = Path(result["state"])
        return path, json.loads(path.read_text())

    def prepare(self, path, state, allow_dirty=True):
        return verify_request.prepare(
            argparse.Namespace(
                repository=state["worktree"],
                baseline=str(path.parent / "baseline-request.json"),
                allow_dirty=allow_dirty,
            )
        )

    def report(self, path, state):
        prepared = self.prepare(path, state)
        verification = {
            "status": "satisfied",
            "rationale": "Safe mode is enabled.",
            "reviewer": "independent-test-verifier",
            "evidence": [{"path": "app.py", "fact": "safe is True"}],
            "checks": [
                {
                    "command": "Inspect app.py",
                    "status": "passed",
                    "summary": "Setting verified.",
                }
            ],
            "acceptance": [
                {
                    "criterion": "Enable safe mode",
                    "status": "passed",
                    "evidence": "app.py sets safe to True.",
                }
            ],
        }
        Path(prepared["verification"]).write_text(
            json.dumps(verification), encoding="utf-8"
        )
        result = verify_request.build(argparse.Namespace(request=prepared["request"]))
        self.assertEqual(result["outcome"], "satisfied")
        self.assertEqual(
            verify_request.build(argparse.Namespace(request=prepared["request"])),
            result,
        )
        return result["envelope"]

    committed = fixtures.ImplementationTests.committed

    def test_azure_request_uses_authenticated_git_and_rest_without_gh(self):
        fixtures.ImplementationTests.test_azure_end_to_end_uses_git_not_gh_and_original_base(
            self
        )

    def test_real_git_worktree_commit_pr_and_idempotent_hub_retry(self):
        fixtures.ImplementationTests.test_uncertain_hub_completion_retries_same_body_without_duplicate_pr(
            self
        )
        self.assertEqual(fixtures.git(self.root, "branch", "--show-current"), "main")
        self.assertEqual(
            fixtures.git(self.root, "rev-parse", "HEAD"), self.original_commit
        )
        self.assertEqual((self.root / "app.py").read_text(), "safe = False\n")
        completion = self.completions[0]["completion"]
        self.assertEqual(completion["report"]["kind"], "request-verification")
        self.assertEqual(
            completion["report"]["baseline"]["specification_digest"],
            self.item["specification_digest"],
        )

    def test_dirty_checkout_existing_branch_and_explicit_paths(self):
        fixtures.ImplementationTests.test_existing_branch_and_dirty_checkout_are_never_overwritten(
            self
        )

    def test_explicit_paths(self):
        fixtures.ImplementationTests.test_remote_branch_exists_before_edits_and_staging_is_explicit(
            self
        )

    def test_source_change_and_provisional_reuse_are_rejected(self):
        path, state = self.plan()
        engine.start(path, state)
        tree = Path(state["worktree"])
        prepared = self.prepare(path, state)
        (tree / "app.py").write_text("safe = True\n")
        with self.assertRaisesRegex(TargetedError, "Source changed"):
            verify_request.build(argparse.Namespace(request=prepared["request"]))
        with self.assertRaises(TargetedError):
            self.prepare(path, state, allow_dirty=False)
        provisional = self.report(path, state)
        engine.commit(
            path, state, argparse.Namespace(report=provisional, paths=["app.py"])
        )
        with self.assertRaises(engine.WorkError):
            requests.verify_source(state, provisional, clean=True)
        requests.verify_source(state, self.report(path, state), clean=True)

    def test_changed_specification_and_missing_verifier_output_block(self):
        path, state = self.plan()
        engine.start(path, state)
        prepared = self.prepare(path, state)
        with self.assertRaises(OSError):
            verify_request.build(argparse.Namespace(request=prepared["request"]))
        Path(prepared["verification"]).write_text(
            '{"status":"ready","agent":"Request Implementation Verifier"}'
        )
        with self.assertRaises(TargetedError):
            verify_request.build(argparse.Namespace(request=prepared["request"]))
        baseline = path.parent / "baseline-request.json"
        changed = json.loads(baseline.read_text())
        changed["analysis"]["specification"] = "An unapproved different design"
        baseline.write_text(json.dumps(changed))
        with self.assertRaisesRegex(TargetedError, "Approved specification changed"):
            verify_request.build(argparse.Namespace(request=prepared["request"]))

    def test_incomplete_acceptance_and_wrong_request_are_rejected(self):
        _path, state, args = self.committed()
        report = json.loads(Path(args.report).read_text())
        report["result"]["acceptance"][0]["status"] = "unavailable"
        with self.assertRaises(TargetedError):
            validate_request_report(report)
        report["result"]["acceptance"][0]["status"] = "passed"
        report["baseline"]["request_id"] = str(uuid.uuid4())
        Path(args.report).write_text(json.dumps(report))
        with self.assertRaises(engine.WorkError):
            requests.verify_source(state, args.report, clean=True)

    def test_specified_queue_filter_and_portable_contract(self):
        with (
            mock.patch.object(
                hub_requests,
                "binding",
                return_value={
                    "hub_url": "https://hub.example.invalid",
                    "repository_external_id": "repo:orders",
                },
            ),
            mock.patch.object(
                hub_requests,
                "request_json",
                return_value={"count": 1, "results": [self.item]},
            ) as api,
        ):
            result = hub_requests.queue(
                self.root, [self.item["id"]], status="specified"
            )
        self.assertEqual(result["items"], [self.item])
        self.assertEqual(result["not_specified_ids"], [])
        self.assertIn("status=specified", api.call_args.args[2])
        self.assertEqual(
            (KIT / "scripts/request_contract.py").read_bytes(),
            (KIT.parents[1] / "hub/hubapp/request_contract.py").read_bytes(),
        )

    def test_agent_graph_is_direct_and_uses_selected_model_and_shared_quality_policy(
        self,
    ):
        manager = (
            KIT.parent / "agents/request-implementation-manager.agent.md"
        ).read_text()
        verifier = (
            KIT.parent / "agents/request-implementation-verifier.agent.md"
        ).read_text()
        prompt = (KIT.parent / "prompts/implement-requests.prompt.md").read_text()
        self.assertIn("agent: Request Implementation Manager", prompt)
        self.assertIn("agents: ['Request Implementation Verifier']", manager)
        self.assertIn("agents: []", verifier)
        self.assertNotIn("model:", manager.split("---")[1])
        for term in (
            "mode=readiness",
            "provisional",
            "committed",
            "Optional Sonar MCP assessment",
            "run_validation.py",
            "--status specified",
            "completion_pending",
        ):
            self.assertIn(term, manager)

    def test_failure_releases_only_request_claim(self):
        import io

        path, state = self.plan()
        engine.start(path, state)
        reason = path.parent / "reason.txt"
        reason.write_text("Required request validation failed.")
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(
                engine.main(
                    [
                        "fail",
                        "--repository",
                        str(self.root),
                        "--state",
                        str(path),
                        "--summary-file",
                        str(reason),
                    ],
                    kind="request",
                ),
                0,
            )
        self.assertEqual(self.completions[-1]["completion"]["outcome"], "failed")
        self.assertIsNone(self.completions[-1]["completion"]["report"])
        self.assertEqual(self.pr_calls, 0)
        with mock.patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(
                engine.main(
                    ["sync", "--repository", str(self.root), "--state", str(path)]
                ),
                2,
            )


class RequestRoutingTests(TestCase):
    def test_request_endpoint_is_project_and_credential_bound(self):
        identifier = str(uuid.uuid4())
        state = {
            "kind": "request",
            "root": "/unused",
            "hub_url": "https://hub.example.invalid",
            "item": {"id": identifier, "repository_external_id": "repo:orders"},
        }
        with (
            mock.patch.object(engine, "check_credential_context") as binding,
            mock.patch.object(engine, "request_json") as api,
        ):
            engine.api(
                state,
                {"action": "claim", "revision": 3, "request_id": str(uuid.uuid4())},
            )
        binding.assert_called_once_with(state)
        self.assertEqual(
            api.call_args.args[2], f"/api/v1/requests/{identifier}/implementation"
        )
        self.assertEqual(api.call_args.args[3]["repository_external_id"], "repo:orders")
        self.assertEqual(api.call_args.args[4], state["hub_url"])
