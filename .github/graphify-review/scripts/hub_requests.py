"""Read open project requests and publish source-bound analysis, never accept or implement it."""

import argparse
import json
import sys
import urllib.parse
import uuid
from pathlib import Path

from export_review import KIT_ROOT, read_json, write_new_json
from hub_findings import project_id, request_json
from publish_review import PublishError, configured_hub, workspace_headers
from revalidate_finding import source
from review_settings import load_settings


def binding(root):
    return {
        "hub_url": configured_hub(root),
        "repository_external_id": project_id(root),
        "workspace_headers": workspace_headers(root),
        "credential_ref": load_settings(root)["project"].get("hub_credential_ref", ""),
    }


def identifiers(value):
    values = [part.strip() for part in value.split(",") if part.strip()]
    if len(values) > 100:
        raise PublishError("Select at most 100 request UUIDs.")
    try:
        return list(dict.fromkeys(str(uuid.UUID(part)) for part in values))
    except ValueError as exc:
        raise PublishError(
            "Use the request UUID displayed on its Hub details page."
        ) from exc


def checked(item, repository, selected=()):
    if (
        not isinstance(item, dict)
        or item.get("repository_external_id") != repository
        or item.get("status") != "open"
        or type(item.get("revision")) is not int
        or item["revision"] < 1
        or not isinstance(item.get("description"), str)
        or not item["description"].strip()
        or item.get("kind") not in {"bug", "feature"}
    ):
        raise PublishError(
            "Hub returned a request outside the expected open project queue."
        )
    identifier = str(uuid.UUID(item.get("id", "")))
    if item["id"] != identifier or (selected and identifier not in selected):
        raise PublishError("Hub returned an unexpected request identifier.")
    return item


def queue(root, selected=()):
    context = binding(root)
    items, seen = [], set()
    for page in range(1, 101):
        query = urllib.parse.urlencode(
            {
                "repository_external_id": context["repository_external_id"],
                "status": "open",
                "ids": ",".join(selected),
                "page": page,
            }
        )
        response = request_json(
            root, "GET", "/api/v1/requests?" + query, expected_hub=context["hub_url"]
        )
        if (
            not isinstance(response.get("results"), list)
            or type(response.get("count")) is not int
            or response["count"] < 0
        ):
            raise PublishError("Invalid Hub request queue response.")
        for item in response["results"]:
            checked(item, context["repository_external_id"], selected)
            if item["id"] in seen:
                raise PublishError(
                    "Request queue changed during pagination; fetch it again."
                )
            seen.add(item["id"])
            items.append(item)
        if not response["results"] or len(items) >= response["count"]:
            break
    else:
        raise PublishError(
            "Queue exceeds the page limit; use a smaller explicit ID filter."
        )
    return {**context, "items": items, "not_open_ids": sorted(set(selected) - seen)}


def prepare(root, identifier):
    context = binding(root)
    identifier = str(uuid.UUID(identifier))
    item = request_json(
        root,
        "GET",
        f"/api/v1/requests/{identifier}/analysis",
        expected_hub=context["hub_url"],
    )
    checked(item, context["repository_external_id"], [identifier])
    directory = KIT_ROOT / "output" / "request-analyses" / str(uuid.uuid4())
    state = {
        "root": str(root),
        "binding": context,
        "request": item,
        "source": source(root),
        "submission_id": str(uuid.uuid4()),
    }
    directory.mkdir(parents=True, exist_ok=False)
    write_new_json(directory / "state.json", state)
    return {
        "state": str(directory / "state.json"),
        "analysis_file": str(directory / "analysis.json"),
        "request": item,
        "source": state["source"],
    }


def submit(root, state_path, analysis_path):
    state, _ = read_json(state_path)
    if state["root"] != str(root) or state["binding"] != binding(root):
        raise PublishError(
            "Checkout, Hub, workspace or credential selection changed; no analysis submitted."
        )
    if state["source"] != source(root):
        raise PublishError(
            "Source changed since preparation. Prepare and analyze again; do not relabel old evidence."
        )
    analysis, _ = read_json(analysis_path)
    if not isinstance(analysis, dict):
        raise PublishError("Analysis must be a JSON object.")
    body = {
        "revision": state["request"]["revision"],
        "repository_external_id": state["binding"]["repository_external_id"],
        "submission_id": state["submission_id"],
        "analysis": analysis,
    }
    submission = state_path.parent / "submission.json"
    if submission.exists():
        saved, _ = read_json(submission)
        if saved != body:
            raise PublishError(
                "A submission already exists. Retry only its unchanged analysis and state."
            )
    else:
        write_new_json(submission, body)
    identifier = str(uuid.UUID(state["request"]["id"]))
    result = request_json(
        root,
        "POST",
        f"/api/v1/requests/{identifier}/analysis",
        body,
        expected_hub=state["binding"]["hub_url"],
    )
    if (
        result.get("id") != identifier
        or result.get("status") != "analyzed"
        or result.get("repository_external_id") != body["repository_external_id"]
        or result.get("analysis_submission_id") != body["submission_id"]
        or result.get("revision") != body["revision"] + 1
        or result.get("analysis") != analysis
    ):
        raise PublishError(
            "Unexpected submission response. A write may have completed; retain the saved submission and inspect the Hub."
        )
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("queue", "prepare", "submit"))
    parser.add_argument("--repository", default=".")
    parser.add_argument("--ids", default="")
    parser.add_argument("--request")
    parser.add_argument("--state")
    parser.add_argument("--analysis")
    args = parser.parse_args(argv)
    try:
        root = Path(args.repository).resolve()
        if args.action == "queue":
            result = queue(root, identifiers(args.ids))
        elif args.action == "prepare" and args.request:
            result = prepare(root, args.request)
        elif args.action == "submit" and args.state and args.analysis:
            result = submit(
                root, Path(args.state).resolve(), Path(args.analysis).resolve()
            )
        else:
            raise PublishError(
                "Prepare requires --request; submit requires --state and --analysis."
            )
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "message": str(exc)
                    if isinstance(exc, PublishError)
                    else "Request analysis blocked; check setup, inputs and permissions.",
                }
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
