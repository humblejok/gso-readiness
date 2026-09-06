# Graphify Production Review Kit — Implementation Specification for Coding Agent

## Purpose

This document defines the implementation work required to evolve the existing **Graphify VS Code Production Review Kit** into a more deterministic, reproducible, evidence-driven production-readiness assessment tool.

## Implemented reproducibility extension (review kit 2.4)

Generated artifacts now live beneath unique `.github/graphify-review/output/runs/<run-id>/` directories, and current evidence/review schemas are version `2.1`.

The workflow has three explicit modes: `fresh`, `revalidate baseline=<review.json>`, and model-free `rescore baseline=<review.json>`. Fresh LLM discovery is not claimed to be byte-for-byte deterministic. Revalidation makes every baseline finding mandatory and requires an evidence-backed disposition; rescore reuses immutable findings and is deterministic. `REVIEW.md` is rendered by code from authoritative JSON.

Authored-source snapshots identify dirty and non-Git inputs. Strict runs require a clean worktree unless the caller records an explicit override. Sampling uses source snapshot hash + sampling configuration hash + profile hash, so scoring/model policy changes cannot alter the sample. Evidence records execution state separately from completeness, and mandatory test/SCA/secret/SAST evidence must be complete before a release decision is permitted.

The implementation must remain compatible with the existing approach:

- all review tooling lives under `.github/`;
- Graphify is used as the structural evidence source;
- specialist VS Code/Copilot agents perform focused review tasks;
- reusable skills define review methodology;
- deterministic scripts establish facts and calculate scores;
- LLMs interpret evidence but must not invent objective facts;
- the authoritative machine-readable result is JSON;
- the human-readable result is `REVIEW.md`;
- the HTML renderer consumes the JSON output.

This specification implements recommendations **1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17 and 18**.

The following recommendation is intentionally excluded:

- **Point 6** — dedicated CI/CD category

Do not implement that item as part of this task.

---

# 1. Core Design Principles

The implementation must enforce the following architecture:

```text
                Source repository
                      +
                  Graphify output
                      +
             deterministic tool output
                      +
                project policy
                      │
                      ▼
              Evidence normalization
                      │
                      ▼
               Candidate scoping
                      │
       ┌──────────────┼──────────────┐
       ▼              ▼              ▼
 specialist       specialist      specialist
 reviewers         reviewers       reviewers
       │              │              │
       └──────────────┼──────────────┘
                      ▼
               Finding verification
                      │
                      ▼
          Production impact classification
                      │
                      ▼
              Deterministic scoring
                      │
                      ▼
              Final synthesis agent
                      │
                      ▼
                 review.json
                      │
                      ▼
           deterministic renderer
                      │
                      ▼
                  REVIEW.md
```

Mandatory rules:

1. **Deterministic tools establish objective facts.**
2. **Graphify anomalies are investigation signals, not findings.**
3. **LLMs classify and interpret evidence but do not calculate the final score.**
4. **The final synthesizer must not alter verified finding semantics.**
5. **Missing evidence reduces assessment confidence, not project quality by default.**
6. **A recommendation is not automatically a defect.**
7. **A technical severity is not automatically a production blocker.**
8. **Every finding must be traceable to its evidence.**
9. **Rescoring must be reproducible for immutable findings/evidence/policy; fresh discovery must preserve continuity through mandatory baseline revalidation.**
10. **The system must be able to return `INSUFFICIENT_EVIDENCE` rather than inventing certainty.**

---

# 2. Target Repository Structure

Extend the existing `.github`-only package toward the following structure.

