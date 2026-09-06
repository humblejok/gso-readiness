# Production Readiness Review

## Executive Assessment

**Production readiness quality:** 5.7 / 10  
**Assessment confidence:** 60% (medium)  
**Status:** conditional  
**Release recommendation:** CONDITIONAL

The deterministic quality score is **5.7/10**. Assessment confidence is **60%**, exactly at the policy decision threshold, so the deterministic release recommendation remains **CONDITIONAL** rather than `INSUFFICIENT_EVIDENCE`. The review contains 13 verified findings: one high, nine medium, and three low. Four are required before production, eight are recommended after release, and one has no production impact. No production blocker was verified.

No ASP.NET Core profile category cap was applied. No release hard gate was applied. There are no accepted or expired risks.

## Release Decision

**Release recommendation: CONDITIONAL.** Production release is conditional on resolving the four required-before-production findings: `DEPLOYABILITY-C001`, `COR-C001`, `COR-C002`, and `SEC-C001`.

The dependency-vulnerability scan passed with no listed advisories. The secret and static-security scanners were unavailable, test execution was unavailable, and coverage was not run. Those states limit assurance and are not clean results. The recorded build failed because the configured local NuGet source `C:\Dev\nuget` does not exist, while the recorded formatter/analyzer command failed with whitespace violations.

## Required Before Production

- `DEPLOYABILITY-C001` — **Build requires workstation-local inputs** (high, material technical-quality impact, P0; supported). Replace machine-local NuGet and assembly inputs with package-managed, repository-neutral dependencies.
- `COR-C001` — **Dynamics endpoint lacks startup validation** (medium, material, P1; supported). Validate required Dynamics URI options during startup.
- `COR-C002` — **Non-mock batch retrieval returns empty results** (medium, material, P0; supported). Implement `RetrieveMultipleAsync` and verify the non-mock simple-batch path.
- `SEC-C001` — **No in-process authorization boundary is configured** (medium, systemic, P0; partially supported). Evidence and test the trusted upstream enforcement boundary or add explicit in-process authorization.

## Production Blockers

None verified (0).

## Category Scores

| Category | Score |
|---|---:|
| Architecture | 9.5 / 10 |
| Correctness | 4.2 / 10 |
| Maintainability | 7.2 / 10 |
| Security | 5.2 / 10 |
| Deployability | 4.8 / 10 |
| Testing | 7.2 / 10 |
| Operations | 8.8 / 10 |

**Applied profile category caps:** none.  
**Applied release hard gates:** none.

Profile category caps and release hard gates are distinct mechanisms; neither was applied in this review.

## Score Calculation

The deterministic score breakdown copied from `score.json` is:

- Category-weighted component: **6.3/10**, weighted at **70%**.
- Weakest category: **correctness**, scored **4.2/10**, weighted at **30%**.
- Pre-gate score: **5.7/10**.
- Applied hard gates: **none**; therefore no hard-gate cap changed the pre-gate score.

The component calculation is $6.3 \times 0.70 + 4.2 \times 0.30 = 5.67$, reported deterministically as **5.7/10**. A finding with `production_impact: none` still retains its technical-quality penalty; absence of production impact does not remove that penalty from category or overall scoring.

## Verified Strengths

- `STR-001` — **Explicit DI composition** (architecture). Integration services are explicitly scoped in `src/Ubp.Dione.ClientData.GraphQL.API/Infrastructure/DependencyInjection.cs:34-155`.
- `STR-002` — **Bounded outbound client timeouts** (operations). Named clients use finite timeouts in `src/Ubp.Dione.ClientData.GraphQL.API/Infrastructure/DependencyInjection.cs:43-213`.

These are source-backed strengths and do not imply successful build or test execution.

## Verified Findings

