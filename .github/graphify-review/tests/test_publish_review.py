from __future__ import annotations

import contextlib
import copy
import getpass
import http.client
import io
import json
import os
import ssl
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
import uuid
import warnings
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "scripts"))
import publish_review as publisher  # noqa: E402

HUB = "https://hub.example.invalid"
TOKEN = "00000000-0000-4000-8000-000000000001." + "x" * 43


def envelope_fixture():
    review = json.loads((KIT / "examples/review.example.json").read_text(encoding="utf-8"))
    source = review["review"]["repository"]
    return {
        "schema_version": "1.0",
        "repository": {"external_id": "repo:orders", "name": "Orders API", "default_branch": "main"},
        "source": {"commit_sha": source["commit_sha"], "branch": source["branch"]},
        "review": review,
        "remediations": [{
            "schema_version": "1.0", "finding_fingerprint": f["fingerprint"],
            "generated_from_commit": source["commit_sha"], "objective": f["recommendation"],
            "preconditions": [], "constraints": [], "non_goals": [],
            "implementation_steps": [{"order": 1, "instruction": f["recommendation"]}],
            "acceptance_criteria": ["Verify a targeted regression test demonstrates the defect is resolved."],
            "validation_commands": [], "validation_notes": "Establish the appropriate regression command before implementing.",
            "risks_and_rollback": ["Revert only the remediation if behavior regresses."],
        } for f in review["findings"]],
    }


