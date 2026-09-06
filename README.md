# Graphify VS Code Production Review Kit

Original concept and project creator: **De Jonckheere Stéphane (humblejok)**.

Copyright © 2026 De Jonckheere Stéphane (humblejok). Licensed under [GNU AGPL-3.0-only](LICENSE). See [NOTICE](NOTICE) for attribution and scope, and [third-party acknowledgements](THIRD_PARTY_NOTICES.md) for Graphify and the integrated tools.

An evidence-first production-readiness workflow for VS Code, GitHub Copilot agents, and Graphify. The runtime kit lives under `.github/`; the repository-root PowerShell installer is a distribution helper. The authoritative output is JSON and the companion `REVIEW.md` is generated for people.

The kit keeps responsibilities deliberately separate:

- Graphify provides structural evidence and investigation targets.
- Deterministic tools establish objective facts.
- Copilot specialist agents interpret scoped evidence.
- Independent verification decides which candidates are supported.
- Policy-driven Python calculates confidence, technical-quality category scores, framework-profile caps, release hard gates, and release state.
- The synthesizer explains authoritative JSON without changing verified finding semantics; Python renders Markdown deterministically.

Missing evidence lowers assessment confidence, not project quality. A Graphify anomaly is not a finding until verified in source. A named CVE is never accepted without structured scanner/advisory evidence.

## Outputs

Review runs are immutable, versioned directories beneath `.github/graphify-review/output/runs/`:

```text
output/
└── runs/
    └── <timestamp>-<identity>/
        ├── run-context.json
        ├── evidence/
        │   ├── project-graph.json
        │   ├── profile.json
        │   ├── normalized-evidence.json
        │   ├── aspnet-anchors.json
        │   ├── spring-anchors.json
        │   ├── tool-results.json
        │   ├── samples.json
        │   ├── manifest.json
        │   ├── verified-findings.json
        │   ├── confidence.json
        │   └── score.json
        ├── review.json
        └── REVIEW.md
```

Current `review.json` and normalized evidence use schema version `2.1`. Earlier review JSON can be imported as a baseline but is not silently treated as current output.

## Workflow

```text
source snapshot + Graphify + deterministic audits + policy + resolved profile
                 │
                 ▼
        normalized evidence
                 │
                 ▼
 anchors + baseline + risk-directed + stable random scoping
                 │
                 ▼
       independent specialists
                 │
                 ▼
       adversarial verification
                 │
                 ▼
  production-impact classification
                 │
                 ▼
 immutable verified-findings.json
                 │
                 ▼
 deterministic confidence + scoring
                 │
                 ▼
       review.json → deterministic REVIEW.md
```

The manager orchestrates only. It does not perform specialist analysis, calculate scores, or choose release state.

## Install on Windows from Artifactory

[`install-graphify-review.ps1`](install-graphify-review.ps1) installs or updates the kit without administrator rights. Publish these artifacts to trusted HTTPS Artifactory locations:

1. A ZIP containing exactly one `.github` directory. Keep `.github/graphify-review/LICENSE`, `NOTICE`, and `THIRD_PARTY_NOTICES.md` in the archive. The installer validates these notices together with the manager, prompt, and kit version before merging it.
2. The corporate Java `cacerts` truststore.
3. Optionally, your approved `jf.exe` binary.

For example, create the kit archive from the source repository with:

```powershell
Compress-Archive -Path .\.github -DestinationPath .\graphify-review-github.zip -CompressionLevel Optimal
```

Record each artifact's SHA-256 value when publishing it:

```powershell
Get-FileHash .\graphify-review-github.zip -Algorithm SHA256
Get-FileHash .\cacerts -Algorithm SHA256
Get-FileHash .\jf.exe -Algorithm SHA256
```

Use a short-lived token through the current process environment so it does not appear in the script or PowerShell command history:

```powershell
$env:ARTIFACTORY_ACCESS_TOKEN = '<short-lived-access-token>'

try {
  .\install-graphify-review.ps1 `
    -TargetRepository 'C:\src\my-api' `
    -GithubBundleUrl 'https://artifactory.example.invalid/artifactory/REPLACE_ME/graphify-review-github.zip' `
    -GithubBundleSha256 '<64-character-sha256>' `
    -TrustStoreUrl 'https://artifactory.example.invalid/artifactory/REPLACE_ME/cacerts' `
    -TrustStoreSha256 '<64-character-sha256>'
}
finally {
  Remove-Item Env:\ARTIFACTORY_ACCESS_TOKEN -ErrorAction SilentlyContinue
}
```

If Artifactory uses Windows authentication, omit the token and add `-UseDefaultCredentials`. An explicit authenticated proxy can be selected with `-Proxy 'https://proxy.example.invalid:8443' -ProxyUseDefaultCredentials`; otherwise PowerShell uses the Windows proxy configuration. To install the approved JFrog CLI at the same time, add:

```powershell
-JfrogCliUrl 'https://artifactory.example.invalid/artifactory/REPLACE_ME/jf.exe' `
-JfrogCliSha256 '<64-character-sha256>'
```

The installer:

- refuses non-HTTPS artifact URLs, credentials embedded in URLs, placeholder URLs, missing hashes, and hash mismatches;
- backs up an existing repository `.github` directory below `%LOCALAPPDATA%\GraphifyReview\backups` before merging, so unrelated workflows and repository settings remain in place;
- installs the truststore as `%LOCALAPPDATA%\GraphifyReview\truststore\cacerts`;
- preserves existing user `MAVEN_OPTS` content while replacing the installer-owned `javax.net.ssl.trustStore` and `trustStoreType` options;
- optionally installs `jf.exe` under `%LOCALAPPDATA%\GraphifyReview\bin` and adds that directory to the user `PATH` idempotently.

Use `-TrustStoreType JKS` or `-TrustStoreType PKCS12` only when the published store requires an explicit type. The script intentionally does not persist a truststore password. After installation, close every VS Code window and restart VS Code so Copilot and its child processes inherit `MAVEN_OPTS` and `PATH`.

The HTTPS download itself relies on the Windows certificate store. If Windows does not yet trust the certificate presented by Artifactory, deploy the corporate root/intermediate CA to Windows first through your normal enterprise mechanism; the installer never bypasses TLS validation.

For corporate distribution, Authenticode-sign the final `.ps1` with your organization's code-signing certificate and publish that signed copy. This lets machines using an `AllSigned` execution policy run it without weakening PowerShell policy.

## Install and run in VS Code

Merge this `.github` directory into the repository being reviewed. In Copilot Chat:

### Review environment

The kit pins Graphify in `.github/graphify-review/requirements.txt`. You can install it manually before the first review:

```bash
python3 .github/graphify-review/scripts/bootstrap_environment.py
```

The command creates the dedicated `.graphify-review-venv` only when necessary, installs the pinned Graphify package, and prints the review Python and Graphify executable paths. An existing application `.venv` is never inspected, activated, modified, or replaced. If `.graphify-review-venv` already exists but is not a Python virtual environment, setup stops without modifying it.

Check without making changes:

```bash
python3 .github/graphify-review/scripts/bootstrap_environment.py --check --json
```

Automatic first-call setup is enabled by default with `runtime.auto_bootstrap: true` in `policy.yaml`. The Graphify Review manager runs:

```bash
python3 .github/graphify-review/scripts/bootstrap_environment.py --first-call --json
```

Set `runtime.auto_bootstrap: false` when package installation must be performed explicitly. In that mode, the first review only checks the environment and reports the manual setup command. For offline package caches, manual setup also supports `--offline`.

The commands below use `.graphify-review-venv/bin/python` on macOS/Linux. On Windows, use `.graphify-review-venv\Scripts\python.exe`. The Copilot manager uses the platform-correct path returned by the bootstrapper rather than assuming either layout.

