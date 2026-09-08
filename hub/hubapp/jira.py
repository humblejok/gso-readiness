"""Optional Jira Data Center adapter; TLS verification and operator egress policy are mandatory."""

import ipaddress
import json
import re
import secrets
import socket
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode, urlsplit

import certifi
import urllib3
from cryptography.fernet import Fernet, MultiFernet
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .contracts import digest, render_remediation
from .models import DeliveryAttempt, JiraAssociation, Observation, Organization, OutboxEvent
from .services import audit


class DeliveryError(Exception):
    def __init__(self, outcome, status=None, retry=False, retry_after=0):
        self.outcome, self.status, self.retry = outcome, status, retry
        self.retry_after = retry_after
        super().__init__(outcome)


def cipher():
    if not settings.SECRET_ENCRYPTION_KEYS:
        raise ValidationError(
            "The operator must configure HUB_ENCRYPTION_KEYS before saving Jira credentials."
        )
    return MultiFernet([Fernet(key.encode()) for key in settings.SECRET_ENCRYPTION_KEYS])


def encrypt_secret(value):
    if not value or len(value) > 4000 or any(ord(character) < 32 for character in value):
        raise ValidationError("A nonempty, single-line PAT of at most 4000 characters is required.")
    return cipher().encrypt(value.encode()).decode()


def validate_url(url):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.port not in (None, 443)
    ):
        raise ValidationError(
            "Jira URL must be HTTPS on port 443, without credentials, query or fragment."
        )
    if parsed.hostname not in settings.JIRA_ALLOWED_HOSTS:
        raise ValidationError("Jira hostname has not been approved by the operator.")
    if any(part in {"..", "."} for part in parsed.path.split("/")) or "%" in parsed.path:
        raise ValidationError("Invalid Jira context path.")
    return parsed


