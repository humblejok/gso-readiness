# Imported pytest fixtures intentionally share names with test parameters.
# ruff: noqa: F811
import copy
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from hubapp.models import RequestImplementation
from hubapp.tenancy import tenant_scope
from tests.test_request_implementation import (  # noqa: F401
    claim,
    completion,
    post,
    specified,
    token,
)


def binding(item, **changes):
    return {
        "branch": f"feature/REQ-{item.pk}",
        "base_branch": "main",
        "commit_sha": "",
        "pull_request": "",
        "predecessor_attempt_id": "",
        **changes,
    }


def failed_predecessor(client, item, headers, org):
    current = claim(client, item, headers).json()
    body = completion(item, current, outcome="failed")
    body["commit_sha"] = "a" * 40
    assert (
        post(
            client,
            item,
            headers,
            action="complete",
            attempt_id=current["attempt_id"],
            completion=body,
        ).status_code
        == 200
    )
    with tenant_scope(org.pk):
        item.refresh_from_db()
    return current["attempt_id"]


def test_bound_normal_claim_preflight_and_precise_rejection(client, org, owner, specified):
    headers = token(org)
    contract = binding(specified)
    response = post(
        client,
        specified,
        headers,
        action="claim",
        revision=specified.revision,
        request_id=str(uuid.uuid4()),
        binding=contract,
    )
    assert response.status_code == 201
    current = response.json()
    assert current["implementation_protocol"]["version"] == 2
    assert current["implementation_binding"] == contract
    body = completion(specified, current)
    body["pull_request"] = ""
    assert (
        post(
            client,
            specified,
            headers,
            action="preflight",
            attempt_id=current["attempt_id"],
            completion=body,
        ).json()["status"]
        == "ready_for_delivery"
    )
    with tenant_scope(org.pk):
        assert RequestImplementation.objects.get(pk=current["attempt_id"]).status == "running"
    body["report"]["source"]["branch"] = "correction/not-registered"
    rejected = post(
        client,
        specified,
        headers,
        action="complete",
        attempt_id=current["attempt_id"],
        completion=body,
    )
    assert rejected.status_code == 400
    assert rejected.json()["checks"] == ["branch", "pull_request"]
    client.force_login(owner)
    page = client.get(f"/w/{org.pk}/requests/{specified.pk}/")
    assert b"Last completion rejected" in page.content
    assert b"Verify the exact source branch" in page.content
    assert str(current["attempt_id"]).encode() in page.content


def test_registered_adoption_completes_on_exact_correction_branch(client, org, specified):
    headers = token(org)
    failed = failed_predecessor(client, specified, headers, org)
    contract = binding(
        specified, branch="correction/REQ-fix", commit_sha="b" * 40, predecessor_attempt_id=failed
    )
    identity = str(uuid.uuid4())
    data = dict(action="claim", revision=specified.revision, request_id=identity, binding=contract)
    response = post(client, specified, headers, **data)
    assert response.status_code == 201
    current = response.json()
    assert post(client, specified, headers, **data).json()["attempt_id"] == current["attempt_id"]
    assert (
        post(
            client,
            specified,
            headers,
            **{**data, "binding": {**contract, "branch": "correction/other"}},
        ).status_code
        == 409
    )
    body = completion(specified, current)
    body["report"]["source"].update(branch=contract["branch"], commit_sha=contract["commit_sha"])
    body["commit_sha"] = contract["commit_sha"]
    response = post(
        client,
        specified,
        headers,
        action="complete",
        attempt_id=current["attempt_id"],
        completion=body,
    )
    assert response.status_code == 200
    with tenant_scope(org.pk):
        row = RequestImplementation.objects.get(pk=current["attempt_id"])
        assert row.data["binding"] == contract
        assert RequestImplementation.objects.get(pk=failed).status == "failed"