### Start the review

In Copilot Chat:

```text
/full-project-review graph=path/to/graphify-output.json
```

Select an ASP.NET Core API or Spring Boot profile:

```text
/full-project-review graph=artifacts/graph.json profile=aspnet-core-api
/full-project-review graph=artifacts/graph.json profile=spring-boot
```

JFrog is selected automatically when its CLI is available, or can be required explicitly:

```text
/full-project-review graph=artifacts/graph.json profile=asp.net security-scanner=jfrog
/full-project-review graph=artifacts/graph.json profile=spring security-scanner=jfrog jfrog-server=corporate
```

Choose the run behavior explicitly when continuity matters:

```text
/full-project-review graph=artifacts/graph.json profile=asp.net mode=fresh
/full-project-review graph=artifacts/graph.json profile=asp.net mode=revalidate baseline=artifacts/previous-review.json
/full-project-review profile=asp.net mode=rescore baseline=artifacts/previous-review.json
```

- `fresh` discovers findings without a baseline.
- `revalidate` makes every previous supported or inconclusive finding mandatory and records it as `still_present`, `resolved`, `superseded`, or `not_reproduced`.
- `rescore` invokes no reviewer; it applies current deterministic grading to immutable baseline findings.

Strict runs require a clean worktree. `allow-dirty=true` is an explicit escape hatch recorded in the run context.

`profile=asp.net`, `profile=aspnet`, and `profile=dotnet` normalize to `aspnet-core-api`; `profile=spring` and `profile=springboot` normalize to `spring-boot`. `profile=auto` performs deterministic marker detection and stops when multiple profiles match. An unknown value is an error. When omitted, the profile is `generic`, preserving the framework-neutral workflow.

Optional scope can be combined with a profile:

```text
/full-project-review graph=artifacts/graph.json profile=asp.net scope=src/pricing
```

Or select **Graphify Review** from the Agents picker. If the graph path is omitted, the manager searches for plausible exports and records ambiguity.

## Framework profiles

Profiles live under `.github/graphify-review/profiles/` and are validated and hashed before evidence collection. The resolved canonical profile is written below the unique run directory; its ID, version, definition hash, and detection state are copied into normalized evidence, the reproducibility manifest, and final review.

- `aspnet-core-api` reviews .NET service lifetimes and dependency resolution, API boundaries, service/interface cohesion and dependency direction, async/cancellation, `HttpClient`, EF Core lifetime/query behavior, typed configuration, middleware ordering, nullable analysis, and analyzers.
- `spring-boot` reviews bean injection/scopes, API/application/persistence boundaries, component/interface cohesion and dependency direction, transactional proxy behavior, JPA query/lifecycle behavior, validation/error contracts, Spring Security, typed configuration, Actuator/health exposure, async executors, and build analysis.

Profiles do not make conventions mandatory. Missing MediatR, repositories, interfaces, a fixed package structure, or another fashionable pattern is not a finding by itself. Every profile finding needs the rule's stated source/configuration/tool evidence and counter-evidence review.

Resolve a profile manually:

```bash
.graphify-review-venv/bin/python .github/graphify-review/scripts/resolve_profile.py \
  --profile asp.net \
  --repository . \
  --output <run-dir>/evidence/profile.json
```

## JFrog Xray and secret scanning

The default `security-scanner=auto` requires no additional review-kit installation. When `jf` is visible on the `PATH` inherited by VS Code, the audit runner prefers JFrog Xray for dependency vulnerability evidence and JFrog Secrets for secret evidence. Restart VS Code after changing the Windows `PATH` so Copilot inherits it.

Before the first review, these PowerShell checks should succeed from VS Code's integrated terminal:

```powershell
Get-Command jf
jf --version
jf c show
jf rt ping
```

If more than one JFrog CLI configuration exists, either select its default with `jf c use <server-id>` or leave the default unchanged and pass `jfrog-server=<server-id>` to `/full-project-review`. Authentication stays in the JFrog CLI configuration; never put a URL, token, user name, or password in the review command.

