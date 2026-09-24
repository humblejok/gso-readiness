# Request analysis and human acceptance

`/analyse-requests` uses the currently selected VS Code model, without subagents, to turn Open Hub bug/feature requests into implementation specifications. It does not implement code, claim findings, create branches/PRs, or accept its own work.

## Setup

For split applications, see [cross-project handoffs](cross_project_handoffs.md). With Hub migration **0011**, analysis reads configured consumer relationships and any approved upstream handoff. It scopes acceptance to the current repository and still requires human review. A source request's confirmed human closure creates the downstream Open request; analysis never publishes or accepts it.

1. Deploy the updated Hub and run migration `0009_request_analysis` using the deployment's usual configuration, then restart. Back up first. Existing requests/history are preserved.
2. Update the reviewed project's `.github` kit and start a fresh VS Code chat with your desired model. Keep the configured Hub URL, workspace, stable repository ID and corporate proxy/CA settings.
3. Create/edit the request in the Hub and select its **Project**. Projects are registered repositories in that workspace. Unassigned requests remain valid drafts but are excluded from analysis to avoid using the wrong checkout. The checkout's stable repository ID must match this project.
4. Grant the Hub token **requests:read** and **requests:analyse**, or issue a new token and save it through the existing `publish_review.py login` terminal helper. Never paste tokens into chat. Repository restrictions remain enforced. Personal tech-lead tokens require the saved Hub workspace selection. Existing tokens retain their old scopes until you change them.

## Command

```text
/analyse-requests
/analyse-requests requests=11111111-1111-4111-8111-111111111111,22222222-2222-4222-8222-222222222222
```

Replace UUIDs with Request IDs from the Hub details pages. Without a filter, all returned Open requests assigned to the current project are processed sequentially. At most 100 explicit UUIDs are supported. Missing/non-Open/unavailable IDs are reported; the filter is never widened. Answer questions in chat. Material unanswered questions leave the request Open, with notes local only.

The model inspects manifests/source/tests to classify the checkout as backend, frontend or fullstack rather than assuming from a framework name. The specification covers current behavior/source references, scope/non-goals, confirmed answers and assumptions, design, ordered implementation steps, affected files, data changes, security, errors, acceptance criteria, tests, rollout/rollback and risks. Work belonging to other repositories is explicitly identified.

Every analysis inventories **new interfaces**, **changed existing interfaces** and **breaking changes**. Backend proposals list routes/methods, auth, schemas, responses/errors and callers, or event/queue/RPC/file contracts and delivery semantics. Breaking changes identify consumers, migration/versioning strategy and contract tests. Frontend work identifies consumed APIs and any required external backend changes. If none apply, the analysis must explain why; empty sections are rejected.

## Review and cancellation

- Creation → **Open**.
- Completed analysis saved by the command → **Analyzed**.
- A workspace owner/admin/reviewer reviews/edits it, then clicks **Accept analysis** → **Specified**.
- **Specified → Implemented** can be performed by [`/implement-requests`](request_implementation_workflow.md) using the accepted specification, independent verification and a PR, or tracked manually in the Hub. Analysis does not execute implementation. **Implemented → Closed** remains a human tracking action; Implemented does not mean merged or deployed.
- The creator can cancel Open/Analyzed/Specified requests → **Cancelled**, but not Implemented/Closed.
- Editing the request/project resets it to Open for fresh analysis. Editing a Specified analysis returns it to Analyzed for renewed acceptance. Previous versions remain in history. Closed/Cancelled are read-only.

API tokens cannot accept or cancel requests. Revision checks and workspace-serialized transactions prevent delayed analysis from overwriting a cancellation, edit, reassignment or competing analysis. UI content is escaped plain text, including Markdown, not executable HTML. Legacy Analyzed records without analysis must be completed manually before acceptance or returned to Open by editing the request.

## Helper, artifacts and retries

The command runs `hub_requests.py queue`, `prepare --request <UUID>` and `submit --state <state.json> --analysis <analysis.json>`, always with `--repository <checkout>` and the dedicated review Python. A new run under `.github/graphify-review/output/request-analyses/` saves the request revision, source snapshot and target binding. The model writes its analysis there, outside application source. No claim is acquired.

Submission refuses changed source or target settings and saves an immutable `submission.json` before posting. Retain these private local artifacts. On a network uncertainty, retry the same state and unchanged analysis. On `409`, inspect the Hub; never replace only the revision or submission ID to force acceptance. If source/requirements changed, fetch fresh context and redo analysis, first inspecting any uncertain prior submission. A local file alone does not mean the Hub was updated.

## API contract

- `GET /api/v1/requests?repository_external_id=...&status=open&ids=...&page=1`: `requests:read`, pages of 50, project-restricted.
- `GET /api/v1/requests/{UUID}/analysis`: same scope; current request context.
- `POST /api/v1/requests/{UUID}/analysis`: both request scopes; exactly `revision`, `repository_external_id`, `submission_id` (UUID), `analysis`.

The analysis object has exactly `project_kind` (`backend`, `frontend`, `fullstack`), `specification` (Markdown, 1–64,000 characters), `new_interfaces`, `changed_interfaces`, `breaking_changes` (Markdown, 1–16,000 characters each). Structural validation does not certify architectural completeness; the agent and human reviewer assess that.

Successful submission/replay returns `200` with the Analyzed request. Identical retries add no duplicate history while the result remains current. Later human edits invalidate the old submission. Conflicts return `409`, malformed input `400`, missing scopes `403`, inaccessible requests `404`. No submitted lifecycle status is accepted. Workspace tokens select their tenant; personal tokens use the existing `X-Workspace-ID`. Writes enforce subscription, storage quotas, suspension and rate limits. Exports/history include analysis and project snapshots.

Automated tests cover contracts, helper, workflow, authorization, history and stale/idempotent writes. Live selected-model behavior and question UX need VS Code acceptance testing; PostgreSQL isolation/concurrency needs the supported production database role.
