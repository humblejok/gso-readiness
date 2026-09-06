# Production policy

The application may be distributed or made available to end users only when the reviewed evidence supports an appropriate level of production readiness for its scope and risk.

Environment assumptions and governance decisions come from `.github/graphify-review/policy.yaml`; this narrative does not override policy. Missing evidence reduces assessment confidence rather than project-quality scores.

## 1. Software quality

Assess:
- correctness and defensive behavior
- clear contracts and error handling
- language/framework practices when they have concrete runtime or maintenance consequences
- dependency hygiene
- maintainable implementation complexity

Do not score based on style preferences or fashionable patterns.

## 2. Security

Assess technology-relevant risks including:
- authentication and authorization
- injection and unsafe parsing/deserialization
- trust-boundary validation
- credential/secret handling
- sensitive-data exposure/logging
- SSRF, path traversal, unsafe redirects, request forwarding, command execution
- cryptographic misuse
- insecure production defaults and security-related configuration
- known vulnerable dependencies **only when a dependency/CVE tool or equivalent authoritative evidence was actually used**

OWASP is a risk taxonomy and review guide, not a checklist that proves security by absence of textual matches.

## 3. Conventional-server deployability

The application must be reasonably deployable without assuming containers.

Inspect where relevant:
- externalized environment configuration
- hardcoded environment-specific values
- hardcoded secrets/credentials
- production startup/process definition
- runtime dependencies and OS/path assumptions
- database/cache/broker configuration
- migrations and startup prerequisites
- filesystem/persistence/write permissions
- logging and diagnostics
- background/scheduled processes
- reverse-proxy/TLS/trusted-host/CORS assumptions

External infrastructure may be managed outside the repository. Record missing evidence rather than inventing missing infrastructure.

## 4. Modularity and maintainability

Use Graphify heavily for investigation prioritization, then source verification for conclusions.

Assess:
- architectural boundaries
- dependency direction
- harmful cycles
- cohesion/responsibility concentration
- coupling and change propagation
- brittle cross-module contracts
- duplication/complexity with demonstrated maintenance cost

A high metric alone is not a finding.

## 5. Testing and operational suitability

Assess proportionately to application risk:
- meaningful automated tests around important behavior
- error/boundary-path tests where warranted
- integration/contract tests at significant boundaries
- deterministic/reproducible test execution
- logging/diagnostic ability
- startup/shutdown/background-task behavior
- health/observability evidence where architecture requires it

Do not invent numerical code coverage.
