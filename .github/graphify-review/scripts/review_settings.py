"""Standard-library configuration for the review kit; never stores credentials."""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

PROJECT_KEYS = {
    "repository_id", "repository_name", "default_branch", "profile", "security_scanner",
    "artifactory_repositories", "git_provider", "azure_api_version",
}
USER_KEYS = {
    "hub_url", "jfrog_server_id", "jfrog_url", "proxy_url", "no_proxy", "ca_bundle",
    "pip_index_url", "java_truststore", "java_truststore_type", "jfrog_cli_path", "jfrog_releases_repo",
    "azure_devops_url", "azure_auth",
}
DEFAULT_PROJECT = {"profile": "generic", "security_scanner": "auto", "artifactory_repositories": []}
INSTALLER_KEYS = {
    "github_bundle_url", "github_bundle_sha256", "truststore_url", "truststore_sha256",
    "truststore_type", "jfrog_cli",
}
SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


class SettingsError(ValueError):
    """User-facing configuration error; do not include input values."""


def user_settings_path() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "GraphifyReview/settings.json"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/GraphifyReview/settings.json"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "graphify-review/settings.json"


def project_settings_path(repository: Path) -> Path:
    return repository.resolve() / ".github/graphify-review/settings.json"


def read_object(path: Path) -> dict:
    try:
        with path.open("rb") as stream:
            raw = stream.read(128 * 1024 + 1)
        if len(raw) > 128 * 1024:
            raise SettingsError("Settings/preset exceeds 128 KiB.")

        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise SettingsError("Duplicate settings keys are not allowed.")
                result[key] = value
            return result

        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SettingsError("Cannot read settings/preset; check file access and UTF-8 JSON syntax.") from exc
    if not isinstance(value, dict) or value.get("schema_version") != "1.0":
        raise SettingsError("Expected a settings/preset object with schema_version 1.0.")
    return value


def validate_url(value: str, key: str) -> None:
    if not isinstance(value, str):
        raise SettingsError(f"{key} must be a URL string.")
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError as exc:
        raise SettingsError(f"Invalid URL in {key}.") from exc
    schemes = {"https"}
    if key == "proxy_url" or (key == "hub_url" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
        schemes.add("http")
    if (parsed.scheme not in schemes or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)):
        raise SettingsError(f"{key} must be a credential-free HTTPS URL (HTTP allowed only for proxies and local Hub development).")


def validate_section(scope: str, data: dict) -> dict:
    keys = PROJECT_KEYS if scope == "project" else USER_KEYS
    if not isinstance(data, dict) or set(data) - keys:
        raise SettingsError(f"Unknown {scope} settings. Tokens, passwords and arbitrary environment variables are not supported.")
    for key, value in data.items():
        if key == "artifactory_repositories":
            if not isinstance(value, list) or any(not isinstance(item, str) or not SAFE_ID.fullmatch(item) for item in value):
                raise SettingsError("Artifactory repositories must be a list of repository keys.")
            continue
        if not isinstance(value, str) or len(value) > 2000 or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise SettingsError(f"{key} must be a bounded single-line string.")
        limit = {"repository_id": 500, "repository_name": 200, "default_branch": 200}.get(key, 2000)
        if len(value) > limit:
            raise SettingsError(f"{key} exceeds its supported length.")
        if not value:
            if key in {"profile", "security_scanner"}:
                raise SettingsError(f"{key} cannot be blank; unset it to restore the default.")
            continue
        if key in {"hub_url", "jfrog_url", "proxy_url", "pip_index_url", "azure_devops_url"}:
            validate_url(value, key)
        if key == "azure_devops_url":
            from git_host_contract import collection_url
            try:
                collection_url(value)
            except ValueError as exc:
                raise SettingsError("Invalid Azure DevOps trusted collection URL.") from exc
        if key == "git_provider" and value not in {"auto", "github", "azure-devops"}:
            raise SettingsError("Git provider must be auto, github or azure-devops.")
        if key == "azure_api_version" and value not in {"6.0", "7.0", "7.1"}:
            raise SettingsError("Azure API version must be 6.0 (Server 2020), 7.0 or 7.1.")
        if key == "azure_auth" and value not in {"auto", "windows", "pat"}:
            raise SettingsError("Azure authentication must be auto, windows or pat.")
        if key == "jfrog_server_id" and not SAFE_ID.fullmatch(value):
            raise SettingsError("JFrog server ID must contain only letters, digits, dots, underscores or hyphens.")
        if key == "jfrog_releases_repo" and not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", value):
            raise SettingsError("JFrog releases repository must be server-id/repository-key.")
        if key == "profile" and value not in {"generic", "auto", "aspnet-core-api", "spring-boot"}:
            raise SettingsError("Profile must be generic, auto, aspnet-core-api or spring-boot.")
        if key == "security_scanner" and value not in {"auto", "jfrog", "native"}:
            raise SettingsError("Security scanner must be auto, jfrog or native.")
        if key == "java_truststore_type" and value not in {"JKS", "PKCS12"}:
            raise SettingsError("Java truststore type must be JKS or PKCS12.")
        if key in {"ca_bundle", "java_truststore", "jfrog_cli_path"}:
            if not Path(value).is_absolute() or any(char in value for char in '\"\'`$\n'):
                raise SettingsError(f"{key} must be an absolute local path without quotes or shell expansion characters.")
        if key == "jfrog_cli_path" and Path(value).name.lower() not in {"jf", "jf.exe"}:
            raise SettingsError("JFrog CLI path must name the jf or jf.exe executable.")
        if re.search(r"-----BEGIN .*PRIVATE KEY|\bBearer\s|\bgh[pousr]_[A-Za-z0-9]{20,}", value):
            raise SettingsError("Possible credential detected; keep authentication in the tool's credential store.")
    return data