- `DEPLOYABILITY-C001` — **Build requires workstation-local inputs** (high, material, required before production, P0; supported). Clean hosts cannot reproduce the build.
- `COR-C001` — **Dynamics endpoint lacks startup validation** (medium, material, required before production, P1; supported). Requests can fail after startup.
- `COR-C002` — **Non-mock batch retrieval returns empty results** (medium, material, required before production, P0; supported). Batch-backed fields can be silently empty.
- `COR-C003` — **Dynamics calls drop request cancellation** (medium, localized, post-release, P2; supported). Abandoned work continues until the 30-second timeout.
- `COR-C004` — **Response formatter blocks on RegData I/O** (medium, localized, post-release, P2; supported). Slow RegData adds a synchronous response wait.
- `DEPLOYABILITY-C002` — **WDX and RegData endpoint validation is deferred** (medium, localized, post-release, P2; partially supported). Endpoint omissions can emerge after startup.
- `ARCH-C001` — **Mock registration builds a temporary provider** (low, localized, no production impact, P4; supported). Conditional mock setup creates avoidable ownership ambiguity.
- `MAINT-C002` — **BaseEntityService is a broad shared change boundary** (medium, material, post-release, P3; supported). Its broad responsibility set increases regression cost.
- `MAINT-C003` — **Excluded legacy GraphQL code remains beside active code** (low, localized, post-release, P3; partially supported). The retained excluded tree adds search and maintenance ambiguity.
- `SEC-C001` — **No in-process authorization boundary is configured** (medium, systemic, required before production, P0; partially supported). Unauthorized access is possible if upstream controls are absent.
- `SEC-C002` — **Dynamics query criteria are logged** (medium, localized, post-release, P2; supported). Sensitive request metadata can persist in rolling logs.
- `TEST-C001` — **A substantial set of tests is currently skipped** (medium, material, post-release, P2; partially supported). Ninety-eight skipped tests across 13 files provide no executable regression check for those scenarios.
- `TEST-C002` — **Several batching tests lack a strict batching bound** (low, localized, post-release, P3; partially supported). Some request-amplification regressions can pass.

Duplicate candidate instances were consolidated without changing canonical findings: `MAINT-C001` into `ARCH-C001`, `OPS-C002` into `COR-C003`, and `OPS-C001` into `COR-C004`.

## Accepted Risks

Active accepted risks: **0**.  
Expired accepted risks: **0**.

Every final finding has `risk_acceptance: null`.

## Rejected Candidate Findings

None. `verification-results.json` contains zero rejected findings, and no unsupported candidate was promoted or resurrected.

## Inconclusive Findings

- `OPS-C003` — **Health readiness semantics are not established** (medium; inconclusive). The source confirms a self-only health check, but its deployment role and probe contract are not established. Both `technical_quality_impact` and `production_impact` remain `none`; document the health-probe contract before drawing a routing-readiness conclusion.

## Assessment Coverage and Limitations

- The explicitly requested `aspnet-core-api` profile version 1 was deterministically matched.
- Verification received 17 candidate instances, consolidated them into 14 unique material claims, produced 13 verified findings, rejected none, and retained one inconclusive finding.
- Sampling used seed `e0931311220fcd0e3fff171b7547d91d2ce7e8f4e2c3ba26d2355acb29a5faaa`: 20 risk-directed entries (18 file entries and two non-file concepts) and 12 random-control file entries, covering 29 unique file entries.
- The worktree is dirty. Scope is tied to commit `bab0d8c137ec5faba95819e8dab37b558af0495c` and the supplied immutable evidence artifacts.
- The two non-file Graphify concepts, `Fact` and `Task`, remain in sampling counts but are excluded from file-focused targets.
- Graphify connectivity and node metadata are prioritization signals only; they do not establish a defect, severity, technical-quality impact, production impact, or release consequence.
- The profile artifact retains `not_run` for its applicable audit recipes. Separate recorded tooling evidence captures the actual build, format, test, and dependency-scan attempts; no successful rerun is inferred.
- Build and test execution could not complete because the configured local NuGet source `C:\Dev\nuget` does not exist.
- Coverage is `not_run`; no percentage is available to compare against the 70% policy minimum.
- Dedicated secret and static-security scans were unavailable. These are assessment limitations, not product findings.
- Ingress, gateway policy, authenticated-request behavior, deployment topology, and external trust boundaries are outside the repository evidence.
- Containers are optional for the declared `conventional_server` deployment (`containers_required: false`); container absence is not a finding.

## Architecture and Graphify Analysis

Graphify evidence passed with **4,117 nodes**, **10,052 edges**, and no reported normalized cycles. These metrics guided prioritization only. `ARCH-C001` is the sole architecture finding and has `production_impact: none`; its localized technical-quality impact still contributes its scoring penalty. Source evidence also verifies explicit scoped DI composition. No ASP.NET Core profile category cap was triggered.

## Security

Two medium security findings were verified. `SEC-C001` is required before production and remains partially supported because ingress and gateway controls are unknown. `SEC-C002` is a post-release logging action mapped to CWE-532. The dependency-vulnerability scan passed with no listed package advisories. `gitleaks` and `semgrep` were unavailable, so no clean secret-scan or static-security-scan claim is made.

