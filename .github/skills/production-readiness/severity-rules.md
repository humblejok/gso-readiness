# Severity, technical quality, production impact, score caps, and release rules

## Technical quality impact

Every final finding independently records one `technical_quality_impact` value:

- `systemic`: foundational or cross-cutting quality debt across an important dependency, lifecycle, or responsibility boundary
- `material`: meaningful design/code-health debt in an important component, or a repeated issue with concrete cost
- `localized`: real but contained quality debt
- `none`: no scoreable technical-quality consequence

Severity plus technical-quality impact determines the category penalty. This is intentionally independent from production impact: a systemic SOLID/Clean Code or framework architecture problem may lower the grade without blocking release.

Every supported/partially supported non-informational finding must be at least `localized`. `none` is reserved for informational observations and unsupported/inconclusive candidates, preventing visible defects from being score-neutral.

## Finding production impact

Every final finding has one `production_impact` value:

- `blocker`: verified risk that should prevent production release until remediated
- `required_before_production`: material verified issue that must be remediated before initial production availability, but is not necessarily an emergency stop for an already-running system
- `post_release`: real issue suitable for scheduled remediation after release under normal change control
- `none`: observation/low-impact issue that does not constrain production release

Only `supported` or policy-approved, carefully narrowed `partially_supported` findings can be `blocker` or `required_before_production`.

## Examples that can justify a blocker

When directly supported by evidence:
- exposed production credential/private key/token
- critical exploitable authentication/authorization bypass
- credible remote code execution/injection path with material exposure
- deterministic destructive data-loss/corruption path
- application cannot start or function under the stated production deployment model
- unavoidable use of development/debug configuration exposing material risk

## Examples normally not blockers by themselves

- naming/style inconsistency
- missing optional abstraction
- preference for a different architectural pattern
- absence of containers
- high fan-in/fan-out without demonstrated harmful impact
- dependency cycle without demonstrated change/runtime consequence
- missing optional optimization
- undocumented code that remains clear and low risk

## Deterministic score caps

These caps prevent optimistic synthesis when a verified severe condition exists. They are maximums, not automatic target scores. `policy.yaml` and `calculate_score.py` are authoritative; agents never apply caps manually.

- one or more verified `critical` production blockers -> overall production readiness `score_10 <= 4.0` and `NO_GO`
- verified exposed production secret/credential -> `score_10 <= 3.0` and `NO_GO`
- the selected framework profile may cap a category when verified systemic or repeated material findings match named profile rules; the applied cap and finding IDs remain visible in output

Do not apply a cap when its premise is inconclusive.

## Release recommendation

- `GO`: no release blockers or pre-production requirements; verified risks are acceptable for release
- `GO_WITH_ACTIONS`: no blockers; remaining verified items can be safely handled after release and are explicitly scheduled/prioritized
- `CONDITIONAL`: one or more material pre-production requirements or important evidence gaps must be resolved before a confident GO
- `NO_GO`: one or more verified production blockers
- `INSUFFICIENT_EVIDENCE`: review coverage is too incomplete to make a responsible release recommendation

## Readiness status mapping

- `ready` -> normally `GO`
- `ready_with_actions` -> normally `GO_WITH_ACTIONS`
- `conditional` -> `CONDITIONAL`
- `not_ready` -> `NO_GO`
- `insufficient_evidence` -> `INSUFFICIENT_EVIDENCE`

## 0-10 rating

`score_10` is the deterministic human-facing readiness rating. Each category starts at `10.0`; severity-by-technical-quality penalties are applied to supported findings and profile category caps are applied. The default overall aggregation blends the category-weighted average at `70%` with the weakest category at `30%`, so a poor maintainability/security category is not hidden by unrelated 10/10 categories. Policy release hard gates are applied afterward. The JSON exposes every component in `score_breakdown`. Evidence confidence is calculated separately.