def read_settings(path: Path, scope: str) -> dict:
    if not path.exists():
        return {}
    data = read_object(path)
    if set(data) != {"schema_version", scope}:
        raise SettingsError(f"Expected only schema_version and {scope} in settings file.")
    return validate_section(scope, data[scope])


def load_settings(repository: Path, user_path: Path | None = None) -> dict:
    project = read_settings(project_settings_path(repository), "project")
    user_path = user_path or user_settings_path()
    return {
        "project": {**DEFAULT_PROJECT, **project}, "user": read_settings(user_path, "user"),
        "configured": all(project.get(key) for key in ("repository_id", "repository_name", "default_branch")),
        "project_file": str(project_settings_path(repository)), "user_file": str(user_path),
    }


def load_preset(path: Path) -> dict:
    data = read_object(path)
    if set(data) - {"schema_version", "project", "user", "installer"}:
        raise SettingsError("Unknown preset section; scripts, credentials and arbitrary environment settings are forbidden.")
    validate_section("project", data.get("project", {}))
    validate_section("user", data.get("user", {}))
    installer = data.get("installer", {})
    if not isinstance(installer, dict) or set(installer) - INSTALLER_KEYS:
        raise SettingsError("Unknown installer preset field.")
    for key, value in installer.items():
        if key == "jfrog_cli":
            if not isinstance(value, dict) or set(value) - {"windows-amd64", "windows-arm64", "darwin-amd64", "darwin-arm64", "linux-amd64", "linux-arm64"}:
                raise SettingsError("Invalid platform-specific JFrog downloads.")
            for artifact in value.values():
                if not isinstance(artifact, dict) or set(artifact) != {"url", "sha256"}:
                    raise SettingsError("Each JFrog download requires url and sha256.")
                validate_url(artifact["url"], "jfrog_url")
                if not re.fullmatch(r"[a-fA-F0-9]{64}", artifact["sha256"]):
                    raise SettingsError("Invalid artifact SHA-256.")
        elif key.endswith("_url"):
            validate_url(value, key)
        elif key.endswith("_sha256"):
            if not isinstance(value, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", value):
                raise SettingsError("Invalid artifact SHA-256.")
        elif value not in {"", "JKS", "PKCS12"}:
            raise SettingsError("Invalid truststore type.")
    return data


def write_settings(path: Path, scope: str, values: dict) -> str | None:
    validate_section(scope, values)
    payload = (json.dumps({"schema_version": "1.0", scope: values}, indent=2, sort_keys=True) + "\n").encode()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise SettingsError("Refusing to write settings through a symbolic link.")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == payload:
        return None
    backup = None
    if path.exists():
        suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        backup = path.with_name(path.name + ".bak-" + suffix)
        shutil.copy2(path, backup)
        if scope == "user":
            backup.chmod(0o600)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".settings-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600 if scope == "user" else 0o644)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return str(backup) if backup else None


def process_environment(repository: Path, base: dict | None = None, user_path: Path | None = None) -> dict:
    """Apply only allowlisted user settings to a child environment; never change the OS/user environment."""
    result = dict(os.environ if base is None else base)
    user = load_settings(repository, user_path)["user"]
    for key, names in {
        "proxy_url": ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"),
        "no_proxy": ("NO_PROXY", "no_proxy"), "pip_index_url": ("PIP_INDEX_URL",),
        "ca_bundle": ("PIP_CERT", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"),
        "jfrog_server_id": ("JFROG_CLI_SERVER_ID",),
        "jfrog_releases_repo": ("JFROG_CLI_RELEASES_REPO",),
    }.items():
        if user.get(key):
            for name in names:
                result[name] = user[key]
    if user.get("jfrog_cli_path"):
        result["PATH"] = str(Path(user["jfrog_cli_path"]).parent) + os.pathsep + result.get("PATH", "")
    if user.get("java_truststore"):
        current = result.get("MAVEN_OPTS", "")
        current = re.sub(r'''(?<!\S)-Djavax\.net\.ssl\.trustStore(?:Type)?=(?:"[^"]*"|'[^']*'|\S+)''', "", current).strip()
        options = f'-Djavax.net.ssl.trustStore="{user["java_truststore"]}"'
        if user.get("java_truststore_type"):
            options += " -Djavax.net.ssl.trustStoreType=" + user["java_truststore_type"]
        result["MAVEN_OPTS"] = " ".join(item for item in (current, options) if item)
    return result


def apply_process_environment(repository: Path) -> dict:
    """Entry points call this once so nested build/scanner processes inherit the saved settings."""
    settings = load_settings(repository)
    os.environ.update(process_environment(repository))
    return settings
