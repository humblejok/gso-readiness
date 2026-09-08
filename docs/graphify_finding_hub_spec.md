# Graphify Finding Hub and Optional Jira Synchronizer — Implementation Specification

## 1. Document status

- **Status:** SaaS/white-label specification with an initial implementation in `hub/`; production acceptance remains subject to the release gates in section 24
- **Revision:** 2 — 2026-09-07
- **Target runtime:** Python 3.12 or newer
- **Web framework:** Django 5.2 LTS, latest supported patch release
- **API framework:** Django REST Framework
- **Production database:** PostgreSQL 17 reference deployment; use a supported PostgreSQL release
- **Development database:** SQLite
- **Primary input:** Authoritative Graphify Review `review.json`, schema version `2.1`
- **Jira target:** Jira Data Center/Server REST API v2
- **Jira integration:** Optional and disabled by default

The implementation must pin exact dependency versions in its lock file or generated requirements files. The Django patch version must be kept current for security fixes without changing the LTS minor line unexpectedly.

---

## 2. Purpose

The application receives validated findings produced by the Graphify Production Review Kit, stores their history, reconciles their lifecycle across review runs, and optionally creates or updates associated Jira issues.

It is a finding registry even when Jira is not configured. Jira availability, permissions, authentication, workflow configuration, or licensing must never be prerequisites for importing and querying Graphify review results.

The application must:

1. Accept a complete review import containing findings and coding-agent-oriented remediation plans.
2. Validate and store the source review without changing its verified meaning.
3. Upsert findings using a stable repository identity and finding fingerprint.
4. Preserve every observation of a finding across runs.
5. Apply only explicit Graphify baseline reconciliation when changing lifecycle state.
6. Expose findings and delivery state through an authenticated REST API and Django administration site.
7. Optionally synchronize eligible findings to Jira using only standard Jira capabilities.
8. Operate normally with no Jira connection.
9. Isolate each customer organization, including uploads, reads, workers, integrations, exports and audit records.
10. Provide verified user accounts, workspace memberships and an end-user dashboard.
11. Support monthly/yearly hosted subscriptions and manually invoiced enterprise agreements without requiring billing configuration for local development.
12. Support configurable white-label branding and dedicated enterprise deployments from the same codebase.

---

## 3. Goals and non-goals

### 3.1 Goals

- Durable, idempotent import of Graphify review results.
- Stable association between a finding and an external work item.
- Complete audit history for received reviews and finding changes.
- Structured remediation instructions suitable as input to a coding agent.
- Optional, retryable and observable Jira delivery.
- No dependency on Jira custom fields or Jira administrator permissions.
- Safe coexistence with human edits and comments in Jira.
- PostgreSQL production support and frictionless SQLite development.
- An API contract that could support adapters other than Jira later.

### 3.2 Non-goals for the first release

- Executing remediation instructions or modifying source repositories.
- Replacing Jira as a project-management application.
- Bidirectional synchronization of every Jira field.
- Installing a Jira plugin or defining custom fields.
- Automatically configuring Jira projects, workflows, screens, permissions, or webhooks.
- Treating Jira status as proof that a source finding is resolved.
- Parsing `REVIEW.md`; only machine-readable JSON is authoritative.
- Supporting Jira Cloud in the first release. The Jira adapter boundary must allow it later.
- Customer-specific source forks, automatic remediation execution, and automatic provisioning of customer infrastructure.
- Automatically issuing enterprise legal terms, choosing real prices, collecting live payments without operator configuration, or changing the existing AGPL license.

---

## 4. Core design decisions

### 4.1 The registry is independent of Jira

The domain model must not contain mandatory foreign keys to Jira configuration. With Jira disabled:

- review import succeeds;
- findings and their history remain queryable;
- no Jira delivery jobs are required;
- application liveness and readiness remain healthy;
- responses explicitly report Jira as `not_configured`, not as an application failure.

Disabling a previously configured Jira connection must preserve all existing findings, delivery records and Jira issue associations. Re-enabling it may enqueue synchronization of the latest desired state.

### 4.2 Stable upsert identity

`candidate_id` values such as `ARCH-C001` and `COR-C005`, and display IDs such as `ARCH-001`, must not be used as the database or Jira upsert identity. They can change between runs and are not globally unique.

The canonical finding key is:

```text
(organization.id, repository.external_id, finding.fingerprint)
```

The fingerprint is the `sha256:...` value already present in a validated Graphify finding. The repository external ID must be stable across renames visible to users. Prefer an organization-assigned UUID or the canonical, normalized source-control repository URL.

The database must enforce unique `(organization, repository.external_id)` and `(repository, finding.fingerprint)` constraints. The Jira association must reference the resulting internal finding UUID. Neither an upload payload nor a display ID may select a tenant. API-token ownership establishes tenant context; browser access requires a verified user's membership in the workspace selected in the URL.

### 4.3 Two separate status domains

The application must not conflate:

- **Graphify verification/lifecycle status:** whether a defect is supported, still present, resolved, superseded, inconclusive, or not reproduced.
- **Jira workflow status:** whether work is To Do, In Progress, Done, or another project-specific state.

Graphify is authoritative for the technical finding lifecycle. Jira is authoritative for work planning. A Jira issue being closed is not evidence that the defect was fixed.

### 4.4 Declarative imports, asynchronous delivery

Import requests describe the complete result of a review run. The API transaction stores the import, observations, current finding state and outbound events. It must not wait for Jira.

Jira synchronization runs asynchronously after the database transaction commits. A Jira outage must not make the import endpoint fail.

### 4.5 Human Jira content is preserved

On Jira issue creation, the integration may populate the initial description. On later synchronization it must not replace the entire description or edit human comments.

The integration must own one dedicated Jira comment and update that comment with the latest Graphify state. Its comment ID is stored in the association table. Material lifecycle events may additionally create an append-only comment when configured.

### 4.6 Source-item selection

The application imports the final validated `review.json`, not the unverified intermediate output of specialist agents.

- `review.findings` are eligible work items and require remediation plans.
- `inconclusive_findings` are retained with their uncertainty but do not create Jira issues by default.
- `rejected_findings` are retained for audit and never create Jira issues by default.
- `strengths`, including identifiers such as `COR-S001` when that naming convention is used, remain visible through the stored review import but are not defects and do not require remediation or Jira issues.
- Raw candidates such as `ARCH-C001` may be retained as `candidate_id` on the final finding, but are not accepted directly without the final verification result and stable fingerprint.

