"""Azure DevOps REST transport, approved-destination authentication and terminal diagnostics."""
import argparse
import base64
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import warnings
from pathlib import Path

from git_host_contract import HostError, collection_url
from publish_review import native_keyring, opener_for
from review_settings import load_settings, process_environment, validate_url

MAX_BYTES = 10 * 1024 * 1024


class AzureError(ValueError):
    pass


def destination(root, host):
    settings = load_settings(root)
    trusted = settings["user"].get("azure_devops_url")
    if not trusted or collection_url(trusted) != host["collection_url"]:
        raise AzureError("Confirm this remote's collection URL with /configure-review azure-devops-url=<collection-url> before API authentication. No credentials were sent.")
    auth = settings["user"].get("azure_auth") or "auto"
    if auth == "auto":
        auth = "windows" if sys.platform == "win32" and urllib.parse.urlsplit(trusted).hostname not in {"dev.azure.com"} and not urllib.parse.urlsplit(trusted).hostname.endswith(".visualstudio.com") else "pat"
    if auth == "windows" and sys.platform != "win32":
        raise AzureError("Windows identity authentication requires Windows. Select pat and use the terminal login helper on other platforms.")
    return collection_url(trusted), auth


def valid_pat(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,200}", value):
        raise AzureError("Expected an Azure DevOps PAT; enter it only in the terminal login helper, never chat/settings.")
    return value


def pat_for(collection):
    if os.environ.get("GRAPHIFY_AZURE_PAT"):
        if collection_url(os.environ.get("GRAPHIFY_AZURE_PAT_URL", "")) != collection:
            raise AzureError("GRAPHIFY_AZURE_PAT_URL must exactly match the approved collection URL.")
        return valid_pat(os.environ["GRAPHIFY_AZURE_PAT"])
    try:
        value = native_keyring().get_password("graphify-review-azure:" + collection, "pat")
    except Exception as exc:
        raise AzureError("Cannot read the Azure credential from native OS storage. Unlock it or use a destination-bound secret-manager environment token. No plaintext fallback is used.") from exc
    if not value:
        raise AzureError("No Azure PAT saved for this collection. Run azure_devops.py login in your own terminal, or select Windows authentication if supported.")
    return valid_pat(value)


def proxy_for(root, url):
    environment = process_environment(root)
    bypass = urllib.request.proxy_bypass_environment(urllib.parse.urlsplit(url).hostname, {"no": environment.get("no_proxy") or environment.get("NO_PROXY", "")})
    proxy = "" if bypass else environment.get("https_proxy") or environment.get("HTTPS_PROXY", "")
    if proxy:
        validate_url(proxy, "proxy_url")
    return proxy


def windows_request(root, collection, method, url, data):
    if sys.platform != "win32":
        raise AzureError("Windows authentication requires Windows.")
    environment = process_environment(root)
    powershell = shutil.which("powershell.exe", path=environment.get("PATH")) or shutil.which("pwsh.exe", path=environment.get("PATH"))
    if not powershell:
        raise AzureError("Windows PowerShell or PowerShell 7 is required for Windows identity authentication.")
    payload = {"collection": collection, "method": method, "url": url,
               "body": json.dumps(data, ensure_ascii=True, allow_nan=False) if data is not None else None,
               "proxy": proxy_for(root, url)}
    # -File uses fixed trusted code; no interpolated shell commands, credentials,
    # profiles or execution-policy bypass. stderr is never exposed.
    completed = subprocess.run([powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(Path(__file__).with_name("azure_windows_request.ps1"))],
                               input=json.dumps(payload, ensure_ascii=True).encode(), capture_output=True,
                               cwd=root, env=environment, timeout=65)
    if completed.returncode or len(completed.stdout) > MAX_BYTES * 6 + 1024:
        raise AzureError("Windows API request failed. Check approved script execution, Windows authentication, proxy and Windows certificate trust. A write may have completed; retry only the saved attempt.")
    response = json.loads(completed.stdout.decode("utf-8-sig"))
    return response["status"], response["body"].encode("utf-8")


