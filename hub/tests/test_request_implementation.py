import json
import uuid
from datetime import timedelta
from io import StringIO
from unittest import mock

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.utils import timezone

from hubapp import change_requests
from hubapp.models import (
    ChangeRequestActivity,
    ProjectRelationship,
    Repository,
    RequestHandoff,
    RequestImplementation,
    Subscription,
)
from hubapp.services import create_workspace, issue_token, usage
from hubapp.targeted_contract import digest
from hubapp.tenancy import tenant_scope


@pytest.fixture
def specified(org, owner):
    with tenant_scope(org.id):
        Repository.objects.create(
            external_id="repo:orders", name="Orders", clone_url="https://github.com/example/orders"
        )
        item = change_requests.create(
            kind="feature",
            description="Export orders",
            actor=owner.username,
            repository_external_id="repo:orders",
        )
        item = change_requests.submit_analysis(
            item.pk,
            revision=1,
            actor="analyzer",
            repository_external_id="repo:orders",
            submission_id=str(uuid.uuid4()),
            analysis={
                "project_kind": "backend",
                "specification": "Implement an authorized order export.",
                "new_interfaces": "POST /exports, admin only, returns 202.",
                "changed_interfaces": "None: additive endpoint.",
                "breaking_changes": "None: existing consumers unchanged.",
            },
        )
        return change_requests.update(item.pk, revision=2, actor=owner.username, action="accept")


def token(org, scopes=("requests:read", "requests:implement"), repository="repo:orders"):
    with tenant_scope(org.id):
        raw = issue_token("Implementer", list(scopes), repository)
    return {"HTTP_AUTHORIZATION": "Bearer " + raw}


def post(client, item, headers, **data):
    return client.post(
        f"/api/v1/requests/{item.pk}/implementation",
        {"repository_external_id": item.repository_external_id, **data},
        content_type="application/json",
        **headers,
    )


def claim(client, item, headers, identity=None):
    return post(
        client,
        item,
        headers,
        action="claim",
        revision=item.revision,
        request_id=identity or str(uuid.uuid4()),
    )


def completion(item, context, outcome="succeeded"):
    report = {
        "schema_version": "1.0",
        "kind": "request-verification",
        "run_id": str(uuid.uuid4()),
        "repository_external_id": item.repository_external_id,
        "baseline": {
            "request_id": str(item.pk),
            "revision": item.revision,
            "specification_digest": context["specification_digest"],
        },
        "source": {
            "commit_sha": "a" * 40,
            "branch": "feature/" + context["display_id"],
            "snapshot_hash": "sha256:" + "b" * 64,
            "dirty": False,
        },
        "result": {
            "status": "satisfied",
            "rationale": "Export is authorized and tested.",
            "reviewer": "independent-verifier",
            "evidence": [{"path": "app.py", "fact": "Admin permission guards the export."}],
            "checks": [
                {
                    "command": "pytest tests/test_exports.py",
                    "status": "passed",
                    "summary": "Authorized and denied export cases pass.",
                }
            ],
            "acceptance": [
                {
                    "criterion": "Export with admin authorization",
                    "status": "passed",
                    "evidence": "Route and regression tests inspected.",
                }
            ],
        },
    }
    return {
        "outcome": outcome,
        "comment": "Implemented and tested order export."
        if outcome == "succeeded"
        else "Required validation unavailable.",
        "report": report if outcome == "succeeded" else None,
        "pull_request": "https://github.com/example/orders/pull/1"
        if outcome == "succeeded"
        else "",
        "commit_sha": "a" * 40 if outcome == "succeeded" else "",
        "base_branch": "main",
    }


