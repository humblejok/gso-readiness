import copy
import json
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from hubapp import finding_work
from hubapp.imports import import_review
from hubapp.models import Finding, FindingActivity, Membership, Observation, ReviewImport
from hubapp.services import create_workspace, issue_token
from hubapp.targeted_contract import digest
from hubapp.tenancy import tenant_scope


@pytest.fixture
def finding(org, envelope, key):
    with tenant_scope(org.id):
        import_review(envelope, key)
        return Finding.objects.get(fingerprint=envelope["review"]["findings"][0]["fingerprint"])


def report_for(finding, envelope, status="resolved"):
    original = envelope["review"]
    return {
        "schema_version": "1.0",
        "kind": "finding-revalidation",
        "run_id": str(uuid.uuid4()),
        "repository_external_id": envelope["repository"]["external_id"],
        "baseline": {
            "run_id": original["run"]["run_id"],
            "review_digest": digest(original),
            "finding_id": finding.display_id,
            "fingerprint": finding.fingerprint,
        },
        "source": {
            "commit_sha": "a" * 40,
            "branch": "feature/" + finding.display_id,
            "snapshot_hash": "sha256:" + "b" * 64,
            "dirty": False,
        },
        "result": {
            "status": status,
            "rationale": "The scoped configuration defect was corrected and verified.",
            "reviewer": "independent-verifier",
            "evidence": [
                {
                    "path": "settings.py",
                    "fact": "The unsafe configuration is absent in the effective startup path.",
                }
            ],
            "checks": [
                {
                    "command": "pytest tests/test_settings.py",
                    "status": "passed",
                    "summary": "Targeted regression passed.",
                }
            ],
        },
        "not_revalidated": sorted(
            f["fingerprint"]
            for group in ("findings", "inconclusive_findings", "rejected_findings")
            for f in original[group]
            if f["fingerprint"] != finding.fingerprint
        ),
    }


def complete_body(report, outcome="succeeded"):
    return {
        "outcome": outcome,
        "comment": "Corrected the configuration and added regression coverage.",
        "report": report,
        "pull_request": "https://github.example.invalid/team/orders/pull/1"
        if outcome == "succeeded"
        else "",
        "commit_sha": "a" * 40 if outcome == "succeeded" else "",
        "base_branch": "main",
    }


def queued_claim(finding, actor="worker"):
    finding = finding_work.configure(
        finding.id, "implement", finding.implementation_revision, "owner"
    )
    claim, _ = finding_work.claim(finding.id, finding.implementation_revision, uuid.uuid4(), actor)
    return finding, claim


def test_dashboard_default_filters_compact_controls_and_search(client, owner, org, finding):
    client.force_login(owner)
    page = client.get(f"/w/{org.id}/")
    assert len(page.context["findings"]) == 2
    assert b"Show rejected and inconclusive" in page.content
    assert b"button compact" in page.content
    assert len(client.get(f"/w/{org.id}/?show_all=1").context["findings"]) == 4
    assert len(client.get(f"/w/{org.id}/?q={finding.display_id}").context["findings"]) == 1
    assert len(client.get(f"/w/{org.id}/?lifecycle=inconclusive").context["findings"]) == 1


def test_queue_edit_cancel_ui_preserves_imported_plan(client, owner, org, finding):
    client.force_login(owner)
    url = f"/w/{org.id}/findings/{finding.id}/"
    with tenant_scope(org.id):
        original = Observation.objects.get(finding=finding).remediation
    response = client.post(
        url + "implementation/",
        {"action": "implement", "revision": finding.implementation_revision, "return": "detail"},
    )
    assert response.status_code == 302 and response.url == url
    with tenant_scope(org.id):
        finding.refresh_from_db()
        revision = finding.implementation_revision
    assert b"Save remediation" in client.get(url).content
    assert (
        client.post(
            url,
            {
                "revision": revision,
                "remediation": "Change only the affected startup configuration. Add a regression check.",
            },
        ).status_code
        == 302
    )
    with tenant_scope(org.id):
        finding.refresh_from_db()
        assert finding.implementation_text.startswith("Change only")
        assert Observation.objects.get(finding=finding).remediation == original
        revision = finding.implementation_revision
    assert (
        client.post(url + "implementation/", {"action": "cancel", "revision": revision}).status_code
        == 302
    )
    with tenant_scope(org.id):
        finding.refresh_from_db()
        assert not finding.implementation_requested and finding.lifecycle == "open"
    assert b"Save remediation" not in client.get(url).content
    assert b"Change only" in client.get(url).content


