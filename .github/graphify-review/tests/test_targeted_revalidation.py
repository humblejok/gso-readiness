import argparse
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "scripts"))
import revalidate_finding as targeted  # noqa: E402
from targeted_contract import TargetedError, validate_targeted  # noqa: E402


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


class TargetedTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.temp = Path(temporary.name).resolve()
        self.root = self.temp / "repo"
        self.root.mkdir()
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.invalid")
        (self.root / "app.py").write_text("safe = True\n")
        git(self.root, "add", "app.py")
        git(self.root, "commit", "-m", "Baseline")
        self.review = json.loads((KIT / "examples/review.example.json").read_text())
        self.baseline = self.temp / "review.json"
        self.baseline.write_text(json.dumps(self.review))
        patcher = mock.patch.object(targeted, "KIT_ROOT", self.temp / "kit")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.args = argparse.Namespace(repository=str(self.root), baseline=str(self.baseline), finding=self.review["findings"][0]["id"], repository_id="repo:orders", allow_dirty=False)

    def prepared(self):
        result = targeted.prepare(self.args)
        request_path = Path(result["request"])
        verification = {"status": "resolved", "rationale": "The effective configuration is corrected.", "reviewer": "independent-checker",
                        "evidence": [{"path": "app.py", "fact": "safe is enabled."}],
                        "checks": [{"command": "Manual inspection of app.py", "status": "passed", "summary": "Confirmed the exact static setting."}]}
        Path(result["verification"]).write_text(json.dumps(verification))
        return argparse.Namespace(request=str(request_path), verification=None)

    def test_single_result_has_no_grades_and_preserves_every_other_finding(self):
        original = self.baseline.read_bytes()
        args = self.prepared()
        result = targeted.build(args)
        report = json.loads(Path(result["envelope"]).read_text())
        self.assertEqual(report["baseline"]["finding_id"], self.args.finding)
        self.assertEqual(len(report["not_revalidated"]), 3)
        self.assertNotIn("production_readiness", report)
        self.assertNotIn("review", report)
        self.assertEqual(report["source"]["commit_sha"], git(self.root, "rev-parse", "HEAD"))
        self.assertEqual(self.baseline.read_bytes(), original)
        self.assertEqual(targeted.build(args), result)

    def test_dirty_requires_opt_in_and_source_changes_block(self):
        (self.root / "app.py").write_text("safe = False\n")
        with self.assertRaisesRegex(TargetedError, "dirty"):
            targeted.prepare(self.args)
        self.args.allow_dirty = True
        args = self.prepared()
        (self.root / "app.py").write_text("safe = True\n")
        with self.assertRaisesRegex(TargetedError, "Source changed"):
            targeted.build(args)

    def test_snapshot_covers_nonstandard_file_extensions(self):
        args = self.prepared()
        (self.root / "runtime.custom").write_bytes(b"changed configuration")
        with self.assertRaisesRegex(TargetedError, "Source changed"):
            targeted.build(args)

    def test_baseline_change_and_ambiguous_selector_block(self):
        args = self.prepared()
        self.baseline.write_text(self.baseline.read_text() + "\n")
        with self.assertRaisesRegex(TargetedError, "Baseline changed"):
            targeted.build(args)
        self.review["findings"][1]["id"] = self.args.finding
        self.baseline.write_text(json.dumps(self.review))
        with self.assertRaisesRegex(TargetedError, "exactly one"):
            targeted.prepare(self.args)

    def test_failed_or_missing_checks_cannot_resolve(self):
        result = targeted.build(self.prepared())
        report = json.loads(Path(result["envelope"]).read_text())
        for change in (
            lambda d: d["result"]["checks"].clear(),
            lambda d: d["result"]["checks"][0].update(status="unavailable"),
            lambda d: d["result"]["evidence"][0].update(path="../secret"),
            lambda d: d.update(production_readiness={"score": 10}),
        ):
            candidate = copy.deepcopy(report)
            change(candidate)
            with self.assertRaises(TargetedError):
                validate_targeted(candidate)


if __name__ == "__main__":
    unittest.main()
