from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / ".github" / "graphify-review" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import run_audits as audits  # noqa: E402
from build_evidence import build  # noqa: E402
from profile_core import resolve_profile  # noqa: E402
from run_audits import (  # noqa: E402
    discover_jfrog_targets,
    extract_jfrog_sca_findings,
    extract_jfrog_secret_findings,
    fallback_result,
    jfrog_failure_hints,
    jfrog_payload_state,
    jfrog_command,
    parse_jfrog_simple_json,
    run_jfrog_sca,
)
from reproducibility import review_input_files  # noqa: E402
from validate_evidence import validate_evidence  # noqa: E402


class JfrogAuditTests(unittest.TestCase):
    def execution(self, exit_code: int = 0, timed_out: bool = False) -> dict:
        return {"stdout": "", "stderr": "", "exit_code": exit_code, "timed_out": timed_out}

    def test_simple_json_parser_tolerates_cli_log_prefix(self) -> None:
        payload = {"scansStatus": {"scaScanStatusCode": 0}, "vulnerabilities": []}
        parsed = parse_jfrog_simple_json("JFrog audit\n" + json.dumps(payload) + "\n")
        self.assertEqual(parsed, payload)

    def test_sca_findings_are_structured_and_deduplicated(self) -> None:
        row = {
            "severity": "High",
            "impactedPackageName": "org.example:library",
            "impactedPackageVersion": "1.2.3",
            "impactedPackageType": "maven",
            "summary": "Example vulnerability",
            "applicable": "Applicable",
            "fixedVersions": ["1.2.4"],
            "cves": [{"id": "CVE-2026-12345", "cvssV3": "8.1"}],
            "issueId": "XRAY-123",
            "impactPaths": [[
                {"name": "application", "version": "1.0"},
                {"name": "org.example:library", "version": "1.2.3"},
            ]],
        }
        violation = {**row, "watch": "production", "policies": ["security"], "fail_build": True}
        findings = extract_jfrog_sca_findings({
            "vulnerabilities": [row], "securityViolations": [violation],
        })
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["advisory_ids"], ["CVE-2026-12345"])
        self.assertEqual(findings[0]["package"], "org.example:library")
        self.assertEqual(findings[0]["result_types"], ["securityViolations", "vulnerabilities"])
        self.assertEqual(findings[0]["source"]["tool"], "jfrog-xray")

    def test_secret_findings_never_persist_secret_content(self) -> None:
        payload = {
            "secrets": [{
                "severity": "High",
                "ruleId": "private-key",
                "file": "src/appsettings.json",
                "startLine": 7,
                "snippet": "DO-NOT-PERSIST-SECRET",
                "finding": "DO-NOT-PERSIST-SECRET",
                "fingerprint": "safe-fingerprint",
            }]
        }
        findings = extract_jfrog_secret_findings(payload, ROOT)
        serialized = json.dumps(findings)
        self.assertEqual(len(findings), 1)
        self.assertNotIn("DO-NOT-PERSIST-SECRET", serialized)
        self.assertNotIn("snippet", findings[0])
        self.assertNotIn("finding", findings[0])
        self.assertEqual(findings[0]["path"], "src/appsettings.json")
        self.assertTrue(findings[0]["active"])

    def test_executed_secret_scan_does_not_retain_raw_cli_output(self) -> None:
        raw_secret = "DO-NOT-PERSIST-EXECUTED-SECRET"
        payload = {
            "scansStatus": {"secretsScanStatusCode": 0},
            "errors": [],
            "secrets": [{
                "severity": "High", "ruleId": "token", "file": "settings.json", "startLine": 2,
                "finding": raw_secret, "snippet": f'"token": "{raw_secret}"',
            }],
        }
        execution = {"stdout": json.dumps(payload), "stderr": "", "exit_code": 0, "timed_out": False}
        with mock.patch.object(audits, "run_command", return_value=execution):
            result = audits.execute_jfrog_target(
                ROOT, {"id": "repository", "directory": ".", "manifests": []}, "secrets", 10, None,
            )
        serialized = json.dumps(result)
        self.assertNotIn(raw_secret, serialized)
        self.assertNotIn("snippet", serialized)
        self.assertNotIn("finding\"", serialized)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["completion"], "complete")

    def test_jfrog_status_distinguishes_findings_from_scanner_failure(self) -> None:
        complete = {"scansStatus": {"scaScanStatusCode": 0}, "errors": []}
        self.assertEqual(
            jfrog_payload_state(complete, "sca", self.execution(), 1)[:2],
            ("failed", "complete"),
        )
        self.assertEqual(
            jfrog_payload_state(complete, "sca", self.execution(), 0)[:2],
            ("passed", "complete"),
        )
        incomplete = {"scansStatus": {}, "errors": []}
        self.assertEqual(
            jfrog_payload_state(incomplete, "secrets", self.execution(), 0)[:2],
            ("unavailable", "not_completed"),
        )
        self.assertEqual(
            jfrog_payload_state(complete, "sca", self.execution(exit_code=2), 0)[:2],
            ("unavailable", "not_completed"),
        )

    def test_sca_command_disables_advanced_contextual_analysis(self) -> None:
        command = jfrog_command("sca", "nuget", "corporate")
        self.assertIn("--sca", command)
        self.assertIn("--without-contextual-analysis", command)
        self.assertIn("--server-id=corporate", command)

    def test_failure_hints_do_not_retain_credentials_or_error_text(self) -> None:
        payload = {"errors": [{"errorMessage": "403 token SECRET-VALUE is not entitled"}]}
        hints = jfrog_failure_hints(payload, {**self.execution(exit_code=1), "stderr": "proxy TLS failure"})
        self.assertEqual(
            hints,
            ["advanced_security_entitlement", "authentication_or_authorization", "network_proxy_or_tls"],
        )
        self.assertNotIn("SECRET-VALUE", json.dumps(hints))

    def test_target_discovery_collapses_modules_and_obeys_profile(self) -> None:
        paths = [
            "pom.xml", "module-a/pom.xml", "services/other/pom.xml",
            "dotnet/App.sln", "dotnet/src/Api/Api.csproj",
        ]
        spring = discover_jfrog_targets(ROOT, paths, "spring-boot")
        dotnet = discover_jfrog_targets(ROOT, paths, "aspnet-core-api")
        self.assertEqual([item["id"] for item in spring], ["maven:."])
        self.assertEqual([item["id"] for item in dotnet], ["nuget:dotnet"])
        self.assertEqual(
            spring[0]["manifests"],
            ["module-a/pom.xml", "pom.xml", "services/other/pom.xml"],
        )

    def test_generic_without_supported_manifests_is_not_applicable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = run_jfrog_sca(Path(directory), ["README.md"], "generic", 1, None)
        self.assertEqual(result["state"], "not_applicable")
        self.assertEqual(result["completion"], "not_applicable")

    def test_generic_package_manifests_are_included_in_review_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in ("requirements-dev.txt", "Pipfile", "go.mod", "Podfile", "gradle.lockfile"):
                (root / relative).write_text("\n", encoding="utf-8")
            paths = [path.relative_to(root).as_posix() for path in review_input_files(root)]
            self.assertEqual(
                paths,
                ["Pipfile", "Podfile", "go.mod", "gradle.lockfile", "requirements-dev.txt"],
            )

    def test_auto_prefers_jfrog_for_dotnet_dependency_and_secret_scans(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Api.sln").write_text("\n", encoding="utf-8")
            (root / "Api.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk.Web"></Project>\n', encoding="utf-8",
            )
            resolved = resolve_profile("asp.net", root)
            sca = self.completed_result("dependency_vulnerability_scan", "jfrog-xray-sca", "jfrog-xray")
            secrets = self.completed_result("secret_scan", "jfrog-xray-secrets", "jfrog-secrets")
            with (
                mock.patch.object(audits.shutil, "which", side_effect=lambda name: "C:/tools/jf.exe" if name == "jf" else None),
                mock.patch.object(audits, "run_jfrog_sca", return_value=sca),
                mock.patch.object(audits, "run_jfrog_secrets", return_value=secrets),
            ):
                results = audits.run_audits(root, resolved, security_scanner="auto")
            ids = {item["audit_id"] for item in results}
            self.assertIn("jfrog-xray-sca", ids)
            self.assertIn("jfrog-xray-secrets", ids)
            self.assertNotIn("dotnet-vulnerable-packages", ids)
            self.assertNotIn("gitleaks-secret-scan", ids)

    def test_explicit_jfrog_does_not_hide_an_unavailable_secrets_entitlement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resolved = resolve_profile("generic", root)
            unavailable = {
                **self.completed_result("secret_scan", "jfrog-xray-secrets", "jfrog-secrets"),
                "state": "unavailable", "completion": "not_completed", "items": [],
            }
            with (
                mock.patch.object(audits.shutil, "which", return_value="C:/tools/jf.exe"),
                mock.patch.object(audits, "run_jfrog_sca", return_value={
                    **self.completed_result("dependency_vulnerability_scan", "jfrog-xray-sca", "jfrog-xray"),
                    "state": "not_applicable", "completion": "not_applicable", "items": [],
                    "targets": [], "covered_targets": [],
                }),
                mock.patch.object(audits, "run_jfrog_secrets", return_value=unavailable),
            ):
                results = audits.run_audits(root, resolved, security_scanner="jfrog")
            secret_result = next(item for item in results if item["kind"] == "secret_scan")
            self.assertEqual(secret_result["audit_id"], "jfrog-xray-secrets")
            self.assertEqual(secret_result["completion"], "not_completed")

    def test_provider_fallback_is_preserved_in_normalized_tool_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary = {
                **self.completed_result("secret_scan", "jfrog-xray-secrets", "jfrog-secrets"),
                "state": "unavailable", "completion": "not_completed", "items": [],
            }
            native = self.completed_result("secret_scan", "gitleaks-secret-scan", "gitleaks")
            evidence = build(
                root, None, [fallback_result(primary, native)], resolve_profile("generic", root),
            )
            self.assertEqual(
                [(item["name"], item["state"]) for item in evidence["tools"]],
                [("jfrog-secrets", "unavailable"), ("gitleaks", "passed")],
            )
            self.assertEqual(validate_evidence(evidence), [])

    @staticmethod
    def completed_result(kind: str, audit_id: str, tool: str) -> dict:
        return {
            "kind": kind,
            "audit_id": audit_id,
            "provider": "jfrog" if tool.startswith("jfrog") else "native",
            "tool": tool,
            "tool_version": "test-version",
            "command": f"{tool} test-command",
            "targets": ["repository"],
            "covered_targets": ["repository"],
            "state": "passed",
            "completion": "complete",
            "exit_code": 0,
            "output_hash": "sha256:" + "a" * 64,
            "items": [{"target": "repository", "state": "passed", "completion": "complete"}],
            "findings": [],
            "summary": "completed",
        }


if __name__ == "__main__":
    unittest.main()
