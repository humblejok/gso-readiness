---
name: framework-profiles
description: Apply the explicitly resolved framework profile, its evidence patterns, audit recipes, contextual rules, and technical-quality scoring metadata.
user-invocable: false
---

# Framework Profiles

The manager resolves `profile=<name>` once into `<run-dir>/evidence/profile.json`. Every agent uses that exact resolved file; never infer or silently replace the selected profile.

Supported canonical profiles are:

- `generic` (default)
- `aspnet-core-api` (aliases include `asp.net`, `aspnet`, and `dotnet`)
- `spring-boot` (aliases include `spring` and `springboot`)
- `auto` requests deterministic detection and fails when multiple framework profiles match

Unknown names are errors. An explicitly selected framework that is not detected remains selected, but its detection mismatch must be recorded as a limitation and every rule's applicability must be confirmed from source.

## Applying rules

Profile YAML under `.github/graphify-review/profiles/` is authoritative. Review only rules relevant to your assigned categories. A profile rule is an investigation lens, never proof of a finding.

- Require the rule's stated evidence and inspect counter-evidence, framework lifecycle, call paths, configuration, and tests.
- Do not turn preferences into defects. In particular, do not require MediatR, repositories, interfaces for every class, field-free Spring components, a fixed layer count, or a particular package layout unless the project has an evidence-backed need.
- When a candidate directly applies a profile rule, include its canonical `profile_rule_id`.
- Generic findings unrelated to a profile rule omit `profile_rule_id`.
- Audit commands are recipes, not automatic authorization. Run them only under the deterministic-audit safety rules and report their actual state.

## Technical quality impact

Every candidate and verdict independently classifies `technical_quality_impact`:

- `systemic`: a foundational or cross-cutting weakness demonstrated across a dependency/lifecycle boundary or multiple important paths.
- `material`: meaningful design or code-health debt in an important component, or a repeated issue with concrete change/test/correctness cost.
- `localized`: a real, contained quality issue with limited blast radius.
- `none`: no established technical debt; required for unsupported and inconclusive candidates.

The profile's default is a starting point, not an automatic classification. Narrow or raise it only from evidence. Technical quality impact controls category penalties and profile category caps; `production_impact` separately controls release actions. Accepted risk can waive release impact but never technical-quality impact.

## Framework coverage

The ASP.NET Core API profile covers dependency injection/lifetimes, controller and endpoint responsibility, service/interface cohesion and dependency direction, async/cancellation, outbound HTTP ownership, Entity Framework context/query behavior, typed configuration, middleware ordering, and nullable/analyzer policy.

The Spring Boot profile covers dependency injection/bean scopes, web/application/persistence responsibility, component/interface cohesion and dependency direction, transactional proxies and boundaries, JPA behavior, API validation/error contracts, Spring Security, typed configuration, Actuator/health exposure, async executors, and compiler/static-analysis policy.