| Review parameter | Dependency scan | Secret scan | Incomplete JFrog result |
|---|---|---|---|
| `security-scanner=auto` (default) | JFrog when `jf` is available; otherwise profile-native | JFrog Secrets when available; otherwise Gitleaks | Preserved, then native fallback where available |
| `security-scanner=jfrog` | JFrog required | JFrog Secrets required | Preserved as incomplete; no silent fallback |
| `security-scanner=native` | Profile-native audit | Gitleaks | JFrog is not invoked |

For ASP.NET Core, JFrog runs NuGet SCA from each detected solution/project root. For Spring Boot, it runs Maven or Gradle SCA and uses a repository wrapper when present. The generic profile supports common JFrog package managers when their manifests are detected. Each declared target must complete before dependency evidence is considered complete.

JFrog can replace Gitleaks only when the connected platform includes the JFrog Advanced Security Secrets scanner and the configured identity may use it. If that sub-scan is absent or fails, `auto` runs Gitleaks and records both attempts; strict `jfrog` mode leaves `secret_scan` incomplete. JFrog does not replace the repository-configured Semgrep SAST step, so `static_security_scan` remains incomplete until `.semgrep.yml` or `.semgrep.yaml` exists and `semgrep` is available.

The application SDK/package manager must also be available to JFrog, and dependencies/build descriptors must be resolvable through the corporate repositories. For Maven/Gradle behind a proxy, set `JFROG_CLI_RELEASES_REPO=<server-id>/<repository-name>` to an approved Artifactory remote repository that proxies `https://releases.jfrog.io`; JFrog uses it to obtain the required extractor resources. No JFrog credentials or raw detected secret values are written to review evidence.

You can smoke-test the two .NET scans directly from the solution directory:

```powershell
jf audit --nuget --sca --without-contextual-analysis --format=simple-json --fail=false --vuln
jf audit --secrets --format=simple-json --fail=false
```

The first command must include `scaScanStatusCode: 0`; the second must include `secretsScanStatusCode: 0`. Findings are allowed—the status code establishes that the scanner completed.

## Vulnerability upgrade commands

Two independent Copilot slash commands turn completed JFrog Xray findings into conservative upgrade decisions:

```text
/check-vulnerability-upgrades
/check-vulnerability-upgrades severity=critical,high jfrog-server=corporate
/check-vulnerability-upgrades severity=high artifactory-repositories=maven-cache,nuget-cache

/update-vulnerable-dependencies severity=critical,high jfrog-server=corporate
```

`severity` defaults to `all`. A named value is an exact filter, so use `severity=critical,high` when both levels are wanted. Supported values are `critical`, `high`, `medium`, `low`, `informational`, and `unknown`.

`/check-vulnerability-upgrades` runs a fresh Xray SCA audit, selects an exact fixed version from Xray, and checks that exact artifact in the configured Artifactory instance. It does not modify dependency files. Its Markdown and JSON reports include a dedicated `missing_libraries` inventory for recommendations that are not stored or cached in the searched repositories.

`/update-vulnerable-dependencies` does not consume or depend on a prior check report. It independently reruns Xray, version selection, Artifactory lookup, and declaration discovery before editing. By default it requires a clean Git worktree; `allow-dirty=true` is an explicit escape hatch for a caller who has arranged another recovery mechanism. Original files are backed up in the command's unique output directory before replacement.

When `artifactory-repositories` is omitted, the command searches all repository keys visible to the configured JFrog identity. Supplying the option limits searches to the named local or remote-cache repository keys. A successful lookup means the exact artifact is currently stored or cached in Artifactory; the command never queries a public package registry.

The no-assumed-breaking-change rule is deliberately conservative: the candidate must be an exact numeric Xray fixed version greater than the installed version and in the same major version. For `0.x`, it must also remain in the same minor version. This reduces obvious compatibility risk but cannot guarantee source, binary, or behavioral compatibility. If any selected vulnerability for a dependency lacks such a fix, the dependency is reported for manual handling and is not updated.

