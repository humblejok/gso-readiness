---
name: review-rubric
description: Apply a neutral, evidence-based scoring and severity rubric to project and production-readiness reviews.
user-invocable: false
---

# Review Rubric

## Bias controls

- The objective is accurate characterization, not finding defects.
- "No material issue found" is valid.
- Do not reward or penalize technologies, architectural styles, ORMs, frameworks, monoliths, microservices, functional/OOP style, test frameworks, or containerization merely for being fashionable or unfashionable.
- Prefer project-specific consequences over generic best-practice statements.
- Distinguish "could be improved" from "creates material production risk".
- Give strengths the same evidence standard as weaknesses.
- Missing evidence lowers confidence; it does not prove poor quality.

## Severity

- `critical`: credible path to catastrophic security, data-loss, financial, safety, or system-wide correctness impact; immediate action warranted.
- `high`: material production risk, serious security weakness, or defect likely to cause significant failures or costly change.
- `medium`: concrete design/correctness/maintainability/deployability problem with meaningful but contained impact.
- `low`: real but limited issue; local friction, readability, or low-impact robustness concern.
- `info`: observation or improvement opportunity without demonstrated harmful impact.

Never assign `critical` or `high` solely from a structural metric, checklist item, framework preference, or missing container asset.

## Confidence

Use `0.0` to `1.0`:
- `0.90-1.00`: directly established by source/tool/test/config evidence with little plausible counter-interpretation.
- `0.75-0.89`: strong evidence, minor uncertainty.
- `0.50-0.74`: plausible and supported, but important context/runtime behavior is missing.
- `<0.50`: generally belongs in `inconclusive`, not verified findings.

## Technical quality impact

- `systemic`: foundational or cross-cutting debt established across an important dependency, lifecycle, or responsibility boundary.
- `material`: meaningful debt in an important component, or a repeated weakness with concrete change, test, or correctness cost.
- `localized`: a real but contained quality issue.
- `none`: no scoreable technical-quality consequence.

Classify this independently from production impact. A maintainability issue can be technically material while legitimately having `production_impact: none`; it still lowers its category score. Conversely, a localized configuration bug can require correction before production without being systemic architecture debt.

A supported/partially supported non-informational finding cannot use `none`. This prevents a report from displaying real findings that have no effect on the grade.

## Deterministic scoring

Reviewers classify evidence; they do not calculate category or overall scores. Every category starts at `10.0`. `.github/graphify-review/policy.yaml` defines severity-by-technical-quality penalties, category weights, the weighted-average/weakest-category aggregation weights, release hard gates, and accepted-risk behavior. The selected profile may define deterministic category caps for verified systemic or repeated framework-rule findings. `.github/graphify-review/scripts/calculate_score.py` is the only authority for category scores, score breakdown, `score_10`, caps, and release recommendation.

Missing evidence affects `calculate_confidence.py`, never a category penalty by default. Unsupported/inconclusive candidates never affect scoring. A synthesizer may explain deterministic results but cannot override them.
