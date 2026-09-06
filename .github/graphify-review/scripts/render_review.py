#!/usr/bin/env python3
"""Render REVIEW.md deterministically from authoritative review.json."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

CATEGORY_ORDER = ("architecture", "correctness", "maintainability", "security", "deployability", "testing", "operations")


def title(value: str) -> str:
    return value.replace("_", " ").title()


def finding_line(item: dict[str, Any]) -> str:
    identity = item.get("fingerprint", "")
    suffix = f" Fingerprint: `{identity}`." if identity else ""
    return (
        f"- `{item.get('id', '')}` — **{item.get('title', '')}** "
        f"({item.get('severity', '')}, {item.get('technical_quality_impact', '')} technical-quality impact, "
        f"{item.get('production_impact', '')}, {item.get('remediation_priority', '')}; "
        f"{item.get('verification_status', '')}). {item.get('impact', '')} "
        f"Recommendation: {item.get('recommendation', '')}.{suffix}"
    ).replace("..", ".")


def anchor_line(item: dict[str, Any]) -> str:
    target = f" as `{item['finding_id']}`" if item.get("finding_id") else ""
    return f"- `{item.get('anchor_id', '')}` — **{item.get('status', '')}**{target}. {item.get('rationale', '')}"


def section(lines: list[str], heading: str, content: list[str]) -> None:
    lines.extend([f"## {heading}", ""])
    lines.extend(content or ["None."])
    lines.append("")


def render(review: dict[str, Any]) -> str:
    readiness = review.get("production_readiness", {})
    confidence = review.get("assessment_confidence", {})
    breakdown = readiness.get("score_breakdown", {})
    run = review.get("run", {})
    lines = [
        "# Production Readiness Review", "", "## Executive Assessment", "",
        f"**Production readiness quality:** {readiness.get('score_10', 0):.1f} / 10  ",
        f"**Assessment confidence:** {confidence.get('score_percent', 0)}% ({confidence.get('level', 'low')})  ",
        f"**Status:** {readiness.get('status', '')}  ",
        f"**Release recommendation:** {readiness.get('release_recommendation', '')}", "",
        readiness.get("summary", ""), "",
        "## Release Decision", "",
        f"**{readiness.get('release_recommendation', '')}.** {readiness.get('scoring_notes', '')}", "",
    ]
    unmet = confidence.get("unmet_decision_requirements", [])
    if unmet:
        lines.extend(["The following mandatory decision evidence is incomplete: " + ", ".join(f"`{item}`" for item in unmet) + ".", ""])

    finding_by_id = {item.get("id"): item for item in review.get("findings", [])}
    for heading, key in (
        ("Production Blockers", "release_blockers"),
        ("Required Before Production", "required_before_production"),
        ("Recommended After Release", "recommended_after_release"),
    ):
        identifiers = readiness.get(key, [])
        section(lines, heading, [finding_line(finding_by_id[item]) for item in identifiers if item in finding_by_id])

    score_lines = ["| Category | Score |", "|---|---:|"]
    for category in CATEGORY_ORDER:
        score_lines.append(f"| {title(category)} | {readiness.get('category_scores', {}).get(category, 0):.1f} / 10 |")
    section(lines, "Category Scores", score_lines)
    weakest = ", ".join(title(item) for item in breakdown.get("weakest_categories", []))
    section(lines, "Score Calculation", [
        f"- Category-weighted component: **{breakdown.get('weighted_category_score_10', 0):.1f}/10** × **{breakdown.get('weighted_average_weight', 0) * 100:.0f}%**.",
        f"- Weakest category: **{weakest}**, **{breakdown.get('weakest_category_score_10', 0):.1f}/10** × **{breakdown.get('weakest_category_weight', 0) * 100:.0f}%**.",
        f"- Pre-gate score: **{breakdown.get('pre_gate_score_10', 0):.1f}/10**.",
        f"- Applied profile caps: **{len(readiness.get('applied_category_caps', []))}**; applied hard gates: **{len(readiness.get('applied_hard_gates', []))}**.",
    ])

    strengths = [
        f"- `{item.get('id', '')}` — **{item.get('title', '')}** ({item.get('category', '')}). {item.get('description', '')}"
        for item in sorted(review.get("strengths", []), key=lambda item: str(item.get("id", "")))
    ]
    section(lines, "Verified Strengths", strengths)
    section(lines, "Verified Findings", [
        finding_line(item) for item in sorted(review.get("findings", []), key=lambda item: (CATEGORY_ORDER.index(item.get("category")) if item.get("category") in CATEGORY_ORDER else 99, str(item.get("id", ""))))
    ])
    section(lines, "Rejected Candidate Findings", [finding_line(item) for item in sorted(review.get("rejected_findings", []), key=lambda item: str(item.get("id", "")))])
    section(lines, "Inconclusive Findings", [finding_line(item) for item in sorted(review.get("inconclusive_findings", []), key=lambda item: str(item.get("id", "")))])

    accepted_risks = [
        f"- `{item.get('finding_id', '')}` — owner `{item.get('owner', '')}`, accepted until "
        f"`{item.get('accepted_until', '')}`. {item.get('rationale', '')}"
        for item in sorted(review.get("accepted_risks", []), key=lambda item: str(item.get("finding_id", "")))
    ]
    section(lines, "Accepted Risks", accepted_risks)

    reconciliation = run.get("finding_reconciliation", [])
    reconciliation_lines = [
        f"- `{item.get('baseline_id', item.get('baseline_fingerprint', ''))}` — **{item.get('status', '')}**. {item.get('rationale', '')}"
        for item in sorted(reconciliation, key=lambda item: str(item.get("baseline_id", item.get("baseline_fingerprint", ""))))
    ]
    section(lines, "Baseline Reconciliation", reconciliation_lines if run.get("mode") != "fresh" else ["Not applicable for a fresh discovery run."])

    anchor_lines = [
        anchor_line(item)
        for item in sorted(run.get("anchor_dispositions", []), key=lambda item: str(item.get("anchor_id", "")))
    ]
    section(lines, "Framework Anchor Dispositions", anchor_lines)

    coverage = review.get("coverage", {})
    coverage_lines = [f"- {key.replace('_', ' ').title()}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}" for key, value in sorted(coverage.items())]
    coverage_lines.extend(f"- Limitation: {item}" for item in confidence.get("limitations", []))
    section(lines, "Assessment Coverage and Limitations", coverage_lines)

    tool_lines = ["| Evidence/tool | State | Completion |", "|---|---|---|"]
    for name, item in sorted(review.get("tooling", {}).items()):
        tool_lines.append(f"| {name} | {item.get('state', '')} | {item.get('completion', 'not_recorded')} |")
    section(lines, "Tooling State", tool_lines)

    meta = review.get("review", {})
    repository = meta.get("repository", {})
    profile = meta.get("profile", {})
    section(lines, "Reproducibility Information", [
        f"- Run ID: `{run.get('run_id', '')}`",
        f"- Run mode: `{run.get('mode', '')}`",
        f"- Source snapshot: `{run.get('source_snapshot_hash', '')}`",
        f"- Review ID: `{meta.get('id', '')}`",
        f"- Timestamp (UTC): `{meta.get('timestamp_utc', '')}`",
        f"- Commit: `{repository.get('commit_sha', '')}`",
        f"- Branch: `{repository.get('branch', '')}`",
        f"- Dirty worktree: `{repository.get('dirty_worktree', '')}`",
        f"- Profile: `{profile.get('id', '')}` version `{profile.get('version', '')}`; hash `{profile.get('hash', '')}`",
    ])
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    review = json.loads(Path(args.review).read_text(encoding="utf-8"))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(review), encoding="utf-8", newline="\n")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
