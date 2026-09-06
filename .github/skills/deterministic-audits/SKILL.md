---
name: deterministic-audits
description: Prefer existing deterministic linters, tests, dependency scanners, and security tools as evidence without installing software or mutating the repository.
user-invocable: false
---

# Deterministic Audits

LLMs interpret tool output; they do not replace deterministic scanners.

## Rules

- Use tools already declared/available in the repository or environment when safe and reasonably bounded.
- Treat selected-profile audit commands as known recipes, then confirm their wrapper/tool and prerequisites exist before execution. A profile never authorizes installing a tool.
- Do **not** install packages, update lockfiles, rewrite configuration, or change application source.
- Prefer repository-provided scripts (`npm test`, project test scripts, Make targets, configured linters) over guessed commands.
- Avoid destructive/integration tests that require unknown external services or mutate production-like data.
- Record exact commands in coverage/tool evidence.
- JFrog CLI configuration and credentials remain external to the review artifacts. Accept only an existing server ID; never persist URLs containing credentials, access tokens, passwords, raw secret findings, or scanner snippets.
- A tool not being installed is a coverage limitation, not a finding.
- A failed scanner invocation is evidence only about scanner execution, not automatically about application quality.
- Record one execution state: `not_run`, `passed`, `failed`, `unavailable`, or `not_applicable`.
- Never label a scan successful when its state is `not_run`, `unavailable`, or unknown.

## Common examples when already available/configured

Python:
- project test command / `pytest`
- `ruff`
- `bandit`
- `pip-audit`

JavaScript/TypeScript:
- repository `test`, `lint`, or `typecheck` scripts
- `npm audit` when the package manager/lockfile context makes it safe and does not mutate files
- configured ESLint/type checker

Java/JVM:
- repository Maven/Gradle test and static-analysis tasks
- OWASP Dependency-Check when already configured
- SpotBugs/PMD/Checkstyle when already configured

.NET:
- repository solution/project `dotnet build --no-restore` and `dotnet test --no-restore`
- `dotnet format --verify-no-changes --no-restore` when the project uses the corresponding analyzers/tooling
- `dotnet list package --vulnerable --include-transitive` when restore state permits a non-mutating result

JFrog Xray, when `jf` is already installed and configured:
- `jf audit --nuget --sca --without-contextual-analysis --format=simple-json --fail=false --vuln` for ASP.NET/.NET dependency evidence
- corresponding `--mvn` or `--gradle` audit for Spring dependency evidence
- `jf audit --secrets --format=simple-json --fail=false` for secret metadata when the JFrog Advanced Security entitlement is available
- a completed scan that reports findings is `failed`/`complete`; authentication, entitlement, dependency-resolution, timeout, and malformed-output failures remain `unavailable`

Other stacks:
- use the repository's declared equivalents rather than inventing tooling requirements.

## CVE claims

Never state that dependencies are free of known CVEs unless an appropriate dependency scanner or authoritative advisory source was actually evaluated during the current review. Never infer a named CVE from package age, version intuition, or model memory. CVE claims require structured `dependency_vulnerability` evidence containing package/version, advisory ID, and tool/advisory source.