def request(root, host, method, suffix="", query=None, data=None):
    collection, auth = destination(root, host)  # Before loading or sending any credential.
    if method not in {"GET", "POST"} or not re.fullmatch(r"(?:/pullrequests(?:/[1-9][0-9]*)?)?", suffix):
        raise AzureError("Invalid Azure API operation.")
    version = host.get("api_version", "6.0")
    if version not in {"6.0", "7.0", "7.1"}:
        raise AzureError("Unsupported Azure API version.")
    url = collection + "/" + urllib.parse.quote(host["project"], safe="") + "/_apis/git/repositories/" + urllib.parse.quote(host["repository"], safe="") + suffix
    url += "?" + urllib.parse.urlencode({**(query or {}), "api-version": version})
    try:
        if auth == "windows":
            status, raw = windows_request(root, collection, method, url, data)
        else:
            # Reuse verified PEM/proxy/NO_PROXY transport, never the Hub bearer token.
            token = base64.b64encode((":" + pat_for(collection)).encode()).decode()
            req = urllib.request.Request(url, method=method,
                    data=json.dumps(data, ensure_ascii=True, allow_nan=False).encode() if data is not None else None,
                    headers={"Authorization": "Basic " + token, "Accept": "application/json", "Content-Type": "application/json"})
            with opener_for(root, url).open(req, timeout=30) as response:
                status = response.status
                raw = response.read(MAX_BYTES + 1)
        if status not in {200, 201}:
            raise AzureError(f"Azure API returned HTTP {status}. Check authentication, repository/PR permissions and API version. Redirects are refused; a write may have completed, so retry only the saved attempt.")
        if len(raw) > MAX_BYTES:
            raise AzureError("Azure API response exceeds 10 MiB.")
        result = json.loads(raw.decode("utf-8"))
        if not isinstance(result, dict):
            raise ValueError
        return result
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        raise AzureError(f"Azure API returned HTTP {status}. Check API authentication, repository/PR permissions and version. Redirects are blocked. For an uncertain write, retain and retry the same saved attempt.") from None
    except AzureError:
        raise
    except Exception as exc:
        raise AzureError("Azure API connection/TLS/response failed. Check proxy and certificates. A write may have completed; retry only the same saved attempt. Raw server/credential errors are suppressed.") from exc


def credential_action(root, action):
    value = load_settings(root)["user"].get("azure_devops_url")
    if not value:
        raise AzureError("Configure the trusted Azure collection URL first.")
    collection = collection_url(value)
    if not sys.stdin.isatty():
        raise AzureError("PAT login/logout must run in your own interactive terminal, never an agent tool.")
    try:
        backend = native_keyring()
        service = "graphify-review-azure:" + collection
        if action == "logout":
            backend.delete_password(service, "pat")
        else:
            print("Azure collection: " + collection + "\nUse a least-privilege PAT with Code read/write and repository PR permissions. Never paste it into chat. Windows authentication does not need a PAT.")
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                token = valid_pat(getpass.getpass("Azure PAT (hidden): "))
            backend.set_password(service, "pat", token)
    except AzureError:
        raise
    except Exception as exc:
        raise AzureError("Azure credential operation failed; no plaintext fallback was used.") from exc
    return {"status": "credential_saved" if action == "login" else "credential_removed", "collection_url": collection,
            "message": "No network request made. Select azure-auth=pat to use this credential; logout does not revoke the server token."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("detect", "check", "login", "logout"))
    parser.add_argument("--repository", default=".")
    parser.add_argument("--remote", default="origin")
    args = parser.parse_args(argv)
    try:
        root = Path(args.repository).resolve()
        if args.action in {"login", "logout"}:
            result = credential_action(root, args.action)
        else:
            from git_providers import describe, provider_for
            def run(repository, *command):
                completed = subprocess.run(command, cwd=repository, env=process_environment(repository), capture_output=True, timeout=30)
                if completed.returncode:
                    raise AzureError("Git/hosting command failed. Check the selected remote or GitHub authentication; raw output is suppressed.")
                return completed.stdout.decode("utf-8").strip()
            host = describe(root, args.remote, run)
            if args.action == "check":
                provider_for(root, host, run).preflight()
            result = {"status": "ready" if args.action == "check" else "detected", "hosting": host,
                      "message": "Read-only API/auth check completed; push/PR-create permissions are not proven." if args.action == "check" else "Local remote inspection only; no credentials read or sent. Confirm the collection URL in setup before checking Azure authentication."}
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "blocked", "message": str(exc) if isinstance(exc, (AzureError, HostError)) else "Hosting operation blocked. Check setup; raw errors/credentials are suppressed."}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