def test_viewer_cross_tenant_and_get_cannot_queue(client, owner, org, finding):
    client.force_login(owner)
    Membership.objects.filter(user=owner, organization=org).update(role="viewer")
    url = f"/w/{org.id}/findings/{finding.id}/implementation/"
    assert client.post(url, {"action": "implement", "revision": 1}).status_code == 403
    assert client.get(url).status_code == 405
    other = create_workspace(owner, "Other workspace")
    assert (
        client.post(
            f"/w/{other.id}/findings/{finding.id}/implementation/",
            {"action": "implement", "revision": 1},
        ).status_code
        == 404
    )


def test_claim_exclusive_edit_invalidates_worker_and_stale_revision(org, finding):
    with tenant_scope(org.id):
        queued, attempt = queued_claim(finding)
        replay, created = finding_work.claim(
            finding.id, queued.implementation_revision, attempt.request_id, "worker"
        )
        assert replay.id == attempt.id and not created
        with pytest.raises(finding_work.WorkConflict):
            finding_work.claim(
                finding.id, queued.implementation_revision, uuid.uuid4(), "other-worker"
            )
        changed = finding_work.configure(
            finding.id, "edit", queued.implementation_revision, "owner", "Updated approved proposal"
        )
        attempt.refresh_from_db()
        assert attempt.status == "cancelled"
        with pytest.raises(finding_work.WorkConflict):
            finding_work.configure(finding.id, "cancel", queued.implementation_revision, "owner")
        assert changed.implementation_requested


def test_success_only_changes_selected_finding_after_verified_pr(org, finding, envelope):
    with tenant_scope(org.id):
        original_import = ReviewImport.objects.get().payload
        before = list(Finding.objects.exclude(pk=finding.pk).values())
        queued, attempt = queued_claim(finding)
        body = complete_body(report_for(finding, envelope))
        result = finding_work.complete(finding.id, attempt.id, body, "worker")
        assert result["result"] == {"lifecycle": "resolved", "to_implement": False}
        assert "not yet merged" in result["comment"]
        assert finding_work.complete(finding.id, attempt.id, body, "worker") == result
        assert list(Finding.objects.exclude(pk=finding.pk).values()) == before
        assert ReviewImport.objects.get().payload == original_import
        assert FindingActivity.objects.count() == 1
        assert Observation.objects.count() == 4  # Not a fabricated full-review observation.


@pytest.mark.parametrize(
    "mutation",
    [
        lambda b: b.update(pull_request=""),
        lambda b: b["report"]["source"].update(dirty=True),
        lambda b: b["report"]["source"].update(branch="main"),
        lambda b: b["report"]["source"].update(commit_sha="f" * 40),
        lambda b: b["report"]["result"].update(status="not_reproduced"),
        lambda b: b["report"]["result"]["checks"][0].update(status="unavailable"),
        lambda b: b["report"].update(not_revalidated=[]),
        lambda b: b.update(pull_request="javascript:alert(1)"),
    ],
)
def test_incomplete_success_does_not_resolve_or_remove_queue(org, finding, envelope, mutation):
    with tenant_scope(org.id):
        queued, attempt = queued_claim(finding)
        body = complete_body(report_for(finding, envelope))
        mutation(body)
        with pytest.raises((finding_work.ValidationError, finding_work.WorkConflict)):
            finding_work.complete(finding.id, attempt.id, body, "worker")
        finding.refresh_from_db()
        assert finding.lifecycle == "open" and finding.implementation_requested
        attempt.refresh_from_db()
        assert attempt.status == "running"


def test_failure_keeps_open_queued_and_adds_reason(org, finding):
    with tenant_scope(org.id):
        _, attempt = queued_claim(finding)
        body = complete_body(None, "failed")
        body["comment"] = "Targeted regression still fails; branch retained for inspection."
        result = finding_work.complete(finding.id, attempt.id, body, "worker")
        assert result["result"] == {"lifecycle": "open", "to_implement": True}
        assert result["comment"] == body["comment"]
        assert finding_work.complete(finding.id, attempt.id, body, "worker") == result
        body["comment"] = "Different reason"
        with pytest.raises(finding_work.WorkConflict):
            finding_work.complete(finding.id, attempt.id, body, "worker")


