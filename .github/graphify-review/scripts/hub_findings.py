"""Explicit, destination-bound Finding Hub queue/revalidation API client."""
import argparse
import http.client
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from export_review import read_json, write_new_json
from publish_review import PublishError, configured_hub, opener_for, token_for, workspace_headers
from review_settings import load_settings
from targeted_contract import validate_targeted


def request_json(root, method, path, data=None, expected_hub=None):
    hub = configured_hub(root)
    if expected_hub and hub != expected_hub:
        raise PublishError("Configured Hub changed during this operation. Restore the intended destination; credentials will not be forwarded.")
    if not path.startswith("/api/v1/") or "#" in path or ".." in path:
        raise PublishError("Invalid internal Hub endpoint.")
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False).encode() if data is not None else None
    request = urllib.request.Request(hub + path, data=payload, method=method, headers={
        "Authorization": "Bearer " + token_for(hub, root), "Accept": "application/json", "Content-Type": "application/json", **workspace_headers(root)})
    try:
        with opener_for(root, hub).open(request, timeout=30) as response:
            raw = response.read(10 * 1024 * 1024 + 1)
            if response.status not in {200, 201} or len(raw) > 10 * 1024 * 1024:
                raise PublishError("Unexpected Hub response; a write may already have completed. Retry the same saved request only.")
        result = json.loads(raw.decode("utf-8"))
        if not isinstance(result, dict):
            raise ValueError
        return result
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        messages = {401: "Hub token expired/invalid: use the terminal login helper.",
                    403: "Hub denied access: check token scopes, repository restriction, workspace status and subscription.",
                    404: "Hub item/endpoint not found. Update the Hub and check the selected workspace/repository.",
                    409: "Hub work state changed or conflicts with this request. Refresh it; do not override another worker, cancellation or newer review.",
                    400: "Hub rejected the request/targeted contract. Check the saved artifacts and current baseline.",
                    429: "Hub rate limit reached. Wait before retrying the identical request."}
        raise PublishError(messages.get(status, "Hub request failed or redirected. Redirects are blocked. A write may have completed; retry only the identical saved request.")) from None
    except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException) as exc:
        raise PublishError("Hub connection/TLS/response failure. Check proxy/CA settings. A write may have completed; retain its request ID and retry unchanged.") from exc


def project_id(root):
    value = load_settings(root)["project"].get("repository_id")
    if not value:
        raise PublishError("Configure this checkout's stable repository ID with /setup-review first.")
    return value


def queue(root, identifiers=()):
    repository = project_id(root)
    hub = configured_hub(root)
    items = []
    for page in range(1, 101):
        query = urllib.parse.urlencode({"repository_external_id": repository, "to_implement": "true", "ids": ",".join(identifiers), "page": page})
        data = request_json(root, "GET", "/api/v1/findings?" + query, expected_hub=hub)
        results = data.get("results")
        if not isinstance(results, list) or type(data.get("count")) is not int or data["count"] < 0:
            raise PublishError("Invalid Hub queue response.")
        for item in results:
            if (not isinstance(item, dict) or not isinstance(item.get("display_id"), str)
                    or item.get("to_implement") is not True or item.get("lifecycle") != "open"
                    or (identifiers and item["display_id"] not in identifiers)):
                raise PublishError("Hub returned an item outside the requested open implementation queue. Nothing will be implemented.")
        items.extend(results)
        if not results or len(items) >= data.get("count", 0):
            break
    else:
        raise PublishError("Queue exceeds the bounded page limit. Select a smaller explicit ID list.")
    ids = [item["display_id"] for item in items]
    if len(ids) != len(set(ids)):
        raise PublishError("Short finding IDs are ambiguous in this repository; resolve the duplicate IDs in the Hub before branching.")
    missing = sorted(set(identifiers) - set(ids))
    return {"hub_url": hub, "repository_external_id": repository, "items": items, "not_queued_ids": missing}


def context(root, finding_id, hub=None):
    finding_id = str(uuid.UUID(finding_id))
    result = request_json(root, "GET", f"/api/v1/findings/{finding_id}/implementation", expected_hub=hub)
    if result.get("finding_id") != finding_id or result.get("repository_external_id") != project_id(root):
        raise PublishError("Hub finding does not belong to this checkout's configured repository.")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("queue", "resolve", "context", "publish-revalidation"))
    parser.add_argument("--repository", default=".")
    parser.add_argument("--ids", default="")
    parser.add_argument("--finding")
    parser.add_argument("--revision", type=int)
    parser.add_argument("--envelope")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        root = Path(args.repository).resolve()
        if args.action == "queue":
            identifiers = [v.strip() for v in args.ids.split(",") if v.strip()]
            if len(identifiers) > 100:
                raise PublishError("Select at most 100 finding IDs.")
            result = queue(root, identifiers)
        elif args.action == "resolve":
            if not args.finding:
                raise PublishError("Provide the baseline short finding ID with --finding.")
            query = urllib.parse.urlencode({"repository_external_id": project_id(root), "ids": args.finding})
            candidates = request_json(root, "GET", "/api/v1/findings?" + query)
            if candidates.get("count") != 1 or len(candidates.get("results", [])) != 1:
                raise PublishError("Finding ID must identify exactly one imported finding in this repository.")
            result = context(root, candidates["results"][0]["id"])
        elif args.action == "context":
            result = context(root, args.finding)
        else:
            if args.revision is None or not args.finding or not args.envelope:
                raise PublishError("Publish requires the finding UUID and revision captured before targeted verification, plus its envelope.")
            report, _ = read_json(Path(args.envelope))
            validate_targeted(report)
            if report["repository_external_id"] != project_id(root):
                raise PublishError("Targeted report belongs to another repository.")
            finding_id = str(uuid.UUID(args.finding))
            result = request_json(root, "POST", f"/api/v1/findings/{finding_id}/revalidation", {"revision": args.revision, "report": report})
        if args.output:
            write_new_json(Path(args.output), result)
            print(json.dumps({"output": str(Path(args.output).resolve())}))
        else:
            print(json.dumps(result, ensure_ascii=True))
        return 0
    except Exception as exc:  # Sanitized credential/network boundary.
        print(json.dumps({"status": "blocked", "message": str(exc) if isinstance(exc, PublishError) else "Hub operation blocked; verify setup and explicit arguments."}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
