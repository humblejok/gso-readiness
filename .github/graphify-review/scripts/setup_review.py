#!/usr/bin/env python3
"""Guided setup and diagnostics for non-developers; Python standard library only."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from review_settings import (
    PROJECT_KEYS, USER_KEYS, SettingsError, load_preset, load_settings,
    process_environment, project_settings_path, read_settings, user_settings_path,
    trusted_azure_collections, validate_section, write_settings,
)


def probe(command: list[str], repository: Path, environment: dict) -> tuple[int | None, str]:
    try:
        result = subprocess.run(command, cwd=repository, env=environment, capture_output=True, timeout=15)
        return result.returncode, result.stdout.decode("utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None, ""


def jfrog_servers(repository: Path, environment: dict) -> dict[str, str]:
    executable = shutil.which("jf", path=environment.get("PATH"))
    if not executable:
        return {}
    code, output = probe([executable, "config", "show"], repository, environment)
    if code != 0:
        return {}
    # Never emit raw configuration output, even though the CLI normally masks secrets.
    result = {}
    selected = None
    for line in output.splitlines():
        match = re.fullmatch(r"\s*Server ID:\s*([A-Za-z0-9._-]+)\s*", line)
        if match:
            selected = match.group(1)
            result[selected] = ""
        elif selected and re.match(r"\s*JFrog (?:Platform )?URL:", line):
            url = line.split(":", 1)[1].strip()
            try:
                validate_section("user", {"jfrog_url": url})
                result[selected] = url
            except SettingsError:
                pass
    return result


def ask(label: str, default: str = "", required: bool = False) -> str:
    while True:
        answer = input(f"{label}" + (f" [{default}]" if default else "") + ": ").strip()
        value = "" if answer == "-" else answer or default
        if value or not required:
            return value
        print("Please enter a value (or press Ctrl+C to cancel without saving).")


def configure(args) -> dict:
    repository = Path(args.repository).resolve()
    user_path = Path(args.user_settings).resolve() if args.user_settings else user_settings_path()
    project = read_settings(project_settings_path(repository), "project")
    user = read_settings(user_path, "user")
    if args.preset:
        preset = load_preset(Path(args.preset))
        # Presets fill gaps. They never reset previously saved choices during reinstall.
        project = {**preset.get("project", {}), **project}
        user = {**preset.get("user", {}), **user}
    if args.installed_root:
        installed = Path(args.installed_root).resolve()
        if (installed / "truststore/cacerts").is_file():
            user.setdefault("java_truststore", str(installed / "truststore/cacerts"))
        binary = installed / "bin" / ("jf.exe" if sys.platform == "win32" else "jf")
        if binary.is_file():
            user.setdefault("jfrog_cli_path", str(binary))
    for assignment in args.default:
        scope_key, separator, value = assignment.partition("=")
        scope, dot, key = scope_key.partition(".")
        if not separator or not dot or scope not in {"project", "user"}:
            raise SettingsError("Use --default project.key=value or --default user.key=value.")
        parsed_value = [item.strip() for item in value.split(",") if item.strip()] if key in {"artifactory_repositories", "azure_devops_collections"} else value
        (project if scope == "project" else user).setdefault(key, parsed_value)
    for assignment in args.set:
        scope_key, separator, value = assignment.partition("=")
        scope, dot, key = scope_key.partition(".")
        if not separator or not dot or scope not in {"project", "user"}:
            raise SettingsError("Use --set project.key=value or --set user.key=value.")
        target = project if scope == "project" else user
        target[key] = [item.strip() for item in value.split(",") if item.strip()] if key in {"artifactory_repositories", "azure_devops_collections"} else value
    for scope_key in args.unset:
        scope, dot, key = scope_key.partition(".")
        if not dot or scope not in {"project", "user"} or key not in (PROJECT_KEYS if scope == "project" else USER_KEYS):
            raise SettingsError("Unknown setting to remove.")
        (project if scope == "project" else user).pop(key, None)
    validate_section("project", project)
    validate_section("user", user)
    if args.trust_azure_collection or args.untrust_azure_collection:
        from git_host_contract import collection_url
        approved = trusted_azure_collections(user)
        for value in args.trust_azure_collection:
            canonical = collection_url(value)
            if canonical not in approved:
                approved.append(canonical)
        removed = {collection_url(value) for value in args.untrust_azure_collection}
        user["azure_devops_collections"] = [value for value in approved if value not in removed]
        user.pop("azure_devops_url", None)  # Migrate legacy trust without losing it.
        validate_section("user", user)
    if not args.non_interactive:
        if not sys.stdin.isatty():
            raise SettingsError("Open a terminal for the wizard, or use /setup-review in Copilot. Automated calls require --non-interactive.")
        print("Graphify Review setup. Enter keeps the displayed value; '-' clears an optional value. Never enter passwords or tokens.")
        project["repository_name"] = ask("Project name", project.get("repository_name", repository.name), True)
        project["repository_id"] = ask("Stable project ID (share this with your team)", project.get("repository_id", "repo:" + uuid.uuid4().hex), True)
        environment = process_environment(repository, user_path=user_path)
        _, branch = probe(["git", "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"], repository, environment)
        suggested_branch = branch.strip().removeprefix("refs/remotes/origin/")
        project["default_branch"] = ask("Default branch", project.get("default_branch", suggested_branch or "main"), True)
        project["profile"] = ask("Review profile: auto, generic, aspnet-core-api, spring-boot", project.get("profile", "auto"), True)
        project["security_scanner"] = ask("Security scanner: auto, jfrog, native", project.get("security_scanner", "auto"), True)
        user["jfrog_cli_path"] = ask("JFrog executable path (optional if jf is already on PATH)", user.get("jfrog_cli_path", ""))
        # Probe only the local CLI, with a saved or newly selected executable path.
        if user["jfrog_cli_path"]:
            validate_section("user", {"jfrog_cli_path": user["jfrog_cli_path"]})
            environment["PATH"] = str(Path(user["jfrog_cli_path"]).parent) + os.pathsep + environment.get("PATH", "")
        servers = jfrog_servers(repository, environment)
        if servers:
            print("Existing JFrog server IDs: " + ", ".join(sorted(servers)))
        user["jfrog_server_id"] = ask("JFrog server ID (optional)", user.get("jfrog_server_id", next(iter(servers)) if len(servers) == 1 else ""))
        user["jfrog_url"] = ask("JFrog platform URL (optional; credentials stay in JFrog CLI)", user.get("jfrog_url", servers.get(user["jfrog_server_id"], "")))
        project["artifactory_repositories"] = [value.strip() for value in ask("Artifactory repository keys, comma-separated (optional)", ",".join(project.get("artifactory_repositories", []))).split(",") if value.strip()]
        user["hub_url"] = ask("Finding Hub URL (optional; no automatic uploads)", user.get("hub_url", ""))
        if user["hub_url"]:
            project["hub_workspace_id"] = ask("Hub workspace UUID (required for tech-lead personal tokens; optional for workspace tokens)", project.get("hub_workspace_id", ""))
            project["hub_credential_ref"] = ask("Hub credential reference for this project (non-secret label)", project.get("hub_credential_ref", "default"))
            project["git_provider"] = ask("Implementation Git host: auto, github, azure-devops", project.get("git_provider", "auto"), True)
            _, remote_url = probe(["git", "remote", "get-url", "origin"], repository, environment)
            try:
                from git_host_contract import repository_host
                host = repository_host(remote_url.strip(), project["git_provider"])
            except ValueError:
                host = {}
            if project["git_provider"] == "azure-devops" or host.get("provider") == "azure-devops":
                print("Git keeps its existing authentication. API calls require a separately trusted collection URL; no authentication is attempted during setup.")
                from git_host_contract import collection_url
                approved = trusted_azure_collections(user)
                candidate = host.get("collection_url", "")
                if candidate not in approved:
                    chosen = collection_url(ask("Confirm trusted Azure collection URL (added to approved collections, not repository URL)", candidate, True))
                    if chosen not in approved:
                        approved.append(chosen)
                user["azure_devops_collections"] = approved
                user.pop("azure_devops_url", None)
                user["azure_auth"] = ask("Azure API authentication: auto, windows, pat", user.get("azure_auth", "auto"), True)
                project["azure_auth"] = ask("Azure authentication override for this project (optional: windows/pat/auto)", project.get("azure_auth", ""))
                project["azure_credential_ref"] = ask("Azure PAT credential reference for this project (non-secret label)", project.get("azure_credential_ref", "default"))
                project["azure_api_version"] = ask("Azure API version: 6.0 (Server 2020), 7.0, 7.1", project.get("azure_api_version", "6.0"), True)
        corporate = any(user.get(key) for key in ("proxy_url", "ca_bundle", "pip_index_url", "java_truststore"))
        if ask("Configure corporate proxy/certificates? yes/no", "yes" if corporate else "no").lower() in {"yes", "y"}:
            for key, label in (
                ("proxy_url", "Proxy URL (no embedded credentials)"), ("no_proxy", "Proxy bypass hosts, comma-separated"),
                ("ca_bundle", "Absolute PEM CA bundle path (not Java cacerts)"),
                ("pip_index_url", "Internal Python package index URL (optional)"),
                ("jfrog_releases_repo", "JFrog extractor mirror server-id/repository-key (optional)"),
            ):
                user[key] = ask(label, user.get(key, ""))
        java_project = project["profile"] == "spring-boot" or any((repository / name).is_file() for name in ("pom.xml", "build.gradle", "build.gradle.kts"))
        if java_project or user.get("java_truststore"):
            user["java_truststore"] = ask("Absolute Java cacerts/truststore path (optional)", user.get("java_truststore", ""))
            if user["java_truststore"]:
                user["java_truststore_type"] = ask("Truststore type: JKS or PKCS12 (optional)", user.get("java_truststore_type", ""))
        validate_section("project", project)
        validate_section("user", user)
        print(json.dumps({"project": project, "user": user}, indent=2, ensure_ascii=True))
        if ask("Save these settings? yes/no", "yes").lower() not in {"yes", "y"}:
            return {"status": "cancelled", "message": "No settings were changed."}
    backups = []
    # Validate both documents before the first write. Each write is atomic and backed up.
    for path, scope, values in ((user_path, "user", user), (project_settings_path(repository), "project", project)):
        backup = write_settings(path, scope, values)
        if backup:
            backups.append(backup)
    result = {"status": "configured", "project_file": str(project_settings_path(repository)), "user_file": str(user_path),
              "backups": backups, "message": "Saved. Run /doctor-review. Commit project settings to share the stable ID; user connection settings stay outside Git."}
    if user.get("hub_url"):
        result["hub_publishing"] = "Optional: /publish-review review=<previous-run> exports and uploads to this Hub, creating a missing project inside your existing workspace. After bootstrap, run <review-python> .github/graphify-review/scripts/publish_review.py login in your own terminal to save a reviews:write workspace token in the OS credential store. Never paste tokens into Copilot. Browser upload remains available without a token. Nothing was uploaded during setup."
    return result


def doctor(args) -> dict:
    repository = Path(args.repository).resolve()
    user_path = Path(args.user_settings).resolve() if args.user_settings else user_settings_path()
    settings = load_settings(repository, user_path)
    environment = process_environment(repository, user_path=user_path)
    checks = []

    def check(name, state, message):
        checks.append({"name": name, "state": state, "message": message})

    check("python", "ok" if sys.version_info >= (3, 10) else "error", "Python 3.10+ is required.")
    check("settings", "ok" if settings["configured"] else "warning", "Project settings saved." if settings["configured"] else "Run /setup-review to save project defaults.")
    if (repository / ".github/graphify-review/policy.yaml").is_file():
        from bootstrap_environment import bootstrap
        from review_core import load_yaml
        state = bootstrap(repository, load_yaml(repository / ".github/graphify-review/policy.yaml"), check_only=True)
        check("runtime_dependencies", "ok" if state["ready"] else "warning", "Pinned review dependencies are ready." if state["ready"] else "Run bootstrap_environment.py to create/update the dedicated review environment using your saved network settings.")
    else:
        check("review_environment", "warning", "The review policy file is missing; install/update the complete .github kit first.")
    required_tools = {"git"}
    project = settings["project"]
    if project["security_scanner"] == "jfrog":
        required_tools.add("jf")
    if project["security_scanner"] == "native":
        required_tools.add("gitleaks")
    if project["profile"] == "aspnet-core-api":
        required_tools.add("dotnet")
    if project["profile"] == "spring-boot":
        required_tools.add("java")
        if (repository / "pom.xml").is_file() and not any((repository / name).is_file() for name in ("mvnw", "mvnw.cmd")):
            required_tools.add("mvn")
    if any((repository / name).is_file() for name in (".semgrep.yml", ".semgrep.yaml")):
        required_tools.add("semgrep")
    for name in ("git", "jf", "dotnet", "java", "mvn", "gradle", "gitleaks", "semgrep"):
        present = shutil.which(name, path=environment.get("PATH")) is not None
        state = "ok" if present else "warning" if name in required_tools else "info"
        message = "Available on the review process PATH." if present else "Required by the selected workflow but not found. Ask your administrator to install/configure this tool." if name in required_tools else "Not found; optional for the current selection. Build wrappers may replace Maven/Gradle."
        check(name, state, message)
    for key in ("ca_bundle", "java_truststore", "jfrog_cli_path"):
        if settings["user"].get(key):
            check(key, "ok" if Path(settings["user"][key]).is_file() else "error", "Configured file exists." if Path(settings["user"][key]).is_file() else "Configured file is missing. Change it with /configure-review.")
    if settings["user"].get("ca_bundle") and Path(settings["user"]["ca_bundle"]).is_file():
        try:
            ssl.create_default_context(cafile=settings["user"]["ca_bundle"])
            check("pem_certificates", "ok", "Python can load the approved PEM CA bundle.")
        except (OSError, ValueError):
            check("pem_certificates", "error", "The CA bundle cannot be loaded as PEM certificates. A Java cacerts file is not a PEM bundle.")
    check("copilot", "info", "In VS Code, verify Copilot is installed and signed in, and this project is trusted. This diagnostic cannot verify your Copilot entitlement/session.")
    if settings["user"].get("hub_url"):
        check("hub_publishing", "info", "Hub destination configured. Use /publish-review for explicit uploads and automatic project creation in an existing workspace. First run publish_review.py login in your own terminal after bootstrap. This diagnostic does not read your credential store or verify token permissions.")
        check("implementation_hosting", "info", "Git uses existing credentials. Azure DevOps needs no gh/Azure CLI: approve collections additively with /configure-review azure-trust-collection=<url>, then explicitly run azure_devops.py check. Each repository selects its collection from its Git remote. Normal doctor checks do not load API credentials.")
        check("credential_selection", "info", "Login stores tokens by stable project ID, destination and credential reference. Set explicit project hub_credential_ref/azure_credential_ref to prohibit legacy shared-token fallback. Finish active implementation claims before changing their token/reference. No secrets were read.")
    servers = jfrog_servers(repository, environment)
    server = settings["user"].get("jfrog_server_id") or environment.get("JFROG_CLI_SERVER_ID")
    if server:
        check("jfrog_configuration", "ok" if server in servers else "warning", "Selected server is present in the local CLI configuration." if server in servers else "Selected server could not be confirmed. Use the terminal JFrog login helper or select an existing server in /configure-review.")
        expected = settings["user"].get("jfrog_url", "").rstrip("/")
        if expected and servers.get(server) and expected != servers[server].rstrip("/"):
            check("jfrog_host", "error", "Saved host differs from the CLI server URL. Changing a saved URL alone does not reconfigure JFrog; run the login helper to edit it.")
    if not (repository / ".semgrep.yml").exists() and not (repository / ".semgrep.yaml").exists():
        check("static_security_rules", "warning", "No Semgrep configuration found. Installing tools alone does not complete static_security_scan; ask your team for approved rules.")
    if args.network:
        executable = shutil.which("jf", path=environment.get("PATH"))
        if executable and server:
            code, _ = probe([executable, "rt", "ping", f"--server-id={server}"], repository, environment)
            check("artifactory_connection", "ok" if code == 0 else "error", "Artifactory ping succeeded; this does not prove Xray licensing or audit completeness." if code == 0 else "Artifactory ping failed. Check authentication, proxy and approved certificates; run the JFrog CLI locally for details.")
        hub = settings["user"].get("hub_url")
        if hub:
            try:
                class NoRedirects(urllib.request.HTTPRedirectHandler):
                    def redirect_request(self, *unused):
                        return None
                context = ssl.create_default_context(cafile=settings["user"].get("ca_bundle") or None)
                proxies = {scheme: environment.get(scheme + "_proxy") or environment.get(scheme.upper() + "_PROXY")
                           for scheme in ("http", "https")}
                proxies = {key: value for key, value in proxies.items() if value}
                bypass = urllib.request.proxy_bypass_environment(
                    urlsplit(hub).hostname, {"no": environment.get("no_proxy") or environment.get("NO_PROXY", "")},
                )
                handlers = [urllib.request.HTTPSHandler(context=context), NoRedirects()]
                handlers.append(urllib.request.ProxyHandler({} if bypass else proxies))
                opener = urllib.request.build_opener(*handlers)
                with opener.open(hub.rstrip("/") + "/health/live", timeout=10) as response:
                    passed = response.status == 200
                check("hub_connection", "ok" if passed else "error", "Hub health endpoint responded; no authentication or report upload was attempted.")
            except (OSError, ValueError, urllib.error.URLError):
                check("hub_connection", "error", "Hub health check failed. Check the URL, proxy and approved PEM CA bundle. No credentials were sent.")
    return {"status": "attention_required" if any(item["state"] in {"error", "warning"} for item in checks) else "ready",
            "checks": checks, "network_checks": bool(args.network), "message": "Diagnostics never run audits, installs, fixes or uploads. Missing mandatory evidence can still block a review."}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("configure", "show", "doctor", "jfrog-login"))
    parser.add_argument("--repository", default=".")
    parser.add_argument("--user-settings", help="Explicit user-file location; useful for portable/test setups")
    parser.add_argument("--preset", help="Locally supplied approved corporate preset JSON; never downloaded/executed")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--set", action="append", default=[])
    parser.add_argument("--default", action="append", default=[], help="Fill a missing value without replacing a saved choice")
    parser.add_argument("--installed-root", help="Installer's dedicated tool/truststore folder; discover defaults only")
    parser.add_argument("--unset", action="append", default=[])
    parser.add_argument("--trust-azure-collection", action="append", default=[], help="Approve an exact collection URL, preserving previously approved collections")
    parser.add_argument("--untrust-azure-collection", action="append", default=[], help="Remove one collection approval without changing others or deleting credentials")
    parser.add_argument("--network", action="store_true", help="Opt in to Artifactory ping and unauthenticated Hub health GET")
    args = parser.parse_args(argv)
    try:
        repository = Path(args.repository).resolve()
        if not repository.is_dir():
            raise SettingsError("Choose an existing project folder.")
        user_path = Path(args.user_settings).resolve() if args.user_settings else user_settings_path()
        if args.action == "configure":
            result = configure(args)
        elif args.action == "show":
            result = load_settings(repository, user_path)
            result["existing_jfrog_servers"] = sorted(jfrog_servers(repository, process_environment(repository, user_path=user_path)))
        elif args.action == "doctor":
            result = doctor(args)
        else:
            if not sys.stdin.isatty():
                raise SettingsError("JFrog authentication must run in your own interactive terminal, not a Copilot tool or log.")
            settings = load_settings(repository, user_path)["user"]
            environment = process_environment(repository, user_path=user_path)
            executable = shutil.which("jf", path=environment.get("PATH"))
            server = settings.get("jfrog_server_id")
            if not executable or not server:
                raise SettingsError("Install/select JFrog CLI and save a server ID first.")
            action = "edit" if server in jfrog_servers(repository, environment) else "add"
            command = [executable, "config", action, server, "--interactive=true"]
            if settings.get("jfrog_url"):
                command.append("--url=" + settings["jfrog_url"])
            print("Authentication is handled directly by JFrog CLI. Keep TLS verification enabled; never paste credentials into Copilot.")
            return subprocess.run(command, env=environment).returncode
        print(json.dumps(result, indent=2, ensure_ascii=True))
        return 1 if result.get("status") == "attention_required" else 0
    except (KeyboardInterrupt, EOFError):
        print("Setup cancelled; no further settings were saved.", file=sys.stderr)
        return 2
    except (SettingsError, OSError, ValueError, TypeError, KeyError) as exc:
        message = str(exc) if isinstance(exc, SettingsError) else "Setup could not complete. Check file access, JSON types and configured paths. Existing files have not been deliberately replaced without a backup."
        print(json.dumps({"status": "blocked", "message": message}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
