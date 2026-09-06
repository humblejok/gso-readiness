# Production Readiness Review

## Executive Assessment

**Production readiness quality:** 6.9 / 10  
**Assessment confidence:** 45% (low)  
**Release recommendation:** INSUFFICIENT_EVIDENCE

The deterministic quality score is 6.9/10, but confidence is below the 60% decision threshold. The release state is therefore `INSUFFICIENT_EVIDENCE`; this is not a `GO`, conditional approval, or `NO_GO` determination. The review contains 20 verified findings: 15 medium, 4 low, and 1 informational. Six are required before production, 10 are recommended after release, and four have no production impact. No production blocker was verified.

No profile category cap was applied. No release hard gate was applied. No accepted or expired risk exists.

## Release Decision

**Release recommendation: INSUFFICIENT_EVIDENCE.** A production release decision cannot be supported at 45% assessment confidence. Before seeking a decision, establish the missing test and security evidence and address the six deterministic required-before-production findings: `SEC-C001`, `SEC-C002`, `COR-C001`, `COR-C002`, `DEP-C001`, and `TST-C004`.

The unavailable dependency-vulnerability, secret, and static-security scans are unknown states, not clean results. Test execution was unavailable because the test project assets file was missing. The recorded formatter/analyzer command failed with whitespace violations. No category cap or hard gate changed the release state.

## Required Before Production

- `SEC-C001` — **No in-process authentication or authorization boundary is configured** (medium, systemic technical-quality impact, P0; partially supported). Document and test the mandatory upstream trust boundary or enforce authentication and operation-level authorization in-process.
- `SEC-C002` — **Global detokenization is not authorization-aware** (medium, material, P0; partially supported). Default to tokenized output and require an explicit authorization decision for each applicable data class.
- `COR-C001` — **Batched ID retrieval ignores selection and filter parameters** (medium, material, P1; supported). Apply selection and caller filters in both batched retrieval branches.
- `COR-C002` — **Dictionary-based scalar filtering omits declared operations** (medium, material, P1; supported). Implement decimal and `Nin` conversion and test the active dynamic operation-input route.
- `DEP-C001` — **API build configuration contains machine-local dependencies** (medium, material, P1; supported). Replace absolute workstation package/cache/assembly paths with reproducible build inputs.
- `TST-C004` — **Test project requires a machine-local EDM assembly** (medium, material, P1; supported). Make the assembly a versioned, repository-neutral dependency so clean CI agents can execute tests.

## Production Blockers

None verified (0).

## Category Scores

| Category | Score |
|---|---:|
| Architecture | 9.1 / 10 |
| Correctness | 7.0 / 10 |
| Maintainability | 9.6 / 10 |
| Security | 4.5 / 10 |
| Deployability | 7.0 / 10 |
| Testing | 3.8 / 10 |
| Operations | 8.5 / 10 |

**Applied profile category caps:** none.  
**Applied release hard gates:** none.

## Verified Strengths

- `STR-ARCH-001` — **Explicit scoped service composition** (architecture). Field-selection, conversion, HTTP factory, and client registrations are explicit in `Infrastructure/DependencyInjection.cs:38-81`.
- `STR-OPS-001` — **Outbound HTTP clients have bounded timeouts and pooled handlers** (operations). The Dynamics client has a 30-second timeout, pooled lifetime, and handler rotation in `Infrastructure/DependencyInjection.cs:45-81`.
- `STR-OPS-002` — **GraphQL limits and health endpoint are configured** (operations). `Program.cs:357-436` configures execution timeout, depth/page limits, and `/health`.

These strengths are limited to source-backed statements supplied by specialist verification; they do not imply successful runtime execution.

## Verified Findings