This prevents a Graphify investigation signal, rejected candidate, or positive strength from silently becoming remediation work.

---

## 5. System context

```text
Graphify review kit / CI / developer workstation
                       │
                       │ authenticated review-import envelope
                       ▼
             Django REST ingestion API
                       │
                       ▼
        PostgreSQL production / SQLite development
             │                    │
             │                    └── findings, observations, audit history
             ▼
       transactional outbox
             │
             ▼
      background delivery worker
             │
             ├── Jira disabled: no external action
             │
             └── Jira enabled
                       │
                       ▼
                Jira REST API v2
```

---

## 6. Suggested Django project structure

```text
hub/
├── manage.py
├── pyproject.toml
├── README.md
├── .env.example
├── Dockerfile                    # optional but recommended
├── config/
│   ├── settings/
│   │   ├── base.py
│   │   ├── development.py
│   │   ├── test.py
│   │   └── production.py
│   ├── urls.py
│   ├── wsgi.py
│   └── asgi.py
├── contracts/                   # vendored review/finding schemas; offline validation
├── templates/                   # customer dashboard and account flows
├── static/                      # local assets, no external frontend runtime
├── tests/                       # SQLite and PostgreSQL acceptance tests
└── hubapp/
    ├── models.py                # tenant-owned domain records and control tables
    ├── tenancy.py               # fail-closed query scope
    ├── middleware.py            # membership/host boundaries
    ├── services.py              # accounts, quotas, tokens and audit services
    ├── contracts.py             # envelope validation and remediation rendering
    ├── imports.py               # immutable imports and lifecycle reconciliation
    ├── api.py                   # token API
    ├── views.py                 # customer interface
    ├── billing.py               # optional Stripe adapter
    ├── jira.py                  # optional Jira adapter and worker processing
    ├── migrations/              # schema, PostgreSQL RLS and reference guards
    └── management/commands/     # delivery worker and operator maintenance
```

Business rules must live in services/domain functions, not in serializers, views, model `save()` overrides, signals, or the Jira HTTP client. Django signals must not drive synchronization.

---

## 7. Import contract

### 7.1 Endpoint

```http
POST /api/v1/review-imports
Authorization: Bearer <ingestion-token>
Content-Type: application/json
Idempotency-Key: <repository-external-id>:<review-run-id>
```

The endpoint accepts an envelope rather than modifying Graphify's authoritative `review.json` contract. Envelope version `1.0` remains unchanged: there is no client-supplied tenant field. Idempotency keys are interpreted only within the authenticated token's organization. Tokens can additionally be restricted to a repository external ID.

### 7.2 Envelope example

The review kit provides `/export-review review=<review.json-or-run-directory> repository-id=<stable-id> default-branch=<branch>` to produce this envelope from a previous schema-2.1 full-project review. It prepares a new local export directory, generates or accepts source-bound remediation plans, then validates and writes `import-envelope.json` without uploading or rerunning audits. The original review is embedded unchanged. Source metadata is taken from the review or matching run context; explicit missing-metadata values must describe the original revision and must not conflict with recorded values. No historical revision is inferred from current HEAD. See the root README for the deterministic `export_review.py prepare` / `build` CLI and optional arguments.

Preparation output is not an import envelope. The builder rejects missing/invalid remediation plans and source review changes between preparation and build. Every export uses new output paths; existing envelopes are never overwritten. Retry imports with the exact same envelope because a changed payload for an already imported repository/run conflicts. Contract validation does not rerun historical policy/evidence checks or prove remediation correctness. The kit and Hub share a portable offline validator, with vendored-copy and schema parity covered by tests.

```json
{
  "schema_version": "1.0",
  "repository": {
    "external_id": "scm:7c61264d-6d52-43cf-bab8-a818ab06ca4c",
    "name": "orders-api",
    "clone_url": "https://git.example.invalid/payments/orders-api.git",
    "default_branch": "main"
  },
  "source": {
    "commit_sha": "0123456789abcdef",
    "branch": "main"
  },
  "review": {
    "schema_version": "2.1",
    "run": {},
    "review": {},
    "production_readiness": {},
    "findings": [],
    "inconclusive_findings": [],
    "rejected_findings": []
  },
  "remediations": [
    {
      "schema_version": "1.0",
      "finding_fingerprint": "sha256:db4619fab307f350aa157100cb8f9c782d9f58090ac31fd5a1aa393a545db395",
      "generated_from_commit": "0123456789abcdef",
      "objective": "Separate pricing policy and notification dispatch from order orchestration.",
      "preconditions": [
        "Existing order behavior and public API contracts must remain unchanged."
      ],
      "constraints": [
        "Use the repository's existing dependency injection conventions.",
        "Do not introduce a new infrastructure dependency."
      ],
      "non_goals": [
        "Do not redesign the public order API."
      ],
      "implementation_steps": [
        {
          "order": 1,
          "instruction": "Extract pricing decisions behind the existing pricing collaborator boundary.",
          "affected_paths": ["src/orders"],
          "affected_symbols": ["OrderService"]
        }
      ],
      "acceptance_criteria": [
        "OrderService no longer implements pricing policy.",
        "Existing public behavior remains covered by tests."
      ],
      "validation_commands": [
        "dotnet test"
      ],
      "risks_and_rollback": [
        "Keep the refactor behavior-preserving and revert the extraction if contract tests change."
      ],
      "notes": "Do not perform unrelated cleanup."
    }
  ]
}
```

The abbreviated `review` object above is illustrative; a real request must contain a complete, valid `review.json` document.

Envelope schema `1.0` uses `additionalProperties: false` and requires `schema_version`, `repository`, `source`, `review`, and `remediations`. `source.commit_sha` must be a 7-to-64-character hexadecimal revision and `source.branch` must be non-empty. The repository external ID and source revision are integration metadata; they do not replace or mutate fields in the authoritative review.

### 7.3 Remediation-plan requirements

A remediation plan is required for every item in `review.findings`. It is optional for inconclusive and rejected findings.

Each plan must:

- reference an imported finding fingerprint;
- identify the source commit against which it was generated;
- state an outcome rather than merely saying “fix the issue”;
- contain ordered, independently understandable implementation steps;
- identify relevant paths or symbols when known, without inventing them;
- define observable acceptance criteria;
- define validation commands or explicitly state why none are available;
- record constraints, non-goals, risks and rollback considerations when applicable;
- be treated as proposed instructions, not as evidence that a change is safe or complete.

The Finding Hub stores and renders remediation plans but never executes their commands.

The remediation schema uses `additionalProperties: false`. Required fields are:

- `schema_version`;
- `finding_fingerprint`;
- `generated_from_commit`;
- `objective`;
- `preconditions`;
- `constraints`;
- `non_goals`;
- `implementation_steps`;
- `acceptance_criteria`;
- `validation_commands`;
- `risks_and_rollback`.

`implementation_steps` and `acceptance_criteria` must each contain at least one entry. Step order values must be unique positive integers and contiguous from `1`. A validation command is stored as an argument-preserving string for human/coding-agent use and is never executed by this service. If `validation_commands` is empty, a non-empty `validation_notes` field is required. `notes` and `validation_notes` are the only optional top-level fields in remediation schema `1.0`.

### 7.4 Import validation

The API must reject a request with `400 Bad Request` when:

- the envelope or supported `review.json` schema is invalid;
- the source review omits required authoritative sections;
- a fingerprint is malformed or does not match the supplied normalized identity;
- finding fingerprints are duplicated within a review collection;
- a remediation references an unknown fingerprint;
- a required remediation plan is missing;
- the envelope source revision conflicts with a source revision present in the review;
- `generated_from_commit` differs from `source.commit_sha`;
- input limits are exceeded.

The implementation should vendor the supported Graphify JSON schemas and validate with `jsonschema`. It must also implement semantic checks that JSON Schema alone cannot express.

Default limits:

- 10 MiB maximum request body;
- 1,000 findings across all collections;
- 64 KiB per rendered remediation plan;
- 500 implementation steps per request;
- no raw secret values, scanner excerpts containing secrets, credentials, or authentication headers.

### 7.5 Idempotency

The unique import identity is `(repository, review.run.run_id)`.

The server calculates a SHA-256 hash of canonicalized envelope JSON:

- first submission: persist and return `201 Created`;
- repeated identity with the same payload hash: perform no work and return `200 OK` with the original import ID;
- repeated identity with a different payload hash: return `409 Conflict` and create an audit event.

An `Idempotency-Key` header is required and must agree with the repository/run identity in the payload. It is not a substitute for database uniqueness.

### 7.6 Import response

```json
{
  "import_id": "a6eb924f-4060-4e8c-a9a4-93eaebda7bfd",
  "repository_id": "f1e43e22-fdb4-4a1e-adcf-e6427628eaf6",
  "run_id": "20260906-example",
  "created_findings": 3,
  "updated_findings": 4,
  "lifecycle_changes": 1,
  "jira": {
    "state": "not_configured",
    "queued_deliveries": 0
  }
}
```

---

## 8. Finding lifecycle rules

### 8.1 Current lifecycle states

The registry uses these internal lifecycle states:

- `open`
- `resolved`
- `superseded`
- `not_reproduced`
- `inconclusive`
- `rejected`

Verification status from the latest observation remains stored separately.

### 8.2 Initial import

- Supported or partially supported `review.findings` become `open`.
- `inconclusive_findings` become `inconclusive` unless an existing finding is being carried by revalidation.
- `rejected_findings` become `rejected` and are retained for audit, but are ineligible for Jira creation by default.

### 8.3 Subsequent fresh or rescore import

- A matching fingerprint updates `last_seen`, descriptive fields and the latest observation.
- A finding missing from a `fresh` or `rescore` import does not change lifecycle state.
- Fresh-review absence must never close, resolve or delete a finding.
- Rescore may change scores or priorities, but not claim that source remediation occurred.

### 8.4 Revalidate import

Only `review.run.finding_reconciliation` may establish these lifecycle changes:

| Reconciliation status | Registry behavior | Default Jira behavior |
|---|---|---|
| `still_present` | Set/currently retain `open` | Update managed comment; do not reopen automatically |
| `resolved` | Set `resolved` with evidence and rationale | Comment; transition only when explicitly configured |
| `superseded` | Set old finding `superseded`; link replacement | Comment and reference replacement issue |
| `not_reproduced` | Set `not_reproduced`; preserve uncertainty | Comment; do not close automatically |

A subsequent revalidation that explicitly finds the same fingerprint again may return `not_reproduced` or `inconclusive` to `open`. Every lifecycle change must create an immutable history record.

### 8.5 Deletion

Imported reviews, observations, fingerprints, and Jira associations must not be physically deleted through the normal API. Administrative erasure, if required by retention policy, must be explicit, privileged, audited and cascading only according to documented rules.

---

## 9. Data model

All primary keys should be UUIDs. All models include `created_at` and `updated_at` where meaningful. Immutable models have only `created_at`.

### 9.1 `Repository`

| Field | Type | Rules |
|---|---|---|
| `id` | UUID | Primary key |
| `organization` | foreign key | Required tenant owner |
| `external_id` | string | Unique within organization, immutable |
| `name` | string | Display value |
| `clone_url` | URL/string | Credential-free HTTPS metadata; not an identity or global uniqueness boundary |
| `default_branch` | string | Defaults to `main` only when supplied or configured |
| `active` | boolean | Default true |

### 9.2 `ReviewImport`

| Field | Type | Rules |
|---|---|---|
| `id` | UUID | Primary key |
| `repository` | foreign key | Required |
| `run_id` | string | Unique with repository |
| `review_id` | string | Source review ID |
| `mode` | enum | `fresh`, `revalidate`, `rescore` |
| `review_schema_version` | string | Initially `2.1` |
| `source_commit` | string | From required envelope source revision; must agree with the review when present |
| `source_snapshot_hash` | string | Required SHA-256 identifier |
| `payload_hash` | string | Canonical envelope SHA-256 |
| `payload` | JSON | Immutable validated envelope or policy-approved subset |
| `received_by` | foreign key | API client |
| `received_at` | datetime | UTC |

Constraint: unique `(repository, run_id)`.

### 9.3 `Finding`

