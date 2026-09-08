"""Offline schema validation: importing a review never fetches a URL or executes commands."""

import hashlib
import json
import re
from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ValidationError
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def identity_fingerprint(identity):
    normalized = {
        "defect_key": re.sub(r"[^a-z0-9]+", "-", identity["defect_key"].lower()).strip("-")[:96]
        or "finding",
        "primary_location": identity["primary_location"].replace("\\", "/").strip(),
        **{k: identity[k].strip() for k in ("symbol", "profile_rule_id", "anchor_id")},
    }
    if normalized != identity:
        raise ValidationError("Finding identity is not normalized.")
    return "sha256:" + digest(normalized)


@lru_cache
def review_validator():
    schemas = [
        json.loads((settings.BASE_DIR / "contracts" / f).read_text())
        for f in ("review.schema.json", "finding.schema.json")
    ]
    registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas)
    # Decimal multipleOf avoids rejecting valid one-decimal JSON floats such as 8.2.
    from decimal import Decimal

    from jsonschema.validators import extend

    def multiple_of(validator, divisor, instance, schema):
        if isinstance(instance, (int, float)) and not isinstance(instance, bool):
            if Decimal(str(instance)) % Decimal(str(divisor)):
                from jsonschema.exceptions import ValidationError as SchemaError

                yield SchemaError("Number is not an allowed increment.")

    validator = extend(Draft202012Validator, {"multipleOf": multiple_of})
    return validator(schemas[0], registry=registry, format_checker=FormatChecker())


def text(value, maximum=1000, minimum=1):
    return isinstance(value, str) and minimum <= len(value.strip()) <= maximum


def strict_object(value, required, optional=()):
    if (
        not isinstance(value, dict)
        or set(value) - set(required) - set(optional)
        or not set(required) <= set(value)
    ):
        raise ValidationError("Missing or unknown contract fields.")


