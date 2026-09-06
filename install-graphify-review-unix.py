#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 De Jonckheere Stéphane (humblejok)
# SPDX-License-Identifier: AGPL-3.0-only
"""Install the Graphify Review kit on macOS/Linux using Python 3.10+ only.

Original concept and project creator: De Jonckheere Stéphane (humblejok).
See LICENSE and NOTICE. Provided without warranty.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shlex
import shutil
import ssl
import stat
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone


MAX_DOWNLOAD = 512 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024
REQUIRED = (
    "graphify-review/VERSION", "graphify-review/LICENSE", "graphify-review/NOTICE",
    "graphify-review/THIRD_PARTY_NOTICES.md", "agents/review-manager.agent.md",
    "prompts/full-project-review.prompt.md",
)
BEGIN = "# >>> Graphify Review environment >>>"
END = "# <<< Graphify Review environment <<<"


class InstallError(ValueError):
    pass


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise InstallError("Download redirected. Supply the final HTTPS artifact URL; redirects are not followed.")


def validate_url(value: str, label: str, proxy: bool = False) -> None:
    try:
        parsed = urllib.parse.urlsplit(value)
        valid_port = parsed.port
        del valid_port
    except ValueError:
        raise InstallError(f"{label}: invalid URL") from None
    if (parsed.scheme not in ({"http", "https"} if proxy else {"https"}) or not parsed.hostname
            or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
            or any(ord(char) <= 32 for char in value)):
        raise InstallError(f"{label}: expected {'HTTP(S)' if proxy else 'HTTPS'} URL without credentials, query, or fragment")
    if parsed.hostname.endswith(".invalid") or "REPLACE_ME" in value:
        raise InstallError(f"{label}: replace the example URL before installation")


def validate_hash(value: str) -> None:
    if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise InstallError("Each artifact requires its expected 64-character SHA-256")


def make_opener(proxy: str | None, ca_bundle: str | None):
    context = ssl.create_default_context()
    if ca_bundle:
        context.load_verify_locations(cafile=ca_bundle)
    handler = urllib.request.ProxyHandler({"https": proxy, "http": proxy}) if proxy else urllib.request.ProxyHandler()
    return urllib.request.build_opener(handler, urllib.request.HTTPSHandler(context=context), NoRedirects())


def download(opener, url: str, checksum: str, destination: Path, token: str, timeout: int) -> None:
    validate_url(url, "Artifact")
    validate_hash(checksum)
    if any(ord(char) < 32 or ord(char) == 127 for char in token):
        raise InstallError("The token environment variable contains invalid control characters")
    headers = {"User-Agent": "Graphify-Review-Installer"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, headers=headers)
    digest = hashlib.sha256()
    count = 0
    try:
        with opener.open(request, timeout=timeout) as response, destination.open("xb") as output:
            if response.status != 200:
                raise InstallError("Artifact download did not return HTTP 200")
            while chunk := response.read(1024 * 1024):
                count += len(chunk)
                if count > MAX_DOWNLOAD:
                    raise InstallError("Artifact exceeds the 512 MiB download limit")
                digest.update(chunk)
                output.write(chunk)
    except urllib.error.HTTPError as exc:
        raise InstallError(f"Artifact download failed (HTTP {exc.code}); check access, URL, and proxy") from None
    except (urllib.error.URLError, TimeoutError, ssl.SSLError):
        raise InstallError("Artifact download failed; check network, proxy, and Python's TLS trust or --ca-bundle") from None
    if not count or digest.hexdigest().lower() != checksum.lower():
        raise InstallError("Artifact was empty or failed SHA-256 verification; no installation was performed")


def read_bundle(path: Path) -> dict[str, bytes]:
    """Validate all ZIP paths before reading only the single .github tree."""
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 20000 or sum(item.file_size for item in entries) > MAX_EXPANDED:
            raise InstallError("Kit archive exceeds extraction limits")
        roots = set()
        seen = set()
        checked = []
        for entry in entries:
            name = entry.filename.replace("\\", "/")
            parts = PurePosixPath(name).parts
            mode = entry.external_attr >> 16
            if (not parts or name.startswith("/") or ".." in parts or ":" in name
                    or any(ord(char) < 32 for char in name)
                    or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                raise InstallError("Kit archive contains an unsafe path or non-regular entry")
            canonical = "/".join(parts).casefold()
            if canonical in seen:
                raise InstallError("Kit archive contains duplicate or case-colliding paths")
            seen.add(canonical)
            for index, part in enumerate(parts):
                if part == ".github":
                    roots.add(parts[:index + 1])
            checked.append((entry, parts))
        if len(roots) != 1:
            raise InstallError("Kit ZIP must contain exactly one .github directory")
        root = next(iter(roots))
        contents = {}
        for entry, parts in checked:
            if parts[:len(root)] == root and len(parts) > len(root) and not entry.is_dir():
                relative = "/".join(parts[len(root):])
                if "output" in parts or "__pycache__" in parts or parts[-1] == ".DS_Store":
                    continue
                contents[relative] = archive.read(entry)
        if any(not contents.get(required) for required in REQUIRED):
            raise InstallError("Kit ZIP is missing a required manager, prompt, version, license, or notice")
        return contents


def assert_writable_target(path: Path) -> None:
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise InstallError(f"Refusing a symlink destination: {parent}")
    if path.exists() and not path.is_file():
        raise InstallError(f"Destination is not a regular file: {path}")
    for parent in path.parents:
        if parent.exists() and not parent.is_dir():
            raise InstallError(f"Destination parent is not a directory: {parent}")


def shell_profiles(home: Path, shell: str, environment: dict[str, str]) -> list[Path]:
    selected = Path(environment.get("SHELL", "")).name if shell == "auto" else shell
    if selected == "none":
        return []
    if selected == "bash":
        login = next((home / name for name in (".bash_profile", ".bash_login", ".profile")
                      if (home / name).exists()), home / ".profile")
        return [login, home / ".bashrc"]
    if selected == "zsh":
        directory = Path(environment.get("ZDOTDIR") or home).expanduser()
        if not directory.is_absolute():
            raise InstallError("ZDOTDIR must be absolute; use --shell none for manual environment setup")
        return [directory / ".zprofile", directory / ".zshrc"]
    raise InstallError("Could not select Bash/Zsh; pass --shell bash, zsh, or none")


def profile_content(original: str, env_path: Path) -> str:
    block = f"{BEGIN}\n. {shlex.quote(str(env_path))}\n{END}"
    if BEGIN in original or END in original:
        if original.count(BEGIN) != 1 or original.count(END) != 1:
            raise InstallError("Shell startup file has ambiguous Graphify environment markers")
        start, end = original.index(BEGIN), original.index(END)
        if end < start:
            raise InstallError("Shell startup file has reversed Graphify environment markers")
        return original[:start] + block + original[end + len(END):]
    return original + ("\n" if original and not original.endswith("\n") else "") + "\n" + block + "\n"


def environment_script(root: Path, store_type: str, include_jfrog: bool) -> str:
    options = f"-Djavax.net.ssl.trustStore={root / 'truststore' / 'cacerts'}"
    if store_type:
        options += f" -Djavax.net.ssl.trustStoreType={store_type}"
    # Shell expansion does not re-evaluate text contained in MAVEN_OPTS.
    # Track the previous installer suffix to keep repeated sourcing idempotent.
    text = '''# Graphify Review user environment; source from Bash or Zsh.
# Original concept and project creator: De Jonckheere Stéphane (humblejok).
# SPDX-License-Identifier: AGPL-3.0-only
if [ -n "${_GRAPHIFY_REVIEW_JAVA_OPTIONS-}" ]; then
    MAVEN_OPTS=${MAVEN_OPTS-}
    MAVEN_OPTS=${MAVEN_OPTS%"${_GRAPHIFY_REVIEW_JAVA_OPTIONS}"}
    MAVEN_OPTS=${MAVEN_OPTS% }
fi
'''
    text += f"_GRAPHIFY_REVIEW_JAVA_OPTIONS={shlex.quote(options)}\n"
    text += '''MAVEN_OPTS="${MAVEN_OPTS:+${MAVEN_OPTS} }${_GRAPHIFY_REVIEW_JAVA_OPTIONS}"
export MAVEN_OPTS _GRAPHIFY_REVIEW_JAVA_OPTIONS
'''
    if include_jfrog:
        text += f"_graphify_review_bin={shlex.quote(str(root / 'bin'))}\n"
        text += '''case ":${PATH-}:" in
    *":${_graphify_review_bin}:"*) ;;
    *) PATH="${_graphify_review_bin}${PATH:+:${PATH}}" ;;
esac
export PATH
unset _graphify_review_bin
'''
    return text


def atomic_write(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".graphify-install-", delete=False) as output:
            temporary = Path(output.name)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def commit_files(plan: dict[Path, tuple[bytes, int]], backup: Path, github: Path) -> int:
    changes = {path: value for path, value in plan.items()
               if not path.exists() or path.read_bytes() != value[0]
               or stat.S_IMODE(path.stat().st_mode) != value[1]}
    if not changes:
        return 0
    backup.mkdir(parents=True, mode=0o700)
    if github.exists():
        shutil.copytree(github, backup / "github-before", symlinks=True)
    records = []
    for index, path in enumerate(changes):
        existed = path.exists()
        record = {"path": str(path), "existed": existed, "backup": None,
                  "mode": stat.S_IMODE(path.stat().st_mode) if existed else None}
        if existed:
            saved = backup / f"file-{index:05d}"
            shutil.copy2(path, saved)
            record["backup"] = saved.name
        records.append(record)
    (backup / "manifest.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    print(f"Recovery records: {backup / 'manifest.json'}", flush=True)
    written = []
    try:
        for record in records:
            path = Path(record["path"])
            atomic_write(path, *changes[path])
            written.append(record)
    except BaseException:
        errors = []
        for record in reversed(written):
            try:
                path = Path(record["path"])
                if record["existed"]:
                    atomic_write(path, (backup / record["backup"]).read_bytes(), record["mode"])
                else:
                    path.unlink()
            except OSError:
                errors.append(str(path))
        if errors:
            raise InstallError(f"Installation and rollback failed; restore using {backup / 'manifest.json'}") from None
        raise
    return len(changes)


def install(args, *, home: Path | None = None) -> dict:
    system = platform.system()
    if system not in ("Darwin", "Linux"):
        raise InstallError("This installer supports macOS and Linux; use the PowerShell installer on Windows")
    home = (home or Path.home()).resolve()
    target = Path(args.target_repository).expanduser().resolve()
    root = Path(args.install_root).expanduser().resolve() if args.install_root else home / ".local/share/graphify-review"
    if not target.is_dir() or target == home or target == Path("/"):
        raise InstallError("Target must be an existing project directory, not your home or filesystem root")
    if root == home or root == Path("/") or root == target or root in target.parents or target in root.parents:
        raise InstallError("Install root must be a separate dedicated directory outside the target project")
    if re.search(r'''[\s:'"\\$`*?;\[\]{}()<>|&!]''', str(root)):
        raise InstallError("Install root must contain no whitespace or shell metacharacters so Maven can read MAVEN_OPTS")
    if bool(args.jfrog_cli_url) != bool(args.jfrog_cli_sha256):
        raise InstallError("Supply both --jfrog-cli-url and --jfrog-cli-sha256, or omit both")
    if args.timeout <= 0:
        raise InstallError("Timeout must be positive")
    profiles = shell_profiles(home, args.shell, dict(os.environ))
    github = target / ".github"
    env_path = root / "env.sh"
    architecture = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower())
    jfrog_url = args.jfrog_cli_url.replace("{os}", "darwin" if system == "Darwin" else "linux").replace("{arch}", architecture or "unsupported")
    if args.jfrog_cli_url and not architecture:
        raise InstallError("Optional JFrog installation supports amd64 and arm64 only")
    artifacts = [("kit", args.github_bundle_url, args.github_bundle_sha256),
                 ("cacerts", args.truststore_url, args.truststore_sha256)]
    if jfrog_url:
        artifacts.append(("jf", jfrog_url, args.jfrog_cli_sha256))
    for name, url, checksum in artifacts:
        validate_url(url, name)
        validate_hash(checksum)
    if args.proxy:
        validate_url(args.proxy, "Proxy", proxy=True)
    token = os.environ.get(args.token_env, "")
    opener = make_opener(args.proxy, args.ca_bundle)
    with tempfile.TemporaryDirectory(prefix="graphify-review-install-") as temporary:
        stage = Path(temporary)
        for name, url, checksum in artifacts:
            print(f"Downloading and verifying {name}...", flush=True)
            download(opener, url, checksum, stage / name, token, args.timeout)
        contents = read_bundle(stage / "kit")
        plan = {github / relative: (data, 0o644) for relative, data in contents.items()}
        plan[root / "truststore/cacerts"] = ((stage / "cacerts").read_bytes(), 0o600)
        if jfrog_url:
            plan[root / "bin/jf"] = ((stage / "jf").read_bytes(), 0o755)
        plan[env_path] = (environment_script(root, args.truststore_type,
                                          bool(jfrog_url) or (root / "bin/jf").is_file()).encode("utf-8"), 0o600)
        for path in profiles:
            assert_writable_target(path)
            original = path.read_text(encoding="utf-8") if path.exists() else ""
            mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
            plan[path] = (profile_content(original, env_path).encode("utf-8"), mode)
        for path in plan:
            assert_writable_target(path)
            if path.exists() and github in path.parents:
                plan[path] = (plan[path][0], stat.S_IMODE(path.stat().st_mode))
        identifier = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        backup = root / "backups" / identifier
        assert_writable_target(backup / "manifest.json")
        changed = commit_files(plan, backup, github)
    return {"platform": system, "architecture": architecture, "target": str(target),
            "environment": str(env_path), "shell_profiles": [str(path) for path in profiles],
            "changed_files": changed, "backup_directory": str(backup) if changed else None}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument("--target-repository", default=".")
    result.add_argument("--github-bundle-url", default="https://artifactory.example.invalid/artifactory/REPLACE_ME/graphify-review-github.zip")
    result.add_argument("--github-bundle-sha256", required=True)
    result.add_argument("--truststore-url", default="https://artifactory.example.invalid/artifactory/REPLACE_ME/cacerts")
    result.add_argument("--truststore-sha256", required=True)
    result.add_argument("--truststore-type", choices=("JKS", "PKCS12"), default="")
    result.add_argument("--jfrog-cli-url", default="", help="Raw Unix jf binary URL; optional {os}/{arch} placeholders")
    result.add_argument("--jfrog-cli-sha256", default="", help="Hash of the exact platform/architecture binary")
    result.add_argument("--install-root", help="Default: ~/.local/share/graphify-review; no spaces/metacharacters")
    result.add_argument("--token-env", default="ARTIFACTORY_ACCESS_TOKEN", help="Environment variable containing a bearer token")
    result.add_argument("--proxy", help="HTTP(S) proxy; otherwise use Python's proxy discovery")
    result.add_argument("--ca-bundle", help="Existing PEM CA bundle for HTTPS downloads, not the Java cacerts file")
    result.add_argument("--shell", choices=("auto", "bash", "zsh", "none"), default="auto")
    result.add_argument("--timeout", type=int, default=120, help="Socket timeout per download (seconds)")
    return result


def main() -> int:
    if sys.version_info < (3, 10):
        print("Python 3.10 or newer is required.", file=sys.stderr)
        return 2
    args = parser().parse_args()
    if platform.system() not in ("Darwin", "Linux"):
        print("This installer supports macOS and Linux; use the PowerShell installer on Windows.", file=sys.stderr)
        return 2
    if os.geteuid() == 0:
        print("Run this installer as your normal user, without sudo.", file=sys.stderr)
        return 2
    try:
        result = install(args)
    except (InstallError, OSError, UnicodeError, zipfile.BadZipFile, RuntimeError) as exc:
        # Avoid raw network/SSL diagnostics, which may contain credentials or URLs.
        message = str(exc) if isinstance(exc, InstallError) else f"Installation failed ({type(exc).__name__}); check filesystem permissions, archive, and CA bundle"
        print(message, file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("Activate in this Bash/Zsh terminal with:")
    print("  . " + shlex.quote(result["environment"]))
    print("Then fully quit VS Code and launch it from that terminal with: code " + shlex.quote(result["target"]))
    print("Configure JFrog separately if needed. The dedicated review environment is installed on first command use.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
