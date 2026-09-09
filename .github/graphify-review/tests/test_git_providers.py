import copy
import io
import os
import subprocess
import sys
import unittest
import urllib.error
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import azure_devops as azure  # noqa: E402
import git_providers as providers  # noqa: E402
from git_host_contract import HostError, repository_host, validate_pr_url  # noqa: E402


CLONE = "https://ado.example.invalid/tfs/Collection/Project%20Name/_git/repo"


class HostContractTests(unittest.TestCase):
    def test_parses_server_cloud_and_virtual_directories(self):
        for url, collection in ((CLONE, "https://ado.example.invalid/tfs/Collection"),
                                ("https://dev.azure.com/organization/project/_git/repo", "https://dev.azure.com/organization"),
                                ("https://org.visualstudio.com/project/_git/repo", "https://org.visualstudio.com")):
            host = repository_host(url)
            self.assertEqual(host["provider"], "azure-devops")
            self.assertEqual(host["collection_url"], collection)
            self.assertEqual(validate_pr_url(url + "/pullrequest/42", url), url + "/pullrequest/42")
        self.assertEqual(repository_host(CLONE)["project"], "Project Name")
        self.assertEqual(repository_host("https://organization@dev.azure.com/organization/project/_git/repo"), repository_host("https://dev.azure.com/organization/project/_git/repo"))

    def test_rejects_unsafe_and_conflicting_urls(self):
        for url in ("http://host/a/b/_git/c", "https://user:secret@host/a/b/_git/c", "https://host/a/b/_git/c?token=secret",
                    "https://host/a/%2e%2e/_git/c", "https://host/a/b%2fc/_git/d", "https://host/a/%252e%252e/_git/c",
                    "https://host/a/b/_git/c#fragment", "https://host/a/b/_git/c\\d"):
            with self.subTest(url=url), self.assertRaises(HostError):
                repository_host(url)
        with self.assertRaises(HostError):
            repository_host(CLONE, "github")
        with self.assertRaises(HostError):
            validate_pr_url(CLONE + "/pullrequest/42", CLONE + "-other")
        with self.assertRaises(HostError):
            validate_pr_url(CLONE + "/pull/42")

    def test_github_still_normalizes_ssh_and_https(self):
        self.assertEqual(repository_host("git@github.example.invalid:org/repo.git"), repository_host("https://github.example.invalid/org/repo"))
        self.assertEqual(validate_pr_url("https://github.com/org/repo/pull/1", "git@github.com:org/repo.git"), "https://github.com/org/repo/pull/1")


class AzureTransportTests(unittest.TestCase):
    def setUp(self):
        self.root = Path.cwd()
        self.host = {**repository_host(CLONE), "api_version": "6.0"}
        self.settings = {"project": {}, "user": {"azure_devops_url": self.host["collection_url"], "azure_auth": "pat"}}
        patch = mock.patch.object(azure, "load_settings", return_value=self.settings)
        patch.start()
        self.addCleanup(patch.stop)

    def test_untrusted_destination_never_reads_credentials_or_calls_windows(self):
        self.settings["user"]["azure_devops_url"] += "-other"
        with mock.patch.object(azure, "pat_for") as pat, mock.patch.object(azure, "windows_request") as windows:
            with self.assertRaises(azure.AzureError):
                azure.request(self.root, self.host, "GET")
            pat.assert_not_called()
            windows.assert_not_called()

    def test_pat_request_encodes_project_uses_v6_and_does_not_follow_errors(self):
        response = mock.MagicMock(status=200)
        response.__enter__.return_value = response
        response.read.return_value = b'{"id":"test"}'
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(azure, "pat_for", return_value="x" * 52), mock.patch.object(azure, "opener_for", return_value=opener):
            self.assertEqual(azure.request(self.root, self.host, "GET"), {"id": "test"})
            request = opener.open.call_args.args[0]
            self.assertIn("/Project%20Name/_apis/git/repositories/repo?api-version=6.0", request.full_url)
            self.assertTrue(request.headers["Authorization"].startswith("Basic "))
            opener.open.side_effect = urllib.error.HTTPError(CLONE, 302, "SECRET", {}, io.BytesIO(b"SECRET-BODY"))
            with self.assertRaises(azure.AzureError) as error:
                azure.request(self.root, self.host, "POST", "/pullrequests", data={})
            self.assertNotIn("SECRET", str(error.exception))
            self.assertEqual(opener.open.call_count, 2)  # no automatic retry

    def test_windows_transport_uses_fixed_script_without_policy_bypass_or_secret(self):
        self.settings["user"]["azure_auth"] = "windows"
        completed = subprocess.CompletedProcess([], 0, b'{"status":200,"body":"{}"}', b"")
        with mock.patch.object(azure.sys, "platform", "win32"), mock.patch.object(azure, "process_environment", return_value={}), mock.patch.object(azure.shutil, "which", return_value="powershell.exe"), mock.patch.object(azure, "proxy_for", return_value=""), mock.patch.object(azure.subprocess, "run", return_value=completed) as run, mock.patch.object(azure, "pat_for") as pat:
            self.assertEqual(azure.request(self.root, self.host, "GET"), {})
            args = run.call_args.args[0]
            self.assertIn("-NoProfile", args)
            self.assertIn("-File", args)
            self.assertNotIn("-ExecutionPolicy", args)
            self.assertTrue(args[-1].endswith("azure_windows_request.ps1"))
            self.assertNotIn("Authorization", run.call_args.kwargs["input"].decode())
            pat.assert_not_called()

    def test_windows_non_success_is_sanitized_and_never_falls_back_to_pat(self):
        self.settings["user"]["azure_auth"] = "windows"
        with mock.patch.object(azure.sys, "platform", "win32"), mock.patch.object(azure, "windows_request", return_value=(401, b"SECRET")), mock.patch.object(azure, "pat_for") as pat:
            with self.assertRaisesRegex(azure.AzureError, "HTTP 401") as error:
                azure.request(self.root, self.host, "GET")
            self.assertNotIn("SECRET", str(error.exception))
            pat.assert_not_called()

    def test_environment_pat_is_destination_bound(self):
        with mock.patch.dict(os.environ, {"GRAPHIFY_AZURE_PAT": "x" * 52, "GRAPHIFY_AZURE_PAT_URL": "https://other.example.invalid/collection"}):
            with self.assertRaises(azure.AzureError):
                azure.pat_for(self.host["collection_url"])

    def test_proxy_bypass_uses_saved_no_proxy(self):
        with mock.patch.object(azure, "process_environment", return_value={"HTTPS_PROXY": "http://proxy.example.invalid:8080", "NO_PROXY": "ado.example.invalid"}):
            self.assertEqual(azure.proxy_for(self.root, CLONE), "")

    def test_login_never_runs_in_an_agent_terminal(self):
        with mock.patch("sys.stdin.isatty", return_value=False), mock.patch.object(azure, "native_keyring") as keyring:
            with self.assertRaises(azure.AzureError):
                azure.credential_action(self.root, "login")
            keyring.assert_not_called()