| Field | Type | Rules |
|---|---|---|
| `id` | UUID | Primary key |
| `repository` | foreign key | Required |
| `fingerprint` | string | `sha256:` plus 64 hexadecimal characters |
| `identity` | JSON | Normalized Graphify identity |
| `display_id` | string | Latest final ID, not identity |
| `candidate_id` | nullable string | Latest candidate ID, not identity |
| `category` | enum | Graphify category |
| `title` | string | Latest validated value |
| `description` | text | Latest validated value |
| `impact` | text | Latest validated value |
| `recommendation` | text | Latest validated value |
| `severity` | enum | Latest value |
| `technical_quality_impact` | enum | Latest value |
| `production_impact` | enum | Latest value |
| `remediation_priority` | enum | Latest value |
| `verification_status` | enum | Latest value |
| `confidence` | decimal | Latest value |
| `lifecycle_state` | enum | Registry lifecycle |
| `first_seen_import` | foreign key | Required |
| `last_seen_import` | foreign key | Required |
| `resolved_at` | nullable datetime | Set only from explicit reconciliation |

Constraint: unique `(repository, fingerprint)`.

### 9.4 `FindingObservation`

An immutable snapshot relating a finding to a review import.

Fields include:

- finding and review import foreign keys;
- display and candidate IDs at observation time;
- verification and lifecycle inputs;
- complete validated finding JSON;
- reconciliation status, rationale and evidence when applicable;
- observation timestamp.

Constraint: unique `(finding, review_import, observation_kind)`.

### 9.5 `RemediationPlan`

| Field | Type | Rules |
|---|---|---|
| `id` | UUID | Primary key |
| `finding_observation` | one-to-one | Required |
| `schema_version` | string | Initially `1.0` |
| `generated_from_commit` | string | Required |
| `structured_plan` | JSON | Validated plan object |
| `rendered_markdown` | text | Deterministically generated server-side |
| `plan_hash` | string | SHA-256 of canonical plan JSON |

Clients must not supply `rendered_markdown`; this prevents differences between API and Jira representations.

### 9.6 `JiraConnection`

Optional configuration representing one Jira instance.

| Field | Type | Rules |
|---|---|---|
| `id` | UUID | Primary key |
| `organization` | foreign key | Required tenant owner |
| `name` | string | Unique display/configuration key within organization |
| `base_url` | HTTPS URL | No path credentials or URL user info |
| `enabled` | boolean | Default false |
| `auth_method` | enum | `pat`, optional `basic_legacy` |
| `encrypted_token` | encrypted text | SaaS customer-entered PAT encrypted with operator-injected Fernet keys; never exposed through read endpoints |
| Corporate CA | operator setting | `JIRA_CA_BUNDLE`; never an arbitrary customer-supplied server filesystem path |
| `request_timeout_seconds` | integer | Bounded default |
| `host_allowlisted` | boolean/config result | Must pass outbound policy |

Deleting a connection with associations is prohibited; disable/archive it instead.

### 9.7 `JiraProjectBinding`

Associates a repository with zero or one active Jira target for the MVP.

Fields include:

- repository and Jira connection;
- Jira project key;
- issue type ID or name;
- enabled flag;
- labels to add;
- severity/remediation-priority mapping;
- creation policy by verification and lifecycle status;
- eligible source branches, defaulting to the repository's default branch;
- logical lifecycle-to-transition mapping;
- whether summary or priority may be updated after creation;
- whether resolved issues may be automatically reopened.

Default policy:

- create only supported and partially supported open findings;
- do not create for rejected or inconclusive findings;
- never auto-reopen;
- never transition to Done unless an explicit `resolved` mapping exists;
- preserve human description changes.

### 9.8 `JiraIssueAssociation`

| Field | Type | Rules |
|---|---|---|
| `id` | UUID | Primary key |
| `finding` | foreign key | Required |
| `connection` | foreign key | Required |
| `issue_id` | string | Jira immutable numeric/string ID when available |
| `issue_key` | string | Jira display key |
| `managed_comment_id` | nullable string | Integration-owned comment |
| `remote_identity_written` | boolean | Property/marker state |
| `last_delivered_observation` | nullable foreign key | Latest successful sync |
| `last_payload_hash` | nullable string | No-op detection |
| `last_known_jira_status` | nullable string | Informational only |

Constraints:

- unique `(finding, connection)`;
- unique `(connection, issue_id)` when issue ID is present;
- unique `(connection, issue_key)`.

### 9.9 `OutboxEvent` and `DeliveryAttempt`

`OutboxEvent` records desired external work in the same transaction as the import. It includes event type, finding/observation, target, state, attempt count, next attempt, lock ownership and a deduplication key.

`DeliveryAttempt` is immutable and stores start/end timestamps, result classification, sanitized HTTP status, correlation ID and sanitized error. It must never store credentials, authorization headers, raw secret findings, or unrestricted response bodies.

### 9.10 `ApiClient` and `AuditEvent`

API tokens are shown once at creation and stored only as Django password hashes. Clients have an immutable organization owner, expiry, revocation state, optional repository restriction, and explicit `reviews:write` / `findings:read` scopes. Customer administrative actions use authenticated, CSRF-protected browser sessions and workspace roles, not a platform-wide `admin` ingestion scope.

`AuditEvent` records authentication failures, import conflicts, configuration changes, manual retry/cancel actions, connection tests, and administrative erasure.

---

## 10. Transaction and concurrency behavior

The import service must run inside `transaction.atomic()` and perform these steps:

1. Authenticate and authorize the API client.
2. Validate request size, envelope, Graphify review and remediation plans.
3. Calculate the canonical payload hash.
4. Lock or create the repository and import identity.
5. Return the prior result for an identical import or reject a conflicting one.
6. Create the immutable `ReviewImport`.
7. Upsert current `Finding` records by repository and fingerprint.
8. Create immutable observations and remediation plans.
9. Apply explicit reconciliation lifecycle transitions.
10. Create deduplicated outbox events only for configured eligible targets.
11. Commit, then return the import response.

PostgreSQL uniqueness constraints are the final defense against concurrent duplicate imports and Jira associations. Catch constraint violations and resolve them as idempotent success or conflict as appropriate.

SQLite development mode supports one API process and one delivery worker only. Production concurrency behavior must be tested against PostgreSQL; SQLite passing tests is not proof of equivalent locking behavior.

---

## 11. Read API

Minimum endpoints:

```text
POST /api/v1/review-imports
GET  /api/v1/review-imports/{id}
GET  /api/v1/repositories
GET  /api/v1/repositories/{id}
GET  /api/v1/repositories/{id}/findings
GET  /api/v1/findings/{id}
GET  /api/v1/findings/{id}/observations
GET  /api/v1/findings/{id}/remediation
GET  /api/v1/findings/{id}/deliveries
POST /api/v1/deliveries/{id}/retry       # privileged
POST /api/v1/jira-connections/{id}/test  # privileged and audited
GET  /health/live
GET  /health/ready
```

Implementation note: the initial application exposes retry/cancel and Jira connection tests through CSRF-protected, role-checked workspace browser routes instead of administrative ingestion-token endpoints. The delivered read/import routes are documented in `hub/contracts/openapi.json`; the two administrative API routes above are reserved for a future separately scoped admin API.

Finding list filters:

- repository;
- lifecycle state;
- verification status;
- category;
- severity;
- technical-quality impact;
- production impact;
- remediation priority;
- Jira association state;
- first/last-seen time.

Responses must be paginated, ordered deterministically, and documented with OpenAPI. The API returns structured remediation JSON and deterministic Markdown separately.

---

## 12. Jira adapter specification

### 12.1 Optional loading and configuration

The Jira adapter is inactive unless all of these are true:

- global outbound integration is enabled;
- the selected Jira connection is enabled;
- the repository has an enabled project binding;
- the finding matches the binding's creation/update policy.

Missing optional configuration must produce `not_configured` or `skipped`, not retries and not application readiness failure.

An enabled but invalid connection is a configuration error visible in administration and health dependency details. It does not prevent imports.

### 12.2 Authentication and TLS

Preferred Jira Data Center authentication is a personal access token belonging to a dedicated, least-privileged service account, sent as a bearer token. Basic authentication may be supported only behind an explicit legacy feature flag.

Customer-entered credentials are encrypted before database persistence. Encryption keys come only from `HUB_ENCRYPTION_KEYS` or operator secret injection, never the database. Plaintext PATs must never appear in source control, logs, task payloads, HTML or read API responses. The credential is write-only; `rotate_jira_secrets` supports key rotation. A future external secret-manager adapter may replace encrypted database storage without exposing arbitrary environment-variable lookup to tenants.

Requirements:

- HTTPS Jira URLs by default;
- server certificate and hostname verification always enabled;
- optional corporate CA bundle;
- no `verify=False`, trust-all callback, or insecure retry path;
- no automatic cross-host redirects carrying authorization;
- mandatory operator-approved outbound hostname allowlist, validated DNS/IP policy, pinned destination address and original-host TLS verification to reduce SSRF/rebinding risk;
- private addresses only for explicitly approved hosts in a dedicated deployment, never through the shared public service.

The service account needs only the existing project permissions required to browse, create, edit, comment and optionally transition issues. It does not require Jira administrator permission or custom-field administration.

### 12.3 Jira issue creation

Default standard-field mapping:

| Source | Jira destination |
|---|---|
| Finding title and display ID | Summary |
| Description, impact, evidence summary, remediation | Initial description |
| Remediation priority/severity | Configured Jira priority, when available |
| Category and integration identity | Labels |
| Configured repository binding | Project and issue type |

Example summary:

```text
[Graphify][ARCH-001] Order orchestration concentrates unrelated collaborators
```

The description contains these deterministic sections:

1. Graphify identity and source revision.
2. Verification and lifecycle status.
3. Description and impact.
4. Evidence locations, excluding raw secrets.
5. Coding-agent remediation plan.
6. Acceptance criteria and validation commands.
7. Link back to the Finding Hub detail endpoint when configured.

Jira-specific markup must be escaped. Source text must never be interpreted as active links, mentions or Jira macros unless the renderer deliberately allows it.

### 12.4 Duplicate prevention and recovery

The `JiraIssueAssociation` table is the primary lookup. Issue labels, description markers and Jira issue properties are secondary recovery mechanisms.

On successful creation, write an issue property when the Jira endpoint and permissions allow it:

```json
{
  "schema_version": "1.0",
  "repository_external_id": "scm:7c61264d-6d52-43cf-bab8-a818ab06ca4c",
  "finding_fingerprint": "sha256:db4619fab307f350aa157100cb8f9c782d9f58090ac31fd5a1aa393a545db395"
}
```

The property key is `graphify.finding.identity`. This does not require a custom field, but the implementation must not assume the property is searchable by JQL without a Jira plugin/index declaration. A tenant UUID must also be included in any external identity representation. The initial adapter uses the full tenant/repository/fingerprint description marker and collision-resistant label; issue-property writing is a future enhancement, not a dependency for synchronization.

Also add:

- label `graphify-review`;
- a collision-resistant label derived from the repository and fingerprint, subject to Jira label limits;
- the complete external identity as a visible, machine-readable description line.

If the local association is lost, recovery may search the narrow `graphify-review` label set, fetch candidate issues and confirm the complete issue property or description marker. Never trust the shortened label alone.

Before creation, acquire a database lock/advisory lock for `(connection, finding)` and recheck the association. Jira issue creation has no dependable client-provided idempotency key, so local transaction and recovery controls are mandatory.

### 12.5 Jira issue update

When an association exists:

1. Fetch the issue and confirm it remains accessible.
2. Create the integration-owned comment if no managed comment exists.
3. Otherwise update only that comment with the latest observation and remediation.
4. Optionally update summary, priority and integration-owned labels according to binding policy.
5. Never remove unrelated labels.
6. Never replace human description changes after initial creation.
7. Record the successful observation and payload hash.

If the desired outbound payload hash equals the last successful payload hash, mark the event as a no-op success without calling Jira.

### 12.6 Jira workflow transitions

Jira status changes must use available workflow transitions; status must not be treated as an editable text field.

Mappings are per project binding and use logical actions:

```text
resolved   -> configured transition ID/name, optional
reopened   -> configured transition ID/name, disabled by default
```

The adapter must query currently available transitions before invoking one. If no configured transition is available, it updates the managed comment, records `manual_transition_required`, and considers the content delivery successful.

Default safety rules:

- no automatic resolution without explicit Graphify `resolved` reconciliation;
- no automatic closure for `not_reproduced` or `inconclusive`;
- no automatic reopen when Jira is already closed but Graphify says `still_present`;
- no transition triggered solely by severity, score, or issue absence from a fresh review.

