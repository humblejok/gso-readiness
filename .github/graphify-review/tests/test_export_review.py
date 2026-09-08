from __future__ import annotations

import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
KIT = ROOT / ".github" / "graphify-review"
sys.path.insert(0, str(KIT / "scripts"))

import export_review as exporter  # noqa: E402
from bootstrap_environment import environment_commands, inspect_environment  # noqa: E402
from import_contract import validate_envelope  # noqa: E402


class ExportReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "past run" / "review.json"
        self.path.parent.mkdir()
        self.review = json.loads((KIT / "examples" / "review.example.json").read_text(encoding="utf-8"))
        self.save_review()
        self.output = self.root / "export folder"

    def save_review(self):
        self.path.write_text(json.dumps(self.review, ensure_ascii=False), encoding="utf-8")

    def invoke(self, args):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout, mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            code = exporter.main(args)
        return code, json.loads(stdout.getvalue() or stderr.getvalue())

    def prepare(self, extra=(), directory=False):
        return self.invoke([
            "prepare", "--review", str(self.path.parent if directory else self.path),
            "--repository-id", "repo:orders", "--default-branch", "main",
            "--output-directory", str(self.output), *extra,
        ])

    def plans(self):
        return [{
            "schema_version": "1.0", "finding_fingerprint": f["fingerprint"],
            "generated_from_commit": self.review["review"]["repository"]["commit_sha"],
            "objective": f["recommendation"], "preconditions": [], "constraints": [], "non_goals": [],
            "implementation_steps": [{"order": 1, "instruction": f["recommendation"]}],
            "acceptance_criteria": ["Verify the reported defect no longer occurs at the affected location."],
            "validation_commands": [], "validation_notes": "No archived test command is available; establish a targeted regression check before implementation.",
            "risks_and_rollback": ["Revert only this remediation if existing behavior regresses."],
        } for f in self.review["findings"]]

    def build(self, plans=None):
        if plans is not None:
            (self.output / "remediations.json").write_text(json.dumps(plans), encoding="utf-8")
        return self.invoke(["build", "--request", str(self.output / "export-request.json")])

    def test_export_preserves_review_and_is_accepted_by_hub_contract(self):
        self.review["findings"][0]["description"] += " Stéphane — 漢字"
        self.save_review()
        before = self.path.read_bytes()
        self.assertEqual(self.prepare(directory=True)[0], 0)
        code, result = self.build(self.plans())
        self.assertEqual(code, 0, result)
        envelope, _ = exporter.read_json(Path(result["envelope"]))
        self.assertEqual(envelope["review"], self.review)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(result["idempotency_key"], "repo:orders:example-run")
        validate_envelope(envelope, result["idempotency_key"], ROOT / "hub" / "contracts")

    def test_missing_plans_block_without_envelope(self):
        self.prepare()
        self.assertEqual(self.build()[0], 2)
        self.assertFalse((self.output / "import-envelope.json").exists())

    def test_same_review_and_plans_produce_identical_envelope_bytes(self):
        self.prepare()
        self.assertEqual(self.build(self.plans())[0], 0)
        original = (self.output / "import-envelope.json").read_bytes()
        self.output = self.root / "second export"
        self.prepare()
        self.assertEqual(self.build(self.plans())[0], 0)
        self.assertEqual((self.output / "import-envelope.json").read_bytes(), original)

    def test_step_and_plan_size_limits_block_export(self):
        self.prepare()
        for count, instruction in ((501, "Perform the targeted change."), (8, "x" * 9000)):
            plans = self.plans()
            plans[0]["implementation_steps"] = [
                {"order": index + 1, "instruction": instruction} for index in range(count)
            ]
            self.assertEqual(self.build(plans)[0], 2)
            self.assertFalse((self.output / "import-envelope.json").exists())

    def test_oversize_serialized_output_is_never_created(self):
        target = self.root / "oversize.json"
        with self.assertRaises(exporter.ExportError):
            exporter.write_new_json(target, {"x": "a" * exporter.MAX_BYTES})
        self.assertFalse(target.exists())

    def test_slash_command_is_wired_to_the_export_agent(self):
        prompt = (ROOT / ".github/prompts/export-review.prompt.md").read_text()
        agent = (ROOT / ".github/agents/review-export-manager.agent.md").read_text()
        self.assertIn("agent: Review Export Manager", prompt)
        self.assertIn("name: Review Export Manager", agent)
        self.assertIn("export_review.py prepare", agent)
        self.assertIn("export_review.py build", agent)

    def test_repeated_build_cannot_overwrite(self):
        self.prepare()
        self.assertEqual(self.build(self.plans())[0], 0)
        original = (self.output / "import-envelope.json").read_bytes()
        self.assertEqual(self.build()[0], 2)
        self.assertEqual((self.output / "import-envelope.json").read_bytes(), original)
        self.assertEqual(self.prepare()[0], 2)

    def test_source_review_change_after_preparation_blocks(self):
        self.prepare()
        self.path.write_text(self.path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.assertEqual(self.build(self.plans())[0], 2)
        self.assertFalse((self.output / "import-envelope.json").exists())

    def test_wrong_commit_or_branch_cannot_relabel_review(self):
        for arguments in (("--source-commit", "deadbeef"), ("--source-branch", "other-branch")):
            with self.subTest(arguments=arguments):
                self.assertEqual(self.prepare(arguments)[0], 2)
                self.assertFalse(self.output.exists())

    def test_missing_source_can_be_filled_explicitly_but_not_guessed(self):
        self.review["review"]["repository"].pop("commit_sha")
        self.save_review()
        self.assertEqual(self.prepare()[0], 2)
        code, result = self.prepare(("--source-commit", "deadbeef"))
        self.assertEqual(code, 0, result)

    def test_matching_context_can_supply_original_metadata(self):
        original = self.review["review"]["repository"].copy()
        self.review["review"]["repository"].pop("commit_sha")
        self.save_review()
        (self.path.parent / "run-context.json").write_text(json.dumps({
            "run_id": "example-run", "repository": original,
            "source_snapshot": {"hash": self.review["run"]["source_snapshot_hash"]},
        }), encoding="utf-8")
        self.assertEqual(self.prepare()[0], 0)

    def test_mismatched_context_blocks(self):
        (self.path.parent / "run-context.json").write_text('{"run_id":"another-run"}', encoding="utf-8")
        self.assertEqual(self.prepare()[0], 2)

    def test_dirty_source_warns_without_modifying_review(self):
        self.review["review"]["repository"]["dirty_worktree"] = True
        self.save_review()
        code, result = self.prepare()
        self.assertEqual(code, 0)
        self.assertTrue(any("uncommitted" in warning for warning in result["warnings"]))

    def test_invalid_plans_are_rejected(self):
        self.prepare()
        mutations = [
            lambda plans: plans.clear(),
            lambda plans: plans[0].update(generated_from_commit="deadbeef"),
            lambda plans: plans[0].update(finding_fingerprint="sha256:" + "0" * 64),
            lambda plans: plans[0].update(acceptance_criteria=[]),
            lambda plans: plans[0].pop("validation_notes"),
            lambda plans: plans[0]["implementation_steps"][0].update(order=2),
            lambda plans: plans.append(copy.deepcopy(plans[0])),
            lambda plans: plans[0].update(unexpected=True),
            lambda plans: plans[0].update(objective="-----BEGIN PRIVATE KEY-----"),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                plans = self.plans()
                mutation(plans)
                code, result = self.build(plans)
                self.assertEqual(code, 2, result)
                self.assertNotIn("-----BEGIN PRIVATE KEY-----", json.dumps(result))
                self.assertFalse((self.output / "import-envelope.json").exists())

    def test_zero_supported_findings_auto_creates_empty_plans(self):
        self.review["findings"] = []
        self.save_review()
        self.assertEqual(self.prepare()[0], 0)
        self.assertEqual(self.build()[0], 0)

    def test_invalid_review_and_vulnerability_reports_are_rejected(self):
        for invalid in ({"mode": "check", "items": []}, {**self.review, "schema_version": "2.0"}):
            self.path.write_text(json.dumps(invalid), encoding="utf-8")
            self.assertEqual(self.prepare()[0], 2)
        self.path.write_text("# REVIEW\n", encoding="utf-8")
        self.assertEqual(self.prepare()[0], 2)

    def test_duplicate_keys_nonfinite_and_oversize_inputs_are_rejected(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'x' * (exporter.MAX_BYTES + 1)):
            self.path.write_bytes(raw)
            self.assertEqual(self.prepare()[0], 2)

    def test_bad_fingerprint_is_not_repaired(self):
        self.review["findings"][0]["fingerprint"] = "sha256:" + "0" * 64
        self.save_review()
        original = self.path.read_bytes()
        self.assertEqual(self.prepare()[0], 2)
        self.assertEqual(self.path.read_bytes(), original)

    def test_credentials_in_clone_url_are_rejected_without_echo(self):
        code, result = self.prepare(("--clone-url", "https://token-secret@git.example.invalid/repo"))
        self.assertEqual(code, 2)
        self.assertNotIn("token-secret", json.dumps(result))

    def test_missing_dependencies_do_not_skip_validation(self):
        with mock.patch.dict(sys.modules, {"import_contract": None}):
            code, result = self.prepare()
        self.assertEqual(code, 2)
        self.assertIn("dependencies", result["error"])

    def test_exporter_is_standalone_and_contract_copies_match(self):
        self.assertEqual((KIT / "scripts" / "import_contract.py").read_bytes(), (ROOT / "hub" / "hubapp" / "import_contract.py").read_bytes())
        for schema in ("review.schema.json", "finding.schema.json"):
            self.assertEqual(json.loads((KIT / "schema" / schema).read_text()), json.loads((ROOT / "hub" / "contracts" / schema).read_text()))
        self.assertNotIn("django", (KIT / "scripts" / "import_contract.py").read_text().lower())

    def test_bootstrap_checks_new_dependencies_in_existing_environment(self):
        venv = self.root / ".graphify-review-venv"
        python, graphify = environment_commands(venv)
        python.parent.mkdir(parents=True)
        python.touch()
        graphify.touch()
        (venv / "pyvenv.cfg").touch()
        details = {"python_version": [3, 12, 2], "graphifyy": "0.9.46", "packages": {}}
        with mock.patch("bootstrap_environment.subprocess.run", return_value=mock.Mock(stdout=json.dumps(details))):
            self.assertFalse(inspect_environment(venv, "0.9.46", (3, 10), {"jsonschema": "4.26.0"})["ready"])


if __name__ == "__main__":
    unittest.main()
