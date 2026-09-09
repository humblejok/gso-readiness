from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / ".github/graphify-review/scripts"))
import review_settings as settings  # noqa: E402
import setup_review as setup  # noqa: E402


class ReviewSettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repository = self.root / "project with spaces"
        self.repository.mkdir()
        self.user = self.root / "user/settings.json"
        self.project = settings.project_settings_path(self.repository)
        for module in (settings, setup):
            patch = mock.patch.object(module, "user_settings_path", return_value=self.user)
            patch.start()
            self.addCleanup(patch.stop)

    def invoke(self, action, *args):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout, mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            code = setup.main([action, "--repository", str(self.repository), "--user-settings", str(self.user), *args])
        return code, stdout.getvalue() + stderr.getvalue()

    def test_defaults_work_without_any_configuration(self):
        result = settings.load_settings(self.repository)
        self.assertFalse(result["configured"])
        self.assertEqual(result["project"]["profile"], "generic")
        self.assertFalse(self.project.exists())

    def test_azure_configuration_accepts_generic_host_and_rejects_secrets_and_versions(self):
        code, _ = self.invoke("configure", "--non-interactive", "--set", "project.git_provider=azure-devops", "--set", "project.azure_api_version=6.0", "--set", "user.azure_devops_url=https://ado.example.invalid/tfs/Collection", "--set", "user.azure_auth=windows")
        self.assertEqual(code, 0)
        saved = settings.load_settings(self.repository)
        self.assertEqual(saved["project"]["azure_api_version"], "6.0")
        self.assertEqual(saved["user"]["azure_auth"], "windows")
        self.assertNotIn("azure_auth", self.project.read_text())
        for assignment in ("user.azure_pat=secret", "user.azure_auth=ntlm-password", "project.git_provider=tfs-custom", "project.azure_api_version=99.0", "user.azure_devops_url=https://token@host/Collection", "user.azure_devops_url=https://host/Collection/%2e%2e"):
            self.assertEqual(self.invoke("configure", "--non-interactive", "--set", assignment)[0], 2)

    def test_azure_wizard_confirms_collection_and_windows_choice_without_network(self):
        values = {"Project name": "Orders", "Stable project ID": "repo:orders", "Default branch": "main", "Review profile": "generic",
                  "Security scanner": "auto", "Finding Hub URL": "https://hub.example.invalid", "Implementation Git host": "auto",
                  "Confirm trusted Azure collection URL": "https://ado.example.invalid/Collection", "Azure API authentication": "windows",
                  "Azure API version": "6.0", "Configure corporate": "no", "Save these settings": "yes"}
        def answer(prompt):
            return next((value for label, value in values.items() if prompt.startswith(label)), "")
        def probe(command, *args):
            return (0, "https://ado.example.invalid/Collection/Project/_git/orders") if command[1:3] == ["remote", "get-url"] else (1, "")
        with mock.patch("sys.stdin.isatty", return_value=True), mock.patch("builtins.input", side_effect=answer), mock.patch.object(setup, "probe", side_effect=probe), mock.patch.object(setup, "jfrog_servers", return_value={}), mock.patch.object(setup.urllib.request, "build_opener") as network:
            self.assertEqual(self.invoke("configure")[0], 0)
            network.assert_not_called()
        self.assertEqual(settings.load_settings(self.repository)["user"]["azure_auth"], "windows")

    def test_noninteractive_config_preserves_unrelated_settings_and_makes_backups(self):
        self.assertEqual(self.invoke("configure", "--non-interactive", "--set", "project.profile=spring-boot", "--set", "user.hub_url=https://hub.example.com")[0], 0)
        self.assertEqual(self.invoke("configure", "--non-interactive", "--set", "user.hub_url=https://other.example.com")[0], 0)
        result = settings.load_settings(self.repository)
        self.assertEqual(result["project"]["profile"], "spring-boot")
        self.assertEqual(result["user"]["hub_url"], "https://other.example.com")
        self.assertEqual(len(list(self.user.parent.glob("settings.json.bak-*"))), 1)
        self.assertNotIn("hub_url", self.project.read_text())
        if os.name != "nt":
            self.assertEqual(self.user.stat().st_mode & 0o777, 0o600)

    def test_identical_configuration_is_idempotent(self):
        arguments = ("--non-interactive", "--set", "project.profile=generic")
        self.invoke("configure", *arguments)
        self.invoke("configure", *arguments)
        self.assertFalse(list(self.project.parent.glob("settings.json.bak-*")))

    def test_credentials_and_unknown_or_cross_scope_settings_are_rejected(self):
        for assignment in (
            "user.hub_url=https://token@example.com", "user.pip_index_url=https://example.com/?token=secret",
            "user.access_token=secret", "project.hub_url=https://example.com", "user.MAVEN_OPTS=-Xmx2g",
            "user.jfrog_cli_path=/tmp/evil.sh", "project.profile=unknown", "user.hub_url=http://remote.example.com",
        ):
            with self.subTest(assignment=assignment):
                code, result = self.invoke("configure", "--non-interactive", "--set", assignment)
                self.assertEqual(code, 2)
                self.assertNotIn("=secret", result)
                self.assertFalse(self.user.exists())
                self.assertFalse(self.project.exists())

    def test_local_hub_http_is_allowed(self):
        self.assertEqual(self.invoke("configure", "--non-interactive", "--set", "user.hub_url=http://localhost:8000")[0], 0)

    def test_preset_fills_gaps_but_explicit_changes_win(self):
        preset = self.root / "company.json"
        preset.write_text(json.dumps({"schema_version": "1.0", "project": {"profile": "spring-boot"},
                                      "user": {"hub_url": "https://hub.example.com", "jfrog_server_id": "corporate"}}))
        self.invoke("configure", "--non-interactive", "--set", "user.jfrog_server_id=existing")
        self.assertEqual(self.invoke("configure", "--non-interactive", "--preset", str(preset), "--set", "project.profile=generic")[0], 0)
        result = settings.load_settings(self.repository)
        self.assertEqual(result["project"]["profile"], "generic")
        self.assertEqual(result["user"]["jfrog_server_id"], "existing")
        self.assertEqual(result["user"]["hub_url"], "https://hub.example.com")

    def test_unsafe_preset_cannot_execute_or_store_arbitrary_settings(self):
        for data in ({"script": "bad"}, {"user": {"token": "secret"}}, {"installer": {"command": "bad"}}):
            preset = self.root / "bad.json"
            preset.write_text(json.dumps({"schema_version": "1.0", **data}))
            self.assertEqual(self.invoke("configure", "--non-interactive", "--preset", str(preset))[0], 2)
            self.assertFalse(self.user.exists())

    def test_installer_defaults_discover_files_without_replacing_user_choices(self):
        installed = self.root / "runtime"
        (installed / "truststore").mkdir(parents=True)
        (installed / "truststore/cacerts").write_bytes(b"test")
        self.invoke("configure", "--non-interactive", "--set", "user.java_truststore=/existing/cacerts")
        self.assertEqual(self.invoke("configure", "--non-interactive", "--installed-root", str(installed))[0], 0)
        self.assertEqual(settings.load_settings(self.repository)["user"]["java_truststore"], "/existing/cacerts")

    def test_child_environment_preserves_unrelated_options_and_is_idempotent(self):
        self.invoke("configure", "--non-interactive", "--set", "user.java_truststore=/new path/cacerts",
                    "--set", "user.java_truststore_type=JKS", "--set", "user.proxy_url=http://proxy.example.com:8080",
                    "--set", "user.no_proxy=localhost,.internal", "--set", "user.ca_bundle=/approved/ca.pem")
        base = {"MAVEN_OPTS": '-Xmx2g -Dcustom=keep -Djavax.net.ssl.trustStore="/old store/cacerts"', "KEEP": "unchanged"}
        result = settings.process_environment(self.repository, base)
        self.assertEqual(result, settings.process_environment(self.repository, result))
        self.assertIn("-Xmx2g -Dcustom=keep", result["MAVEN_OPTS"])
        self.assertNotIn("old store", result["MAVEN_OPTS"])
        self.assertEqual(result["http_proxy"], result["HTTPS_PROXY"])
        self.assertEqual(result["PIP_CERT"], "/approved/ca.pem")
        self.assertEqual(result["KEEP"], "unchanged")
        self.assertNotIn("HTTP_PROXY", base)

    def test_unset_reverts_to_inherited_environment(self):
        self.invoke("configure", "--non-interactive", "--set", "user.proxy_url=http://proxy.example.com")
        self.invoke("configure", "--non-interactive", "--unset", "user.proxy_url")
        result = settings.process_environment(self.repository, {"HTTP_PROXY": "inherited"})
        self.assertEqual(result["HTTP_PROXY"], "inherited")

    def test_local_doctor_never_runs_network_probes_or_audits(self):
        with mock.patch.object(setup, "probe", return_value=(0, "")) as probe, mock.patch.object(setup.urllib.request, "build_opener") as opener:
            code, output = self.invoke("doctor")
        self.assertEqual(code, 1)
        self.assertIn("attention_required", output)
        opener.assert_not_called()
        for call in probe.call_args_list:
            self.assertEqual(call.args[0][1:], ["config", "show"])

    def test_server_discovery_only_exposes_valid_ids_and_urls(self):
        with mock.patch.object(setup.shutil, "which", return_value="jf"), mock.patch.object(setup, "probe", return_value=(0, "Server ID: corp\nJFrog Platform URL: https://jf.example.com\nAccess token: DO-NOT-PRINT\n")):
            result = setup.jfrog_servers(self.repository, {})
        self.assertEqual(result, {"corp": "https://jf.example.com"})
        self.assertNotIn("DO-NOT-PRINT", json.dumps(result))

    def test_wizard_saves_confirmed_project_and_cancel_preserves_files(self):
        answers = ["Sample", "repo:sample", "main", "generic", "auto", "", "", "", "", "", "no", "yes"]
        with mock.patch("sys.stdin.isatty", return_value=True), mock.patch("builtins.input", side_effect=answers), mock.patch.object(setup, "probe", return_value=(1, "")), mock.patch.object(setup, "jfrog_servers", return_value={}):
            self.assertEqual(self.invoke("configure")[0], 0)
        before = self.project.read_bytes()
        with mock.patch("sys.stdin.isatty", return_value=True), mock.patch("builtins.input", side_effect=KeyboardInterrupt):
            self.assertEqual(self.invoke("configure")[0], 2)
        self.assertEqual(self.project.read_bytes(), before)

    def test_nonterminal_wizard_does_not_hang(self):
        with mock.patch("sys.stdin.isatty", return_value=False):
            self.assertEqual(self.invoke("configure")[0], 2)
            self.assertEqual(self.invoke("jfrog-login")[0], 2)

    def test_invalid_json_and_symlinks_do_not_overwrite_settings(self):
        self.user.parent.mkdir()
        self.user.write_text('{"schema_version":"1.0","user":{},"user":{}}')
        self.assertEqual(self.invoke("show")[0], 2)
        target = self.root / "target.json"
        link = self.root / "link.json"
        link.symlink_to(target)
        with self.assertRaises(settings.SettingsError):
            settings.write_settings(link, "user", {})
        self.assertFalse(target.exists())

    def test_vulnerability_cli_uses_saved_server_and_explicit_override(self):
        import vulnerability_upgrades as upgrades
        self.invoke("configure", "--non-interactive", "--set", "user.jfrog_server_id=corp",
                    "--set", "project.artifactory_repositories=maven-cache")
        for extra, expected in (([], "corp"), (["--jfrog-server-id", "explicit"], "explicit")):
            with mock.patch.dict(os.environ), mock.patch.object(upgrades, "build_report", return_value={"status": "completed", "summary": {}}) as build, mock.patch.object(upgrades, "write_report", return_value=("report.json", "report.md")), mock.patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(upgrades.main(["check", "--repository", str(self.repository), *extra]), 0)
            self.assertEqual(build.call_args.args[3], expected)
            self.assertEqual(build.call_args.args[4], ["maven-cache"])

    def test_bootstrap_passes_saved_network_settings_to_pip(self):
        import bootstrap_environment as bootstrap
        self.invoke("configure", "--non-interactive", "--set", "user.pip_index_url=https://packages.example.com/simple")
        venv = self.repository / ".graphify-review-venv"
        python, _ = bootstrap.environment_commands(venv)
        python.parent.mkdir(parents=True)
        python.touch()
        (self.repository / "requirements.txt").write_text("graphifyy==0.9.46\n")
        policy = {"runtime": {"venv_path": ".graphify-review-venv", "requirements_file": "requirements.txt"}}
        with mock.patch.object(bootstrap, "inspect_environment", side_effect=[{"ready": False, "is_virtual_environment": True}, {"ready": True}]), mock.patch.object(bootstrap.subprocess, "run") as runner:
            bootstrap.bootstrap(self.repository, policy)
        self.assertEqual(runner.call_args.kwargs["env"]["PIP_INDEX_URL"], "https://packages.example.com/simple")

    def test_export_uses_saved_identity_but_original_source_revision(self):
        import export_review as exporter
        self.invoke("configure", "--non-interactive", "--set", "project.repository_id=repo:saved",
                    "--set", "project.repository_name=Saved Name", "--set", "project.default_branch=develop")
        original = json.loads((ROOT / ".github/graphify-review/examples/review.example.json").read_text())
        review = self.repository / "review.json"
        review.write_text(json.dumps(original))
        output = self.repository / "export"
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            code = exporter.main(["prepare", "--review", str(review), "--repository", str(self.repository), "--output-directory", str(output)])
        self.assertEqual(code, 0)
        request = json.loads((output / "export-request.json").read_text())
        self.assertEqual(request["repository"]["external_id"], "repo:saved")
        self.assertEqual(request["repository"]["default_branch"], "develop")
        self.assertEqual(request["source"]["branch"], original["review"]["repository"]["branch"])

    def test_profile_cli_uses_saved_profile_and_explicit_override(self):
        import resolve_profile as resolver
        self.invoke("configure", "--non-interactive", "--set", "project.profile=spring-boot")
        for extra, expected in (([], "spring-boot"), (["--profile", "generic"], "generic")):
            with mock.patch.object(sys, "argv", ["resolve_profile.py", "--repository", str(self.repository), *extra]), mock.patch.object(resolver, "resolve_profile", return_value={}) as resolve, mock.patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(resolver.main(), 0)
            self.assertEqual(resolve.call_args.args[0], expected)

    def test_network_doctor_honors_bypass_without_uploading(self):
        self.invoke("configure", "--non-interactive", "--set", "user.hub_url=http://localhost:8000",
                    "--set", "user.proxy_url=http://proxy.example.com:8080", "--set", "user.no_proxy=localhost")
        opener = mock.MagicMock()
        opener.open.return_value.__enter__.return_value.status = 200
        with mock.patch.object(setup.urllib.request, "build_opener", return_value=opener), mock.patch.object(setup.urllib.request, "ProxyHandler", wraps=setup.urllib.request.ProxyHandler) as proxy, mock.patch.object(setup, "jfrog_servers", return_value={}):
            _, output = self.invoke("doctor", "--network")
        proxy.assert_called_with({})
        self.assertEqual(opener.open.call_args.args[0], "http://localhost:8000/health/live")
        self.assertIn("hub_connection", output)

    def test_setup_slash_commands_and_first_use_are_wired(self):
        for command in ("setup-review", "configure-review", "doctor-review"):
            self.assertIn("agent: Review Setup Manager", (ROOT / f".github/prompts/{command}.prompt.md").read_text())
        for agent in ("review-manager", "vulnerability-upgrade-manager", "review-export-manager"):
            self.assertIn("/setup-review", (ROOT / f".github/agents/{agent}.agent.md").read_text())


if __name__ == "__main__":
    unittest.main()
