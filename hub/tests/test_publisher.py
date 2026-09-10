"""Exercise the shipped client against Django's real API, without external I/O."""

import io
import json
import urllib.error
from argparse import Namespace
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from hubapp.models import Finding, Repository, ReviewImport
from hubapp.services import create_workspace, issue_token
from hubapp.tenancy import tenant_scope


@pytest.fixture
def publisher(monkeypatch, client):
    scripts = Path(__file__).resolve().parents[2] / ".github/graphify-review/scripts"
    monkeypatch.syspath_prepend(str(scripts))
    import publish_review

    monkeypatch.setattr(
        publish_review, "load_settings", lambda _: {"user": {"hub_url": "https://hub.example.invalid"}}
    )

    class LocalAPI:
        def open(self, request, timeout):
            assert request.full_url == "https://hub.example.invalid/api/v1/review-imports"
            assert timeout == 30
            response = client.post(
                urlsplit(request.full_url).path,
                data=request.data,
                content_type=request.get_header("Content-type"),
                HTTP_AUTHORIZATION=request.get_header("Authorization"),
                HTTP_IDEMPOTENCY_KEY=request.get_header("Idempotency-key"),
            )
            body = io.BytesIO(response.content)
            if response.status_code >= 400:
                raise urllib.error.HTTPError(request.full_url, response.status_code, "Rejected", {}, body)
            body.status = response.status_code
            return body

    monkeypatch.setattr(publish_review, "opener_for", lambda *_: LocalAPI())
    return publish_review


def test_publish_creates_project_replays_and_updates_existing_project(
    publisher, monkeypatch, org, envelope, tmp_path
):
    with tenant_scope(org.id):
        token = issue_token("Publisher", ["reviews:write"], "repo:orders")
        assert Repository.objects.count() == 0
    monkeypatch.setattr(publisher, "token_for", lambda _, repository: token)
    path = tmp_path / "import-envelope.json"
    path.write_text(json.dumps(envelope), encoding="utf-8")
    first = publisher.publish(tmp_path, path, 30)
    assert first["status"] == "published"
    assert first["repository_created"] is True
    assert first["organization_id"] == str(org.id)
    assert first["created_findings"] == 4
    assert Path(first["receipt"]).is_file()
    replay = publisher.publish(tmp_path, path, 30)
    assert replay["status"] == "already_published"
    assert replay["repository_created"] is False
    assert replay["import_id"] == first["import_id"]
    with tenant_scope(org.id):
        assert Repository.objects.count() == ReviewImport.objects.count() == 1
        assert Finding.objects.count() == 4
    envelope["review"]["run"]["run_id"] = "second-run"
    path.write_text(json.dumps(envelope), encoding="utf-8")
    second = publisher.publish(tmp_path, path, 30)
    assert second["status"] == "published"
    assert second["repository_created"] is False
    assert second["repository_id"] == first["repository_id"]
    assert second["updated_findings"] == 4
    with tenant_scope(org.id):
        assert Repository.objects.count() == 1
        assert ReviewImport.objects.count() == 2


@pytest.mark.parametrize(
    "scopes, restriction", [(["findings:read"], ""), (["reviews:write"], "repo:other")]
)
def test_publisher_cannot_bypass_scope_or_project_restriction(
    publisher, monkeypatch, org, envelope, tmp_path, scopes, restriction
):
    with tenant_scope(org.id):
        token = issue_token("Restricted", scopes, restriction)
    monkeypatch.setattr(publisher, "token_for", lambda _, repository: token)
    path = tmp_path / "import-envelope.json"
    path.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(publisher.PublishError, match="Hub refused publication"):
        publisher.publish(tmp_path, path, 30)
    with tenant_scope(org.id):
        assert Repository.objects.count() == ReviewImport.objects.count() == 0
    assert not list(tmp_path.glob("publish-receipt*"))


def test_publisher_project_identity_is_scoped_to_token_workspace(
    publisher, monkeypatch, org, owner, envelope, tmp_path
):
    other = create_workspace(owner, "Other workspace")
    path = tmp_path / "import-envelope.json"
    path.write_text(json.dumps(envelope), encoding="utf-8")
    results = []
    for workspace in (org, other):
        with tenant_scope(workspace.id):
            token = issue_token("Publisher", ["reviews:write"])
        monkeypatch.setattr(publisher, "token_for", lambda _, repository, value=token: value)
        result = publisher.publish(tmp_path, path, 30)
        assert result["organization_id"] == str(workspace.id)
        assert result["repository_created"] is True
        results.append(result)
    assert results[0]["repository_id"] != results[1]["repository_id"]


def test_run_local_revalidation_export_updates_hub_resolution_without_rewriting_baseline(
    publisher, monkeypatch, org, envelope, tmp_path
):
    import export_review
    import review_settings

    settings = {
        "project": {"repository_id": "repo:orders", "repository_name": "Orders API", "default_branch": "main"},
        "user": {"hub_url": "https://hub.example.invalid"},
    }
    monkeypatch.setattr(review_settings, "load_settings", lambda _: settings)
    with tenant_scope(org.id):
        token = issue_token("Publisher", ["reviews:write"])
    monkeypatch.setattr(publisher, "token_for", lambda _, repository: token)

    def ensure(review_path):
        return export_review.ensure(Namespace(
            review=str(review_path), repository=str(tmp_path), repository_id=None, repository_name=None,
            default_branch=None, clone_url=None, source_commit=None, source_branch=None,
            output_directory=None, remediations=None, if_hub_configured=True,
        ))

    baseline = tmp_path / "baseline" / "review.json"
    baseline.parent.mkdir()
    baseline.write_text(json.dumps(envelope["review"]), encoding="utf-8")
    pending = ensure(baseline)
    Path(pending["remediations"]).write_text(json.dumps(envelope["remediations"]), encoding="utf-8")
    first_export = ensure(baseline)
    first_path = Path(first_export["envelope"])
    original_bytes = first_path.read_bytes()
    first_import = publisher.publish(tmp_path, first_path, 30)

    review = envelope["review"]
    resolved = review["findings"]
    review["findings"] = []
    review["review"]["repository"]["commit_sha"] = "a" * 40
    review["run"].update(run_id="resolved-run", mode="revalidate", finding_reconciliation=[{
        "baseline_id": finding["id"], "baseline_fingerprint": finding["fingerprint"],
        "status": "resolved", "rationale": "Verified the targeted correction against the new revision.",
        "evidence": [{"type": "source", "path": "settings.py"}],
    } for finding in resolved])
    current = tmp_path / "revalidated" / "review.json"
    current.parent.mkdir()
    current.write_text(json.dumps(review), encoding="utf-8")
    new_export = ensure(current)
    assert new_export["status"] == "exported"  # No supported findings => valid empty plans, not skipped.
    new_path = Path(new_export["envelope"])
    assert json.loads(new_path.read_text())["remediations"] == []
    assert json.loads(new_path.read_text())["review"] == review
    updated = publisher.publish(tmp_path, new_path, 30)
    assert updated["repository_id"] == first_import["repository_id"]
    assert updated["lifecycle_changes"] == len(resolved)
    with tenant_scope(org.id):
        assert Finding.objects.filter(lifecycle="resolved").count() == len(resolved)
        assert ReviewImport.objects.count() == 2
    assert ensure(current)["reused"] is True
    assert publisher.publish(tmp_path, new_path, 30)["status"] == "already_published"
    assert first_path.read_bytes() == original_bytes