## Deployability

`DEPLOYABILITY-C001` is required before production because conventional-server build inputs depend on machine-local paths, and the recorded build failed on the absent local NuGet source. `DEPLOYABILITY-C002` is a post-release startup-validation concern. The resolved deployment is `conventional_server` and does not require containers. The profile's applicable build audit recipe remains `not_run`, separately from the recorded failed build attempt.

## Testing

`TEST-C001` and `TEST-C002` are post-release findings. The recorded test attempt was `unavailable` because `C:\Dev\nuget` does not exist. Coverage was not run, so the review does not claim a coverage result against the 70% policy threshold. The inventory of 98 skipped tests is a verified source/tool fact, not evidence that the remaining suite executed successfully.

## Maintainability

`MAINT-C002` and `MAINT-C003` are post-release actions. The recorded formatter/analyzer command executed and failed with whitespace violations. That tooling state is reported as evidence and was not converted into an additional finding. The retained `GraphQL/Old` tree may be intentional; no migration plan was supplied.

## Operational Considerations

`COR-C003` and `COR-C004` are operations-relevant post-release actions: propagate request cancellation through Dynamics calls and remove synchronous RegData waiting from response formatting. The configured 30-second timeout bounds Dynamics calls but does not propagate abandoned-request cancellation. `OPS-C003` remains inconclusive because the deployment probe contract is unknown. Finite outbound client timeouts are a verified strength.

## Standards Mapping

- `SEC-C001` — CWE-862, Missing Authorization.
- `SEC-C002` — CWE-532, Insertion of Sensitive Information into Log File.
- No OWASP Top 10, OWASP ASVS, NIST SSDF, or CVE mapping was asserted without supporting evidence.

## Reproducibility Information

- Review ID: `review-6e60687c8c09a117`
- Timestamp (UTC): `2026-08-31T07:07:08.071022+00:00`
- Commit: `bab0d8c137ec5faba95819e8dab37b558af0495c`
- Branch: `backups/develop`
- Dirty worktree: `true`
- Profile: `aspnet-core-api` version `1`, display name `ASP.NET Core API`, detection state `matched`, hash `sha256:880c2c2c2c1618388dcdfb67807e4db8b95350613e2f6d95ebfee2b3accfb476`
- Graphify: version `0.9.46`, input hash `sha256:a850f276bd0b72673981c9b617fc66c4772480e7e8a8c9a24073c05dfab0e264`
- Policy: version `1`, hash `sha256:e403f9f390277d0a1afcc5b3407af9995b908afd9ad6271088c40921e5e79a2c`
- Review kit: version `2.3.0`, hash `sha256:71c9ae89d59e59b3365c225c7fdcf9f3d4953e394b468607482eba9e99c36b36`
- Sampling seed: `e0931311220fcd0e3fff171b7547d91d2ce7e8f4e2c3ba26d2355acb29a5faaa`
- Agent/model roles and exact recorded tool commands, summaries, and states are preserved in `review.json` under `review.manifest` and `tooling`.

Tooling state summary:

| Evidence/tool | State |
|---|---|
| Graphify | passed |
| Recorded .NET build | failed |
| Recorded formatter/analyzer verification | failed |
| Recorded .NET tests | unavailable |
| Dependency vulnerability scan | passed |
| Secret scan (`gitleaks`) | unavailable |
| Static security scan (`semgrep`) | unavailable |
| Coverage | not_run |

## Prioritized Remediation Plan

1. **P0 / required before production:** `DEPLOYABILITY-C001` — remove machine-local build inputs and prove a clean-host build.
2. **P0 / required before production:** `COR-C002` — implement non-mock batch retrieval and add regression coverage for the simple-batch path.
3. **P0 / required before production:** `SEC-C001` — establish and test the mandatory authorization boundary.
4. **P1 / required before production:** `COR-C001` — validate required Dynamics endpoint configuration at startup.
5. **Release evidence:** establish test execution and coverage, dedicated secret scanning, and static-security scanning; confirm deployment topology, probe semantics, and upstream trust controls.
6. **P2 / post-release:** `COR-C003`, `COR-C004`, `DEPLOYABILITY-C002`, `SEC-C002`, and `TEST-C001`.
7. **P3 / post-release:** `MAINT-C002`, `MAINT-C003`, and `TEST-C002`.
8. **P4 / no production impact:** `ARCH-C001`.

The authoritative machine-readable artifact is `review.json`.