Automatic edits are limited to recognizable direct declarations inside the Xray-affected target:

| Ecosystem | Automatically editable declarations |
|---|---|
| Maven | Literal dependency versions and a unique, unshared `${property}` in `pom.xml` |
| Gradle | Literal `group:artifact:version` string notation |
| Python | Exact unhashed `requirements*.txt`/`.in` pins; recognized PEP 508 arrays in `pyproject.toml`; Poetry/Pipfile exact, `^`, or `~` constraints |
| NuGet | Literal `PackageReference`, central `PackageVersion`, and `packages.config` versions |
| npm | Exact, `^`, or `~` versions in direct dependency, development-dependency, and optional-dependency sections |

Transitive dependencies, shared/ambiguous version properties, unsupported ranges, hashed Python requirements, comments, and generated lockfiles are not edited. The update report lists lockfiles that must be refreshed with the repository's configured package manager. After an update, refresh only those lockfiles, run the relevant restore/build/tests, and rerun Xray.

Each invocation writes immutable output beneath `.github/graphify-review/output/vulnerability-upgrades/<run-id>/`. Check runs produce `check.json` and `CHECK.md`; update runs produce `apply.json`, `APPLY.md`, and file backups when changes are made.

## Policy

`.github/graphify-review/policy.yaml` controls:

- automatic environment bootstrap, dedicated review-environment location, requirements file, and minimum Python version;
- project and deployment assumptions, including whether containers are required;
- security/test evidence expectations;
- category weights, severity/quality penalties, weakest-category aggregation, and hard gates;
- confidence weights and decision threshold;
- mandatory completed evidence required before any release decision;
- reproducible random sample size;
- clean-worktree enforcement, versioned output, and baseline disposition requirements;
- verification requirements by severity;
- accepted risks and expiry;
- task-complexity model routing.

Validate it before review:

```bash
.graphify-review-venv/bin/python .github/graphify-review/scripts/validate_policy.py \
  .github/graphify-review/policy.yaml
```

The scripts have no mandatory third-party dependency. They use PyYAML/jsonschema when available and otherwise apply their dependency-free parser and validations.

## Graphify and evidence

Prepare a unique run directory before writing evidence:

```bash
.graphify-review-venv/bin/python .github/graphify-review/scripts/reproducibility.py \
  --repository . --mode fresh --exclude-input path/to/graphify-output.json
```

The command prints `<run-dir>`. Generated Graphify input is excluded from the authored-source snapshot; the graph receives its own hash in the manifest. For revalidation/rescore, add `--mode revalidate|rescore --baseline path/to/review.json` (the baseline is excluded automatically).

The provided adapter preserves existing JSON, GraphML, and optional YAML input expectations:

```bash
.graphify-review-venv/bin/python .github/skills/graphify-evidence/scripts/graphify_adapter.py \
  --input path/to/graphify-output.json \
  --output <run-dir>/evidence/project-graph.json
```

Build the normalized evidence envelope before specialist review:

```bash
.graphify-review-venv/bin/python .github/graphify-review/scripts/run_audits.py \
  --repository . --profile-file <run-dir>/evidence/profile.json \
  --security-scanner auto \
  --output <run-dir>/evidence/tool-results.json
```

The manager also creates mandatory investigation anchors for the selected framework profile. These are stable signals that must receive a verifier disposition; they are never findings by themselves:

```bash
.graphify-review-venv/bin/python .github/graphify-review/scripts/aspnet_checks.py \
  --repository . --output <run-dir>/evidence/aspnet-anchors.json

.graphify-review-venv/bin/python .github/graphify-review/scripts/spring_checks.py \
  --repository . --output <run-dir>/evidence/spring-anchors.json
```

Run only the command for the resolved framework profile.

