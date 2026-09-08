"""Explicit, authenticated Finding Hub publication of a validated immutable envelope."""
from __future__ import annotations

import argparse
import getpass
import hashlib
import http.client
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
import uuid
import warnings
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from export_review import KIT_ROOT, MAX_BYTES, ExportError, read_json, write_new_json
from review_settings import (
    SettingsError,
    load_settings,
    process_environment,
    validate_url,
)

TOKEN_PATTERN = re.compile(r"^[a-fA-F0-9-]{36}\.[A-Za-z0-9_-]{20,200}$")
MAX_RESPONSE = 64 * 1024


class PublishError(ValueError):
    """Safe error message; raw server responses and credentials must not be exposed."""


def canonical_hub(value: str) -> str:
    validate_url(value, "hub_url")
    parsed = urlsplit(value)
    host = parsed.hostname.lower()
    if ":" in host:
        host = "[" + host + "]"
    port = parsed.port
    authority = host + (f":{port}" if port and port != (443 if parsed.scheme == "https" else 80) else "")
    return urlunsplit((parsed.scheme.lower(), authority, parsed.path.rstrip("/"), "", ""))


def configured_hub(repository: Path) -> str:
    value = load_settings(repository)["user"].get("hub_url")
    if not value:
        raise PublishError("No Hub URL is configured. Run /configure-review hub-url=https://your-hub first.")
    return canonical_hub(value)


def native_keyring():
    """Select native OS backends directly: never load third-party/plaintext fallback backends."""
    try:
        if sys.platform == "darwin":
            from keyring.backends.macOS import Keyring
        elif sys.platform == "win32":
            from keyring.backends.Windows import WinVaultKeyring as Keyring
        else:
            from keyring.backends.SecretService import Keyring
        backend = Keyring()
        if backend.priority <= 0:
            raise ValueError
        return backend
    except Exception as exc:
        raise PublishError("System credential storage is unavailable or locked. Unlock your OS keyring, or use destination-bound FINDING_HUB_TOKEN and FINDING_HUB_TOKEN_URL supplied by your secret manager. Plaintext storage is never used.") from exc


def validate_token(value: str) -> str:
    if not isinstance(value, str) or not TOKEN_PATTERN.fullmatch(value):
        raise PublishError("Expected a Hub workspace API token (UUID.secret) with reviews:write permission.")
    try:
        uuid.UUID(value.split(".", 1)[0])
    except ValueError as exc:
        raise PublishError("Invalid Hub token identifier.") from exc
    return value


def token_for(hub: str) -> str:
    if os.environ.get("FINDING_HUB_TOKEN"):
        destination = os.environ.get("FINDING_HUB_TOKEN_URL")
        if not destination or canonical_hub(destination) != hub:
            raise PublishError("FINDING_HUB_TOKEN_URL must match the configured Hub before an environment token can be sent. This prevents reuse after changing destinations.")
        return validate_token(os.environ["FINDING_HUB_TOKEN"])
    try:
        value = native_keyring().get_password("graphify-review-hub:" + hub, "workspace-token")
    except PublishError:
        raise
    except Exception as exc:
        raise PublishError("Could not read the Hub credential. Unlock the system credential store or run the terminal login helper.") from exc
    if not value:
        raise PublishError("No token is saved for this Hub. Run publish_review.py login in your own terminal; never paste tokens into Copilot.")
    return validate_token(value)


def credential_action(action: str, repository: Path) -> dict:
    hub = configured_hub(repository)
    if not sys.stdin.isatty():
        raise PublishError("Login/logout must run in your own interactive terminal, not an agent tool or shared log.")
    backend = native_keyring()
    service = "graphify-review-hub:" + hub
    try:
        if action == "logout":
            backend.delete_password(service, "workspace-token")
            return {"status": "logged_out", "hub_url": hub, "message": "Local credential removed. Revoke the token in Hub to invalidate it elsewhere. Environment tokens are unaffected."}
        print(f"Hub: {hub}\nIn the intended Hub workspace, create an API token with reviews:write permission.\nPaste it below; it is stored in your OS credential store, never in this repository. This replaces any locally saved token for this Hub.")
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            token = validate_token(getpass.getpass("Hub workspace token (hidden): "))
        backend.set_password(service, "workspace-token", token)
    except PublishError:
        raise
    except Exception as exc:
        raise PublishError("Credential operation failed. Check your OS credential store; no plaintext fallback was used.") from exc
    return {"status": "credential_saved", "hub_url": hub,
            "message": "Saved locally for this Hub only. Authentication/scope will be checked when publishing; no network request was made."}


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a bearer credential or report to a different URL, even on the same host.
        return None