@pytest.mark.parametrize(
    "case,reason",
    [
        ("availability", "handoff_must_be_proposed"),
        ("target", "handoff_target_not_configured"),
        ("summary", "handoff_summary_missing_or_too_long"),
        ("report", "verification_contract"),
    ],
)
def test_rejected_completion_has_safe_correlated_diagnostic(
    client, org, specified, no_handoff, case, reason
):
    headers = token(org)
    context = claim(client, specified, headers).json()
    proposal = {**no_handoff, "availability": "proposed", "summary": "SECRET-HANDOFF"}
    body = {**completion(specified, context), "handoff": proposal, "comment": "SECRET-COMMENT"}
    if case == "availability":
        proposal["availability"] = "production_available"
    elif case == "target":
        proposal["targets"] = [
            {
                "repository_external_id": "SECRET-TARGET",
                "requirements": "SECRET-REQUIREMENTS",
                "acceptance_criteria": "SECRET-ACCEPTANCE",
            }
        ]
    elif case == "summary":
        proposal["summary"] = ""
    else:
        body["report"] = {"SECRET-FIELD": "SECRET-REPORT"}
    with mock.patch("hubapp.diagnostics.logger.log") as log:
        response = post(
            client,
            specified,
            headers,
            action="complete",
            attempt_id=context["attempt_id"],
            completion=body,
        )
    assert response.status_code == 400
    log.assert_called_once()
    raw = log.call_args.args[1]
    event = json.loads(raw)
    assert event["diagnostic_id"] == response["X-Hub-Request-ID"]
    assert str(uuid.UUID(event["diagnostic_id"])) == event["diagnostic_id"]
    assert event["reason"] == reason
    assert event["operation"] == "request.implementation.complete"
    assert event["attempt_id"] == context["attempt_id"]
    assert event["pk"] == str(specified.pk)
    assert "SECRET" not in raw
    assert headers["HTTP_AUTHORIZATION"] not in raw
    with tenant_scope(org.pk):
        assert RequestImplementation.objects.get(pk=context["attempt_id"]).status == "running"
        specified.refresh_from_db()
        assert specified.status == "specified"


def test_success_history_export_and_exact_retry(client, org, owner, specified, no_handoff):
    headers, identity = token(org), str(uuid.uuid4())
    first = claim(client, specified, headers, identity)
    assert first.status_code == 201
    context = first.json()
    assert claim(client, specified, headers, identity).status_code == 200
    data = completion(specified, context)
    done = post(
        client,
        specified,
        headers,
        action="complete",
        attempt_id=context["attempt_id"],
        completion=data,
    )
    assert done.status_code == 200 and done.json()["result"]["status"] == "implemented"
    assert (
        post(
            client,
            specified,
            headers,
            action="complete",
            attempt_id=context["attempt_id"],
            completion=data,
        ).json()
        == done.json()
    )
    with tenant_scope(org.id):
        specified.refresh_from_db()
        assert specified.status == "implemented" and specified.revision == 4
        assert ChangeRequestActivity.objects.filter(change_request=specified).count() == 4
        attempt = RequestImplementation.objects.get(pk=context["attempt_id"])
        assert digest(attempt.specification) == context["specification_digest"]
        change_requests.update(
            specified.pk,
            revision=4,
            actor=owner.username,
            action="edit_handoff",
            handoff=no_handoff,
        )
        change_requests.update(
            specified.pk,
            revision=5,
            actor=owner.username,
            action="transition",
            status="closed",
            handoff_reviewed=True,
        )
    assert (
        post(
            client,
            specified,
            headers,
            action="complete",
            attempt_id=context["attempt_id"],
            completion=data,
        ).json()
        == done.json()
    )
    client.force_login(owner)
    assert (
        b"Recent implementation attempts"
        in client.get(f"/w/{org.id}/requests/{specified.pk}/").content
    )
    export = b"".join(client.get(f"/w/{org.id}/export/").streaming_content)
    assert b'"request_implementations"' in export and b"not merged or deployed" in export


