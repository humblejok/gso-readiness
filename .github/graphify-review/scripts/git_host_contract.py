"""Portable Git host/PR URL parsing. No network, credentials or company-specific hosts."""
import re
from urllib.parse import quote, unquote, urlsplit


class HostError(ValueError):
    pass


def https_url(value):
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or len(value) > 2000 or any(ord(c) <= 32 or ord(c) == 127 for c in value)):
            raise ValueError
        segments = [unquote(p, errors="strict") for p in parsed.path.rstrip("/").split("/")[1:]]
        if segments == [""]:
            segments = []
        if any(not p or p in {".", ".."} or any(c in p for c in "/\\%?#")
               or any(ord(c) < 32 or ord(c) == 127 for c in p) for p in segments):
            raise ValueError
        host = parsed.hostname.lower()
        if ":" in host:
            host = "[" + host + "]"
        origin = "https://" + host + (":" + str(port) if port and port != 443 else "")
        return origin, segments
    except (ValueError, TypeError, UnicodeError) as exc:
        raise HostError("Use a credential-free HTTPS URL without ambiguous path segments, query or fragment.") from exc


def join_url(origin, parts):
    return origin + ("/" + "/".join(quote(p, safe="-._~") for p in parts) if parts else "")


def collection_url(value):
    origin, parts = https_url(value)
    return join_url(origin, parts)


def repository_host(value, provider="auto"):
    if provider not in {"auto", "github", "azure-devops"}:
        raise HostError("Unknown Git hosting provider.")
    if value.startswith("git@"):
        match = re.fullmatch(r"git@([A-Za-z0-9.-]+):([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", value)
        if not match or provider == "azure-devops":
            raise HostError("Azure DevOps currently requires an HTTPS remote; GitHub also supports git@host:owner/repo.")
        host, owner, name = match.groups()
        value = "https://" + host + "/" + owner + "/" + name
    # Azure Services emits a redundant organization username in remoteUrl. Only
    # that exact non-secret organization hint is accepted; passwords/arbitrary
    # usernames (which might be tokens) remain forbidden.
    parsed = urlsplit(value)
    if parsed.username is not None and parsed.password is None:
        segments = parsed.path.strip("/").split("/")
        org = segments[0] if parsed.hostname == "dev.azure.com" and segments else None
        if parsed.hostname and parsed.hostname.endswith(".visualstudio.com"):
            org = parsed.hostname.removesuffix(".visualstudio.com")
        if org and parsed.username == org:
            value = "https://" + parsed.netloc.rsplit("@", 1)[1] + parsed.path + ("?" + parsed.query if parsed.query else "") + ("#" + parsed.fragment if parsed.fragment else "") if parsed.scheme == "https" else value
    origin, parts = https_url(value)
    azure = len(parts) >= 3 and parts[-2] == "_git"
    detected = "azure-devops" if azure else "github"
    if provider != "auto" and provider != detected:
        raise HostError("Configured hosting provider does not match the Git remote URL structure.")
    if azure:
        # Collection may include virtual directories (/tfs/Collection) or be the
        # organization in dev.azure.com. Legacy organization.visualstudio.com
        # permits an empty collection path. Names are encoded as single segments.
        return {"provider": detected, "clone_url": join_url(origin, parts),
                "collection_url": join_url(origin, parts[:-3]), "project": parts[-3],
                "repository": parts[-1]}
    if len(parts) != 2 or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", p) for p in parts):
        raise HostError("Unsupported Git remote. Use GitHub owner/repository or Azure collection/project/_git/repository.")
    parts[-1] = parts[-1].removesuffix(".git")
    if parts[-1] in {"", ".", ".."}:
        raise HostError("Invalid Git repository name.")
    return {"provider": detected, "clone_url": join_url(origin, parts),
            "github_repository": origin.removeprefix("https://") + "/" + "/".join(parts)}


def pull_request_url(clone, number):
    if type(number) is not int or number <= 0:
        raise HostError("Invalid pull request ID.")
    host = repository_host(clone)
    return host["clone_url"] + ("/pullrequest/" if host["provider"] == "azure-devops" else "/pull/") + str(number)


def validate_pr_url(value, clone=""):
    if len(value) > 1000:
        raise HostError("Pull request URL is too long.")
    origin, parts = https_url(value)
    if len(parts) < 4 or parts[-2] not in {"pull", "pullrequest"} or not re.fullmatch(r"[1-9][0-9]*", parts[-1]):
        raise HostError("Use a GitHub or Azure DevOps HTTPS pull request URL.")
    host = repository_host(join_url(origin, parts[:-2]))
    expected = "pullrequest" if host["provider"] == "azure-devops" else "pull"
    if parts[-2] != expected:
        raise HostError("Pull request URL does not match its hosting provider.")
    if clone and repository_host(clone)["clone_url"].casefold() != host["clone_url"].casefold():
        raise HostError("Pull request belongs to a different repository than the configured clone URL.")
    return pull_request_url(host["clone_url"], int(parts[-1]))
