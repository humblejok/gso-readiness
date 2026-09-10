import contextlib
import io
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import azure_devops as azure  # noqa: E402
import publish_review as hub  # noqa: E402
from credential_refs import LOGGED_OUT, slot  # noqa: E402
from git_host_contract import repository_host  # noqa: E402

HUB = "https://hub.example.invalid"
CLONE = "https://ado.example.invalid/Collection/Project/_git/repo"


class MemoryKeyring:
    def __init__(self):
        self.values = {}
        self.reads = []

    def get_password(self, service, account):
        self.reads.append((service, account))
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[service, account] = value


class ProjectCredentialTests(unittest.TestCase):
    def test_personal_workspace_header_and_pinned_attempt(self):
        import hub_findings
        import implement_findings

        root = self.roots[0]
        workspace = str(uuid.uuid4())
        self.settings[root]["project"]["hub_workspace_id"] = workspace
        with mock.patch.object(hub_findings, "configured_hub", return_value=HUB), mock.patch.object(hub_findings, "token_for", return_value=self.token()), mock.patch.object(hub_findings, "opener_for") as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = b'{"results":[]}'
            hub_findings.request_json(root, "GET", "/api/v1/findings")
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.get_header("X-workspace-id"), workspace)
        with mock.patch.object(implement_findings, "load_settings", return_value=self.settings[root]):
            state = {"root": str(root), "credential_context": implement_findings.credential_context(root)}
            self.settings[root]["project"]["hub_workspace_id"] = str(uuid.uuid4())
            with self.assertRaises(implement_findings.WorkError):
                implement_findings.check_credential_context(state)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.roots = [self.root / "one", self.root / "two"]
        self.keyring = MemoryKeyring()
        self.settings = {root: {"project": {"repository_id": "repo:" + root.name, "hub_credential_ref": "review", "azure_credential_ref": "review"},
                               "user": {"hub_url": HUB, "azure_devops_collections": ["https://ado.example.invalid/Collection"], "azure_auth": "pat"}}
                         for root in self.roots}
        self.host = repository_host(CLONE)
        for patch in (mock.patch.object(hub, "load_settings", side_effect=lambda root: self.settings[root]),
                      mock.patch.object(azure, "load_settings", side_effect=lambda root: self.settings[root]),
                      mock.patch.object(hub, "native_keyring", return_value=self.keyring),
                      mock.patch.object(azure, "native_keyring", return_value=self.keyring),
                      mock.patch.object(azure, "detected_host", return_value=(self.host, None)),
                      mock.patch.dict(os.environ, {}, clear=True)):
            patch.start()
            self.addCleanup(patch.stop)

    def token(self):
        return str(uuid.uuid4()) + "." + "x" * 43

    def login(self, root, kind, value):
        module = hub if kind == "hub" else azure
        with mock.patch("sys.stdin.isatty", return_value=True), mock.patch.object(module.getpass, "getpass", return_value=value), contextlib.redirect_stdout(io.StringIO()) as output:
            result = hub.credential_action("login", root) if kind == "hub" else azure.credential_action(root, "login")
        self.assertNotIn(value, output.getvalue() + str(result))
        return result

    def test_two_projects_on_same_hub_have_independent_tokens_and_logout(self):
        tokens = [self.token(), self.token()]
        for root, token in zip(self.roots, tokens):
            self.login(root, "hub", token)
        for root, token in zip(self.roots, tokens):
            self.assertEqual(hub.token_for(HUB, root), token)
        with mock.patch("sys.stdin.isatty", return_value=True):
            hub.credential_action("logout", self.roots[0])
        with self.assertRaises(hub.PublishError):
            hub.token_for(HUB, self.roots[0])
        self.assertEqual(hub.token_for(HUB, self.roots[1]), tokens[1])

    def test_two_projects_in_same_collection_keep_separate_pats(self):
        tokens = ["a" * 52, "b" * 52]
        collection = self.host["collection_url"]
        for root, token in zip(self.roots, tokens):
            self.login(root, "azure", token)
        for root, token in zip(self.roots, tokens):
            self.assertEqual(azure.pat_for(collection, root), token)
        with mock.patch("sys.stdin.isatty", return_value=True):
            azure.credential_action(self.roots[0], "logout")
        with self.assertRaises(azure.AzureError):
            azure.pat_for(collection, self.roots[0])
        self.assertEqual(azure.pat_for(collection, self.roots[1]), tokens[1])

    def test_explicit_reference_never_borrows_legacy_or_another_reference(self):
        root = self.roots[0]
        legacy = self.token()
        self.keyring.set_password("graphify-review-hub:" + HUB, "workspace-token", legacy)
        with self.assertRaises(hub.PublishError):
            hub.token_for(HUB, root)
        self.assertNotIn(("graphify-review-hub:" + HUB, "workspace-token"), self.keyring.reads)
        self.login(root, "hub", self.token())
        self.settings[root]["project"]["hub_credential_ref"] = "other-workspace"
        with self.assertRaises(hub.PublishError):
            hub.token_for(HUB, root)

    def test_legacy_compatible_without_explicit_reference_but_logout_prevents_fallback(self):
        root = self.roots[0]
        del self.settings[root]["project"]["hub_credential_ref"]
        token = self.token()
        self.keyring.set_password("graphify-review-hub:" + HUB, "workspace-token", token)
        self.assertEqual(hub.token_for(HUB, root), token)
        with mock.patch("sys.stdin.isatty", return_value=True):
            hub.credential_action("logout", root)
        selected = slot("hub", HUB, self.settings[root]["project"])
        self.assertEqual(self.keyring.values[selected["service"], "workspace-token"], LOGGED_OUT)
        with self.assertRaises(hub.PublishError):
            hub.token_for(HUB, root)
        self.assertEqual(self.keyring.values["graphify-review-hub:" + HUB, "workspace-token"], token)

    def test_destination_and_reference_form_distinct_slots(self):
        project = self.settings[self.roots[0]]["project"]
        current = slot("azure", self.host["collection_url"], project)["service"]
        self.assertNotEqual(current, slot("azure", "https://ado.example.invalid/Other", project)["service"])
        self.assertNotEqual(current, slot("azure", self.host["collection_url"], {**project, "azure_credential_ref": "write"})["service"])

    def test_process_token_requires_project_and_explicit_reference_bindings(self):
        root = self.roots[0]
        for kind, prefix, destination in (("hub", "FINDING_HUB_TOKEN", HUB), ("azure", "GRAPHIFY_AZURE_PAT", self.host["collection_url"])):
            token = self.token() if kind == "hub" else "c" * 52
            read = hub.token_for if kind == "hub" else azure.pat_for
            error = hub.PublishError if kind == "hub" else azure.AzureError
            with mock.patch.dict(os.environ, {prefix: token, prefix + "_URL": destination}):
                with self.assertRaises(error):
                    read(destination, root)
                os.environ[prefix + "_REPOSITORY_ID"] = "repo:one"
                with self.assertRaises(error):
                    read(destination, root)
                os.environ[prefix + "_CREDENTIAL_REF"] = "review"
                self.assertEqual(read(destination, root), token)
                with self.assertRaises(error):
                    read(destination, self.roots[1])

    def test_multiple_collection_trust_and_project_auth_override(self):
        root = self.roots[0]
        other = repository_host(CLONE.replace("/Collection/", "/Other/"))
        with self.assertRaises(azure.AzureError):
            azure.destination(root, other)
        self.settings[root]["user"]["azure_devops_collections"].append(other["collection_url"])
        for host in (self.host, other):
            self.assertEqual(azure.destination(root, host), (host["collection_url"], "pat"))
        self.settings[root]["project"]["azure_auth"] = "windows"
        with mock.patch.object(azure.sys, "platform", "win32"):
            self.assertEqual(azure.destination(root, other)[1], "windows")
        self.assertEqual(azure.destination(self.roots[1], self.host)[1], "pat")

    def test_explicit_empty_trust_list_revokes_legacy_trust(self):
        root = self.roots[0]
        self.settings[root]["user"].update(azure_devops_url=self.host["collection_url"], azure_devops_collections=[])
        with self.assertRaises(azure.AzureError):
            azure.destination(root, self.host)

    def test_login_requires_stable_project_identity_before_reading_a_secret(self):
        self.settings[self.roots[0]]["project"] = {}
        with mock.patch("sys.stdin.isatty", return_value=True), mock.patch.object(hub.getpass, "getpass") as prompt:
            with self.assertRaises(hub.PublishError):
                hub.credential_action("login", self.roots[0])
            prompt.assert_not_called()


if __name__ == "__main__":
    unittest.main()