def test_failed_attempt_can_be_reclaimed_without_rewriting_failure(org, finding, envelope):
    with tenant_scope(org.id):
        _, first = queued_claim(finding)
        failed = complete_body(None, "failed")
        finding_work.complete(finding.id, first.id, failed, "worker")
        first.refresh_from_db()
        old_data, old_comment = first.data.copy(), first.comment
        finding.refresh_from_db()
        successor, created = finding_work.claim(
            finding.id, finding.implementation_revision, uuid.uuid4(), "worker"
        )
        assert created and successor.id != first.id
        replay, created = finding_work.claim(
            finding.id, successor.revision, successor.request_id, "worker"
        )
        assert not created and replay.id == successor.id
        report = report_for(finding, envelope)
        report["result"]["rationale"] += " Optional Sonar unavailable; full Quality Gate not verified."
        finding_work.complete(finding.id, successor.id, complete_body(report), "worker")
        first.refresh_from_db()
        assert first.status == "failed" and first.data == old_data and first.comment == old_comment
        finding.refresh_from_db()
        assert finding.lifecycle == "resolved" and not finding.implementation_requested


def test_expired_claim_and_imported_baseline_invalidate_worker(org, finding, envelope):
    with tenant_scope(org.id):
        queued, first = queued_claim(finding)
        first.expires_at = timezone.now() - timedelta(seconds=1)
        first.save()
        second, _ = finding_work.claim(
            finding.id, queued.implementation_revision, uuid.uuid4(), "worker"
        )
        first.refresh_from_db()
        assert first.status == "failed"
        newer = copy.deepcopy(envelope)
        newer["review"]["run"]["run_id"] = "new-run"
        import_review(newer, "repo:orders:new-run")
        second.refresh_from_db()
        assert second.status == "cancelled"
        with pytest.raises(finding_work.WorkConflict):
            finding_work.complete(
                finding.id, second.id, complete_body(report_for(finding, envelope)), "worker"
            )


def test_standalone_targeted_revalidation_does_not_touch_grades_or_others(org, finding, envelope):
    with tenant_scope(org.id):
        before = list(Finding.objects.exclude(pk=finding.pk).values())
        original = ReviewImport.objects.get().payload
        report = report_for(finding, envelope)
        result = finding_work.revalidate(
            finding.id, finding.implementation_revision, report, "review-token"
        )
        assert result["result"]["lifecycle"] == "resolved"
        assert finding_work.revalidate(finding.id, 0, report, "review-token") == result
        assert list(Finding.objects.exclude(pk=finding.pk).values()) == before
        assert ReviewImport.objects.get().payload == original


def test_api_scopes_repository_filter_and_claim_completion(client, org, finding):
    with tenant_scope(org.id):
        finding = finding_work.configure(
            finding.id, "implement", finding.implementation_revision, "owner"
        )
        reader = issue_token("reader", ["findings:read"])
        worker = issue_token("worker", ["findings:read", "findings:implement"], "repo:orders")
        other = issue_token(
            "other repository", ["findings:read", "findings:implement"], "repo:other"
        )
    url = f"/api/v1/findings/{finding.id}/implementation"
    body = {
        "action": "claim",
        "revision": finding.implementation_revision,
        "request_id": str(uuid.uuid4()),
    }

    def post(token, value):
        return client.post(
            url,
            json.dumps(value),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + token,
        )

    assert post(reader, body).status_code == 403
    assert post(other, body).status_code == 404
    response = post(worker, body)
    assert response.status_code == 201, response.content
    assert post(worker, body).status_code == 200
    queue = client.get(
        "/api/v1/findings?repository_external_id=repo:orders&to_implement=true&ids="
        + finding.display_id,
        HTTP_AUTHORIZATION="Bearer " + worker,
    ).json()
    assert queue["count"] == 1
    completion = {
        "action": "complete",
        "attempt_id": response.json()["attempt_id"],
        "completion": complete_body(None, "failed"),
    }
    assert post(worker, completion).status_code == 200


def test_new_activity_contract_copies_match():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    assert (root / ".github/graphify-review/scripts/targeted_contract.py").read_bytes() == (
        root / "hub/hubapp/targeted_contract.py"
    ).read_bytes()
    assert (root / ".github/graphify-review/scripts/git_host_contract.py").read_bytes() == (
        root / "hub/hubapp/git_host_contract.py"
    ).read_bytes()