### 12.7 Jira errors and retries

Classify failures:

- `401`/`403`: configuration/permission failure; limited retries, prominent operator alert;
- `404`: missing project/issue or inaccessible resource; attempt safe association recovery before terminal failure;
- `409`: refetch state and retry when operation is safe;
- `429`: honor `Retry-After`;
- `5xx`, network timeout or temporary DNS failure: retryable;
- Jira validation `400`: terminal until configuration or mapping changes.

Use bounded connect and read timeouts and bounded retry attempts. The initial worker uses exponential backoff capped at six hours plus jitter, respects bounded `Retry-After` delays up to 24 hours, permits eight automated attempts, and uses a ten-minute stale-processing lease. Superseded queued observations must not overwrite more recent default-branch observations. A failed or interrupted first creation must enter identity recovery before another create is possible. If no unique matching remote issue can be confirmed, require operator investigation instead of blindly creating another issue. Operators can retry terminal events after correcting configuration, but retries must preserve attempt history and recovery safeguards.

Response bodies stored in logs or delivery attempts must be length-limited and sanitized.

---

## 13. Background worker

The MVP may use a database-backed transactional outbox and a Django management command:

```text
python manage.py process_delivery_outbox
```

Production runs the web process and at least one separate worker process. The initial PostgreSQL implementation serializes work per organization using row locks, persists an attempt before external work, and processes at most one event per workspace per sweep for fairness. A non-expired processing lease prevents another worker from overtaking that workspace. SQLite permits only one worker. Skip-locked worker claiming is a later throughput optimization, not required by the initial implementation.

The worker must support graceful shutdown, stale-processing recovery, deduplication, bounded batches, and tenant/event correlation. Shared-host rate limiting and jitter remain release-hardening requirements; deployment-level egress controls are required before enabling large-scale integrations.

Celery/Redis is not required for the MVP. The delivery service interface must allow replacing the management-command worker with Celery or another enterprise scheduler later without changing domain or Jira adapter code.

---

## 14. Configuration

Minimum environment settings:

```text
DJANGO_SETTINGS_MODULE
DJANGO_SECRET_KEY
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS
DATABASE_URL                         # PostgreSQL production; SQLite development default
HUB_PUBLIC_URL                       # canonical HTTPS origin in production
OUTBOUND_INTEGRATIONS_ENABLED=false
JIRA_ALLOWED_HOSTS                   # mandatory when Jira is enabled
LOG_LEVEL=INFO
```

Example optional Jira secret settings:

```text
HUB_ENCRYPTION_KEYS=<fernet-key-newest-first>
JIRA_CA_BUNDLE=C:\path\to\corporate-ca.pem
```

The corresponding database connection stores authenticated ciphertext, not a plaintext PAT. Customer forms cannot choose arbitrary environment keys or CA paths. See `hub/.env.example` for the executable configuration contract, including billing and deployment modes.

Configuration precedence and validation must be documented. Production startup must fail for missing core Django/database secrets, but never because Jira is absent when outbound integration is disabled.

---

## 15. Security requirements

- Terminate TLS at the application or a trusted reverse proxy.
- Require authentication for customer data and administrative endpoints. Account onboarding/recovery and legal pages are public by design; billing webhooks require provider signatures rather than a user session. Health endpoints reveal only bounded status.
- Store inbound API tokens only as password hashes and rotate them.
- Apply least-privilege scopes and Django Admin permissions.
- Keep Jira credentials encrypted with keys injected separately, and redact headers.
- Validate all schemas, enums, lengths and relationships server-side.
- Escape Jira markup and Django HTML output.
- Apply request body, rate and pagination limits.
- Disable Django debug mode in production.
- Use secure cookies, CSRF protection and standard Django security middleware for the admin site.
- Protect against SSRF through HTTPS URL validation, hostname allowlisting, DNS/IP policy and redirect controls.
- Never persist detected secret values. Preserve only the sanitized evidence already allowed by the Graphify schema.
- Record security-relevant administrative and delivery events.
- Run dependency, static-security and secret scans in this application's CI.
- Never log full imported payloads at normal logging levels.

---

## 16. Observability and health

Structured logs include:

- request/import correlation ID;
- repository and run IDs;
- internal finding UUID, never raw secret evidence;
- outbox/delivery IDs;
- Jira connection and issue key when known;
- outcome and duration.

Metrics should include:

- imports accepted, rejected, idempotently replayed and conflicted;
- findings created/updated by lifecycle state;
- outbox depth and oldest pending age;
- Jira deliveries, no-ops, retries and terminal failures;
- request and Jira latency.

Health behavior:

- `/health/live`: process can serve requests; no external calls.
- `/health/ready`: database and migrations are usable.
- optional dependency details report Jira as `not_configured`, `healthy`, `degraded`, or `disabled` without making readiness fail when Jira is optional.

---

## 17. Django Admin requirements

The MVP administration site provides:

- read-only repositories, imports, findings and observations;
- formatted remediation-plan inspection;
- Jira connection and project-binding configuration without displaying secrets;
- connection-test action;
- delivery queue and sanitized attempts;
- manual retry/cancel actions with confirmation;
- audit-event inspection;
- filters for repository, state, severity, category and delivery status.

Immutable records must not expose normal edit/delete actions. Administrative actions require explicit permission and create audit events.

---

## 18. Testing strategy

Use `pytest`, `pytest-django`, model factories and a mocked Jira HTTP transport. Core unit tests may use SQLite; concurrency and database-specific behavior must run against PostgreSQL in CI.

### 18.1 Required functional tests

1. A valid import succeeds with Jira completely unconfigured.
2. Jira-disabled import creates no outbound delivery and reports `not_configured`.
3. Replaying the same run and payload is a no-op.
4. Reusing a run ID with different content returns `409`.
5. The same fingerprint in two repositories creates two distinct findings.
6. A changed candidate/display ID with the same fingerprint updates the existing finding.
7. A missing finding in a fresh run remains open.
8. Explicit `still_present`, `resolved`, `superseded` and `not_reproduced` reconciliation applies the specified lifecycle.
9. A missing or stale required remediation plan is rejected.
10. Rendered remediation Markdown is deterministic.
11. A supported eligible finding creates one Jira issue.
12. Concurrent delivery attempts cannot create two Jira associations.
13. Subsequent observations update only the managed Jira comment.
14. Human description, comments and unrelated labels are preserved.
15. An unchanged outbound payload produces a no-op without an HTTP request.
16. A resolved finding transitions only when an explicit valid mapping exists.
17. Not-reproduced and inconclusive findings never auto-close.
18. Retryable and terminal Jira errors are classified correctly.
19. Jira tokens and authorization headers never appear in logs or attempts.
20. Corporate CA verification is honored and TLS bypass is impossible through configuration.

