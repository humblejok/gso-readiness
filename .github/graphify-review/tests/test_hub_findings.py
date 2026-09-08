import io
import sys
import unittest
import urllib.error
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hub_findings as hub  # noqa: E402
from publish_review import PublishError  # noqa: E402


class HubFindingClientTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(".").resolve()
        self.url = "https://hub.example.invalid"
        patches = [mock.patch.object(hub, "configured_hub", return_value=self.url),
                   mock.patch.object(hub, "project_id", return_value="repo:orders")]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def item(self, identifier="ARCH-C001"):
        return {"id": str(uuid.uuid4()), "display_id": identifier, "to_implement": True, "lifecycle": "open"}

    def test_queue_paginates_with_project_and_filter_and_reports_missing(self):
        item = self.item()
        with mock.patch.object(hub, "request_json", side_effect=[{"count": 2, "results": [item]}, {"count": 2, "results": []}]) as request:
            result = hub.queue(self.root, ["ARCH-C001", "COR-C002"])
        self.assertEqual(result["not_queued_ids"], ["COR-C002"])
        self.assertEqual(len(result["items"]), 1)
        for call in request.call_args_list:
            self.assertIn("repository_external_id=repo%3Aorders", call.args[2])
            self.assertIn("to_implement=true", call.args[2])
            self.assertIn("ids=ARCH-C001%2CCOR-C002", call.args[2])
            self.assertEqual(call.kwargs["expected_hub"], self.url)

    def test_queue_refuses_ambiguous_or_unrequested_or_nonqueued_items(self):
        cases = [[self.item(), self.item()], [self.item("COR-C002")], [{**self.item(), "to_implement": False}]]
        for items in cases:
            with self.subTest(items=items), mock.patch.object(hub, "request_json", return_value={"count": len(items), "results": items}):
                with self.assertRaises(PublishError):
                    hub.queue(self.root, ["ARCH-C001"])

    def test_changed_destination_fails_before_credentials_are_loaded(self):
        with mock.patch.object(hub, "token_for") as token:
            with self.assertRaises(PublishError):
                hub.request_json(self.root, "POST", "/api/v1/findings", expected_hub="https://other.example.invalid")
            token.assert_not_called()

    def test_http_errors_do_not_expose_server_body_or_token(self):
        for status in (302, 400, 401, 403, 409, 500):
            error = urllib.error.HTTPError(self.url, status, "SENSITIVE-MESSAGE", {}, io.BytesIO(b"SECRET-SERVER-BODY"))
            opener = mock.Mock()
            opener.open.side_effect = error
            with mock.patch.object(hub, "token_for", return_value="SYNTHETIC-TOKEN"), mock.patch.object(hub, "opener_for", return_value=opener):
                with self.assertRaises(PublishError) as raised:
                    hub.request_json(self.root, "GET", "/api/v1/findings")
                self.assertNotIn("SENSITIVE", str(raised.exception))
                self.assertNotIn("SECRET", str(raised.exception))
                self.assertNotIn("SYNTHETIC", str(raised.exception))
                self.assertEqual(opener.open.call_count, 1)

    def test_context_refuses_other_project(self):
        identifier = str(uuid.uuid4())
        with mock.patch.object(hub, "request_json", return_value={"finding_id": identifier, "repository_external_id": "repo:other"}):
            with self.assertRaises(PublishError):
                hub.context(self.root, identifier)


if __name__ == "__main__":
    unittest.main()