```text
.github/
├── agents/
│   ├── review-manager.agent.md
│   ├── evidence-scoper.agent.md
│   ├── architecture-reviewer.agent.md
│   ├── correctness-reviewer.agent.md
│   ├── maintainability-reviewer.agent.md
│   ├── security-reviewer.agent.md
│   ├── testing-reviewer.agent.md
│   ├── deployability-reviewer.agent.md
│   ├── finding-verifier.agent.md
│   ├── production-readiness-reviewer.agent.md
│   └── review-synthesizer.agent.md
│
├── prompts/
│   └── full-project-review.prompt.md
│
├── skills/
│   ├── graphify-evidence/
│   ├── review-rubric/
│   ├── finding-format/
│   ├── verify-findings/
│   ├── production-readiness/
│   ├── deterministic-audits/
│   ├── standards-mapping/
│   └── model-routing/
│
└── graphify-review/
    ├── README.md
    ├── MODEL_STRATEGY.md
    ├── VERSION
    ├── policy.yaml
    ├── policy.schema.json
    │
    ├── schema/
    │   ├── review.schema.json
    │   ├── evidence.schema.json
    │   └── finding.schema.json
    │
    ├── scripts/
    │   ├── reproducibility.py
    │   ├── run_audits.py
    │   ├── finding_identity.py
    │   ├── aspnet_checks.py
    │   ├── spring_checks.py
    │   ├── rescore_review.py
    │   ├── render_review.py
    │   ├── build_evidence.py
    │   ├── calculate_score.py
    │   ├── calculate_confidence.py
    │   ├── select_review_samples.py
    │   ├── validate_review.py
    │   ├── validate_policy.py
    │   ├── hash_inputs.py
    │   └── map_standards.py
    │
    ├── examples/
    │   ├── graphify.example.json
    │   ├── evidence.example.json
    │   ├── review.example.json
    │   └── policy.example.yaml
    │
    ├── viewer/
    │   └── index.html
    │
    └── output/
        └── runs/
            └── <run-id>/
                ├── run-context.json
                ├── evidence/
                │   ├── project-graph.json
                │   ├── normalized-evidence.json
                │   └── manifest.json
                ├── review.json
                └── REVIEW.md
```

If some files already exist, modify them instead of creating duplicates.

---

# 3. Recommendation 1 — Deterministic Final Score

## Objective

Remove responsibility for calculating the final `0–10` production-readiness score from the LLM.

The LLM may classify findings, assign evidence-backed severity, classify production impact, identify uncertainty and propose recommendations. It must **not** choose the overall production-readiness score.

## Implementation

Create:

```text
.github/graphify-review/scripts/calculate_score.py
```

The script must read:

- verified findings;
- category classifications;
- production-impact classifications;
- project policy;
- hard-gate rules;
- assessment coverage.

It must calculate output like:

```json
{
  "score_10": 7.8,
  "category_scores": {
    "architecture": 7.4,
    "correctness": 8.1,
    "maintainability": 7.2,
    "security": 8.6,
    "deployability": 9.0,
    "testing": 6.9,
    "operations": 7.7
  }
}
```

Use configurable category weights from `policy.yaml`:

```yaml
scoring:
  category_weights:
    architecture: 0.15
    correctness: 0.20
    maintainability: 0.10
    security: 0.20
    deployability: 0.15
    testing: 0.10
    operations: 0.10
```

Weights must sum to `1.0`.

Support policy-defined caps:

```yaml
scoring:
  hard_gates:
    critical_blocker:
      max_score: 4.0
      release_recommendation: NO_GO
    exposed_secret:
      max_score: 3.0
      release_recommendation: NO_GO
```

The scoring code, not the LLM, applies the caps.

## Acceptance Criteria

- Two identical inputs produce identical scoring output.
- The synthesizer cannot override `score_10`.
- The validator rejects a final score inconsistent with deterministic calculation.
- A verified critical production blocker applies the configured cap.

---

# 4. Recommendation 2 — Assessment Confidence / Coverage Score

## Objective

Separate project quality from assessment completeness.

Required output:

```json
{
  "assessment_confidence": {
    "score_percent": 78,
    "level": "medium",
    "coverage": {
      "source_analysis": true,
      "graph_analysis": true,
      "tests_executed": true,
      "dependency_vulnerability_scan": false,
      "secret_scan": false,
      "static_security_scan": true
    },
    "limitations": [
      "Dependency CVE status was not established.",
      "No dedicated secret scanner output was available."
    ]
  }
}
```