- `ARCH-C001-selection` — **Standard account resolvers discard precomputed selection metadata** (medium, localized, post-release, P2; partially supported). Relationship and XDW selections can be recomputed differently from the initial GraphQL selection plan.
- `ARCH-C002` — **Mock-data registration builds a temporary service provider** (low, localized, no production impact, P4; supported). Mock-mode registration has avoidable parallel-container ownership and disposal ambiguity.
- `COR-C001` — **Batched ID retrieval ignores selection and filter parameters** (medium, material, required before production, P1; supported). Nested GraphQL relationship filters can be ignored and requested projections bypassed.
- `COR-C002` — **Dictionary-based scalar filtering omits declared operations** (medium, material, required before production, P1; supported). Declared operations can produce no predicate and broaden results.
- `DEP-C001` — **API build configuration contains machine-local dependencies** (medium, material, required before production, P1; supported). Clean build or release hosts require undeclared local artifacts and directory layout.
- `DEP-C002` — **Dockerfile copies absent build inputs** (medium, localized, no production impact, P4; supported). The optional container build fails before restore, but containers are not required by policy.
- `DEP-C003` — **Outbound integration configuration is not validated at startup** (medium, localized, post-release, P2; partially supported). Invalid endpoint settings can be discovered after startup rather than before traffic.
- `MAINT-C001` — **Reciprocal team resolvers have asymmetric public contracts** (low, localized, no production impact, P4; partially supported). Future contract changes must account for inconsistent reciprocal navigation APIs.
- `MAINT-C002` — **Regulatory reporting documentation names an unimplemented relationship** (informational, localized, no production impact, P4; supported). Maintainers can infer an unavailable schema relationship.
- `MAINT-C003` — **Excluded legacy GraphQL code remains beside active code** (low, localized, post-release, P3; partially supported). Search and maintenance can be confused about the authoritative implementation.
- `OPS-C002` — **Dynamics OData calls do not receive request cancellation** (medium, material, post-release, P2; supported). Abandoned Dynamics work can continue to the configured timeout.
- `SEC-C001` — **No in-process authentication or authorization boundary is configured** (medium, systemic, required before production, P0; partially supported). Unauthorized access is possible if direct reachability or an unverified upstream boundary permits it.
- `SEC-C002` — **Global detokenization is not authorization-aware** (medium, material, required before production, P0; partially supported). Cleartext data may be returned when upstream access control is absent or insufficient.
- `SEC-C003` — **Complete outbound OData URLs are logged** (medium, localized, post-release, P2; supported). Sensitive query criteria or tokens can be exposed to log readers.
- `SEC-C004` — **Account mutations expose raw exception messages** (medium, localized, post-release, P2; supported). Internal or downstream details can reach callers.
- `TST-C001` — **Request-capture tests provide limited response-contract coverage** (medium, material, post-release, P2; partially supported). Response shaping, mapping, and union payload regressions can evade request-focused tests.
- `TST-C002` — **Several batching tests permit unbatched request patterns** (medium, material, post-release, P2; supported). Request-amplification regressions can pass the suite.
- `TST-C003` — **Schema introspection test is diagnostic-only** (low, localized, post-release, P3; supported). The test cannot detect its intended schema regressions.
- `TST-C004` — **Test project requires a machine-local EDM assembly** (medium, material, required before production, P1; supported). Clean developers and CI agents can fail before executing tests.
- `TST-C005` — **End-to-end fixture does not use the production composition root** (medium, material, post-release, P2; supported). Host-level schema-composition regressions in omitted registrations can escape the suite.

## Accepted Risks

Active accepted risks: **0**.  
Expired accepted risks: **0**.

## Rejected Candidate Findings

None. `verification-results.json` contains zero rejected findings; unsupported specialist observations were not resurrected into the final review.

## Inconclusive Findings

None. `verification-results.json` contains zero inconclusive findings; specialist uncertainties remain coverage context only and were not promoted.

## Assessment Coverage and Limitations

- The ASP.NET Core API profile was explicitly selected and deterministically matched.
- Verification received 24 candidate instances, consolidated them into 20 unique material claims, and supported or partially supported all 20. No high or critical candidate was supported.
- Sampling included 20 risk-directed entries: 18 file entries and two excluded non-file Graphify concepts (`Fact` and `Task`), plus 12 random-control entries, producing 30 unique file entries.
- The worktree was dirty. Scope is tied to commit `bab0d8c137ec5faba95819e8dab37b558af0495c` and the supplied evidence artifacts.
- Graphify passed. Connectivity and node metadata were used only for prioritization and do not prove a defect, severity, impact, or release consequence.
- The profile artifact preserves `not_run` for applicable `dotnet-build`, `dotnet-test`, `dotnet-format`, and `dotnet-vulnerable-packages` audit recipes.
- Separate recorded tooling evidence reports `dotnet-format` as `failed` because it found whitespace violations.
- Separate recorded tooling evidence reports `dotnet-test` as `unavailable` because the test project assets file is missing.
- Coverage is `not_run`; no percentage can be compared with the policy minimum of 70%.
- Dependency vulnerability scanning is `unavailable` because restore assets are missing.
- Secret scanning is `unavailable` because `gitleaks` is not installed.
- Static security scanning is `unavailable` because `semgrep` is not installed.
- Deployment topology, ingress controls, network policy, upstream authentication, and upstream authorization are not established.
- Generated build output, graph output, and non-authored concepts are context or exclusions rather than authored application review targets.

## Architecture and Graphify Analysis

Graphify evidence passed with **4,117 nodes**, **10,052 edges**, and an empty normalized cycle collection. These metrics guided prioritization only. Source-backed architecture findings are `ARCH-C001-selection` and `ARCH-C002`; the first is post-release and the second has no production impact. Verified source evidence also confirms explicit scoped composition. No architecture category cap was triggered.

