# Finding Hub

Self-hosted Django application for importing Graphify reviews, tracking findings and remediation history, and optionally synchronizing Jira. This is a working pilot application, not a claim of commercial production readiness. See the launch checklist below and the [implementation specification](../docs/graphify_finding_hub_spec.md).

The application is separate from the `.github` review kit and its installers. Python 3.12/3.13, Django 5.2 LTS and PostgreSQL are the supported deployment stack. SQLite is development-only. The application does not execute Graphify, GitHub Copilot, scanners or remediation commands.

## Local quick start

From the repository root, on macOS/Linux:

```bash
python3.13 -m venv .finding-hub-venv
.finding-hub-venv/bin/python -m pip install --upgrade pip
.finding-hub-venv/bin/python -m pip install -r hub/requirements-dev.txt
.finding-hub-venv/bin/python hub/manage.py migrate
.finding-hub-venv/bin/python hub/manage.py runserver 127.0.0.1:8000
```

Windows PowerShell:

```powershell
py -3.13 -m venv .finding-hub-venv
.\.finding-hub-venv\Scripts\python.exe -m pip install --upgrade pip
.\.finding-hub-venv\Scripts\python.exe -m pip install -r hub\requirements-dev.txt
.\.finding-hub-venv\Scripts\python.exe hub\manage.py migrate
.\.finding-hub-venv\Scripts\python.exe hub\manage.py runserver 127.0.0.1:8000
```

Open `http://127.0.0.1:8000/accounts/signup/`. Register an email and a strong password. Development emails print in the server console: follow the verification link, confirm it, then sign in using your email as the username. Create a workspace. No default account/password is installed. Workspace creators receive a 14-day trial; no card or provider is required.

The dedicated `.finding-hub-venv` does not touch `.venv` or `.graphify-review-venv`. Settings read environment variables; `.env` is **not loaded automatically** by Django. Use shell exports, your process manager, or Docker Compose's `env_file`. Do not commit secrets or the development database.

## Tech-lead personal tokens

The Hub supports **user-level tokens** for operator-designated tech leads, separate from workspace API tokens. These tokens can create/name-update workspaces and import, read or manage findings across the Hub according to selected scopes. This is an **instance-wide** privilege, not a customer/workspace-admin role; billing, memberships, deletion and evidence checks remain protected. Apply migration **0008**, then have a superuser operator enable **Is tech lead** on the user. The user can issue/revoke tokens through **Personal tokens** in the account navigation.

Personal tokens require an explicit workspace UUID for review/finding operations. Configure the kit with `/configure-review hub-workspace-id=<UUID>` and use the existing terminal login to store the token. Existing workspace tokens continue working without that setting. See [permissions, setup, revocation and API examples](../docs/tech_lead_tokens.md).

## Workspace bug and feature requests

Open a workspace and select **Bug & feature requests → New request**. Choose **Bug fix** or **Feature request**, enter a description (up to 20,000 characters), and save. No imported review or repository is required.

Requests start **Open** and advance one stage at a time: **Open → Analyzed → Specified → Implemented → Closed**. The detail page offers a **Mark …** button for the next stage and an **Edit request** section for changing the type or description. Closed requests are read-only; backward transitions and reopening are not currently supported. These are manual tracking statuses: they do not execute a coding agent, create a branch/PR, or synchronize with Jira.

Owners, admins and reviewers can create and update requests; viewers can browse them. Search and type/status filters are available on the list. Each creation, edit and transition records its author, time, revision and description snapshot. Stale browser submissions are rejected so they cannot overwrite newer edits. Read-only subscriptions preserve browsing and export access.

For existing installations, back up the database and run `.finding-hub-venv/bin/python hub/manage.py migrate` (Windows: `.\.finding-hub-venv\Scripts\python.exe hub\manage.py migrate`). Migration **0007** creates the request/history tables and PostgreSQL tenant isolation/reference guards. Restart the application; production deployments must also rebuild static assets or run `collectstatic --noinput` using their existing configuration. Do not migrate a production database with the development settings.

Request data and revision history count toward workspace storage. Workspace exports include additive `change_requests` and `change_request_activities` arrays (archival data, not a supported restore/import format). Workspace erasure includes both tables. No new API token permissions are needed: this feature uses the authenticated Hub web interface only. Run the PostgreSQL isolation/concurrency tests with a non-superuser, non-BYPASSRLS runtime role before production deployment.

## Upgrade for finding implementation

After deploying the updated code, back up your database, then run migrations using your Hub environment (not the review kit environment):