def test_azure_pr_success_is_recorded_and_other_repositories_are_refused(org, finding, envelope):
    with tenant_scope(org.id):
        clone = "https://ado.example.invalid/tfs/Collection/Project%20Name/_git/repo"
        finding.repository.clone_url = clone
        finding.repository.save()
        _, attempt = queued_claim(finding)
        body = complete_body(report_for(finding, envelope))
        for url in (clone.replace("/repo", "/other") + "/pullrequest/7", clone + "/pull/7", clone + "/pullrequest/7?secret=x"):
            body["pull_request"] = url
            with pytest.raises(finding_work.ValidationError):
                finding_work.complete(finding.id, attempt.id, body, "worker")
        body["pull_request"] = clone + "/pullrequest/7"
        result = finding_work.complete(finding.id, attempt.id, body, "worker")
        assert result["result"] == {"lifecycle": "resolved", "to_implement": False}
        assert "not yet merged" in result["comment"]
        assert finding_work.complete(finding.id, attempt.id, body, "worker") == result


def test_documented_targeted_schema_accepts_result_and_rejects_fake_grade(finding, envelope):
    from pathlib import Path

    from jsonschema import Draft202012Validator, ValidationError

    schema = json.loads(
        (
            Path(__file__).resolve().parents[1] / "contracts/targeted-revalidation.schema.json"
        ).read_text()
    )
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    report = report_for(finding, envelope)
    validator.validate(report)
    report["grades"] = {"score": 10}
    with pytest.raises(ValidationError):
        validator.validate(report)


def test_success_pr_must_match_configured_repository(org, finding, envelope):
    with tenant_scope(org.id):
        finding.repository.clone_url = "https://github.example.invalid/team/another-project.git"
        finding.repository.save()
        _, attempt = queued_claim(finding)
        with pytest.raises(finding_work.ValidationError):
            finding_work.complete(
                finding.id, attempt.id, complete_body(report_for(finding, envelope)), "worker"
            )


def test_cancelled_worker_cannot_complete_and_claim_cannot_be_bypassed(org, finding, envelope):
    with tenant_scope(org.id):
        queued, attempt = queued_claim(finding)
        report = report_for(finding, envelope)
        with pytest.raises(finding_work.WorkConflict):
            finding_work.revalidate(
                finding.id, queued.implementation_revision, report, "review-token"
            )
        finding_work.configure(finding.id, "cancel", queued.implementation_revision, "owner")
        with pytest.raises(finding_work.WorkConflict):
            finding_work.complete(finding.id, attempt.id, complete_body(report), "worker")
        finding.refresh_from_db()
        assert finding.lifecycle == "open" and not finding.implementation_requested


def test_activity_storage_counted_and_over_quota_rolls_back(org, finding, envelope, monkeypatch):
    with tenant_scope(org.id):
        _, attempt = queued_claim(finding)
        assert finding_work.usage()["storage_bytes"] >= attempt.payload_bytes > 0
        monkeypatch.setattr(finding_work, "usage", lambda: {"storage_bytes": 10**15})
        with pytest.raises(finding_work.ValidationError):
            finding_work.complete(
                finding.id, attempt.id, complete_body(report_for(finding, envelope)), "worker"
            )
        finding.refresh_from_db()
        attempt.refresh_from_db()
        assert finding.lifecycle == "open" and finding.implementation_requested
        assert attempt.status == "running"


def test_standalone_api_requires_write_scope_and_rejects_stale_revision(
    client, org, finding, envelope
):
    with tenant_scope(org.id):
        reader = issue_token("reader", ["findings:read"])
        writer = issue_token("writer", ["reviews:write"], "repo:orders")
    url = f"/api/v1/findings/{finding.id}/revalidation"
    body = {"revision": finding.implementation_revision, "report": report_for(finding, envelope)}

    def post(token):
        return client.post(
            url,
            json.dumps(body),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + token,
        )

    assert post(reader).status_code == 403
    body["revision"] -= 1
    assert post(writer).status_code == 409
    body["revision"] += 1
    assert post(writer).status_code == 200
    assert post(writer).status_code == 200


def test_export_preserves_queue_drafts_and_activity(client, owner, org, finding):
    with tenant_scope(org.id):
        queued, attempt = queued_claim(finding)
        finding_work.complete(finding.id, attempt.id, complete_body(None, "failed"), "worker")
        finding.refresh_from_db()
        finding_work.configure(
            finding.id,
            "edit",
            finding.implementation_revision,
            "owner",
            "Human-approved updated remediation",
        )
    client.force_login(owner)
    response = client.get(f"/w/{org.id}/export/")
    exported = json.loads(b"".join(response.streaming_content))
    assert len(exported["imports"]) == 1
    work = next(row for row in exported["finding_work"] if row["finding_id"] == str(finding.id))
    assert (
        work["to_implement"] and work["remediation_draft"] == "Human-approved updated remediation"
    )
    assert exported["finding_activities"][0]["status"] == "failed"