class JiraClient:
    def __init__(self, connection):
        self.connection = connection

    def request(self, method, path, payload=None):
        if not settings.OUTBOUND_INTEGRATIONS_ENABLED or not self.connection.enabled:
            raise DeliveryError("disabled")
        parsed = validate_url(self.connection.base_url)
        try:
            addresses = sorted(
                {a[4][0] for a in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)}
            )
        except OSError as exc:
            raise DeliveryError("network_error", retry=True) from exc
        allow_private = (
            settings.DEPLOYMENT_MODE == "dedicated"
            and parsed.hostname in settings.JIRA_PRIVATE_HOSTS
        )
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global and not (
                allow_private
                and ip.is_private
                and not ip.is_loopback
                and not ip.is_link_local
                and not ip.is_unspecified
                and not ip.is_multicast
            ):
                raise DeliveryError("blocked_destination")
        if not addresses:
            raise DeliveryError("network_error", retry=True)
        # Pin the validated address; keep original host for SNI/certificate verification.
        pool = urllib3.HTTPSConnectionPool(
            addresses[0],
            port=443,
            server_hostname=parsed.hostname,
            assert_hostname=parsed.hostname,
            cert_reqs="CERT_REQUIRED",
            ca_certs=certifi.where()
            if settings.JIRA_CA_BUNDLE is True
            else settings.JIRA_CA_BUNDLE,
            timeout=urllib3.Timeout(connect=5, read=10),
            retries=False,
        )
        response = None
        try:
            token = cipher().decrypt(self.connection.encrypted_token.encode()).decode()
            response = pool.urlopen(
                method,
                parsed.path.rstrip("/") + "/rest/api/2/" + path,
                body=json.dumps(payload).encode() if payload is not None else None,
                headers={
                    "Authorization": "Bearer " + token,
                    "Host": parsed.hostname,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                redirect=False,
                preload_content=False,
            )
            status = response.status
            body = response.read(1024 * 1024 + 1)
            if not 200 <= status < 300:
                retry_after = 0
                header = response.headers.get("Retry-After", "")
                if header:
                    try:
                        retry_after = int(header)
                    except ValueError:
                        try:
                            retry_after = int(
                                (parsedate_to_datetime(header) - timezone.now()).total_seconds()
                            )
                        except (TypeError, ValueError, OverflowError):
                            retry_after = 0
                raise DeliveryError(
                    "http_error",
                    status,
                    retry=status == 429 or status >= 500,
                    retry_after=max(0, min(retry_after, 86400)),
                )
            if len(body) > 1024 * 1024:
                raise DeliveryError("response_too_large")
            return json.loads(body) if body else {}
        except (urllib3.exceptions.HTTPError, OSError) as exc:
            raise DeliveryError("network_error", retry=True) from exc
        except (ValueError, KeyError) as exc:
            raise DeliveryError("invalid_response") from exc
        finally:
            if response is not None:
                response.close()
            pool.close()


def literal(value):
    # Escape wiki delimiters; imported prose must not become macros, mentions or links.
    return re.sub(r"([\\\[\]{}|!*_~^@])", r"\\\1", str(value))


def identity_marker(finding):
    return (
        f"graphify:{finding.organization_id}:{finding.repository.external_id}:{finding.fingerprint}"
    )


def managed_body(obs):
    finding = obs.finding
    lines = [
        identity_marker(finding),
        f"Source: {obs.review_import.source_commit}",
        f"Graphify lifecycle: {obs.lifecycle}",
        f"Verification: {obs.data['verification_status']}",
        f"Severity: {obs.data['severity']} | Category: {obs.data['category']} | Priority: {obs.data.get('remediation_priority', '')}",
        obs.data.get("description", ""),
        obs.data.get("impact", ""),
        "Evidence: " + json.dumps(obs.data.get("evidence", []), sort_keys=True),
        render_remediation(obs.remediation),
    ]
    if obs.reconciliation:
        lines.append(json.dumps(obs.reconciliation, sort_keys=True))
    body = literal("\n\n".join(lines))
    detail_url = f"{settings.PUBLIC_URL}/w/{finding.organization_id}/findings/{finding.id}/"
    if len(body) > 29000:
        return (
            body[:29000]
            + "\n\nContent truncated for Jira. Full finding and remediation: "
            + detail_url
        )
    return body + "\n\nFull finding and remediation: " + detail_url


def deliver(event):
    obs, binding = event.observation, event.binding
    finding = obs.finding
    if any(
        obj.organization_id != event.organization_id
        for obj in (obs, binding, finding, binding.connection, binding.repository)
    ):
        raise DeliveryError("tenant_mismatch")
    if (
        not binding.enabled
        or not binding.connection.enabled
        or not settings.OUTBOUND_INTEGRATIONS_ENABLED
    ):
        return "disabled"
    latest = (
        Observation.objects.filter(
            finding=finding, review_import__source_branch=binding.repository.default_branch
        )
        .order_by("-created_at", "-pk")
        .first()
    )
    if latest and latest.pk != obs.pk:
        return "superseded_delivery"
    client = JiraClient(binding.connection)
    association = JiraAssociation.objects.filter(
        finding=finding, connection=binding.connection
    ).first()
    body = managed_body(obs)
    body_hash = digest(body)
    if association and association.payload_hash == body_hash:
        return "noop"
    marker = literal(identity_marker(finding))
    label = "graphify-" + digest(identity_marker(finding))[:24]
    if not association and event.attempts > 1:
        # A prior create may have reached Jira before a timeout/process death.
        jql = f'project = "{binding.project_key}" AND labels = "{label}"'
        response = client.request(
            "GET",
            "search?" + urlencode({"jql": jql, "fields": "description", "maxResults": 100}),
        )
        matches = [
            i
            for i in response.get("issues", [])
            if marker in (i.get("fields", {}).get("description") or "")
        ]
        if len(matches) != 1 or response.get("total", 0) > 100:
            raise DeliveryError("manual_creation_recovery_required")
        issue = matches[0]
        association = JiraAssociation.objects.create(
            finding=finding,
            connection=binding.connection,
            issue_id=str(issue["id"]),
            issue_key=issue["key"],
        )
    if not association:
        if obs.lifecycle != "open" or obs.data["verification_status"] not in {
            "supported",
            "partially_supported",
        }:
            return "ineligible"
        issue = client.request(
            "POST",
            "issue",
            {
                "fields": {
                    "project": {"key": binding.project_key},
                    "issuetype": {"name": binding.issue_type},
                    "summary": f"[Graphify][{obs.data['id']}] {obs.data['title']}"[:255],
                    "description": body,
                    "labels": ["graphify-review", label],
                }
            },
        )
        association = JiraAssociation.objects.create(
            finding=finding,
            connection=binding.connection,
            issue_id=str(issue["id"]),
            issue_key=issue["key"],
        )
    if not str(association.issue_id).isdigit():
        raise DeliveryError("invalid_issue_id")
    issue_path = "issue/" + association.issue_id
    client.request("GET", issue_path + "?fields=status")
    if association.managed_comment_id:
        client.request(
            "PUT",
            issue_path + "/comment/" + association.managed_comment_id,
            {"body": "Graphify managed update\n" + body},
        )
    else:
        if event.attempts > 1:
            comments = client.request("GET", issue_path + "/comment?maxResults=100")
            matches = [
                c
                for c in comments.get("comments", [])
                if c.get("body", "").startswith("Graphify managed update\n")
                and marker in c.get("body", "")
            ]
            if comments.get("total", 0) > 100 or len(matches) > 1:
                raise DeliveryError("manual_comment_recovery_required")
            if matches:
                association.managed_comment_id = str(matches[0]["id"])
                client.request(
                    "PUT",
                    issue_path + "/comment/" + association.managed_comment_id,
                    {"body": "Graphify managed update\n" + body},
                )
        if not association.managed_comment_id:
            comment = client.request(
                "POST",
                issue_path + "/comment",
                {"body": "Graphify managed update\n" + body},
            )
            association.managed_comment_id = str(comment["id"])
    outcome = "delivered"
    if obs.lifecycle == "resolved" and binding.resolved_transition:
        transitions = client.request("GET", issue_path + "/transitions").get("transitions", [])
        match = next(
            (t for t in transitions if binding.resolved_transition in {str(t["id"]), t["name"]}),
            None,
        )
        if match:
            client.request("POST", issue_path + "/transitions", {"transition": {"id": match["id"]}})
        else:
            outcome = "manual_transition_required"
    association.payload_hash = body_hash
    association.save()
    return outcome


def process_one(org):
    from datetime import timedelta

    # Persist an attempt before outbound work. A crash leaves a recoverable processing event.
    with transaction.atomic():
        Organization.objects.select_for_update().get(pk=org.pk)
        if OutboxEvent.objects.filter(
            state="processing", next_attempt_at__gt=timezone.now()
        ).exists():
            return False
        from django.db.models import Q

        event = (
            OutboxEvent.objects.select_for_update()
            .filter(
                Q(state__in=["pending", "retry"])
                | Q(state="processing", next_attempt_at__lt=timezone.now())
            )
            .filter(next_attempt_at__lte=timezone.now())
            .order_by("created_at", "pk")
            .first()
        )
        if not event:
            return False
        event.attempts += 1
        event.state = "processing"
        event.next_attempt_at = timezone.now() + timedelta(minutes=10)
        event.save()
    with transaction.atomic():
        Organization.objects.select_for_update().get(pk=org.pk)
        event = (
            OutboxEvent.objects.select_for_update()
            .select_related(
                "observation__finding__repository",
                "observation__review_import",
                "binding__repository",
                "binding__connection",
            )
            .get(pk=event.pk)
        )
        status = None
        try:
            outcome = deliver(event)
            event.state, event.completed_at = "done", timezone.now()
        except DeliveryError as error:
            outcome, status = error.outcome, error.status
            event.state = "retry" if error.retry and event.attempts < 8 else "failed"
            delay = max(error.retry_after, min(21600, 60 * 2 ** min(event.attempts, 9)))
            event.next_attempt_at = timezone.now() + timedelta(
                seconds=delay + secrets.randbelow(30)
            )
        except Exception:
            # No exception text: HTTP clients/cryptography may embed credentials or payloads.
            outcome, event.state = "configuration_or_adapter_error", "failed"
        event.save()
        DeliveryAttempt.objects.create(event=event, outcome=outcome, http_status=status)
        audit("delivery." + outcome, target=event.pk)
    return True