def opener_for(repository: Path, hub: str):
    settings = load_settings(repository)["user"]
    environment = process_environment(repository)
    context = ssl.create_default_context(cafile=settings.get("ca_bundle") or environment.get("SSL_CERT_FILE") or None)
    proxies = {scheme: environment.get(scheme + "_proxy") or environment.get(scheme.upper() + "_PROXY") for scheme in ("http", "https")}
    proxies = {key: value for key, value in proxies.items() if value}
    bypass = urllib.request.proxy_bypass_environment(urlsplit(hub).hostname, {"no": environment.get("no_proxy") or environment.get("NO_PROXY", "")})
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=context), urllib.request.ProxyHandler({} if bypass else proxies), NoRedirects(),
    )


def response_summary(data: dict, run_id: str) -> dict:
    """Accept only bounded known response fields; never persist raw errors or echoed content."""
    if not isinstance(data, dict) or data.get("run_id") != run_id:
        raise PublishError("Hub response did not identify the expected run. Outcome is uncertain; retry the exact same envelope.")
    result = {"run_id": run_id}
    for key in ("import_id", "repository_id"):
        try:
            result[key] = str(uuid.UUID(data[key]))
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise PublishError("Hub response is incomplete. Outcome is uncertain; retry the exact same envelope.") from exc
    if data.get("organization_id"):
        try:
            result["organization_id"] = str(uuid.UUID(data["organization_id"]))
        except (ValueError, TypeError, AttributeError):
            pass
    if type(data.get("repository_created")) is bool:
        result["repository_created"] = data["repository_created"]
    for key in ("created_findings", "updated_findings", "lifecycle_changes"):
        value = data.get(key)
        if type(value) is int and 0 <= value <= 10000:
            result[key] = value
    jira = data.get("jira", {})
    if isinstance(jira, dict) and isinstance(jira.get("state"), str) and jira["state"] in {"not_configured", "queued", "skipped"}:
        result["jira_state"] = jira["state"]
    return result