class PublishReviewTests(unittest.TestCase):
    def test_personal_token_workspace_selection_and_response_binding(self):
        self.settings["project"] = {"hub_workspace_id": self.reply["organization_id"]}
        result = self.publish()
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.get_header("X-workspace-id"), self.reply["organization_id"])
        self.assertEqual(result["hub_workspace_id"], self.reply["organization_id"])
        self.settings["project"]["hub_workspace_id"] = str(uuid.uuid4())
        with self.assertRaisesRegex(publisher.PublishError, "workspace ID"):
            self.publish()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "import-envelope.json"
        self.data = envelope_fixture()
        self.save()
        self.settings = {"user": {"hub_url": HUB}}
        patcher = mock.patch.object(publisher, "load_settings", return_value=self.settings)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Never use real credentials, OS keyrings or remote services in tests.
        patcher = mock.patch.object(publisher, "native_keyring")
        self.keyring = patcher.start().return_value
        self.keyring.get_password.return_value = TOKEN
        self.addCleanup(patcher.stop)
        patcher = mock.patch.dict(os.environ, {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(publisher, "opener_for")
        self.opener_factory = patcher.start()
        self.opener = self.opener_factory.return_value
        self.addCleanup(patcher.stop)
        self.reply = {
            "run_id": self.data["review"]["run"]["run_id"],
            "import_id": str(uuid.uuid4()), "repository_id": str(uuid.uuid4()),
            "organization_id": str(uuid.uuid4()), "repository_created": True,
            "created_findings": 4, "updated_findings": 0, "lifecycle_changes": 0,
            "jira": {"state": "not_configured"},
        }
        self.respond()

    def save(self):
        self.path.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")

    def respond(self, status=201, raw=None):
        response = self.opener.open.return_value.__enter__.return_value
        response.status = status
        response.read.return_value = raw if raw is not None else json.dumps(self.reply).encode()

    def publish(self, dry_run=False):
        return publisher.publish(self.root, self.path, 30, dry_run)

    def invoke(self, *args):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out, mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            code = publisher.main([*args, "--repository", str(self.root)])
        return code, json.loads(out.getvalue() or err.getvalue())

    def test_dry_run_validates_without_credentials_or_network(self):
        result = self.publish(True)
        self.assertEqual(result["status"], "ready_to_publish")
        self.assertEqual(result["hub_url"], HUB)
        self.keyring.get_password.assert_not_called()
        self.opener_factory.assert_not_called()
        self.assertEqual(list(self.root.glob("publish-receipt*")), [])

    def test_publication_is_utf8_authenticated_and_immutable(self):
        self.data["review"]["findings"][0]["description"] += " Stéphane — 漢字"
        self.save()
        before = self.path.read_bytes()
        result = self.publish()
        self.assertEqual(result["status"], "published")
        self.assertTrue(result["repository_created"])
        self.assertEqual(result["workspace_url"], HUB + "/w/" + self.reply["organization_id"] + "/")
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, HUB + "/api/v1/review-imports")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertEqual(request.get_header("Idempotency-key"), "repo:orders:example-run")
        self.assertEqual(json.loads(request.data.decode("utf-8")), self.data)
        receipt = Path(result["receipt"]).read_text()
        self.assertNotIn(TOKEN, receipt)
        self.assertEqual(self.path.read_bytes(), before)
        self.opener.open.assert_called_once()

    def test_replay_and_new_runs_into_existing_repository(self):
        self.respond(status=200)
        result = self.publish()
        self.assertEqual(result["status"], "already_published")
        self.assertFalse(result["repository_created"])
        self.reply["repository_created"] = False
        self.respond()
        self.assertFalse(self.publish()["repository_created"])

    def test_legacy_hub_response_supported(self):
        del self.reply["repository_created"], self.reply["organization_id"]
        self.respond()
        result = self.publish()
        self.assertEqual(result["status"], "published")
        self.assertNotIn("repository_created", result)
        self.assertNotIn("workspace_url", result)

    def test_no_configured_hub_blocks_before_reading_credentials(self):
        self.settings["user"].clear()
        with self.assertRaisesRegex(publisher.PublishError, "No Hub URL"):
            self.publish()
        self.keyring.get_password.assert_not_called()
        self.opener_factory.assert_not_called()

    def test_invalid_inputs_block_before_credentials(self):
        original = copy.deepcopy(self.data)
        for mutation in (
            lambda d: d["remediations"].clear(),
            lambda d: d.update(hub_url="https://attacker.example.invalid"),
            lambda d: d["repository"].update(external_id="repo:bad\r\nheader"),
        ):
            self.data = copy.deepcopy(original)
            mutation(self.data)
            self.save()
            code, _ = self.invoke("publish", "--envelope", str(self.path))
            self.assertEqual(code, 2)
        self.keyring.get_password.assert_not_called()
        self.opener_factory.assert_not_called()

    def test_missing_or_non_envelope_input_is_actionable(self):
        code, result = self.invoke("publish")
        self.assertEqual(code, 2)
        self.assertIn("explicit", result["message"])
        self.path.write_text(json.dumps(self.data["review"]), encoding="utf-8")
        code, result = self.invoke("publish", "--envelope", str(self.path))
        self.assertEqual(code, 2)
        self.assertIn("import-envelope.json", result["message"])
        self.opener_factory.assert_not_called()

    def test_http_errors_are_sanitized_and_never_retried(self):
        before = self.path.read_bytes()
        for status, phrase in ((400, "baseline"), (401, "authentication"), (403, "scope"), (404, "base URL"), (409, "different content"), (413, "size"), (429, "rate limit"), (500, "uncertain"), (302, "Redirects are blocked")):
            with self.subTest(status=status):
                self.opener.open.reset_mock()
                body = io.BytesIO(TOKEN.encode())
                self.opener.open.side_effect = urllib.error.HTTPError(HUB, status, TOKEN, {}, body)
                code, result = self.invoke("publish", "--envelope", str(self.path))
                self.assertEqual(code, 2)
                self.assertIn(phrase, result["message"])
                self.assertNotIn(TOKEN, str(result))
                self.opener.open.assert_called_once()
                self.assertTrue(body.closed)
                self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.root.glob("publish-receipt*")), [])

    def test_network_tls_errors_preserve_uncertain_outcome(self):
        for error in (TimeoutError(TOKEN), urllib.error.URLError(TOKEN), ssl.SSLError(TOKEN), http.client.IncompleteRead(TOKEN.encode())):
            self.opener.open.side_effect = error
            code, result = self.invoke("publish", "--envelope", str(self.path))
            self.assertEqual(code, 2)
            self.assertIn("may have received", result["message"])
            self.assertNotIn(TOKEN, str(result))

    def test_unexpected_response_never_claims_success(self):
        for status, raw in ((202, b"{}"), (201, b"["), (201, b"\xff"), (201, b"{}"), (201, b"x" * (publisher.MAX_RESPONSE + 1))):
            self.respond(status, raw)
            with self.assertRaisesRegex(publisher.PublishError, "uncertain"):
                self.publish()
        self.assertEqual(list(self.root.glob("publish-receipt*")), [])

    def test_response_prose_never_persisted(self):
        self.reply.update(secret=TOKEN, created_findings=TOKEN, organization_id=TOKEN, jira={"state": [TOKEN]})
        self.respond()
        result = self.publish()
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertNotIn(TOKEN, Path(result["receipt"]).read_text())

    def test_receipt_write_failure_does_not_misreport_accepted_import(self):
        with mock.patch.object(publisher, "write_new_json", side_effect=OSError(TOKEN)):
            result = self.publish()
        self.assertEqual(result["status"], "published")
        self.assertIn("warning", result)
        self.assertNotIn(TOKEN, str(result))

    def test_other_project_envelope_is_blocked_before_credentials(self):
        self.settings["project"] = {"repository_id": "repo:different-project", "hub_credential_ref": "default"}
        with self.assertRaisesRegex(publisher.PublishError, "different project"):
            self.publish()
        self.keyring.get_password.assert_not_called()
        self.opener_factory.assert_not_called()

    def test_environment_token_must_be_bound_to_destination(self):
        with mock.patch.dict(os.environ, {"FINDING_HUB_TOKEN": TOKEN}):
            for url in ("", "https://other.example.invalid", "https://hub.example.invalid/other"):
                os.environ["FINDING_HUB_TOKEN_URL"] = url
                with self.assertRaises(publisher.PublishError):
                    publisher.token_for(HUB)
            os.environ["FINDING_HUB_TOKEN_URL"] = "https://HUB.example.invalid:443/"
            self.assertEqual(publisher.token_for(HUB), TOKEN)
        self.keyring.get_password.assert_not_called()

    def test_credentials_are_destination_scoped_and_missing_is_actionable(self):
        publisher.token_for(HUB)
        self.keyring.get_password.assert_called_with("graphify-review-hub:" + HUB, "workspace-token")
        publisher.token_for("https://other.example.invalid")
        self.keyring.get_password.assert_called_with("graphify-review-hub:https://other.example.invalid", "workspace-token")
        self.keyring.get_password.return_value = None
        with self.assertRaisesRegex(publisher.PublishError, "own terminal"):
            publisher.token_for(HUB)

    def test_credentials_validate_format_without_leaking_input(self):
        for token in ("", TOKEN + "\r\n", "password", "z" * 36 + "." + "x" * 43):
            with self.assertRaises(publisher.PublishError):
                publisher.validate_token(token)

    def test_login_is_interactive_hidden_and_has_no_network(self):
        from credential_refs import slot
        self.settings["project"] = {"repository_id": "repo:orders", "hub_credential_ref": "reviewer"}
        with mock.patch("sys.stdin.isatty", return_value=True), mock.patch.object(getpass, "getpass", return_value=TOKEN), contextlib.redirect_stdout(io.StringIO()) as out:
            result = publisher.credential_action("login", self.root)
        self.assertEqual(result["status"], "credential_saved")
        self.assertNotIn(TOKEN, out.getvalue() + str(result))
        selected = slot("hub", HUB, self.settings["project"])
        self.keyring.set_password.assert_called_once_with(selected["service"], "workspace-token", TOKEN)
        self.opener_factory.assert_not_called()

    def test_login_refuses_agent_tool_and_echo_fallback(self):
        self.settings["project"] = {"repository_id": "repo:orders"}
        with mock.patch("sys.stdin.isatty", return_value=False):
            with self.assertRaisesRegex(publisher.PublishError, "own interactive terminal"):
                publisher.credential_action("login", self.root)
        def insecure_prompt(*args):
            warnings.warn("Cannot disable echo", getpass.GetPassWarning)
            return TOKEN
        with mock.patch("sys.stdin.isatty", return_value=True), mock.patch.object(getpass, "getpass", side_effect=insecure_prompt), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(publisher.PublishError, "no plaintext fallback"):
                publisher.credential_action("login", self.root)
        self.keyring.set_password.assert_not_called()

    def test_logout_removes_only_current_hub_credential(self):
        from credential_refs import LOGGED_OUT, slot
        self.settings["project"] = {"repository_id": "repo:orders"}
        with mock.patch("sys.stdin.isatty", return_value=True):
            result = publisher.credential_action("logout", self.root)
        self.assertEqual(result["status"], "logged_out")
        selected = slot("hub", HUB, self.settings["project"])
        self.keyring.set_password.assert_called_once_with(selected["service"], "workspace-token", LOGGED_OUT)
        self.opener_factory.assert_not_called()

    def test_keyring_errors_are_sanitized(self):
        self.keyring.get_password.side_effect = RuntimeError(TOKEN)
        with self.assertRaisesRegex(publisher.PublishError, "credential") as error:
            publisher.token_for(HUB)
        self.assertNotIn(TOKEN, str(error.exception))

    def test_timeout_bounds(self):
        for timeout in ("0", "61"):
            code, result = self.invoke("publish", "--envelope", str(self.path), "--timeout", timeout)
            self.assertEqual(code, 2)
            self.assertIn("between 1 and 60", result["message"])
        self.opener_factory.assert_not_called()

    def test_missing_file_and_invalid_login_flags_are_actionable(self):
        code, result = self.invoke("publish", "--envelope", str(self.root / "missing.json"))
        self.assertEqual(code, 2)
        self.assertIn("Cannot read", result["message"])
        for action in ("login", "logout"):
            code, result = self.invoke(action, "--dry-run")
            self.assertEqual(code, 2)
            self.assertIn("apply only to publish", result["message"])
        self.keyring.set_password.assert_not_called()
        self.keyring.delete_password.assert_not_called()
        self.opener_factory.assert_not_called()

    def test_slash_command_is_wired_and_requires_explicit_upload(self):
        prompt = (KIT.parent / "prompts/publish-review.prompt.md").read_text()
        agent = (KIT.parent / "agents/review-publish-manager.agent.md").read_text()
        self.assertIn("agent: Review Publish Manager", prompt)
        for text in ("name: Review Publish Manager", "--dry-run", "publish_review.py publish", "review-export-manager.agent.md", "identical envelope", "own interactive terminal"):
            self.assertIn(text, agent)


