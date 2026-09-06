# Changelog

## 2.7.1

- Licensed the original review kit under GNU AGPL-3.0-only, crediting De Jonckheere Stéphane (humblejok) as its original concept and project creator.
- Added root and packaged license, attribution, and third-party acknowledgement documents, with visible credit in the viewer and documentation.
- Required license and notice files in installer bundles so `.github`-only distributions retain them; documented license scope for host repositories and generated reports.

## 2.7.0

- Added `/check-vulnerability-upgrades` with exact severity filtering, fresh JFrog Xray discovery, conservative fixed-version selection, exact Artifactory availability checks, and a dedicated missing-library inventory.
- Added independent `/update-vulnerable-dependencies` discovery and conservative direct-manifest updates for Maven, Gradle, Python requirements/TOML, NuGet, and npm.
- Added clean-worktree enforcement, per-run backups, affected-target scoping, comment/unsupported-expression protection, generated-lockfile refresh reporting, and sanitized JFrog command provenance.
- Added focused regression coverage for filtering, selection boundaries, Artifactory outcomes, supported declaration formats, target isolation, backups, and independence between the two commands.

## 2.6.0

- Added an idempotent Windows PowerShell installer for distributing the `.github` review kit and corporate Java truststore from Artifactory with mandatory HTTPS and SHA-256 verification.
- Added recoverable `.github`, truststore, and optional JFrog CLI backups without requiring administrator rights or modifying an application's virtual environment.
- Added user-scoped `MAVEN_OPTS` configuration that preserves unrelated options, plus optional verified `jf.exe` installation and idempotent user `PATH` configuration.
- Documented bearer-token, Windows-integrated-authentication, explicit-proxy, truststore-type, TLS bootstrap, and VS Code restart behavior.

## 2.5.0

- Added `security-scanner=auto|jfrog|native` and optional `jfrog-server=<configured-id>` parameters to `/full-project-review`.
- Added first-class JFrog Xray SCA for ASP.NET/NuGet, Spring Maven/Gradle, and common generic-profile package managers, with per-target coverage and structured advisory provenance.
- Added JFrog Secrets as a sanitized Gitleaks replacement when the licensed sub-scan completes; raw findings, snippets, and secret values are never persisted.
- Made `auto` prefer an available configured `jf` CLI while retaining native dependency/Gitleaks fallbacks and recording every provider attempt.
- Kept Semgrep as the independent static-security evidence provider and preserved strict incomplete-evidence behavior for scanner, entitlement, authentication, and resolution failures.
- Added regression coverage for JFrog output parsing, target selection, provider choice/fallback, evidence validation, and secret redaction.

## 2.4.0

- Added explicit `fresh`, `revalidate`, and model-free `rescore` modes with versioned, non-overwriting run directories.
- Added authored-source snapshots and clean-worktree enforcement; random sampling now depends only on source content, sampling configuration, and profile, so grading-policy edits cannot resample files.
- Added stable finding identities/fingerprints and mandatory baseline dispositions (`still_present`, `resolved`, `superseded`, or `not_reproduced`); unreproduced findings remain visible as inconclusive.
- Added deterministic ASP.NET and Spring Boot investigation anchors for DI, async, configuration, tests, error responses, sensitive logging, authentication/security, and operations signals.
- Made the complete framework-anchor disposition inventory part of authoritative JSON and validation, preventing deterministic signals from being silently omitted.
- Added deterministic profile-audit execution with exact commands, target coverage, tool versions, exit codes, completion state, and output hashes.
- Separated evidence execution state from completeness and added mandatory test/SCA/secret/SAST prerequisites for a release decision. A weighted confidence total can no longer compensate for incomplete required evidence.
- Pinned one exact model per route and removed silent model fallback for strict reviews.
- Added deterministic `REVIEW.md` rendering from authoritative schema v2.1 JSON and conservative legacy-baseline rescoring.
- Expanded validation and regression coverage for run identity, baseline continuity, anchors, policy-isolated sampling, mandatory evidence, fingerprints, and deterministic rendering.