def publish(repository: Path, path: Path, timeout: int, dry_run: bool = False) -> dict:
    from import_contract import canonical, validate_envelope

    hub = configured_hub(repository)  # Never take the destination from the untrusted envelope/request.
    try:
        data, file_hash = read_json(path)
    except OSError as exc:
        raise PublishError("Cannot read the selected envelope. Check its path and file permissions; nothing was sent.") from exc
    try:
        key = f"{data['repository']['external_id']}:{data['review']['run']['run_id']}"
    except (KeyError, TypeError) as exc:
        raise PublishError("Publish requires an import-envelope.json from /export-review, not review.json or a vulnerability report.") from exc
    # HTTP headers must be representable and cannot carry control characters.
    if not key.isascii() or any(ord(char) < 32 or ord(char) == 127 for char in key):
        raise PublishError("Repository/run IDs must be ASCII without control characters for the Idempotency-Key header.")
    validate_envelope(data, key, KIT_ROOT / "schema")
    payload = canonical(data).encode("utf-8")
    if len(payload) > MAX_BYTES:
        raise PublishError("Envelope exceeds the Hub upload limit.")
    metadata = {"hub_url": hub, "repository_external_id": data["repository"]["external_id"],
                "run_id": data["review"]["run"]["run_id"], "idempotency_key": key,
                "payload_sha256": "sha256:" + hashlib.sha256(payload).hexdigest(), "envelope_sha256": file_hash}
    if dry_run:
        return {**metadata, "status": "ready_to_publish", "message": "Validated locally. No credential was read and no network request was made. Publishing may create a repository and queue Jira deliveries already configured in the workspace."}
    token = token_for(hub)
    request = urllib.request.Request(hub + "/api/v1/review-imports", data=payload, method="POST", headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json",
        "Accept": "application/json", "Idempotency-Key": key,
    })
    try:
        with opener_for(repository, hub).open(request, timeout=timeout) as response:
            status = response.status
            raw = response.read(MAX_RESPONSE + 1)
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()  # Never read/print an error body that could echo a credential or private finding.
        messages = {
            400: "Hub rejected the envelope. Check contract compatibility and import baseline history for revalidation; no client-side repair was attempted.",
            401: "Hub authentication failed. The token may be expired/revoked; run the terminal login helper with a valid workspace token.",
            403: "Hub refused publication. Check reviews:write scope, token repository restrictions, workspace status and subscription/project quotas.",
            404: "The Hub import endpoint was not found. Check the configured Hub base URL.",
            409: "This repository/run already exists with different content. Reuse the original envelope or publish a genuinely new review run; do not change the run ID to bypass the conflict.",
            413: "The server/proxy rejected the upload size. Check the Hub and reverse-proxy request limits; do not split or drop findings silently.",
            429: "Hub rate limit reached. Wait and retry the exact same envelope.",
        }
        if 300 <= status < 400:
            raise PublishError("Hub redirected the request. Redirects are blocked to protect the token/report. Configure the final Hub base URL and log in for that destination.") from None
        raise PublishError(messages.get(status, "Hub/server error; outcome may be uncertain. Retry only the exact same envelope and idempotency key.")) from None
    except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException) as exc:
        raise PublishError("Connection or TLS failure. Check the configured URL, proxy and approved CA. The server may have received the import; retry the exact same envelope. TLS verification was not disabled.") from exc
    if status not in {200, 201} or len(raw) > MAX_RESPONSE:
        raise PublishError("Unexpected Hub response. Outcome is uncertain; retry the exact same envelope.")
    try:
        summary = response_summary(json.loads(raw.decode("utf-8")), metadata["run_id"])
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PublishError("Invalid Hub response. Outcome is uncertain; retry the exact same envelope.") from exc
    if status == 200:
        summary["repository_created"] = False  # Replayed receipts describe this call, not the original creation.
    result = {**metadata, **summary, "status": "published" if status == 201 else "already_published", "http_status": status}
    if summary.get("organization_id"):
        result["workspace_url"] = hub + "/w/" + summary["organization_id"] + "/"
    receipt = path.parent / ("publish-receipt-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8] + ".json")
    try:
        write_new_json(receipt, result)
        result["receipt"] = str(receipt)
    except OSError:
        result["warning"] = "Hub confirmed publication, but the local receipt could not be written. Retain this response; do not regenerate the envelope."
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("login", "logout", "publish"))
    parser.add_argument("--repository", default=".")
    parser.add_argument("--envelope", help="Explicit exported import-envelope.json; never auto-select latest")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true", help="Validate and show destination, without credentials or network")
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.timeout <= 60:
            raise PublishError("Timeout must be between 1 and 60 seconds.")
        repository = Path(args.repository).resolve()
        if args.action == "publish":
            if not args.envelope:
                raise PublishError("Select an explicit import-envelope.json, or use /publish-review review=<previous-run> to export first.")
            result = publish(repository, Path(args.envelope).resolve(), args.timeout, args.dry_run)
        else:
            if args.envelope or args.dry_run:
                raise PublishError("--envelope and --dry-run apply only to publish, not login/logout.")
            result = credential_action(args.action, repository)
        print(json.dumps(result, indent=2, ensure_ascii=True))
        return 0
    except (KeyboardInterrupt, EOFError):
        print(json.dumps({"status": "cancelled", "message": "Operation cancelled. If a publish request was in progress, retry only the identical envelope."}), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI security boundary: never leak raw credential/backend errors.
        # Imports may be missing before bootstrap; never fall back to unvalidated publication.
        from_exception = isinstance(exc, (PublishError, ExportError, SettingsError))
        message = str(exc) if from_exception else "Publish/authentication could not complete. Check setup and bootstrap dependencies. No raw server errors or credentials are logged."
        # Contract errors have sanitized text, but keep the CLI independent of an optional import.
        if type(exc).__module__ == "import_contract" and type(exc).__name__ == "ValidationError":
            message = str(exc)
        print(json.dumps({"status": "blocked", "message": message}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