Create:

```text
.github/graphify-review/scripts/calculate_confidence.py
```

Confidence must be policy-driven:

```yaml
confidence:
  evidence_weights:
    source_analysis: 20
    graph_analysis: 15
    tests_executed: 20
    dependency_vulnerability_scan: 15
    secret_scan: 10
    static_security_scan: 10
    configuration_analysis: 10
```

Missing evidence lowers confidence but must not automatically lower quality scores.

## Acceptance Criteria

- A project may have high readiness and medium confidence.
- The HTML viewer shows both values separately.
- `INSUFFICIENT_EVIDENCE` is possible below a configurable confidence threshold.

---

# 5. Recommendation 3 — Project Policy Profile

## Objective

Move environment-specific assumptions out of prompts and into configuration.

Create:

```text
.github/graphify-review/policy.yaml
```

Minimum policy:

```yaml
version: 1

project:
  type: generic

deployment:
  type: conventional_server
  containers_required: false

security:
  require_sca: true
  require_sast: true
  require_secret_scan: true

testing:
  minimum_coverage_percent: 70

scoring:
  category_weights:
    architecture: 0.15
    correctness: 0.20
    maintainability: 0.10
    security: 0.20
    deployability: 0.15
    testing: 0.10
    operations: 0.10

confidence:
  minimum_for_decision_percent: 60

review:
  random_sampling_percent: 5

accepted_risks: []
```

Agents must read policy before relevant analysis.

If `containers_required: false`, a missing Dockerfile must not be treated as a production defect.

Create:

```text
.github/graphify-review/policy.schema.json
.github/graphify-review/scripts/validate_policy.py
```

## Acceptance Criteria

- Invalid weights are rejected.
- Unknown enums are rejected.
- Project assumptions can change without editing agent prompts.

---

# 6. Recommendation 4 — Evidence-First Review Pipeline

## Objective

Do not start specialist review until objective evidence has been collected and normalized.

Create:

```text
.github/graphify-review/scripts/build_evidence.py
```

Normalized evidence model:

```json
{
  "schema_version": "2.1",
  "repository": {},
  "graph": {},
  "source": {},
  "tests": {},
  "coverage": {},
  "security": {},
  "dependencies": {},
  "configuration": {},
  "tools": [],
  "limitations": []
}
```

Normalize available outputs from:

- Graphify;
- source inspection;
- test execution;
- coverage;
- linters;
- SAST;
- SCA/dependency scanners;
- secret scanners;
- configuration inspection.

Evidence execution states must distinguish:

```text
not_run
passed
failed
unavailable
not_applicable
```

The `review-manager` must build and validate evidence before scoping and specialist review.

---

# 7. Recommendation 5 — CVE Claims Must Be Tool-Backed

## Objective

Prevent hallucinated or memory-based CVE reporting.

An agent may only claim a dependency is affected by a specific CVE when evidence comes from:

- a dependency vulnerability scanner;
- a trusted advisory source;
- structured vulnerability data supplied to the review.

Example evidence:

```json
{
  "type": "dependency_vulnerability",
  "package": "example",
  "installed_version": "1.2.3",
  "advisory_id": "CVE-2026-1234",
  "source": {
    "tool": "pip-audit",
    "tool_version": "..."
  }
}
```

If no SCA scan exists:

```json
{
  "dependency_vulnerability_status": "unknown"
}
```

Never convert that to “No vulnerabilities detected.”

Update `security-reviewer.agent.md` with an explicit prohibition.

The validator must reject a CVE-bearing finding without structured vulnerability evidence.

---

# 8. Recommendation 7 — Separate Severity, Production Impact, Likelihood and Remediation Priority

Every finding must include independent fields:

```json
{
  "severity": "high",
  "production_impact": "required_before_production",
  "likelihood": "medium",
  "remediation_priority": "P1"
}
```

Allowed severity:

```text
critical
high
medium
low
informational
```

Allowed production impact:

```text
blocker
required_before_production
post_release
none
```

Allowed likelihood:

```text
high
medium
low
unknown
not_applicable
```

Allowed remediation priority:

```text
P0
P1
P2
P3
P4
```

These fields must remain independent. A medium-severity issue can be a blocker if it prevents deployment. A technically high-severity issue can be post-release if the verified context supports that classification.

---

# 9. Recommendation 8 — Evidence Provenance

Every negative finding must have structured provenance.

Example:

```json
{
  "evidence": [
    {
      "type": "graphify",
      "source_id": "cycle-17",
      "nodes": ["orders.services", "pricing.services"]
    },
    {
      "type": "source",
      "path": "src/orders/services.py",
      "start_line": 41,
      "end_line": 87,
      "content_hash": "sha256:..."
    },
    {
      "type": "tool",
      "tool": "bandit",
      "tool_version": "1.9.1",
      "rule_id": "B608"
    }
  ]
}
```

High and critical findings must have at least one of:

- source evidence;
- deterministic tool evidence;
- Graphify evidence plus source confirmation.

Graphify-only anomalies remain candidates until source verification.

---

# 10. Recommendation 9 — Reproducible Assessment Manifest

Create:

```text
<run-dir>/evidence/manifest.json
.github/graphify-review/scripts/hash_inputs.py
```

Minimum fields:

```json
{
  "review_id": "...",
  "timestamp_utc": "...",
  "repository": {
    "commit_sha": "...",
    "branch": "...",
    "dirty_worktree": false
  },
  "graphify": {
    "version": "...",
    "input_hash": "sha256:..."
  },
  "policy": {
    "version": 1,
    "hash": "sha256:..."
  },
  "review_kit": {
    "version": "2.x",
    "hash": "sha256:..."
  },
  "models": [],
  "tools": []
}
```

Use SHA-256.

Record model routing metadata where feasible:

```json
{
  "agent": "architecture-reviewer",
  "model": "GPT-5.6 Terra",
  "role": "primary_reviewer"
}
```

Do not store chain-of-thought or hidden reasoning.

---

# 11. Recommendation 10 — Random Sampling in Addition to Graphify Risk Sampling

## Objective

Reduce selection bias from reviewing only structural hotspots.

Create:

```text
.github/graphify-review/scripts/select_review_samples.py
```

Policy:

```yaml
review:
  random_sampling_percent: 5
  random_sampling_min_files: 3
  random_sampling_max_files: 20
```

Selection must be reproducible. Seed using:

```text
authored source snapshot hash + sampling configuration hash + resolved profile hash
```

Scoring, confidence, model-routing, accepted-risk, and unrelated policy changes must not alter the random-control sample.

Output:

```json
{
  "risk_selected": [],
  "random_selected": [],
  "seed": "..."
}
```

The scoper must label items as:

```text
risk_directed
random_control_sample
```

---

# 12. Recommendation 11 — Stronger Verification for High/Critical Findings

Suggested default policy:

```yaml
verification:
  informational:
    reviewers_required: 1
    independent_verifier_required: false
  low:
    reviewers_required: 1
    independent_verifier_required: false
  medium:
    reviewers_required: 1
    independent_verifier_required: true
  high:
    reviewers_required: 2
    independent_verifier_required: true
  critical:
    reviewers_required: 2
    independent_verifier_required: true
    deterministic_evidence_required: true
  production_blocker:
    independent_verifier_required: true
```

For High/Critical findings, reviewer B receives the claim and evidence but not reviewer A's hidden reasoning.

Verification states:

```text
supported
partially_supported
unsupported
inconclusive
```

Only `supported`, or explicitly policy-approved `partially_supported`, findings may affect deterministic scoring.

---

# 13. Recommendation 12 — Verified Findings Must Be Immutable During Synthesis

Once verification completes, these fields are immutable:

```text
finding.id
finding.category
finding.title
finding.severity
finding.production_impact
finding.likelihood
finding.remediation_priority
finding.verification_status
finding.evidence
```

Create:

```text
<run-dir>/evidence/verified-findings.json
```