def test_worker_only_saves_proposal_human_closure_publishes(
    client, org, owner, specified, no_handoff
):
    with tenant_scope(org.pk):
        source = Repository.objects.get(external_id="repo:orders")
        for name in ("web", "mobile"):
            target = Repository.objects.create(external_id="repo:" + name, name=name)
            ProjectRelationship.objects.create(
                source=source, target=target, description="Consumes orders"
            )
    headers = token(org)
    context = claim(client, specified, headers).json()
    assert len(context["related_projects"]) == 2
    proposal = {
        **no_handoff,
        "summary": "Consumers should expose order export.",
        "availability": "proposed",
        "targets": [
            {
                "repository_external_id": "repo:" + name,
                "requirements": "Expose export action.",
                "acceptance_criteria": "Test permissions and returned data.",
            }
            for name in ("web", "mobile")
        ],
    }
    data = {**completion(specified, context), "handoff": proposal}
    bad = {**data, "handoff": {**proposal, "availability": "production_available"}}
    assert (
        post(
            client,
            specified,
            headers,
            action="complete",
            attempt_id=context["attempt_id"],
            completion=bad,
        ).status_code
        == 400
    )
    response = post(
        client,
        specified,
        headers,
        action="complete",
        attempt_id=context["attempt_id"],
        completion=data,
    )
    assert response.status_code == 200
    with tenant_scope(org.pk):
        specified.refresh_from_db()
        assert specified.status == "implemented" and not RequestHandoff.objects.exists()
        assert specified.handoff["implementation"]["commit_sha"] == data["commit_sha"]
        assert specified.handoff["implementation"]["pull_request"] == data["pull_request"]
        with pytest.raises(ValidationError) as error:
            change_requests.update(
                specified.pk,
                revision=specified.revision,
                actor=owner.username,
                action="edit_handoff",
                handoff={
                    **specified.handoff,
                    "implementation": {
                        "commit_sha": "f" * 40,
                        "pull_request": data["pull_request"],
                    },
                },
            )
        assert "verified implementation" in str(error.value)
        closed = change_requests.update(
            specified.pk,
            revision=specified.revision,
            actor=owner.username,
            action="transition",
            status="closed",
            handoff_reviewed=True,
        )
        assert closed.status == "closed" and RequestHandoff.objects.count() == 2
    assert (
        post(
            client,
            specified,
            headers,
            action="complete",
            attempt_id=context["attempt_id"],
            completion=data,
        ).json()
        == response.json()
    )
    with tenant_scope(org.pk):
        assert RequestHandoff.objects.count() == 2


@pytest.mark.parametrize(
    "mutation",
    [
        "dirty",
        "revision",
        "digest",
        "commit",
        "branch",
        "project",
        "failed_check",
        "acceptance",
        "inconclusive",
        "foreign_pr",
        "build_receipt",
        "receipt_result",
        "raw_result",
    ],
)
def test_incomplete_or_foreign_evidence_never_implements(client, org, specified, mutation):
    headers = token(org)
    context = claim(client, specified, headers).json()
    data = completion(specified, context)
    report = data["report"]
    if mutation == "dirty":
        report["source"]["dirty"] = True
    if mutation == "revision":
        report["baseline"]["revision"] += 1
    if mutation == "digest":
        report["baseline"]["specification_digest"] = "sha256:" + "c" * 64
    if mutation == "commit":
        data["commit_sha"] = "c" * 40
    if mutation == "branch":
        report["source"]["branch"] = "main"
    if mutation == "project":
        report["repository_external_id"] = "repo:other"
    if mutation == "failed_check":
        report["result"]["checks"][0]["status"] = "failed"
    if mutation == "acceptance":
        report["result"]["acceptance"][0]["status"] = "unavailable"
    if mutation == "inconclusive":
        report["result"]["status"] = "inconclusive"
    if mutation == "foreign_pr":
        data["pull_request"] = "https://github.com/foreign/repo/pull/1"
    if mutation == "build_receipt":
        data["report"] = {
            "status": "verified",
            "outcome": "satisfied",
            "envelope": "do-not-follow.json",
        }
    if mutation == "receipt_result":
        report["result"] = {"status": "verified", "outcome": "satisfied"}
    if mutation == "raw_result":
        data["report"] = report["result"]
    assert (
        post(
            client,
            specified,
            headers,
            action="complete",
            attempt_id=context["attempt_id"],
            completion=data,
        ).status_code
        == 400
    )
    with tenant_scope(org.id):
        specified.refresh_from_db()
        assert specified.status == "specified" and specified.revision == 3