class AzureProviderTests(unittest.TestCase):
    def setUp(self):
        self.host = {**repository_host(CLONE), "api_version": "6.0", "repository_uuid": str(uuid.uuid4())}
        self.provider = providers.AzureDevOps(Path.cwd(), self.host, None)
        self.state = {"branch": "feature/ARCH-C001", "base_branch": "develop", "commit_sha": "a" * 40, "item": {"display_id": "ARCH-C001"}}
        self.pr = {"pullRequestId": 42, "status": "active", "sourceRefName": "refs/heads/feature/ARCH-C001", "targetRefName": "refs/heads/develop",
                   "lastMergeSourceCommit": {"commitId": "a" * 40}, "repository": {"id": self.host["repository_uuid"]}}

    def test_create_persists_marker_before_post_and_verifies(self):
        checkpoint = mock.Mock()
        def api(root, host, method, suffix, query=None, data=None):
            if method == "GET":
                self.assertEqual(query["searchCriteria.targetRefName"], "refs/heads/develop")
                return {"count": 0, "value": []}
            checkpoint.assert_called_once()
            self.assertEqual(data["targetRefName"], "refs/heads/develop")
            self.assertNotIn("autoCompleteSetBy", data)
            return self.pr
        with mock.patch.object(providers, "request", side_effect=api):
            url = self.provider.ensure_pr(self.state, Path("state.json"), "Actual fix summary", checkpoint)
        self.assertEqual(url, CLONE + "/pullrequest/42")

    def test_ambiguous_create_recovery_never_posts_twice(self):
        with mock.patch.object(providers, "request", side_effect=[{"count": 0, "value": []}, azure.AzureError("Timeout")]) as api:
            with self.assertRaises(azure.AzureError):
                self.provider.ensure_pr(self.state, Path("state.json"), "Summary", mock.Mock())
        self.assertTrue(self.state["pr_submission_started"])
        with mock.patch.object(providers, "request", return_value={"count": 0, "value": []}) as api:
            with self.assertRaisesRegex(azure.AzureError, "uncertain"):
                self.provider.ensure_pr(self.state, Path("state.json"), "Summary", mock.Mock())
            self.assertEqual(api.call_count, 1)
        with mock.patch.object(providers, "request", return_value={"count": 1, "value": [self.pr]}) as api:
            self.assertEqual(self.provider.ensure_pr(self.state, Path("state.json"), "Summary", mock.Mock()), CLONE + "/pullrequest/42")
            self.assertEqual(api.call_count, 1)

    def test_wrong_commit_target_repository_closed_or_autocomplete_refuses_success(self):
        for change in (lambda p: p.update(status="completed"), lambda p: p.update(targetRefName="refs/heads/main"),
                       lambda p: p["lastMergeSourceCommit"].update(commitId="b" * 40),
                       lambda p: p["repository"].update(id=str(uuid.uuid4())), lambda p: p.update(autoCompleteSetBy={"id": "someone"}),
                       lambda p: p.update(forkSource={"repository": "other"})):
            value = copy.deepcopy(self.pr)
            change(value)
            with self.assertRaises(azure.AzureError):
                self.provider.validate(value, self.state)

    def test_preflight_binds_repository_id(self):
        with mock.patch.object(providers, "request", return_value={"id": self.host["repository_uuid"], "remoteUrl": CLONE}):
            self.provider.preflight()
        with mock.patch.object(providers, "request", return_value={"id": str(uuid.uuid4()), "remoteUrl": CLONE}):
            with self.assertRaises(azure.AzureError):
                self.provider.preflight()


if __name__ == "__main__":
    unittest.main()