### 18.2 Required validation and security tests

- malformed fingerprints and identity/fingerprint mismatches;
- duplicate fingerprints;
- unknown remediation references;
- oversized bodies and fields;
- Jira markup injection and unsafe links;
- unauthorized scopes and token rotation;
- SSRF attempts through connection URLs, redirects and DNS/IP resolution;
- imported evidence resembling credentials;
- migration tests from an empty database;
- PostgreSQL uniqueness and worker claim concurrency;
- SQLite developer setup and single-worker behavior.

---

## 19. Deployment requirements

Production topology:

```text
reverse proxy / ingress
        │
        ▼
Gunicorn Django web process ───── PostgreSQL
                                      ▲
delivery worker ──────────────────────┘
        │
        └── Jira, only when configured
```

Requirements:

- production uses PostgreSQL, never SQLite;
- database migrations run as a controlled release step;
- web and worker use the same immutable application release;
- secrets are injected at runtime;
- regular PostgreSQL backups and restore testing;
- graceful application and worker shutdown;
- time kept in UTC internally;
- static files for Django Admin served through an approved mechanism;
- rollback instructions account for forward-only database migrations.

SQLite is for local development and fast unit tests only. It must not be presented as a production option.

---

## 20. Delivery phases

### Phase 1 — Tenant registry and customer interface without Jira

- Django project and environment configuration.
- PostgreSQL/SQLite support and migrations.
- API client authentication.
- Import envelope and Graphify schema validation.
- Repository, review, finding, observation and remediation models.
- Idempotent import and lifecycle reconciliation.
- Read API, OpenAPI and Django Admin.
- Jira explicitly disabled.
- Verified accounts, invitations, roles, workspace selection, customer dashboard and upload/token workflows.
- Fail-closed tenant scope and PostgreSQL row security/reference guards.

### Phase 2 — Optional Jira adapter

- Jira connection and repository project binding.
- Tenant-encrypted credentials and operator corporate CA support.
- Transactional outbox and worker.
- Jira create/update, managed comments and association recovery.
- Optional transition mappings.
- Delivery administration, retry and audit records.

### Phase 3 — Operational hardening

- PostgreSQL concurrency tests.
- Metrics and alerts.
- Rate limiting and stale-lock recovery.
- Backup/restore and lost-association recovery exercise.
- Threat-model review and deployment documentation.
- Subscription entitlements, monthly/yearly provider prices, signed webhook reconciliation, manual enterprise agreements and read-only grace/expiry behavior.
- White-label configuration, DNS domain ownership workflow and dedicated deployment documentation.
- Cross-tenant tests for browser, API, workers, billing and streaming exports.

### Deferred extensions

- Jira Cloud adapter.
- Other work-item adapters.
- OIDC/mTLS inbound authentication.
- Customer-side outbound Jira connector; use dedicated hosting for private Jira in the initial release.
- Enterprise SSO/MFA integration, delegated reseller hierarchy, and automatic custom-domain certificate provisioning.
- Jira webhooks or polling for workflow divergence.
- Celery or an enterprise queue implementation.

---

## 21. Acceptance criteria

The first production release is acceptable only when:

1. The application imports, stores and exposes Graphify findings with Jira absent.
2. Repository-plus-fingerprint uniqueness prevents finding collisions.
3. Full review and observation history is retained.
4. Fresh-run absence cannot resolve a finding.
5. Revalidation dispositions deterministically update lifecycle state.
6. Every supported finding includes a structured, source-revision-bound remediation plan.
7. Import idempotency works under PostgreSQL concurrency.
8. Jira integration can be enabled or disabled without migration or code changes.
9. No Jira custom field or installed Jira plugin is required.
10. Existing associated Jira issues are updated rather than duplicated.
11. Human-authored Jira content is preserved.
12. Jira failure cannot roll back or reject a valid review import.
13. TLS validation and credential redaction cannot be disabled through ordinary configuration.
14. SQLite provides a documented one-process/one-worker development experience.
15. Strengths, rejected candidates and unverified intermediate candidates cannot create Jira work under the default policy.
16. All required unit, API, integration, migration, security and PostgreSQL tests pass.
17. An unrelated user, token or worker cannot read or mutate another customer's data, including through raw SQL using the runtime role.
18. Billing events cannot activate a different organization or restore stale entitlements, and duplicate events are idempotent.
19. Expiry/downgrade never silently deletes customer findings; documented export/erasure paths remain available.
20. Branding changes cannot alter legal notices, authorize cross-workspace access, or activate unverified domains.
21. The operator has completed the launch checklist in `hub/README.md`, including real provider/network tests, restore testing and commercial/legal configuration.

---

## 22. Recommended defaults requiring organizational confirmation

These decisions are safe defaults but must be confirmed before production deployment:

| Decision | Recommended default |
|---|---|
| Repository identity | Organization-assigned SCM repository UUID |
| Reviews allowed to create Jira work | Default branch reviews only |
| Jira creation eligibility | Supported and partially supported `review.findings` |
| Inconclusive findings | Store only; no Jira issue by default |
| Rejected findings | Store for audit; never create Jira work |
| Resolved transition | Disabled until mapped per Jira project |
| Automatic reopen | Disabled |
| Initial Jira issue type | Existing `Task` unless project owners specify another type |
| Jira description updates | Creation only; managed comment thereafter |
| Jira authentication | Dedicated service-account PAT |
| Raw import retention | At least the governance retention period, encrypted backups |
| SQLite | Development and unit tests only |

---

## 23. External technical references