The synthesizer may summarize, group, order and explain findings. It must not change their semantics.

After synthesis, `validate_review.py` must compare final `review.json` findings with `verified-findings.json` and fail on semantic changes.

---

# 14. Recommendation 13 — Category-Specific Scoring and Policy Weighting

Required categories:

```text
architecture
correctness
maintainability
security
deployability
testing
operations
```

Each category starts at `10.0` and receives deterministic policy-driven penalties.

Example:

```yaml
scoring:
  penalties:
    critical:
      blocker: 6.0
      required_before_production: 5.0
      post_release: 3.0
    high:
      blocker: 4.0
      required_before_production: 3.0
      post_release: 1.5
    medium:
      blocker: 2.5
      required_before_production: 1.5
      post_release: 0.75
    low:
      blocker: 1.0
      required_before_production: 0.5
      post_release: 0.2
```

Exact defaults may be adjusted, but they must be explicit, policy-based, deterministic and documented.

Clamp category scores to `0.0–10.0`.

---

# 15. Recommendation 14 — Expanded Release Assessment States

Required states:

```text
GO
GO_WITH_ACTIONS
CONDITIONAL
NO_GO
INSUFFICIENT_EVIDENCE
```

Suggested semantics:

- `GO`: no verified blockers or required-before-production findings.
- `GO_WITH_ACTIONS`: no blocker; non-blocking remediation should be tracked.
- `CONDITIONAL`: release requires explicitly identified conditions or risk acceptance.
- `NO_GO`: at least one verified policy-defined blocker exists.
- `INSUFFICIENT_EVIDENCE`: evidence confidence is below policy threshold.

Release state must be selected by deterministic code. The LLM explains it but does not choose it.

---

# 16. Recommendation 16 — Accepted Risk / Technical Debt Mechanism

Add policy support:

```yaml
accepted_risks:
  - finding_id: ARCH-017
    status: accepted
    owner: architecture-team
    rationale: >
      Existing coupling is accepted until completion of the Q4 migration.
    accepted_until: 2026-12-31
    production_impact_override: post_release
```

Accepted risks must remain visible, searchable and clearly marked. They must include rationale and preferably owner/expiry.

If current date exceeds `accepted_until`, the exception is expired and no longer suppresses or downgrades release impact.

JSON:

```json
{
  "risk_acceptance": {
    "status": "accepted",
    "owner": "architecture-team",
    "accepted_until": "2026-12-31",
    "rationale": "..."
  }
}
```

Default behavior:

```text
technical score impact remains
release-blocking impact may be waived
```

This preserves technical honesty while allowing explicit governance decisions.

---

# 17. Recommendation 17 — Standards Mapping

Create:

```text
.github/skills/standards-mapping/SKILL.md
.github/skills/standards-mapping/mappings.json
```

Support optional mapping to:

```text
CWE
OWASP Top 10
OWASP ASVS
NIST SSDF
```

Example:

```json
{
  "standards": [
    {
      "framework": "CWE",
      "id": "CWE-798",
      "title": "Use of Hard-coded Credentials"
    }
  ]
}
```

Prefer deterministic mapping when scanner data already provides a CWE or rule mapping.

Use LLM-assisted mapping only when necessary, and require verifier confirmation for uncertain mappings.

Do not attach standards merely because a finding is in the security category.

---

# 18. Recommendation 18 — Explicit Model Routing by Task Complexity

Create:

```text
.github/skills/model-routing/SKILL.md
```

Add policy:

```yaml
models:
  routing:
    trivial:
      preferred:
        - GPT-5.6 Luna
        - Claude Haiku
    normal:
      preferred:
        - GPT-5.6 Luna
        - Claude Haiku
    complex:
      preferred:
        - GPT-5.6 Terra
        - Claude Sonnet
    critical:
      preferred:
        - GPT-5.6 Sol
        - Claude Sonnet

  agent_defaults:
    evidence-scoper: normal
    maintainability-reviewer: normal
    security-reviewer: normal
    testing-reviewer: normal
    deployability-reviewer: normal
    architecture-reviewer: complex
    correctness-reviewer: complex
    finding-verifier: complex
    production-readiness-reviewer: complex
    review-synthesizer: critical
```