class TransportTests(unittest.TestCase):
    def test_redirects_never_forward_any_credentials_or_body(self):
        request = urllib.request.Request(HUB, data=b"private", headers={"Authorization": "Bearer " + TOKEN})
        for status in (301, 302, 303, 307, 308):
            self.assertIsNone(publisher.NoRedirects().redirect_request(request, None, status, "", {}, "https://other.example.invalid"))

    def test_proxy_bypass_and_tls_use_saved_ca(self):
        for bypass, expected in (("hub.example.invalid", {}), ("elsewhere.invalid", {"https": "http://proxy.invalid:8080"})):
            environment = {"HTTPS_PROXY": "http://proxy.invalid:8080", "NO_PROXY": bypass}
            with mock.patch.object(publisher, "load_settings", return_value={"user": {"ca_bundle": "/approved.pem"}}), mock.patch.object(publisher, "process_environment", return_value=environment), mock.patch.object(ssl, "create_default_context") as context, mock.patch.object(urllib.request, "build_opener") as build:
                publisher.opener_for(Path("."), HUB)
                context.assert_called_once_with(cafile="/approved.pem")
                handlers = build.call_args.args
                self.assertEqual(next(h.proxies for h in handlers if isinstance(h, urllib.request.ProxyHandler)), expected)
                self.assertTrue(any(isinstance(h, publisher.NoRedirects) for h in handlers))

    def test_native_keyring_does_not_discover_third_party_backends(self):
        for platform, module, backend_name in (("darwin", "macOS", "Keyring"), ("win32", "Windows", "WinVaultKeyring"), ("linux", "SecretService", "Keyring")):
            fake = mock.Mock()
            backend = getattr(fake, backend_name).return_value
            backend.priority = 1
            with mock.patch.object(sys, "platform", platform), mock.patch.dict(sys.modules, {"keyring.backends." + module: fake}):
                self.assertIs(publisher.native_keyring(), backend)
                backend.priority = 0
                with self.assertRaisesRegex(publisher.PublishError, "Plaintext storage is never used"):
                    publisher.native_keyring()


if __name__ == "__main__":
    unittest.main()