@pytest.mark.parametrize("expired", [False, True])
def test_resume_legacy_adoption_existing_pr_preserves_history_and_requires_fresh_run(
    client, org, specified, expired
):
    headers = token(org)
    failed = failed_predecessor(client, specified, headers, org)
    legacy = claim(client, specified, headers).json()
    body = completion(specified, legacy)
    body["report"]["source"]["branch"] = "correction/REQ-retained"
    assert (
        post(
            client,
            specified,
            headers,
            action="complete",
            attempt_id=legacy["attempt_id"],
            completion=body,
        ).status_code
        == 400
    )
    if expired:
        with tenant_scope(org.pk):
            RequestImplementation.objects.filter(pk=legacy["attempt_id"]).update(
                expires_at=timezone.now() - timedelta(seconds=1)
            )
    contract = binding(
        specified,
        branch=body["report"]["source"]["branch"],
        commit_sha=body["commit_sha"],
        pull_request=body["pull_request"],
        predecessor_attempt_id=failed,
    )
    data = dict(
        action="resume",
        revision=specified.revision,
        request_id=str(uuid.uuid4()),
        predecessor_attempt_id=legacy["attempt_id"],
        binding=contract,
        previous_completion_digest="sha256:" + "c" * 64,
    )
    response = post(client, specified, headers, **data)
    assert response.status_code == 201
    current = response.json()
    retry = post(client, specified, headers, **data)
    assert retry.status_code == 200
    assert retry.json()["attempt_id"] == current["attempt_id"]
    assert retry.json()["required_run_id"] == current["required_run_id"]
    rejected = post(
        client,
        specified,
        headers,
        action="complete",
        attempt_id=current["attempt_id"],
        completion=body,
    )
    assert rejected.json()["checks"] == ["fresh_verification"]
    fresh = copy.deepcopy(body)
    fresh["report"]["run_id"] = current["required_run_id"]
    assert (
        post(
            client,
            specified,
            headers,
            action="complete",
            attempt_id=current["attempt_id"],
            completion=fresh,
        ).status_code
        == 200
    )
    assert (
        post(
            client,
            specified,
            headers,
            action="complete",
            attempt_id=current["attempt_id"],
            completion=fresh,
        ).status_code
        == 200
    )
    with tenant_scope(org.pk):
        assert RequestImplementation.objects.count() == 3
        assert RequestImplementation.objects.get(pk=failed).status == "failed"
        assert RequestImplementation.objects.get(pk=legacy["attempt_id"]).status == (
            "expired" if expired else "cancelled"
        )
        row = RequestImplementation.objects.get(pk=current["attempt_id"])
        assert row.data["resume_of"] == legacy["attempt_id"]
        assert row.data["completion"]["pull_request"] == body["pull_request"]


@pytest.mark.parametrize("change", ["actor", "scope", "cancelled", "binding", "revision"])
def test_resume_cannot_override_authority_or_bound_work(client, org, specified, change):
    headers = token(org)
    contract = binding(specified)
    current = post(
        client,
        specified,
        headers,
        action="claim",
        revision=specified.revision,
        request_id=str(uuid.uuid4()),
        binding=contract,
    ).json()
    data = dict(
        action="resume",
        revision=specified.revision,
        request_id=str(uuid.uuid4()),
        predecessor_attempt_id=current["attempt_id"],
        binding={**contract, "commit_sha": "a" * 40},
        previous_completion_digest="",
    )
    if change == "actor":
        headers = token(org)
    elif change == "scope":
        headers = token(org, scopes=("requests:read",))
    elif change == "cancelled":
        with tenant_scope(org.pk):
            RequestImplementation.objects.filter(pk=current["attempt_id"]).update(
                status="cancelled"
            )
    elif change == "binding":
        data["binding"]["base_branch"] = "other"
    else:
        data["revision"] += 1
    assert post(client, specified, headers, **data).status_code in {403, 404, 409}
    with tenant_scope(org.pk):
        assert RequestImplementation.objects.count() == 1


def test_arbitrary_correction_without_failed_provenance_is_rejected(client, org, specified):
    headers = token(org)
    response = post(
        client,
        specified,
        headers,
        action="claim",
        revision=specified.revision,
        request_id=str(uuid.uuid4()),
        binding=binding(specified, branch="correction/arbitrary", commit_sha="a" * 40),
    )
    assert response.status_code == 400
    with tenant_scope(org.pk):
        assert not RequestImplementation.objects.exists()
