import json
from pathlib import Path

import pytest

from hubapp.models import User
from hubapp.services import create_workspace


@pytest.fixture
def owner(db):
    return User.objects.create_user(
        username="owner@example.invalid",
        email="owner@example.invalid",
        password="test-password-long-823",
        email_verified=True,
    )


@pytest.fixture
def org(owner):
    return create_workspace(owner, "Example team")


@pytest.fixture
def envelope():
    review = json.loads((Path(__file__).parent / "review.example.json").read_text())
    source = review["review"]["repository"]
    return {
        "schema_version": "1.0",
        "repository": {
            "external_id": "repo:orders",
            "name": "Orders API",
            "default_branch": "main",
        },
        "source": {"commit_sha": source["commit_sha"], "branch": source["branch"]},
        "review": review,
        "remediations": [
            {
                "schema_version": "1.0",
                "finding_fingerprint": f["fingerprint"],
                "generated_from_commit": source["commit_sha"],
                "objective": f["recommendation"],
                "preconditions": [],
                "constraints": ["Preserve the public API."],
                "non_goals": [],
                "implementation_steps": [
                    {
                        "order": 1,
                        "instruction": f["recommendation"],
                        "affected_paths": [],
                        "affected_symbols": [],
                    }
                ],
                "acceptance_criteria": ["The regression test passes."],
                "validation_commands": ["pytest"],
                "risks_and_rollback": ["Revert the change if behavior regresses."],
            }
            for f in review["findings"]
        ],
    }


@pytest.fixture
def key(envelope):
    return f"{envelope['repository']['external_id']}:{envelope['review']['run']['run_id']}"


@pytest.fixture
def no_handoff():
    return {
        "summary": "No downstream work: this change is internal.",
        "contract": "None: no interface changes.",
        "compatibility": "No consumer migration required.",
        "availability": "unknown",
        "availability_details": "Deployment not confirmed.",
        "targets": [],
    }