```bash
.graphify-review-venv/bin/python .github/graphify-review/scripts/build_evidence.py \
  --repository . \
  --graph <run-dir>/evidence/project-graph.json \
  --profile-file <run-dir>/evidence/profile.json \
  --tool-results <run-dir>/evidence/tool-results.json \
  --run-context <run-dir>/run-context.json \
  --output <run-dir>/evidence/normalized-evidence.json
```

Validate it before scoping:

```bash
.graphify-review-venv/bin/python .github/graphify-review/scripts/validate_evidence.py \
  <run-dir>/evidence/normalized-evidence.json
```

Execution states are `not_run`, `passed`, `failed`, `unavailable`, and `not_applicable`; completion is separately recorded as `complete`, `partial`, `not_completed`, or `not_applicable`. Confidence is awarded only for completed evidence. A dependency scan is complete only when every declared project target and the command-output hash are recorded.

Create the reproducibility manifest after tool execution, then finalize it after the manager has recorded the exact executed agent/model routes in `model-records.json`:

```bash
.graphify-review-venv/bin/python .github/graphify-review/scripts/hash_inputs.py \
  --repository . \
  --graph <run-dir>/evidence/project-graph.json \
  --profile-file <run-dir>/evidence/profile.json \
  --run-context <run-dir>/run-context.json \
  --tools <run-dir>/evidence/tool-results.json \
  --models <run-dir>/evidence/model-records.json \
  --output <run-dir>/evidence/manifest.json
```

The manifest identity includes source, graph, policy, selected profile, review-kit version, baseline, tool results, and model routing. Its timestamp comes from the immutable run context, so rerunning manifest generation inside one run is stable.

Random-control sampling uses the authored source snapshot, sampling subsection, and profile hash. Scoring, confidence, and model-routing policy changes do not alter the sample:

```bash
.graphify-review-venv/bin/python .github/graphify-review/scripts/select_review_samples.py \
  --repository . \
  --graph <run-dir>/evidence/project-graph.json \
  --profile-file <run-dir>/evidence/profile.json \
  --output <run-dir>/evidence/samples.json
```

## Deterministic outcomes

Confidence is separate from quality:

```bash
.graphify-review-venv/bin/python .github/graphify-review/scripts/calculate_confidence.py \
  --evidence <run-dir>/evidence/normalized-evidence.json \
  --output <run-dir>/evidence/confidence.json
```

Each category starts at `10.0`. Policy penalties combine verified severity with `technical_quality_impact` (`systemic`, `material`, `localized`, or `none`), independently from `production_impact`. Supported non-informational findings must be at least `localized`, so a real finding with no production consequence still lowers its category.

The overall grade defaults to `70%` category-weighted average plus `30%` weakest-category score. This makes a weak maintainability or security score visible in the overall grade instead of diluting it among unrelated 10/10 categories. Both weights are configurable under `scoring.overall_aggregation`; `review.json` records the weighted component, weakest category, pre-gate score, and final score in `production_readiness.score_breakdown`.

Selected profiles can additionally apply deterministic category caps for verified systemic or repeated framework-rule findings. Release hard gates and action lists continue to use production impact. Accepted risk can waive release impact while preserving technical score impact.

```bash
.graphify-review-venv/bin/python .github/graphify-review/scripts/calculate_score.py \
  --findings <run-dir>/evidence/verified-findings.json \
  --confidence-file <run-dir>/evidence/confidence.json \
  --profile-file <run-dir>/evidence/profile.json \
  --output <run-dir>/evidence/score.json
```

Release recommendations are:

- `GO`
- `GO_WITH_ACTIONS`
- `CONDITIONAL`
- `NO_GO`
- `INSUFFICIENT_EVIDENCE`

The synthesizer cannot override these results. Even when the numeric threshold is met, incomplete mandatory test/SCA/secret/SAST evidence forces `INSUFFICIENT_EVIDENCE`.

## Validate and test