```bash
.finding-hub-venv/bin/python hub/manage.py migrate
```

On Windows use `.\.finding-hub-venv\Scripts\python.exe hub\manage.py migrate`. Migration `0006` adds the queue flag, editable remediation and work-history table, with PostgreSQL tenant policies/reference guards. Restart the web/worker processes; production deployments must also rebuild/redeploy static assets or run `collectstatic --noinput` using their existing configuration.

Owners/admins/reviewers can queue or cancel open findings. Queued findings expose a remediation editor on their detail page; edits preserve the imported plan and invalidate an active worker. Viewers cannot change these controls. The list hides rejected/inconclusive findings by default and offers a show-all checkbox and queue-only filter.

For Copilot implementation, issue a token with **both** `findings:read` and `findings:implement`, preferably restricted to the project's stable repository ID. Existing tokens do not gain the new scope: issue a replacement, save it through the kit's terminal login, and revoke the old token according to your rotation procedure. Standalone targeted result uploads additionally need `reviews:write`. The Hub never receives GitHub/Azure credentials and never runs repository code; Git, hosting API calls and verification run on the user's machine. GitHub uses `gh`; Azure DevOps Server/Services uses REST with Windows identity or a locally stored PAT, not `gh` or Azure CLI.

Deploy/restart the Hub with the updated `git_host_contract.py` when enabling Azure implementations: completion validation now accepts Azure `.../_git/repo/pullrequest/123` as well as GitHub `.../repo/pull/123`, and checks the configured repository identity. This adapter adds no new migration. It does not loosen tenant/token/revision checks or contact a Git host from the server. See [Azure setup](../README.md#azure-devops-server-and-services).

Success records **resolved on the feature branch, PR not yet merged**, removes the flag and appends a factual comment. Failure keeps it open/queued. Single-item results leave imported whole-project scores and other findings untouched. Targeted updates do not enqueue Jira delivery. Workspace exports include additive `finding_work` and `finding_activities` arrays so edited drafts and targeted history are retained; these arrays are archival data, not full-review import envelopes or a supported restore API.

See [commands, safety constraints, retries and API](../docs/finding_implementation_workflow.md). PostgreSQL isolation/concurrency tests must pass with a non-superuser runtime role before production rollout; SQLite tests alone are insufficient.

## Upload a review

The kit's login now supports project-scoped tokens: select `/configure-review hub-credential-ref=default`, then run `publish_review.py login --repository .` in that project's terminal. The secret is stored by Hub URL, stable project ID and reference, so another project using the same Hub cannot overwrite it through its own login. The Hub's API permissions and token lifecycle are unchanged; this credential-selection enhancement requires no server migration. See [multi-project connection configuration](../docs/project_connections.md).

In the reviewed project's Copilot chat, first export a previous full-project run:

```text
/export-review review=.github/graphify-review/output/runs/<run-id>/review.json repository-id=repo:orders-api default-branch=main
```

This creates a validated `import-envelope.json` under the review kit's `output/exports/<export-id>/`, including proposed source-bound remediation plans. It does not rerun the review or upload anything. The command can also reuse an existing plans array via `remediations=path/to/remediations.json`. See the root [export instructions](../README.md#export-a-previous-review-to-finding-hub) for manual Python usage and missing historical source metadata. Reuse the exact same envelope for import retries; changing the payload for an already imported repository/run causes a conflict.

With a Hub configured in the kit, new full reviews/revalidations/rescores prepare an envelope in their own `<run-dir>/hub/` after validation. To recover a missing/interrupted envelope, use `/export-review review=<new-run>/review.json resume=true`. Never upload a baseline envelope expecting it to describe a revalidation: the new run needs its own envelope with current source, findings and explicit lifecycle reconciliation. Import the baseline history before a revalidation referencing it. An all-resolved run still needs to be imported, even with zero remediation plans.

The input is the JSON import envelope defined in the specification, not `REVIEW.md` and not a bare `review.json`. It contains:

- `schema_version: "1.0"`;
- `repository`: stable `external_id`, `name`, `default_branch`, optional credential-free HTTPS `clone_url`;
- `source`: `commit_sha` and `branch`;
- `review`: complete authoritative schema-2.1 review;
- `remediations`: source-bound plans for every supported finding.

Use **Import review** in the dashboard, or configure the reviewed project's Hub URL and use `/publish-review review=<previous-run>` or `/publish-review envelope=<import-envelope.json>`. The kit's terminal login stores a `reviews:write` token in the native OS credential store. See [one-time connection and publishing](../README.md#publish-results-to-the-configured-hub). No manual project creation is required: the import transaction creates a missing repository by its stable external ID **inside the workspace token's own workspace, or the explicitly selected workspace for a personal tech-lead token**, subject to subscription/quotas and token restrictions. Subsequent runs reuse that project; identical retries cannot create duplicates.

For custom integrations, create a shown-once workspace API token with `reviews:write`, then submit:

```bash
curl --fail-with-body \
  -H "Authorization: Bearer $FINDING_HUB_TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: repo:orders:example-run' \
  --data-binary @import-envelope.json \
  https://hub.example.invalid/api/v1/review-imports
```

Use the actual repository external ID and `review.run.run_id` in the header. Tokens can be repository-restricted and expire after 1–365 days. Rotation means issuing a replacement and revoking the old token. Do not embed tokens in committed scripts or pass them through verbose HTTP logging.

`201` means new import; identical replay returns `200`; the same run with different content returns `409`. Payloads are limited to 10 MiB, 1,000 findings, 500 remediation steps and 64 KiB per plan. The Hub preserves imported scores/verification rather than recomputing them. Schema validation and selected semantic checks do not prove that a review was correctly produced; run the review kit's validation before upload. Credential-pattern rejection is an additional guard, **not a guarantee that arbitrary prose contains no secrets**.

The read/import API contract is documented in [OpenAPI 3.1](contracts/openapi.json). New import results include `organization_id` (for a workspace link) and `repository_created`; the latter describes the original import transaction. A `200` replay returns the stored original result, not a new project creation. Older stored imports can lack these optional fields. Jira retries, connection tests and other administrative mutations deliberately use the role-protected browser interface rather than granting administrative rights to ingestion tokens.

The portable offline validator is maintained in `.github/graphify-review/scripts/import_contract.py` and vendored identically in `hubapp/import_contract.py`. Keep both copies and the review/finding schemas synchronized when changing the contract; regression tests check parity. The Hub adapter keeps Django validation errors at its application boundary. No repository-root files are needed in a deployed Hub container.

Missing findings never resolve themselves. Only explicit revalidation reconciliation changes lifecycle. A fresh review cannot undo a prior explicit resolution. Browser rendering escapes imported prose; remediation commands are never executed.

## Accounts and tenant isolation

Users may join multiple organizations. Owners manage billing/branding and invite administrators. Administrators manage integrations, tokens and non-owner membership. Reviewers can import; viewers can read. Billing permission can be granted independently by the owner. Platform operators do not automatically become tenant members.

Authentication/billing routing uses narrow global control-table lookups. Domain query managers return no records without tenant context. PostgreSQL migration `0002` adds FORCE row-level security and cross-tenant foreign-reference guards. Runtime roles must be **NOSUPERUSER NOBYPASSRLS**, never the container's initial superuser. The readiness endpoint rejects unsafe production database roles. Database-level protection does not defend against arbitrary code execution or a privileged database operator.

Do not put transaction-mode PgBouncer in front of the current session-scoped tenant settings. Connections are not persisted between requests by default; use a compatible session pool only after testing isolation. Core SQLite tests cannot substitute for PostgreSQL isolation/concurrency tests.

## Optional Jira

Without configuration, no outbound delivery occurs. Configure the operator environment before allowing customers to enter PATs:

```bash
export OUTBOUND_INTEGRATIONS_ENABLED=true
export JIRA_ALLOWED_HOSTS=jira.example.invalid
# Generate a key once; store it in your secret manager, separately from the database.
.finding-hub-venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
export HUB_ENCRYPTION_KEYS='REPLACE_WITH_GENERATED_KEY'
# Optional approved corporate CA bundle; TLS validation is never disabled.
export JIRA_CA_BUNDLE=/run/secrets/corporate-ca.pem
```

Add a connection in the customer UI, enable it, test it, and bind a repository to an existing Jira project/issue type. No Jira custom fields or plugins are required. Only default-branch imports queue delivery. Start a separate worker:

```bash
.finding-hub-venv/bin/python hub/manage.py process_delivery_outbox
# Or one bounded sweep, useful for tests/scheduled jobs:
.finding-hub-venv/bin/python hub/manage.py process_delivery_outbox --once
```

The worker preserves human issue descriptions/comments and updates a managed comment. It never auto-reopens and only resolves through an explicitly configured available transition. Timed-out/ambiguous creation attempts trigger full-identity recovery; when recovery is inconclusive, an operator must inspect the remote issue instead of blindly retrying creation. Do not reset event attempt counts. Run one worker for SQLite. Work is serialized per tenant and scheduled one event per tenant per sweep.

For private Jira use **dedicated hosting** with `HUB_DEPLOYMENT_MODE=dedicated`, explicit `JIRA_ALLOWED_HOSTS` and `JIRA_PRIVATE_HOSTS`. Loopback, metadata/link-local and unspecified destinations remain blocked. The shared service must not provide arbitrary access to internal networks. The adapter pins validated DNS destinations and does not follow redirects. Its direct HTTPS transport does not inherit shell HTTP proxy settings; route it through approved network-level egress or dedicated network access. An application-level outbound corporate connector/proxy adapter is not included.

Rotate encryption keys by setting `HUB_ENCRYPTION_KEYS=new-key,old-key`, running `rotate_jira_secrets --operator YOUR_HANDLE`, then retiring the old live key after verification. Retain backup keys according to the restore policy. Keep access tokens out of command arguments, logs, backups without encryption and screenshots.

## Subscription configuration

`HUB_BILLING_PROVIDER=manual` is the default. It does not collect money. To activate an already agreed/invoiced enterprise subscription:

```bash
.finding-hub-venv/bin/python hub/manage.py set_subscription WORKSPACE_UUID \
  --plan enterprise --days 365 --operator YOUR_HANDLE
```

For online subscriptions configure Stripe test mode first:

```text
HUB_BILLING_PROVIDER=stripe
STRIPE_SECRET_KEY=<operator secret>
STRIPE_WEBHOOK_SECRET=<endpoint signing secret>
STRIPE_PRICES={"individual:month":"price_...","individual:year":"price_...","team:month":"price_...","team:year":"price_..."}
```

Create the corresponding recurring Prices in your Stripe account. Configure a customer billing portal and `https://YOUR_HOST/billing/webhook/` for `customer.subscription.*` events. Checkout displays the configured amount before confirmation; this project does not choose a currency, charge live accounts or invent prices. Start with monthly/yearly self-service plans and a separate annual enterprise agreement/setup/support price. Tax, refunds, merchant registration, invoices and customer terms require your decisions.

Webhook signatures and event IDs are verified; current provider state is reconciled under the workspace lock. The checkout return URL grants no access. Schedule `python manage.py reconcile_billing` hourly. Failed payments get at most seven days' grace; expiry/cancellation preserves reads and exports. Quotas are server-enforced on new imports and invitations. Existing over-limit data is retained. Retention-day values in settings are policy targets, **not automatic archival/deletion promises**.

Stripe API calls and Jira have mocked adapter tests. A real sandbox checkout/cancellation/failure/recovery exercise and corporate Jira smoke test are release gates, not already-completed setup.

## White label and custom domains

Activate the enterprise plan, then use **Branding** for the display name, accent color and optional HTTPS logo URL. Legal attribution/source access remain. One application build serves all customers; do not fork per customer.

For a custom domain, save its hostname and publish the displayed random TXT value at `_finding-hub.CUSTOM_HOST`. An operator runs:

```bash
python manage.py verify_domain WORKSPACE_UUID --operator YOUR_HANDLE
```

Then provision the TLS certificate and ingress route and add the exact hostname to `DJANGO_ALLOWED_HOSTS`. Domain verification alone does not publish a domain or provision TLS. Membership is still required; another workspace cannot be opened through that branded host. API/operator access uses the canonical origin. Sessions are host-only; users may need to sign in separately on the branded origin. Signup/verification/billing email links use the canonical service origin. Per-tenant sender domains are not implemented.

## Deployment

`Dockerfile` builds the same web/worker artifact. `compose.yaml` is a **local PostgreSQL topology**, not a hardened public deployment: it initially uses the database image's superuser, omits ingress/TLS and does not run migrations automatically.

```bash
cd hub
cp .env.example .env
# Edit .env: replace passwords and configuration; do not commit it.
docker compose up -d db
docker compose run --rm web python manage.py migrate
docker compose up --build web worker
```

For production:

1. Provision PostgreSQL, encrypted backups and a dedicated non-superuser/no-BYPASSRLS runtime role. Run migrations using a separate controlled migration role. Grant runtime table/sequence access; do not grant schema DDL or role-management rights. Set `DATABASE_URL` to the runtime role for both web and worker.
2. Set `HUB_ENV=production`, random `DJANGO_SECRET_KEY` (at least 50 characters), exact allowed hosts, HTTPS `HUB_PUBLIC_URL`, real SMTP settings and a `HUB_SOURCE_URL` linking to the complete corresponding source of the deployed release.
3. Terminate TLS at a trusted ingress. Bind Gunicorn privately. Strip incoming forwarding headers; only then set `HUB_TRUST_PROXY=true`. Configure body limits, request/header timeouts and rate limits, especially login/reset/signup, upload and billing routes. Do not log cookies, authorization, payloads, or verification/invitation/reset URL tokens. Exclude health endpoints from public diagnostics beyond status.
4. Serve static files using WhiteNoise or your ingress. Run web and a separate worker using the same image. Set worker termination grace to at least 90 seconds, and schedule billing reconciliation when enabled.
5. Limit outbound network destinations. Shared SaaS Jira requires approved public hosts. Use dedicated/customer infrastructure for private hosts; do not disable TLS checks to work around corporate certificates.
6. Protect `/operator/` with network access controls and an identity-aware/MFA gateway. Native Hub SSO/MFA is not implemented. Do not describe gateway protection as native tenant SSO.

Supply concrete TLS/SMTP/provider/source configuration before claiming production readiness. Startup refuses obvious unsafe core settings; readiness also checks migrations and the database role. The template does not obtain certificates, publish to a cloud provider or buy services.

### Backups, export and erasure

Export from the dashboard remains available to owners/admins after subscription expiry and contains the source import envelopes. The current export is not a complete administrative backup: memberships, credentials, billing and delivery state need a database backup. Snapshot PostgreSQL before releases; back up encryption keys through a separate secured process. Restore into an isolated instance, disable outbound integrations/billing jobs, verify tenant counts and associations, then conduct a controlled cutover. Never restore over a running production database as a test.

Normal customer APIs cannot delete immutable review history. A workspace owner may request erasure. `erase_workspace WORKSPACE_UUID --operator HANDLE` is a dry run. After seven days and after provider cancellation, an operator may use `--execute --confirm WORKSPACE_UUID`. It deletes that workspace's live data, not external Jira issues or shared accounts. Capture its minimal erasure receipt in your external audit system and expire backups according to the contractual policy. The operator must also coordinate legal holds and outstanding export requests. Nothing runs automatically.

### Release checklist / limitations

- Run both SQLite and PostgreSQL suites; the RLS test deliberately refuses superuser/BYPASSRLS test connections.
- Run dependency and static-security scans, plus your secret scanner; inspect Docker/base-image vulnerabilities separately.
- Exercise Stripe sandbox signup, renewals, cancellation, failed payments and missed/duplicated events. Test actual Jira credentials, permissions, TLS and ambiguous-creation recovery against a test project.
- Test backup restoration, worker restart, database migration/rollback and tenant erasure on disposable data. Treat schema migrations as forward releases; use an approved restore plan for incompatible rollback.
- Review quotas, fairness, abuse controls, monitoring/alerts, support/SLA, privacy/data-processing terms, retention and incident handling before taking payments.
- Native SSO/MFA, delegated reseller hierarchy, automatic custom-domain certificates, customer-side outbound connectors, per-tenant email domains, automatic retention/archive jobs, and advanced metrics/alert routing are not included in this pilot.
- Jira identity markers, superseded-event suppression, bounded `Retry-After` handling and jitter are implemented. Optional issue properties, broad lost-association recovery tools and per-host shared rate control require further hardening before high-volume service.

## Tests and independent CI

```bash
cd hub
../.finding-hub-venv/bin/python -m pytest -q
../.finding-hub-venv/bin/python manage.py check
../.finding-hub-venv/bin/python -m pip_audit -r requirements.txt
../.finding-hub-venv/bin/python -m bandit -r hubapp config -q
```

Set `DATABASE_URL` to a disposable PostgreSQL database using a NOSUPERUSER/NOBYPASSRLS role with CREATEDB for tests. Do not use production data. `tests/test_postgres.py` covers concurrency, raw-query RLS and cross-workspace reference rejection and is skipped on SQLite. An independent GitHub Actions workflow template is in `ci/github-actions.yml`; copy it to this project's workflows when enabling Hub CI. It lives outside the kit's installable `.github` bundle to avoid distributing Hub-only build jobs into reviewed projects.

## License

AGPL-3.0-only, as in the repository [LICENSE](../LICENSE). Original creator: **De Jonckheere Stéphane (humblejok)**. The application vendors original review-kit schemas and retains that attribution. Graphify/Copilot/JFrog are not installed or bundled by the Hub. Other dependencies retain their licenses. A proprietary commercial/dual-license agreement has not been created; review rights and contributor agreements before promising one. Provide the deployed release's corresponding source via `HUB_SOURCE_URL` and retain the legal page in white-label deployments.
