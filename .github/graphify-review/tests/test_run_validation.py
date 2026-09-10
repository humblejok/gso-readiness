import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_validation as runner


class ValidationRunnerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        self.root = self.base / "checkout with spaces"
        self.root.mkdir()
        self.output = self.base / "evidence"
        self.source = {
            "branch": "feature/ARCH-C001",
            "commit_sha": "a" * 40,
            "snapshot_hash": "b" * 64,
            "dirty": True,
        }
        for patch in (
            mock.patch.object(runner, "source", return_value=self.source),
            mock.patch.object(
                runner, "process_environment", return_value=dict(os.environ)
            ),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def execute(self, code, **kwargs):
        return runner.execute(
            self.root, [sys.executable, "-c", code], output_root=self.output, **kwargs
        )

    def test_success_with_stderr_and_non_utf8_is_not_a_build_failure(self):
        result = self.execute(
            "import sys; sys.stdout.buffer.write(b'BUILD SUCCESS\\xff'); sys.stderr.buffer.write(b'warning\\x96')"
        )
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["native_exit_code"], 0)
        self.assertEqual(Path(result["stderr"]).read_bytes(), b"warning\x96")
        self.assertEqual(
            json.loads(Path(result["receipt"]).read_text())["source_before"],
            self.source,
        )

    def test_non_maven_commands_still_use_native_exit(self):
        result = self.execute("import sys; print('BUILD SUCCESS'); sys.exit(1)")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["native_exit_code"], 1)

    def test_maven_success_overrides_exit_one(self):
        result = self.execute(
            "import sys; print('[INFO] BUILD SUCCESS'); sys.exit(1)", policy="maven"
        )
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["native_exit_code"], 1)
        self.assertTrue(result["exit_code_disagrees"])
        self.assertEqual(result["reason"], "maven_build_success")

    def test_cli_returns_success_when_maven_summary_overrides_exit_one(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = runner.main(
                [
                    "--repository",
                    str(self.root),
                    "--output-root",
                    str(self.output),
                    "--result-policy",
                    "maven",
                    "--",
                    sys.executable,
                    "-c",
                    "import sys; print('[INFO] BUILD SUCCESS'); sys.exit(1)",
                ]
            )
        self.assertEqual(code, 0)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["native_exit_code"], 1)
        self.assertEqual(summary["status"], "passed")

    def test_maven_failure_overrides_exit_zero(self):
        result = self.execute("print('[INFO] BUILD FAILURE')", policy="maven")
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["exit_code_disagrees"])

    def test_maven_missing_or_conflicting_summaries_are_unavailable(self):
        for code, reason in (
            ("print('Tests passed')", "maven_summary_missing"),
            ("print('example: BUILD SUCCESS')", "maven_summary_missing"),
            (
                "print('[INFO] BUILD SUCCESS'); print('[INFO] BUILD FAILURE')",
                "maven_conflicting_summaries",
            ),
            (
                "import sys; print('[INFO] BUILD SUCCESS'); print('[INFO] BUILD FAILURE', file=sys.stderr)",
                "maven_conflicting_summaries",
            ),
        ):
            with self.subTest(code=code):
                result = self.execute(code, policy="maven")
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["reason"], reason)

    def test_maven_ansi_and_stderr_summary(self):
        result = self.execute(
            "import sys; sys.stderr.buffer.write(b'noise\\xff\\n\\x1b[34m[INFO]\\x1b[0m BUILD \\x1b[32mSUCCESS\\x1b[0m\\r\\n'); sys.exit(1)",
            policy="maven",
        )
        self.assertEqual(result["status"], "passed")

    def test_maven_success_cannot_override_timeout_output_limit_or_source_change(self):
        result = self.execute(
            "import time; print('[INFO] BUILD SUCCESS', flush=True); time.sleep(10)",
            policy="maven",
            timeout=1,
        )
        self.assertEqual(
            (result["status"], result["reason"]), ("unavailable", "timeout")
        )
        result = self.execute(
            "print('[INFO] BUILD SUCCESS'); print('x'*4096)",
            policy="maven",
            max_output_bytes=1024,
        )
        self.assertEqual(
            (result["status"], result["reason"]), ("unavailable", "output_limit")
        )
        with mock.patch.object(
            runner,
            "source",
            side_effect=[self.source, {**self.source, "snapshot_hash": "changed"}],
        ):
            result = self.execute("print('[INFO] BUILD SUCCESS')", policy="maven")
        self.assertEqual(
            (result["status"], result["reason"]),
            ("unavailable", "source_changed_during_check"),
        )

    def test_maven_detection_is_automatic_for_windows_and_unix_launchers(self):
        for executable in (
            "mvn",
            "mvnw",
            "mvn.cmd",
            r"C:\Maven home\mvn.cmd",
            "./mvnw",
            r".\mvnw.cmd",
        ):
            self.assertEqual(runner.result_policy([executable, "verify"]), "maven")
        self.assertEqual(runner.result_policy(["mvn", "verify"], "native"), "native")
        self.assertEqual(runner.result_policy(["dotnet", "test"]), "native")

    def test_timeout_and_excess_output_are_unavailable(self):
        result = self.execute("import time; time.sleep(10)", timeout=1)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "timeout")
        result = self.execute("print('x'*4096)", max_output_bytes=1024)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "output_limit")

    def test_source_changes_invalidate_success(self):
        with mock.patch.object(
            runner,
            "source",
            side_effect=[self.source, {**self.source, "snapshot_hash": "changed"}],
        ):
            result = self.execute("pass")
        self.assertEqual(result["native_exit_code"], 0)
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(result["source_unchanged"])

    def test_missing_tool_is_unavailable_not_a_failed_test(self):
        with mock.patch.object(runner.shutil, "which", return_value=None):
            result = runner.execute(
                self.root, ["missing-tool"], output_root=self.output
            )
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["native_exit_code"])

    def test_evidence_outside_checkout_and_unique_per_run(self):
        with self.assertRaises(runner.ValidationRunError):
            runner.execute(self.root, [sys.executable], output_root=self.root / "logs")
        self.assertNotEqual(
            self.execute("pass")["receipt"], self.execute("pass")["receipt"]
        )

    def test_batch_quoting_uses_raw_cmd_line_and_rejects_expansion(self):
        fake = str(self.base / "Maven home" / "mvn.cmd")
        with mock.patch.object(runner.shutil, "which", return_value=fake):
            command = runner.native_command(
                ["mvn", "verify", "-Dmessage=hello world"], self.root, {}, windows=True
            )
            self.assertIsInstance(command, str)
            self.assertIn(
                '/d /v:off /s /c ""' + fake + '" "verify" "-Dmessage=hello world""',
                command,
            )
            for unsafe in ("a&whoami", "%SECRET%", "x|echo", "!VAR!", 'quote"'):
                with self.assertRaises(runner.ValidationRunError):
                    runner.native_command(["mvn", unsafe], self.root, {}, windows=True)

    def test_rejected_credentials_are_not_recorded(self):
        for flag in (
            "-Dsonar.token=SENSITIVE_TEST_VALUE",
            "--password=SENSITIVE_TEST_VALUE",
            "/d:sonar.token=SENSITIVE_TEST_VALUE",
        ):
            result = runner.execute(
                self.root, [sys.executable, flag], output_root=self.output
            )
            self.assertEqual(result["status"], "unavailable")
            self.assertNotIn(
                "SENSITIVE_TEST_VALUE", Path(result["receipt"]).read_text()
            )

    @unittest.skipUnless(os.name == "nt", "Requires Windows cmd.exe; run in Windows CI")
    def test_windows_batch_path_and_arguments_with_spaces(self):
        batch = self.base / "launcher with spaces.cmd"
        batch.write_text("@echo off\r\necho %~1\r\nexit /b 0\r\n")
        command = runner.native_command(
            [str(batch), "hello world"], self.root, dict(os.environ)
        )
        result = subprocess.run(
            command, cwd=self.root, capture_output=True, shell=False, check=False
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn(b"hello world", result.stdout)


if __name__ == "__main__":
    unittest.main()