Validate final JSON against policy, evidence, deterministic scoring, CVE provenance, verification thresholds, accepted-risk expiry, and immutable findings:

```bash
.graphify-review-venv/bin/python .github/graphify-review/scripts/validate_review.py \
  <run-dir>/review.json \
  --policy .github/graphify-review/policy.yaml \
  --profile-file <run-dir>/evidence/profile.json \
  --verified-findings <run-dir>/evidence/verified-findings.json \
  --evidence <run-dir>/evidence/normalized-evidence.json \
  --anchors <run-dir>/evidence/aspnet-anchors.json
```

Use the Spring anchor path for `spring-boot`; omit `--anchors` for `generic`. Validation requires exactly one evidence-backed disposition for every supplied anchor.

Validate the bundled example and run tests:

```bash
.graphify-review-venv/bin/python .github/graphify-review/scripts/validate_review.py \
  .github/graphify-review/examples/review.example.json

.graphify-review-venv/bin/python -m unittest discover \
  -s .github/graphify-review/tests \
  -v
```

Tests cover profile validation/aliases/detection, ASP.NET and Spring Boot deterministic anchors, JFrog parsing/provider fallback/secret redaction, stable finding fingerprints, baseline reconciliation (including inconclusive claims), versioned runs, source snapshots, non-production quality findings, weakest-category aggregation, mandatory confidence evidence, `INSUFFICIENT_EVIDENCE`, CVE safety, policy-isolated sampling, deterministic rendering, accepted-risk expiry, and synthetic end-to-end scenarios.

## HTML viewer

Open `.github/graphify-review/viewer/index.html` and select `review.json`, or serve the repository:

```bash
.graphify-review-venv/bin/python -m http.server 8000
```

Then open:

```text
Select a versioned `output/runs/<run-id>/review.json` in the viewer.
```

The viewer displays the selected run and profile, baseline reconciliation, quality and confidence separately, the weighted/weakest-category score breakdown, profile category caps, release controls, category scores, supported/rejected/inconclusive findings, accepted/expired risks, evidence provenance, tool execution/completion states, and reproducibility metadata. It filters technical-quality impact and production impact independently alongside the existing filters.

## Review governance

- Severity, technical-quality impact, production impact, likelihood, and remediation priority are independent.
- High/Critical findings require stronger policy-defined review and evidence.
- Critical findings require deterministic evidence when configured.
- Unsupported/inconclusive findings never affect scoring by default.
- Active risk acceptance remains visible and expirable; expired waivers do not affect release decisions.
- Standards mappings are evidence-based and do not increase severity by themselves.
- Model routing metadata records agent/model/role, never hidden reasoning.
- CI/CD is not a separate scoring category. Baseline revalidation and deterministic rescore are explicit run modes.

## License and attribution

This kit's original code, prompts, agent/skill instructions, configuration, and documentation are licensed under GNU AGPL version 3 only (`AGPL-3.0-only`). Commercial use and compliant redistribution are permitted. Redistributors must retain applicable legal notices, identify modifications, and satisfy the license's source-availability obligations. Operators of modified versions must also comply with section 13 where users interact with those versions remotely over a network. The full [license](LICENSE) governs these obligations.

The original concept and project creator is **De Jonckheere Stéphane (humblejok)**. Preserve the copyright and applicable attribution notices with the licensed material. There is no separate requirement to mention the author in every advertisement, and this license does not reserve ownership of an abstract idea or an independently implemented workflow.

Using the kit to inspect a proprietary application does not by itself place that application under the AGPL. Installing the kit into `.github` does not relicense unrelated repository files. Generated output is covered only when its content constitutes a covered work under section 2. Graphify and other third-party components retain their own terms; see [third-party acknowledgements](THIRD_PARTY_NOTICES.md).

When distributing just `.github`, retain the copies of the license and notices in `.github/graphify-review/`. Standalone source-file distributions must also carry the applicable notices and license. Update both root and packaged notice copies together. This project is provided without warranty, as described in the license.