Task complexity may be classified as:

```text
trivial
normal
complex
critical
```

Use deterministic factors where possible:

- number of files;
- Graphify subgraph size;
- dependency-edge count;
- cross-module behavior;
- candidate severity;
- security sensitivity;
- whether the result can influence a release blocker.

Constraints:

- skills do not own models;
- agents own model execution;
- routing metadata is recorded in the manifest;
- do not silently upgrade all tasks to the most expensive model.

---

# 19. Revised Review JSON Contract

The authoritative JSON should evolve toward:

```json
{
  "schema_version": "2.1",
  "run": {
    "run_id": "...",
    "mode": "fresh",
    "source_snapshot_hash": "sha256:...",
    "output_directory": "...",
    "baseline": null,
    "finding_reconciliation": [],
    "anchor_dispositions": []
  },
  "review": {
    "id": "review-...",
    "timestamp_utc": "...",
    "repository": {},
    "manifest": {}
  },
  "assessment_confidence": {
    "score_percent": 78,
    "level": "medium",
    "limitations": []
  },
  "production_readiness": {
    "score_10": 7.8,
    "status": "conditional",
    "release_recommendation": "CONDITIONAL",
    "category_scores": {},
    "release_blockers": [],
    "required_before_production": [],
    "recommended_after_release": []
  },
  "findings": [],
  "accepted_risks": [],
  "rejected_findings": [],
  "inconclusive_findings": [],
  "coverage": {},
  "tooling": {},
  "statistics": {}
}
```

Use strict enums for verification state, severity, production impact, likelihood, remediation priority, release recommendation, evidence type and risk-acceptance status.

---

# 20. Required Finding Object

A verified finding should approximately match:

```json
{
  "id": "SEC-004",
  "category": "security",
  "type": "finding",
  "title": "Production secret is stored in source configuration",
  "severity": "critical",
  "production_impact": "blocker",
  "likelihood": "high",
  "remediation_priority": "P0",
  "verification_status": "supported",
  "confidence": 0.98,
  "description": "...",
  "impact": "...",
  "recommendation": "...",
  "evidence": [
    {
      "type": "source",
      "path": "src/config/settings.py",
      "start_line": 18,
      "end_line": 18,
      "content_hash": "sha256:..."
    }
  ],
  "standards": [
    {
      "framework": "CWE",
      "id": "CWE-798",
      "title": "Use of Hard-coded Credentials"
    }
  ],
  "risk_acceptance": null
}
```

---

# 21. Agent Responsibility Changes

## Review Manager

Must orchestrate only:

- validate policy;
- build evidence;
- validate evidence;
- run scoper;
- invoke specialists;
- invoke verifier;
- run deterministic scoring;
- run deterministic confidence calculation;
- invoke final synthesizer;
- validate final output.

It must not perform specialist review itself.

## Evidence Scoper

Must:

- use Graphify risk indicators;
- select investigation candidates;
- select reproducible random samples;
- classify task complexity;
- produce scoped work items.

It must not create final findings.

## Specialist Reviewers

Must:

- inspect assigned evidence;
- inspect source where necessary;
- distinguish candidate from verified fact;
- output structured candidate findings;
- avoid score calculations.

## Finding Verifier

Must:

- attempt to disprove findings;
- verify evidence;
- validate standards mappings when needed;
- apply stronger evidence requirements to High/Critical findings;
- emit verification state.

## Production Readiness Reviewer

Must:

- interpret verified findings and policy;
- classify production consequences;
- identify missing evidence;
- identify suggested pre-production actions;
- not calculate the numerical score;
- not choose the final release recommendation where deterministic rules can do so.

## Review Synthesizer

Must:

- consume immutable verified findings;
- consume deterministic score;
- consume deterministic confidence;
- consume deterministic release recommendation;
- generate human-readable narrative;
- preserve findings exactly;
- produce `REVIEW.md`;
- populate only allowed narrative fields in `review.json`.

