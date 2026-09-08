---
name: full-project-review
description: Run the complete evidence-first Graphify production-readiness review and generate JSON plus REVIEW.md.
argument-hint: "graph=path/to/graphify-output.json [profile=generic|aspnet-core-api|spring-boot|auto] [security-scanner=auto|jfrog|native] [jfrog-server=config-id] [mode=fresh|revalidate|rescore] [baseline=path/to/review.json] [scope=optional/path] [allow-dirty=false]"
agent: Graphify Review
---

Run the full Graphify production-readiness review workflow for this workspace.

First read saved settings using `setup_review.py show --repository .` as described by the manager. Explicit parameters win; otherwise use saved `profile`, `security_scanner` and `jfrog_server_id` before the fallback defaults below. Offer `/setup-review` when project setup is incomplete. Use `/configure-review` to change saved values and `/doctor-review` for diagnostics.

Use any `graph=...`, `profile=...`, `security-scanner=...`, `jfrog-server=...`, `mode=...`, `baseline=...`, `scope=...`, and `allow-dirty=...` values supplied with this prompt. `profile` defaults to `generic`; accept documented aliases such as `asp.net` and `spring`, normalize them to canonical IDs, and reject unknown or ambiguous values. `security-scanner` defaults to `auto`, which prefers an available configured JFrog CLI for dependency and secret evidence and otherwise uses native scanners; `jfrog` requires JFrog results without a silent fallback, and `native` uses the profile-native dependency audit plus Gitleaks. `jfrog-server` is an optional existing JFrog CLI configuration ID, never a URL, token, or password. `mode` defaults to `fresh`. `revalidate` and `rescore` require a machine-readable baseline `review.json`; `fresh` rejects a baseline. If `graph` is omitted outside rescore mode, locate the most plausible Graphify export and record ambiguity rather than inventing facts.

Validate and read `.github/graphify-review/policy.yaml` first, then resolve the selected profile into a hashed profile artifact. Build normalized evidence before specialist review. Evaluate code quality, security, policy-defined deployability, modularity/maintainability, testing, and operational suitability, applying the selected framework rules only when source evidence establishes applicability. Keep technical-quality impact separate from production/release impact. Do not require containers when policy makes them optional. Graphify anomalies and framework checklist items are investigation signals and require source verification before becoming findings.

The authoritative deliverable is `<run-dir>/review.json`; the human companion is deterministically rendered as `<run-dir>/REVIEW.md`. Never overwrite an earlier run. Scores, confidence, and release state must come from deterministic scripts. Validate JSON against policy, normalized evidence, immutable verified findings, run identity, and baseline reconciliation before finishing.

After validation/rendering, follow the manager's mandatory local Hub export completion step for every mode, including `revalidate` and `rescore`. If a Hub is configured, produce and verify `<run-dir>/hub/import-envelope.json` for this exact new run, with current source-bound remediation plans and complete reconciliation. An older/baseline envelope is not an updated result. Report `ready`, `incomplete` (with blocker), or `not configured` explicitly; missing plans/metadata must never be hidden behind a successful review message. Do not upload automatically.