## Security

Four medium security findings were verified. `SEC-C001` and `SEC-C002` are required before production and remain partially supported because deployment exposure and upstream authorization controls are unknown. `SEC-C003` and `SEC-C004` are post-release actions. Dependency vulnerability, secret, and static-security scans were unavailable, so this review makes no clean-scan claim and no CVE claim.

## Deployability

`DEP-C001` is required before production because conventional-server restore/build inputs depend on machine-local paths. `DEP-C003` is a post-release startup-validation concern. `DEP-C002` confirms that the checked-in Dockerfile references absent inputs, but its production impact is `none` because policy declares `conventional_server` deployment and `containers_required: false`. The profile `dotnet-build` audit remains `not_run`.

## Testing

Five testing findings were verified. `TST-C004` is required before production; `TST-C001`, `TST-C002`, `TST-C003`, and `TST-C005` are post-release actions. The recorded test command was unavailable before execution because the test project assets file was missing. Coverage was not run, so no numeric coverage conclusion is made against the 70% policy threshold.

## Maintainability

`MAINT-C003` is a post-release action. `MAINT-C001` and `MAINT-C002` have no production impact. The recorded formatter/analyzer command ran and failed on whitespace violations; this audit state is reported as tooling evidence and was not converted into an additional finding.

## Operational Considerations

`OPS-C002` requires post-release propagation of request cancellation through Dynamics HTTP calls; the 30-second timeout bounds but does not cancel abandoned work. Source-backed strengths include pooled HTTP handlers, bounded timeouts, GraphQL execution/page limits, and a health endpoint. Operational assurance remains incomplete because tests and security scans were not established.

## Standards Mapping

- `SEC-C001` — CWE-862, Missing Authorization.
- `SEC-C002` — CWE-200, Exposure of Sensitive Information to an Unauthorized Actor.
- `SEC-C003` — CWE-532, Insertion of Sensitive Information into Log File.
- `SEC-C004` — CWE-209, Generation of Error Message Containing Sensitive Information.
- No OWASP Top 10, OWASP ASVS, NIST SSDF, or CVE mapping was asserted without supporting evidence.

## Reproducibility Information

- Review ID: `review-a72d22092df058c4`
- Timestamp (UTC): `2026-08-28T13:28:01.369022+00:00`
- Commit: `bab0d8c137ec5faba95819e8dab37b558af0495c`
- Branch: `backups/develop`
- Dirty worktree: `true`
- Profile: `aspnet-core-api` version `1`, matched; hash `sha256:880c2c2c2c1618388dcdfb67807e4db8b95350613e2f6d95ebfee2b3accfb476`
- Graphify: `0.9.46`; input hash `sha256:799d1007890b355e0ccd4e278aeaa086e431d05d7ec7a7901381e3ad545caa4d`
- Policy: version `1`; hash `sha256:202323dbe44580f9c8b21f42ef9133224364f2e2fd8f6e88cdd7913a9eea4f9c`
- Review kit: `2.2.0`; hash `sha256:5a7e1e38be303072303845b0b2a0edcbf376694e46f0d60627eaf604501be94f`
- Sampling seed: `56476e5a54b09e3001f811ec06a0473cfda2232fe00e174b84b7654ea107f2ed`
- Agent/model roles and exact recorded tool commands/states are preserved in `review.json` under `review.manifest` and `tooling`.

## Prioritized Remediation Plan

1. **P0 / required before production:** `SEC-C001` — establish and test the mandatory authentication/authorization boundary.
2. **P0 / required before production:** `SEC-C002` — make detokenization authorization-aware and default to tokenized output.
3. **P1 / required before production:** `COR-C001` and `COR-C002` — restore filter/projection correctness and cover the active routes with regression tests.
4. **P1 / required before production:** `DEP-C001` and `TST-C004` — remove machine-local build/test dependencies and prove clean-host restore, build, and test execution.
5. **Evidence required for a release decision:** establish test execution and coverage, dependency-vulnerability scanning, secret scanning, and static-security scanning; document deployment topology and upstream trust controls.
6. **P2 / post-release:** `ARCH-C001-selection`, `DEP-C003`, `OPS-C002`, `SEC-C003`, `SEC-C004`, `TST-C001`, `TST-C002`, and `TST-C005`.
7. **P3 / post-release:** `MAINT-C003` and `TST-C003`.
8. **P4 / no production impact:** `ARCH-C002`, `DEP-C002`, `MAINT-C001`, and `MAINT-C002`.

The authoritative machine-readable artifact is `review.json`.
