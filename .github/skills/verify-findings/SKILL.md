---
name: verify-findings
description: Adversarially verify candidate findings under policy-specific evidence and reviewer thresholds.
user-invocable: false
---

# Verify Findings

Attempt to disprove each candidate by inspecting cited evidence, callers/callees, tests, guards, configuration, design constraints, and Graphify freshness.

Verdicts are `supported`, `partially_supported`, `unsupported`, and `inconclusive`. A partially supported claim must be narrowed to exactly what evidence establishes. Unsupported and inconclusive candidates cannot constrain production or affect scores by default.

Verify `technical_quality_impact` independently from severity and production impact. When `profile_rule_id` is present, verify the ID exists in the resolved profile, allows the finding category, and has its stated evidence. A framework convention or profile rule is an investigation lens, not proof. Supported/partially supported non-informational findings must be at least `localized`, including findings with `production_impact: none`; unsupported and inconclusive verdicts must set technical-quality impact to `none`.

Enforce `.github/graphify-review/policy.yaml` verification thresholds:

- Medium and above normally require an independent verifier.
- High/Critical normally require two reviewers and independent verification.
- Critical requires deterministic evidence when policy enables it.
- Production blockers require independent verification.

Reviewer B receives the claim and evidence, not reviewer A's hidden reasoning. Graphify-only evidence is insufficient for High/Critical support. Validate uncertain standards mappings. Never turn absence of proof into proof of absence.