def validate_envelope(data, idempotency_key):
    strict_object(data, ("schema_version", "repository", "source", "review", "remediations"))
    if data["schema_version"] != "1.0":
        raise ValidationError("Unsupported import envelope version.")
    repo, source, review = data["repository"], data["source"], data["review"]
    strict_object(repo, ("external_id", "name", "default_branch"), ("clone_url",))
    strict_object(source, ("commit_sha", "branch"))
    if not all(
        text(repo.get(k), n)
        for k, n in (("external_id", 500), ("name", 200), ("default_branch", 200))
    ):
        raise ValidationError("Invalid repository metadata.")
    if (
        not text(source["branch"], 200)
        or not isinstance(source["commit_sha"], str)
        or not re.fullmatch(r"[a-fA-F0-9]{7,64}", source["commit_sha"])
    ):
        raise ValidationError("Invalid source revision or branch.")
    if repo.get("clone_url"):
        from urllib.parse import urlsplit

        url = urlsplit(repo["clone_url"])
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or len(repo["clone_url"]) > 1000
        ):
            raise ValidationError("clone_url must be credential-free HTTPS.")
    error = next(review_validator().iter_errors(review), None)
    if error:
        # Schema error messages can contain submitted secrets; expose only the path.
        raise ValidationError(
            "Invalid review schema at " + ".".join(str(p) for p in error.absolute_path)
        )
    run = review["run"]
    if not text(run["run_id"], 250) or idempotency_key != f"{repo['external_id']}:{run['run_id']}":
        raise ValidationError("Idempotency-Key must match repository external ID and run ID.")
    revision = review["review"]["repository"].get("commit_sha")
    if revision and revision.lower() != source["commit_sha"].lower():
        raise ValidationError("Source revision conflicts with the authoritative review.")
    found = {}
    statuses = {
        "findings": {"supported", "partially_supported"},
        "inconclusive_findings": {"inconclusive"},
        "rejected_findings": {"unsupported"},
    }
    for group, allowed in statuses.items():
        for finding in review[group]:
            fp = finding["fingerprint"]
            if fp in found or fp != identity_fingerprint(finding["identity"]):
                raise ValidationError("Duplicate fingerprint or identity mismatch.")
            if finding["verification_status"] not in allowed:
                raise ValidationError("Verification status conflicts with collection.")
            if finding["identity"]["profile_rule_id"] != (finding.get("profile_rule_id") or ""):
                raise ValidationError("Profile rule identity mismatch.")
            if not text(finding["title"], 1000) or not text(finding["id"], 100):
                raise ValidationError("Finding title or ID exceeds limits.")
            found[fp] = finding
    if len(found) > 1000 or not isinstance(data["remediations"], list):
        raise ValidationError("Finding limit exceeded or invalid remediation collection.")
    plans, steps_count = {}, 0
    required = (
        "schema_version",
        "finding_fingerprint",
        "generated_from_commit",
        "objective",
        "preconditions",
        "constraints",
        "non_goals",
        "implementation_steps",
        "acceptance_criteria",
        "validation_commands",
        "risks_and_rollback",
    )
    for plan in data["remediations"]:
        strict_object(plan, required, ("notes", "validation_notes"))
        fp = plan["finding_fingerprint"]
        if not isinstance(fp, str) or fp not in found or fp in plans:
            raise ValidationError("Unknown or duplicate remediation fingerprint.")
        if (
            plan["schema_version"] != "1.0"
            or plan["generated_from_commit"] != source["commit_sha"]
            or not text(plan["objective"])
        ):
            raise ValidationError("Invalid or stale remediation plan.")
        for key in (
            "preconditions",
            "constraints",
            "non_goals",
            "acceptance_criteria",
            "validation_commands",
            "risks_and_rollback",
        ):
            if not isinstance(plan[key], list) or not all(text(v, 10000) for v in plan[key]):
                raise ValidationError("Remediation lists must contain bounded nonempty strings.")
        for key in ("notes", "validation_notes"):
            if key in plan and not text(plan[key], 10000):
                raise ValidationError("Invalid remediation notes.")
        if not plan["acceptance_criteria"] or (
            not plan["validation_commands"] and not text(plan.get("validation_notes"))
        ):
            raise ValidationError("Acceptance criteria and validation instructions are required.")
        steps = plan["implementation_steps"]
        if not isinstance(steps, list) or not steps:
            raise ValidationError("Implementation steps are required.")
        orders = []
        for step in steps:
            strict_object(step, ("order", "instruction"), ("affected_paths", "affected_symbols"))
            if type(step["order"]) is not int or not text(step["instruction"], 10000):
                raise ValidationError("Invalid implementation step.")
            for key in ("affected_paths", "affected_symbols"):
                if key in step and (
                    not isinstance(step[key], list) or not all(text(v, 1000) for v in step[key])
                ):
                    raise ValidationError("Invalid affected paths or symbols.")
            orders.append(step["order"])
        if sorted(orders) != list(range(1, len(steps) + 1)):
            raise ValidationError("Step order must be unique and contiguous from one.")
        if len(canonical(plan).encode()) > 65536:
            raise ValidationError("Remediation plan exceeds 64 KiB.")
        steps_count += len(steps)
        plans[fp] = plan
    if steps_count > 500 or any(f["fingerprint"] not in plans for f in review["findings"]):
        raise ValidationError("Missing remediation plan or too many steps.")
    reconciliation = run["finding_reconciliation"]
    if run["mode"] != "revalidate" and reconciliation:
        raise ValidationError("Only revalidation can change lifecycle.")
    seen = set()
    for item in reconciliation:
        fp = item["baseline_fingerprint"]
        if fp in seen:
            raise ValidationError("Duplicate reconciliation.")
        seen.add(fp)
        if item["status"] == "still_present" and (
            fp not in found
            or found[fp]["id"] != item["current_id"]
            or found[fp]["verification_status"] not in statuses["findings"]
        ):
            raise ValidationError(
                "Still-present reconciliation requires a supported current finding."
            )
        if item["status"] in {"resolved", "superseded"} and fp in found:
            raise ValidationError("Resolved/superseded findings cannot also be current findings.")
        if item["status"] == "superseded" and item.get("replacement_fingerprint") not in found:
            raise ValidationError("Superseded finding requires a current replacement.")
    # Reject recognizable raw credentials; no heuristic can prove arbitrary prose secret-free.
    encoded = canonical(data)
    if re.search(
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bAKIA[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{30,}|\bBearer\s+[A-Za-z0-9._~-]{20,}",
        encoded,
    ):
        raise ValidationError(
            "Possible raw credential detected. Sanitize the review before upload."
        )
    return plans


def render_remediation(plan):
    if not plan:
        return "No remediation plan for this observation."
    lines = [
        "# Remediation",
        "",
        plan["objective"],
        "",
        f"Source revision: {plan['generated_from_commit']}",
    ]
    for field in (
        "preconditions",
        "constraints",
        "non_goals",
        "implementation_steps",
        "acceptance_criteria",
        "validation_commands",
        "risks_and_rollback",
    ):
        lines.extend(["", "## " + field.replace("_", " ").title(), ""])
        values = (
            sorted(plan[field], key=lambda x: x["order"])
            if field == "implementation_steps"
            else plan[field]
        )
        for item in values:
            if isinstance(item, dict):
                lines.append(f"{item['order']}. {item['instruction']}")
                for kind in ("affected_paths", "affected_symbols"):
                    if item.get(kind):
                        lines.append("   " + kind.replace("_", " ") + ": " + ", ".join(item[kind]))
            else:
                lines.append("- " + item)
    for field in ("validation_notes", "notes"):
        if plan.get(field):
            lines.extend(["", plan[field]])
    return "\n".join(lines) + "\n"
