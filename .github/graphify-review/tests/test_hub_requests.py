import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "scripts"))
import hub_requests as hub
from publish_review import PublishError


class RequestClientTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.context = {
            "hub_url": "https://hub.example.invalid",
            "repository_external_id": "repo:orders",
            "workspace_headers": {},
            "credential_ref": "default",
        }
        self.item = {
            "id": str(uuid.uuid4()),
            "repository_external_id": "repo:orders",
            "status": "open",
            "revision": 2,
            "kind": "feature",
            "description": "Export orders",
        }
        self.source = {
            "commit_sha": "a" * 40,
            "snapshot_hash": "b" * 64,
            "branch": "main",
            "dirty": False,
        }
        for patch in (
            mock.patch.object(hub, "binding", return_value=self.context),
            mock.patch.object(hub, "source", return_value=self.source),
            mock.patch.object(hub, "KIT_ROOT", self.root / "kit"),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def prepare(self):
        with mock.patch.object(hub, "request_json", return_value=self.item):
            prepared = hub.prepare(self.root, self.item["id"])
        analysis = Path(prepared["analysis_file"])
        analysis.write_text(
            json.dumps(
                {
                    "project_kind": "backend",
                    "specification": "Design",
                    "new_interfaces": "POST /export",
                    "changed_interfaces": "None",
                    "breaking_changes": "None",
                }
            ),
            encoding="utf-8",
        )
        return Path(prepared["state"]), analysis

    def test_queue_pagination_scoping_and_missing_ids(self):
        missing = str(uuid.uuid4())
        responses = [{"count": 2, "results": [self.item]}, {"count": 1, "results": []}]
        with mock.patch.object(hub, "request_json", side_effect=responses) as api:
            result = hub.queue(self.root, [self.item["id"], missing])
        self.assertEqual(result["not_open_ids"], [missing])
        self.assertEqual(result["items"], [self.item])
        for call in api.call_args_list:
            self.assertIn("repository_external_id=repo%3Aorders", call.args[2])
            self.assertIn("status=open", call.args[2])
            self.assertEqual(call.kwargs["expected_hub"], self.context["hub_url"])

    def test_unrequested_foreign_nonopen_and_duplicate_items_block(self):
        for items in (
            [{**self.item, "status": "cancelled"}],
            [{**self.item, "repository_external_id": "foreign"}],
            [{**self.item, "id": str(uuid.uuid4())}],
            [self.item, self.item],
        ):
            with (
                self.subTest(items=items),
                mock.patch.object(
                    hub,
                    "request_json",
                    return_value={"count": len(items), "results": items},
                ),
                self.assertRaises(PublishError),
            ):
                hub.queue(self.root, [self.item["id"]])

    def test_filter_rejects_invalid_or_excessive_ids(self):
        for value in ("invalid", ",".join(str(uuid.uuid4()) for _ in range(101))):
            with self.assertRaises(PublishError):
                hub.identifiers(value)
        self.assertEqual(
            hub.identifiers(self.item["id"] + "," + self.item["id"]), [self.item["id"]]
        )

    def test_prepare_refuses_unassigned_or_changed_state(self):
        for change in (
            {"repository_external_id": ""},
            {"status": "analyzed"},
            {"revision": True},
        ):
            with (
                mock.patch.object(
                    hub, "request_json", return_value={**self.item, **change}
                ),
                self.assertRaises(PublishError),
            ):
                hub.prepare(self.root, self.item["id"])

    def test_submit_is_bound_and_idempotent(self):
        state, analysis = self.prepare()

        def response(root, method, path, data, **kwargs):
            self.assertEqual(method, "POST")
            self.assertEqual(
                set(data),
                {"revision", "submission_id", "repository_external_id", "analysis"},
            )
            return {
                **self.item,
                "status": "analyzed",
                "revision": 3,
                "analysis_submission_id": data["submission_id"],
                "analysis": data["analysis"],
            }

        with mock.patch.object(hub, "request_json", side_effect=response) as api:
            result = hub.submit(self.root, state, analysis)
            self.assertEqual(result["status"], "analyzed")
            hub.submit(self.root, state, analysis)
            self.assertEqual(api.call_args_list[0], api.call_args_list[1])
        analysis.write_text('{"specification": "Changed"}', encoding="utf-8")
        with (
            mock.patch.object(hub, "request_json") as api,
            self.assertRaises(PublishError),
        ):
            hub.submit(self.root, state, analysis)
        api.assert_not_called()

    def test_source_or_destination_change_blocks_before_post(self):
        state, analysis = self.prepare()
        for attribute, value in (
            ("source", {**self.source, "commit_sha": "c" * 40}),
            ("binding", {**self.context, "hub_url": "https://other.invalid"}),
            (
                "binding",
                {
                    **self.context,
                    "workspace_headers": {"X-Workspace-ID": str(uuid.uuid4())},
                },
            ),
        ):
            with (
                mock.patch.object(hub, attribute, return_value=value),
                mock.patch.object(hub, "request_json") as api,
            ):
                with self.assertRaises(PublishError):
                    hub.submit(self.root, state, analysis)
                api.assert_not_called()

    def test_uncertain_submission_preserves_payload_and_never_claims_success(self):
        state, analysis = self.prepare()
        with (
            mock.patch.object(
                hub, "request_json", side_effect=PublishError("Network error")
            ),
            self.assertRaises(PublishError),
        ):
            hub.submit(self.root, state, analysis)
        self.assertTrue(state.with_name("submission.json").exists())
        with (
            mock.patch.object(
                hub, "request_json", return_value={**self.item, "status": "specified"}
            ),
            self.assertRaises(PublishError),
        ):
            hub.submit(self.root, state, analysis)

    def test_prompt_keeps_selected_model_and_no_delegation(self):
        github = KIT.parent
        prompt = (github / "prompts/analyse-requests.prompt.md").read_text()
        agent = (github / "agents/request-analysis-manager.agent.md").read_text()
        self.assertIn("agent: Request Analysis Manager", prompt)
        self.assertIn("agents: []", agent)
        self.assertNotIn("model:", agent.split("---")[1])
        for text in (
            "material unanswered question blocks submission",
            "breaking_changes",
            "changed_interfaces",
            "new_interfaces",
            "frontend",
            "backend",
            "fullstack",
            "only that human action marks it Specified",
        ):
            self.assertIn(text, agent)


if __name__ == "__main__":
    unittest.main()