## 2.3.0

- Increased severity-by-technical-quality penalties so repeated localized/material maintainability and security findings reduce their categories proportionately.
- Added configurable overall aggregation: `70%` category-weighted average plus `30%` weakest-category score by default.
- Added deterministic `score_breakdown` provenance to final JSON and the viewer, including weighted score, weakest categories, component weights, and pre-gate score.
- Rejected score-neutral supported findings: every supported/partially supported non-informational finding must have at least localized technical-quality impact, even when production impact is `none`.
- Added regression scenarios showing six non-production maintainability/security findings reduce both categories to `6.2` and the overall score to `8.1` while leaving the release recommendation independent.

## 2.2.0

- Added strict `profile=` support to `/full-project-review`, defaulting to `generic`, with canonical aliases, deterministic `auto` detection, unknown/ambiguous-profile rejection, and hashed profile provenance.
- Added evidence-based ASP.NET Core API and Spring Boot rules, framework manifest/config/startup/test discovery, and safe deterministic audit recipes.
- Separated `technical_quality_impact` from `production_impact` so SOLID/Clean Code and framework architecture findings can lower technical category scores without fabricating release blockers.
- Added deterministic framework category caps for verified systemic or repeated design debt, with profile rule IDs validated against the selected profile.
- Updated every review agent, schemas, examples, manifest, final validator, documentation, tests, and HTML viewer for framework profiles and independent quality/release impact reporting.

## 2.1.2

- Renamed the repository-local runtime to `.graphify-review-venv` so it cannot conflict with an application's existing `.venv`.
- Updated automatic bootstrap, documentation, ignore rules, evidence exclusion, sampling exclusion, and tests to use the dedicated name.

## 2.1.1

- Added a pinned Graphify runtime requirement and safe `bootstrap_environment.py` installer.
- Added policy-controlled automatic setup on the first Copilot review call, with manual, check-only, and offline modes.
- Refuse to overwrite an existing directory that is not recognizably a Python virtual environment.

## 2.1.0

- Added policy schema/validation and moved deployment, scoring, confidence, verification, accepted-risk, sampling, and model-routing assumptions into `policy.yaml`.
- Added strict evidence, finding, and review schema v2 contracts.
- Added deterministic evidence normalization, confidence, scoring/release state, sampling, manifest hashing, and standards mapping scripts.
- Enforced CVE provenance, verification thresholds, accepted-risk expiry, deterministic outcomes, and immutable verified finding semantics in the final validator.
- Added separate supported/rejected/inconclusive finding collections, `GO_WITH_ACTIONS`, and `INSUFFICIENT_EVIDENCE`.
- Added standards-mapping and model-routing skills and updated all Copilot agents for evidence-first orchestration.
- Expanded the HTML viewer with confidence, risk, tool, provenance, reproducibility, and all required filters.
- Added dependency-free unit and synthetic end-to-end tests.

## 2.0.0

Production-readiness policy release.

- Added Deployability Reviewer for conventional-server production environments.
- Added Production Readiness Reviewer after adversarial finding verification.
- Added production-readiness policy, severity/release rules, and deterministic-audit skill.
- Added explicit GO / GO_WITH_REMEDIATION / CONDITIONAL / NO_GO / INSUFFICIENT_EVIDENCE decision.
- Added human-facing 0-10 readiness rating.
- Added per-finding production impact: blocker, required_before_production, post_release, none.
- Added release-blocker and pre-production requirement lists.
- Added deployability and operational-readiness category scores.
- Added CVE/dependency-scan and static-security-scan coverage fields.
- Added OWASP/CWE/policy standards labels on findings when evidence-backed.
- Updated validator with release consistency and critical-blocker score-cap checks.
- Updated HTML viewer for production readiness, impact filtering, and security scan coverage.
- Human-readable output renamed to `REVIEW.md`; `review.json` remains authoritative.
