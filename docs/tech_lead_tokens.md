# Tech-lead personal API tokens

## Security boundary

A personal token belongs to a **user**, not a workspace. An active, email-verified user with the `is_tech_lead` attribute can issue scoped, expiring tokens for **every non-suspended workspace on this Hub instance**, including workspaces where they have no membership.

This is an instance-wide operational role, not a tenant administrator role. On a shared SaaS instance, it crosses customer boundaries: grant it only to trusted service operators/architects, **not customer admins**. Existing workspace roles and tokens are unchanged. Personal tokens do not grant Django staff/superuser access or implicit browser memberships.

Tokens cannot change billing, membership, ownership of existing workspaces, branding/domain configuration, suspension, other users' privileges or tokens, or erase workspaces. They do not bypass subscription write restrictions, quotas, evidence validation, optimistic revisions or PostgreSQL row-level security. Each workspace-data request selects exactly one tenant context; a token cannot accidentally query all tenant findings at once.

## Activate and issue

1. Back up the Hub database, deploy the code and apply migration **0008** with the Hub environment: `python hub/manage.py migrate`. Use the existing production database/environment configuration, not development defaults. Restart the Hub.
2. A **superuser operator** opens `/operator/`, selects the user, and enables **Is tech lead** under **Verification and technical access**. This does not require making that user staff or superuser. Ordinary workspace owners/admins cannot grant the attribute.
3. The designated user signs in and opens **Personal tokens** in the account navigation (`/accounts/tokens/`). Select only the needed scopes, an expiry (1–365 days, default 30), and acknowledge instance-wide access.
4. Copy the token once and store it in your approved secret store. The Hub stores a password hash, not the original secret. At most 20 unexpired, unrevoked personal tokens per user are allowed.

Revoke a token on the same page; users can only view/revoke their own tokens. Revocation takes effect on subsequent API requests. Authentication also checks the user's active, verified and tech-lead attributes on every request. Removing any eligibility attribute through the operator user editor permanently revokes their existing personal tokens; granting the attribute again requires fresh tokens. Direct database edits are not a supported role-management workflow.

Token creation/revocation has a `UserTokenEvent` record; operator account edits appear in Django's operator log. Workspace accesses and mutations produce workspace audit events identifying the personal token and/or its owner. Secrets are never included in these audit records.

## Scopes

| Scope | Capability |
| --- | --- |
| `workspaces:read` | List/read all non-suspended workspace IDs, names and management revisions. |
| `workspaces:write` | Create a workspace; update the name of an existing workspace. |
| `reviews:write` | Import reviews (creating/updating repositories/findings), and submit evidence-based targeted revalidation. |
| `findings:read` | Read findings, remediation, observations and existing implementation context. |
| `findings:write` | Queue/cancel implementation and edit the queued remediation draft. |
| `findings:implement` | Claim/complete queued implementations; also requires `findings:read`. |

Scopes are immutable after issuance: replace and revoke a token to change its scopes. A user token does not allow arbitrary rewrites of review evidence, grades or finding lifecycle outcomes. Use the existing review-import/revalidation contracts for those changes.

## Use with the review kit

Update the repository's `.github` kit, then select its target workspace explicitly:

```text
/configure-review hub-workspace-id=00000000-0000-4000-8000-000000000001 hub-credential-ref=default
```

Use the actual workspace UUID, available in the Hub's `/w/<UUID>/` URL or the workspace-list API below. This is **not** a repository ID. The non-secret value is saved as `project.hub_workspace_id`; it may be committed with the project settings. Do not set one universal workspace in a company preset covering unrelated workspaces.

Save the personal token through the existing terminal login, never in chat:

```powershell
.\.graphify-review-venv\Scripts\python.exe .github\graphify-review\scripts\publish_review.py login --repository .
```

On macOS/Linux use `.graphify-review-venv/bin/python`. The same user token may be stored in several project-specific credential slots; each checkout independently selects its workspace. `/publish-review`, `/implement-findings` and targeted finding uploads send `X-Workspace-ID` automatically. Publishing receipts record the selector, and active implementation attempts reject changes to it. Finish an active claim before changing its token, credential reference or workspace.

Existing workspace tokens may omit the selector. If supplied, it **must** match the token's own workspace; a mismatched header fails rather than redirecting publication to a different tenant.

## Management API

All calls require `Authorization: Bearer <UUID.secret>`. Only personal tech-lead tokens may use the workspace-management and finding-management endpoints. Workspaces are identified by UUID, not their display name.

- `GET /api/v1/workspaces` — `workspaces:read`; paginated (`page`, 50 results/page). No workspace header required.
- `POST /api/v1/workspaces` — `workspaces:write`; body `{"name":"Payments"}`. Returns 201 with `id`, `name`, `revision`. Uses normal workspace creation: the token owner becomes the new workspace's owner, with the standard trial and ten-owned-workspace limit. This endpoint is not idempotent: after an uncertain response, inspect the workspace list before submitting again.
- `GET /api/v1/workspaces/<UUID>` — `workspaces:read`; returns `id`, `name`, `revision`.
- `PATCH /api/v1/workspaces/<UUID>` — `workspaces:write`; body `{"name":"Payments API","revision":0}`. Only `name` can change. Use the returned revision on the next update; stale revisions return 409. Other fields are rejected.

For workspace-detail endpoints the URL selects the workspace. If an `X-Workspace-ID` header is also supplied, it must match the URL.

**All existing review/finding API endpoints and finding management require `X-Workspace-ID: <UUID>` with a personal token.** Missing/malformed selectors fail with 400; unavailable/suspended workspaces or insufficient scope fail with 403. A finding UUID from a different selected workspace returns 404.

Example with PowerShell (the token is supplied by your secret manager):

```powershell
$hubBase = 'https://YOUR-HUB'
$headers = @{ Authorization = "Bearer $env:FINDING_HUB_TOKEN" }
Invoke-RestMethod "$hubBase/api/v1/workspaces" -Headers $headers
$headers['X-Workspace-ID'] = 'YOUR-WORKSPACE-UUID'
Invoke-RestMethod "$hubBase/api/v1/findings" -Headers $headers
```

`PATCH /api/v1/findings/<finding-UUID>/management` requires `findings:write` and supports:

```json
{"action":"implement","revision":3}
```

```json
{"action":"edit","revision":4,"remediation":"Add a regression test, then correct the affected configuration."}
```

```json
{"action":"cancel","revision":5}
```

Read the actual finding revision first; the numbers above are examples. Only open findings can be queued, and remediation editing requires the implementation flag. Edits/cancellation invalidate in-flight claims using older instructions. To resolve a finding, submit a validated targeted report to the existing `/revalidation` endpoint with `reviews:write`, or complete the existing implementation workflow. Never substitute a manual lifecycle change for evidence.

SQLite tests cover the API/UI permission checks; before production rollout run the Hub suite against PostgreSQL with a non-superuser, non-BYPASSRLS runtime role as described in the Hub README.