@pytest.mark.parametrize("action", ["cancel", "edit_analysis", "edit"])
def test_human_change_invalidates_claim(client, org, owner, specified, action):
    headers = token(org)
    context = claim(client, specified, headers).json()
    data = completion(specified, context)
    with tenant_scope(org.id):
        extra = {}
        if action == "edit_analysis":
            extra["analysis"] = {
                **specified.analysis,
                "specification": "A revised design needs acceptance.",
            }
        if action == "edit":
            extra.update(kind="bug", description="Different request")
        change_requests.update(
            specified.pk, revision=3, actor=owner.username, action=action, **extra
        )
        assert RequestImplementation.objects.get(pk=context["attempt_id"]).status == "cancelled"
    assert (
        post(
            client,
            specified,
            headers,
            action="complete",
            attempt_id=context["attempt_id"],
            completion=data,
        ).status_code
        == 409
    )


def test_claim_exclusivity_expiry_and_failure_release(client, org, specified):
    headers = token(org)
    context = claim(client, specified, headers).json()
    assert claim(client, specified, headers).status_code == 409
    data = completion(specified, context, "failed")
    response = post(
        client,
        specified,
        headers,
        action="complete",
        attempt_id=context["attempt_id"],
        completion=data,
    )
    assert response.status_code == 200 and response.json()["result"]["status"] == "specified"
    with tenant_scope(org.id):
        specified.refresh_from_db()
    second = claim(client, specified, headers)
    assert second.status_code == 201
    with tenant_scope(org.id):
        RequestImplementation.objects.filter(pk=second.json()["attempt_id"]).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
    assert (
        post(
            client,
            specified,
            headers,
            action="complete",
            attempt_id=second.json()["attempt_id"],
            completion=completion(specified, second.json(), "failed"),
        ).status_code
        == 409
    )
    assert claim(client, specified, headers).status_code == 201


def test_expired_recovery_status_history_and_idempotence(client, org, specified):
    headers = token(org)
    old = claim(client, specified, headers).json()
    url = f"/api/v1/requests/{specified.pk}/implementation?attempt_id={old['attempt_id']}"
    status = client.get(url, **headers).json()["claim"]
    assert status["expired"] is False and status["owned_by_caller"] is True
    identity = str(uuid.uuid4())
    body = dict(
        action="recover",
        revision=specified.revision,
        request_id=identity,
        predecessor_attempt_id=old["attempt_id"],
    )
    assert post(client, specified, headers, **body).status_code == 409
    with tenant_scope(org.id):
        prior = RequestImplementation.objects.get(pk=old["attempt_id"])
        prior.expires_at = timezone.now() - timedelta(seconds=1)
        prior.save(update_fields=["expires_at"])
        frozen = prior.specification.copy()
    assert client.get(url, **headers).json()["claim"]["expired"] is True
    recovered = post(client, specified, headers, **body)
    assert recovered.status_code == 201
    assert post(client, specified, headers, **body).status_code == 200
    assert recovered.json()["attempt_id"] != old["attempt_id"]
    assert (
        post(client, specified, headers, **{**body, "request_id": str(uuid.uuid4())}).status_code
        == 409
    )
    with tenant_scope(org.id):
        prior.refresh_from_db()
        assert prior.status == "expired" and prior.specification == frozen
        assert prior.comment == "" and prior.data == {}
    new = recovered.json()
    done = post(
        client,
        specified,
        headers,
        action="complete",
        attempt_id=new["attempt_id"],
        completion=completion(specified, new),
    )
    assert done.status_code == 200
    with tenant_scope(org.id):
        successor = RequestImplementation.objects.get(pk=new["attempt_id"])
        assert successor.data["recovery_of"] == old["attempt_id"]


def test_claim_status_is_read_only_scoped_and_recovery_needs_write_scope(client, org, specified):
    headers = token(org)
    old = claim(client, specified, headers).json()
    url = f"/api/v1/requests/{specified.pk}/implementation?attempt_id={old['attempt_id']}"
    reader = token(org, scopes=("requests:read",))
    result = client.get(url, **reader)
    assert result.status_code == 200 and result.json()["claim"]["owned_by_caller"] is False
    assert client.get(url, **token(org, repository="repo:other")).status_code == 404
    assert (
        client.get(url.replace(old["attempt_id"], str(uuid.uuid4())), **headers).status_code == 404
    )
    assert client.get(url.replace(old["attempt_id"], "invalid"), **headers).status_code == 400
    response = post(
        client,
        specified,
        reader,
        action="recover",
        revision=specified.revision,
        request_id=str(uuid.uuid4()),
        predecessor_attempt_id=old["attempt_id"],
    )
    assert response.status_code == 403
    with tenant_scope(org.id):
        assert RequestImplementation.objects.count() == 1
        assert RequestImplementation.objects.get().status == "running"


