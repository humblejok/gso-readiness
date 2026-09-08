"""Exercise the installable kit CLI against the actual Hub import service."""

import json
import subprocess
import sys
from pathlib import Path

from hubapp.imports import import_review
from hubapp.tenancy import tenant_scope


def test_cli_export_imports_and_replays_unchanged(tmp_path, org, envelope):
    script = (
        Path(__file__).resolve().parents[2]
        / ".github/graphify-review/scripts/export_review.py"
    )
    review_path = tmp_path / "review.json"
    plans_path = tmp_path / "plans.json"
    review_path.write_text(json.dumps(envelope["review"]), encoding="utf-8")
    plans_path.write_text(json.dumps(envelope["remediations"]), encoding="utf-8")
    output = tmp_path / "export"
    subprocess.run(
        [
            sys.executable, "-B", str(script), "prepare", "--review", str(review_path),
            "--repository-id", envelope["repository"]["external_id"],
            "--repository-name", envelope["repository"]["name"],
            "--default-branch", "main", "--output-directory", str(output),
        ],
        check=True, capture_output=True, encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable, "-B", str(script), "build", "--request",
            str(output / "export-request.json"), "--remediations", str(plans_path),
        ],
        check=True, capture_output=True, encoding="utf-8",
    )
    metadata = json.loads(result.stdout)
    exported = json.loads(Path(metadata["envelope"]).read_text(encoding="utf-8"))
    assert exported == envelope
    with tenant_scope(org.id):
        imported, created = import_review(exported, metadata["idempotency_key"])
        assert created
        replay, created = import_review(exported, metadata["idempotency_key"])
        assert not created
        assert replay.id == imported.id