---

# 22. Validation Rules

Extend:

```text
.github/graphify-review/scripts/validate_review.py
```

Reject:

1. `GO` with a verified production blocker.
2. `GO` with a verified `required_before_production` finding unless policy explicitly allows it.
3. A rejected finding marked as a blocker.
4. A CVE claim without structured vulnerability evidence.
5. Category weights that do not sum to `1.0`.
6. A final score differing from deterministic calculation.
7. A final release recommendation differing from deterministic state selection.
8. Modified immutable finding fields after synthesis.
9. A High/Critical finding lacking required verification.
10. A Critical finding lacking deterministic evidence when policy requires it.
11. An expired accepted-risk waiver still affecting release decision.
12. Invalid evidence references where local-source validation is required.
13. Invalid standard identifiers where format validation is possible.
14. Unknown enum values.
15. A claimed scan success when evidence state is `not_run`, `unavailable` or `unknown`.

---

# 23. HTML Viewer Changes

Update:

```text
.github/graphify-review/viewer/index.html
```

Display separately:

- Production readiness score
- Assessment confidence
- Release recommendation
- Category scores
- Release blockers
- Required before production
- Post-release recommendations
- Accepted risks
- Expired risks
- Rejected findings
- Inconclusive findings
- Evidence coverage
- Tool execution status
- Reproducibility metadata

Support filters by:

- category;
- severity;
- production impact;
- verification status;
- likelihood;
- remediation priority;
- accepted-risk status;
- standard/framework;
- evidence type.

Show provenance per finding.

---

# 24. REVIEW.md Structure

Generate:

```text
# Production Readiness Review

## Executive Assessment

Production readiness: 7.8 / 10
Assessment confidence: 78%
Release recommendation: CONDITIONAL

## Release Decision
## Required Before Production
## Production Blockers
## Category Scores
## Verified Strengths
## Verified Findings
## Accepted Risks
## Rejected Candidate Findings
## Inconclusive Findings
## Assessment Coverage and Limitations
## Architecture and Graphify Analysis
## Security
## Deployability
## Testing
## Maintainability
## Operational Considerations
## Standards Mapping
## Reproducibility Information
## Prioritized Remediation Plan
```

Do not hide uncertainty.

If confidence is insufficient, clearly show:

```text
Release recommendation: INSUFFICIENT_EVIDENCE
```

---

# 25. Migration Compatibility

If existing `review.json` files use schema v1:

- do not silently reinterpret them as v2;
- retain explicit schema versioning;
- optionally support v1 rendering in the HTML viewer;
- require new fields for v2 validation.

Do not break the Graphify adapter unless required. Prefer extending normalized evidence rather than changing raw Graphify input expectations.

---

# 26. Testing Requirements

Add automated tests for deterministic scripts.

## Scoring

- same input → same score;
- critical blocker → score cap;
- accepted risk changes release decision according to policy;
- category weights honored;
- category scores clamped to `0–10`.

## Confidence

- missing scanner lowers confidence;
- missing scanner does not directly lower quality score;
- confidence threshold triggers `INSUFFICIENT_EVIDENCE`.

## CVE Safety

- CVE finding without scanner/advisory evidence fails validation;
- CVE finding with evidence passes validation.

## Verification

- unsupported finding cannot affect score;
- inconclusive finding cannot become blocker by default;
- critical finding without required evidence fails validation.

## Immutability

- synthesizer-modified severity fails validation;
- synthesizer-modified production impact fails validation.

## Sampling

- same commit/policy → same random sample;
- changed commit → sample may change;
- sample count respects min/max configuration.

## Accepted Risk

- active acceptance behaves according to policy;
- expired acceptance is ignored for release waiver;
- risk remains visible in output.

## Policy

- bad enum fails;
- invalid category-weight sum fails;
- missing mandatory keys fails with actionable message.

---

# 27. Implementation Order

## Phase 1 — Data Contracts