- [Django 5.2 release and Python compatibility](https://docs.djangoproject.com/en/5.2/releases/5.2/)
- [Django database installation guidance](https://docs.djangoproject.com/en/5.2/intro/install/)
- [Jira Data Center REST API — issues, comments, properties and transitions](https://developer.atlassian.com/server/jira/platform/rest/v10005/api-group-issue/)
- [Updating Jira issues through the REST API](https://developer.atlassian.com/server/jira/platform/updating-an-issue-via-the-jira-rest-apis-6848604/)
- [Jira Data Center entity properties and JQL indexing limitation](https://developer.atlassian.com/server/jira/platform/entity-properties/)
- [Atlassian Data Center personal access tokens](https://confluence.atlassian.com/enterprise/using-personal-access-tokens-1026032365.html)

---

## 24. Hosted SaaS and white-label product requirements

### 24.1 Organization and identity model

An organization is a tenant and subscription boundary, including for an individual subscriber. One verified account can hold memberships in several organizations. Membership roles are `owner`, `admin`, `reviewer`, and `viewer`; billing permission is independent except that owners always have billing access. Account identity and tenant authorization must not be conflated. Django platform staff privileges do not imply customer workspace membership.

Tenant-owned records include repositories, imports, findings, observations/remediations, invitations, service accounts, subscriptions, Jira connections/bindings/associations, outbox events, delivery attempts and audit records. The database control tables needed for login/token routing and provider-event routing have narrowly scoped unfiltered lookup paths. Domain data uses fail-closed managers and PostgreSQL FORCE RLS. No privileged/superuser/BYPASSRLS connection may serve production requests. Cross-tenant relationships must be rejected even when both IDs are otherwise valid.

Database connections use session tenant settings with explicit cleanup and no persistent connection reuse by default. Transaction-mode external connection pooling is unsupported until tenant context is set transaction-locally on every transaction. Never infer tenant identity from arbitrary request headers. Custom domains are presentation/routing boundaries and never replace membership authorization.

### 24.2 Customer journeys

1. Register, confirm email, sign in, reset credentials, and create or join a workspace.
2. Invite colleagues using expiring single-use invitations bound to verified email; owners control administrator invitations.
3. Create expiring, revocable, shown-once API tokens or upload an import envelope in the browser.
4. Inspect findings by severity/lifecycle/search, review evidence and coding-agent remediation, and compare immutable observations.
5. Configure optional Jira credentials/bindings and inspect/retry delivery failures without changing technical finding state.
6. Inspect plan/usage, subscribe or use a manual enterprise agreement, manage/cancel through a billing portal, and retain read/export access after expiry.
7. Export source imports and request privileged erasure with an auditable cooling-off workflow.

The customer dashboard is a first-release requirement. Django Admin is restricted to platform operations. The application never runs Graphify, Copilot, scanners, uploaded commands or remediation scripts on the server.

### 24.3 Billing and entitlements

One subscription belongs to one organization. Pricing is operator-controlled: individual and team plans may use monthly or yearly Stripe Price IDs; enterprise agreements use annual manual invoicing/activation, with setup/support fees negotiated separately. Example quotas are implementation defaults, not advertised contractual promises or market-tested prices. Do not charge by finding count.

Entitlements are enforced server-side, including in API imports. The initial limits cover repository count, members including pending invitations, calendar-month imports and payload storage. Store subscription plan/status, current entitlement end, bounded failed-payment grace period and provider identifiers. Serialize quota-consuming writes per organization to prevent concurrent allowance bypass. Default trials expire after 14 days; failed payments may receive seven days of read/write grace. No payment configuration is needed for local development.

Stripe checkout/portal require real operator configuration. Do not accept customer-supplied prices or customer IDs. Verify signatures over raw webhook bytes, deduplicate provider event IDs, and reconcile current provider subscription state rather than applying stale payloads in delivery order. Schedule periodic reconciliation for missed webhooks. Confirming a checkout redirect never grants access. Cancellation/expiry makes the workspace read-only; it does not delete records. Repository/member overages block new additions but preserve existing data. Grace must not extend on every retry.

Retention durations listed in plans are proposed policy parameters, not an automatic deletion schedule in the initial application. Before offering paid retention guarantees, implement or operate an approved archival/deletion procedure that preserves required finding continuity and handles backups. Erasure requires a tenant request, operator identity, a seven-day cooling-off period and exact tenant confirmation; it must never automatically delete external Jira issues or shared user accounts.

### 24.4 Editions, domains and licensing boundaries

Use one build for standard shared SaaS, branded organizations and dedicated/customer-hosted deployments. Enterprise branding supports display name, colors and an HTTPS logo. Custom domains require a random DNS TXT ownership challenge, operator verification, an ingress certificate, explicit allowed-host configuration and host-only session cookies. DNS verification alone is not TLS provisioning. Changes invalidate prior verification. Prevent domain reuse while already assigned to another verified customer.

Private Jira instances use dedicated deployment/network access in the initial release; shared SaaS cannot proxy arbitrary corporate destinations. A customer-side outbound connector is an explicitly deferred alternative. No customer-specific branches, secrets in source, or automatic infrastructure purchases are required.

Keep `hub/` in this repository with its own dependencies, tests, migrations, releases and container build. The `.github` kit installer must not distribute or provision the application. The AGPL-3.0-only license remains unchanged and applies to the new original application material. Preserve De Jonckheere Stéphane (humblejok) attribution and provide a corresponding-source link for each hosted release. White labeling changes product appearance, not legal notices. A proprietary/dual-license edition requires a separate rights and contract review; this implementation neither introduces such a license nor revokes existing grants.

### 24.5 Production gates and explicitly unfinished enterprise extensions

The initial application is a development/pilot implementation, not evidence that a commercial service is production-ready. Require PostgreSQL CI using a non-superuser role, API/browser isolation tests, signed billing-event tests, corporate Jira/TLS/network integration tests, dependency/static/secret scans, restore/rollback exercises, and an operator threat-model review before paid public launch. Bound request sizes/rates at the reverse proxy and configure SMTP, encryption keys, canonical origin and source URL.

Before promising an enterprise feature, validate its actual implementation and operating model. Native enterprise SSO/MFA, reseller hierarchies, an outbound customer-side Jira connector, automatic domain/TLS provisioning, storage archival/retention automation, advanced metrics/alert routing, and full Jira lost-association/operator recovery tooling remain follow-on work. A dedicated enterprise deployment may use an organization-approved identity-aware ingress; this must not be described as native Hub SSO/MFA. No live Stripe/Jira account or public deployment is created by this repository change.