@pytest.mark.parametrize(
    "change", ["cancelled", "failed", "succeeded", "different_actor", "different_revision"]
)
def test_expired_recovery_rejects_invalid_predecessor(client, org, specified, change):
    headers = token(org)
    old = claim(client, specified, headers).json()
    with tenant_scope(org.id):
        patch = {"expires_at": timezone.now() - timedelta(seconds=1)}
        if change in {"cancelled", "failed", "succeeded"}:
            patch["status"] = change
        elif change == "different_revision":
            patch["revision"] = 1
        RequestImplementation.objects.filter(pk=old["attempt_id"]).update(**patch)
    if change == "different_actor":
        headers = token(org)
    result = post(
        client,
        specified,
        headers,
        action="recover",
        revision=specified.revision,
        request_id=str(uuid.uuid4()),
        predecessor_attempt_id=old["attempt_id"],
    )
    assert result.status_code in {404, 409}


def test_write_entitlement_quota_and_erasure(client, org, specified, settings):
    headers = token(org)
    with tenant_scope(org.id):
        before = usage()["storage_bytes"]
        Subscription.objects.update(valid_until=timezone.now() - timedelta(days=1))
    assert claim(client, specified, headers).status_code == 403
    with tenant_scope(org.id):
        Subscription.objects.update(valid_until=timezone.now() + timedelta(days=1))
    original = settings.PLANS
    settings.PLANS = {key: {**value, "storage_mb": 0} for key, value in original.items()}
    assert claim(client, specified, headers).status_code == 400
    with tenant_scope(org.id):
        assert not RequestImplementation.objects.exists()
    settings.PLANS = original
    context = claim(client, specified, headers).json()
    with tenant_scope(org.id):
        assert usage()["storage_bytes"] > before
        Subscription.objects.update(valid_until=timezone.now() - timedelta(days=1))
    assert (
        post(
            client,
            specified,
            headers,
            action="complete",
            attempt_id=context["attempt_id"],
            completion=completion(specified, context),
        ).status_code
        == 403
    )
    org.deletion_requested_at = timezone.now() - timedelta(days=8)
    org.save(update_fields=["deletion_requested_at"])
    output = StringIO()
    call_command(
        "erase_workspace",
        str(org.pk),
        execute=True,
        confirm=str(org.pk),
        operator="test",
        stdout=output,
    )
    assert "ERASURE RECEIPT" in output.getvalue()
    assert not RequestImplementation.all_objects.filter(organization_id=org.pk).exists()


def test_scope_actor_project_tenant_and_approval_boundaries(client, org, owner, specified):
    for scopes in [
        ("requests:read",),
        ("requests:implement",),
        ("requests:read", "requests:analyse"),
    ]:
        assert claim(client, specified, token(org, scopes)).status_code == 403
    assert claim(client, specified, token(org, repository="repo:other")).status_code == 404
    other = create_workspace(owner, "Other")
    assert claim(client, specified, token(other)).status_code == 404
    headers = token(org)
    identity = str(uuid.uuid4())
    context = claim(client, specified, headers, identity).json()
    different_actor = token(org)
    assert claim(client, specified, different_actor, identity).status_code == 409
    assert (
        post(
            client,
            specified,
            different_actor,
            action="complete",
            attempt_id=context["attempt_id"],
            completion=completion(specified, context, "failed"),
        ).status_code
        == 404
    )
    with tenant_scope(org.id):
        change_requests.update(
            specified.pk,
            revision=3,
            actor=owner.username,
            action="edit_analysis",
            analysis={**specified.analysis, "specification": "Not yet reaccepted"},
        )
        specified.refresh_from_db()
    assert claim(client, specified, headers).status_code == 409