1. policy schema;
2. evidence schema;
3. finding schema;
4. review schema v2.

Do not modify agents before contracts exist.

## Phase 2 — Deterministic Core

Implement:

```text
validate_policy.py
hash_inputs.py
build_evidence.py
select_review_samples.py
calculate_confidence.py
calculate_score.py
validate_review.py
```

Add tests.

## Phase 3 — Finding Lifecycle

Implement:

```text
candidate
    ↓
verification
    ↓
verified-findings.json
    ↓
production classification
    ↓
deterministic scoring
```

Enforce immutability.

## Phase 4 — Policy and Risk Acceptance

Implement policy loading, accepted-risk lookup, expiry, release impact and reporting.

## Phase 5 — Agent Changes

Update all agents to respect evidence-first flow, policy, provenance, CVE restrictions, verification requirements and model routing.

## Phase 6 — Standards Mapping

Implement deterministic mappings where possible and verifier-assisted mappings only where needed.

## Phase 7 — Output

Update `review.json`, `REVIEW.md` and HTML viewer.

## Phase 8 — End-to-End Tests

Use at least three synthetic projects.

### Project A — Healthy

Expected:

```text
GO or GO_WITH_ACTIONS
high confidence
high readiness score
few findings
```

### Project B — Serious Defects

Include:

- verified hardcoded production secret;
- weak test evidence;
- Graphify structural cycle.

Expected:

```text
NO_GO
score cap applied
security blocker visible
cycle affects score only if source verification supports it
```

### Project C — Insufficient Evidence

No SCA, SAST, tests or reliable source analysis.

Expected:

```text
INSUFFICIENT_EVIDENCE
```

Do not automatically assign a poor readiness score merely because evidence is missing.

---

# 28. Non-Goals

Do not implement in this task:

- CI/CD pipeline review as a separate category;
- automatic remediation;
- automatic source-code modification;
- automatic acceptance of technical debt;
- autonomous deployment;
- hidden model reasoning storage;
- container requirements unless policy explicitly enables them.

---

# 29. Definition of Done

- [ ] all tooling remains under `.github/`;
- [ ] `policy.yaml` controls environment-specific review behavior;
- [ ] final production score is deterministic;
- [ ] final release state is deterministic;
- [ ] assessment confidence is separate from production quality;
- [ ] Graphify anomalies remain investigation signals until verified;
- [ ] CVE claims require structured evidence;
- [ ] findings separate severity, production impact, likelihood and remediation priority;
- [ ] every finding has evidence provenance;
- [ ] review manifest records commit, policy, tool and model metadata;
- [ ] random sampling is deterministic and reproducible;
- [ ] review outputs are versioned and previous run findings cannot disappear without a disposition;
- [ ] source snapshots and finding fingerprints provide stable cross-run identities;
- [ ] every deterministic framework anchor has an authoritative evidence-backed disposition;
- [ ] rescoring immutable baseline findings does not invoke a model;
- [ ] High/Critical findings receive stronger verification;
- [ ] verified finding semantics are immutable during synthesis;
- [ ] category weighting is policy-driven;
- [ ] `INSUFFICIENT_EVIDENCE` is supported;
- [ ] accepted risks are explicit, visible and expirable;
- [ ] standards mappings are evidence-based;
- [ ] model routing is explicit and recorded;
- [ ] JSON schema and validator enforce these rules;
- [ ] HTML viewer supports all new fields;
- [ ] `REVIEW.md` clearly distinguishes quality, confidence and release decision;
- [ ] automated tests cover scoring, confidence, verification and validation;
- [ ] existing Graphify input compatibility is preserved where practical.

---

# 30. Final Implementation Principle

> **Use deterministic mechanisms to establish facts and calculate outcomes. Use Graphify to identify structural risk. Use LLM agents to interpret evidence and explain consequences. Never use an LLM where a deterministic rule can provide a more reproducible answer.**

The completed implementation should produce a review strict enough for production governance while remaining evidence-based, reproducible, auditable and capable of concluding that a project is healthy when the evidence supports that conclusion.
