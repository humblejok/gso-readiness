# Implement accepted Hub requests

`/implement-requests` implements user-approved bug/feature specifications using the selected VS Code model. It shares the findings workflow's Git/PR engine and quality policy, but has its own request claims, evidence and lifecycle. It does not resolve findings or recalculate review grades.

## Setup and usage

1. Update the project's installed `.github` kit, including both new request implementation agents and the prompt. Reload the VS Code window if discovery needs refreshing. The active manager must be allowed to call **Request Implementation Verifier** directly. No nested subagent permission is required; readiness is checked before claiming work.
2. Update the Hub and run `python manage.py migrate` from `hub/` using its environment (includes migration **0010_request_implementation**). Restart the Hub processes. Do not run the migration with the review-kit environment.
3. Grant the saved token **requests:read** and **requests:implement**, or issue a replacement. Analysis permission does not grant implementation authority. Personal tech-lead tokens require a configured workspace ID; tenant/project restrictions and subscription write limits still apply. Reuse saved Hub/Git credentials, proxy and CA configuration; never put secrets in prompts.
4. Assign the request to this checkout's Hub Project, run `/analyse-requests`, review/edit the analysis and **Accept analysis** in the Hub. Only **Specified** requests are eligible. Ensure the Git checkout is clean, on its intended target branch at the remote tip, with authenticated push/PR access.

```text
/implement-requests
/implement-requests requests=11111111-1111-4111-8111-111111111111,22222222-2222-4222-8222-222222222222
/implement-requests requests=11111111-1111-4111-8111-111111111111 remote=origin
```

The optional filter accepts at most 100 UUIDs, as shown on request details. No filter selects all Specified requests assigned to the configured project. Unassigned or non-Specified requests are excluded. Missing/inaccessible IDs are reported without broadening the selection. Backend, frontend and full-stack work follows all five accepted analysis fields, including new/changed/breaking interfaces. Ambiguous or cross-repository requirements need clarification/reacceptance, not silently reduced scope.

## Execution and evidence

Each request receives a four-hour exclusive, token-bound claim of its exact accepted revision. The description, type, project and analysis are frozen and hashed. Human edits/cancellation invalidate running claims immediately; stale workers cannot overwrite them.

The helper creates an isolated worktree and remote `feature/REQ-<full-request-UUID>` branch before edits. Every request in a batch starts from the same original commit; branches are not chained. Existing branches are not reused or overwritten. Mutually dependent requests require user direction. The original checkout is preserved.

The manager implements the accepted requirements and runs inspected native build/tests through `run_validation.py`, including the existing Windows/Maven summary handling. If configured Sonar MCP tools are available, it checks their actual worktree/project mapping and assesses safe local issues (including unused imports/fields/variables). Corrections are bounded to three rounds and must preserve DI/reflection/serialization behavior. Unavailable optional Sonar feedback is reported, not called a passed Quality Gate; mandatory gates still block completion. PR-dependent mandatory CI gates need a separate pending-gate workflow and are not automatically bypassed.

The manager directly invokes **Request Implementation Verifier** three times: readiness, independent provisional verification, and fresh clean-commit verification. The leaf does not implement, invoke another agent, or write to the Hub. It maps every accepted requirement to evidence/checks and returns `satisfied`, `not_satisfied` or `inconclusive`, with an acceptance checklist. All required checks and criteria must pass for `satisfied`.

`verify_request.py` packages source-bound `request-verification` envelopes. These include request UUID/revision/specification digest, actual commit/branch/source snapshot, evidence, checks and acceptance results. They are not finding envelopes or cryptographically signed attestations. Packaging validates structure and source binding; the independent agent supplies the substantive assessment. It cannot prove by itself that the model listed every requirement correctly. Human PR review remains important.

The shared engine commits only explicitly selected verified files, rejects unexpected changes, pushes the exact clean verified commit, and creates/verifies an open PR to the original branch. GitHub uses authenticated `gh`; Azure DevOps uses authenticated Git plus the existing REST integration, without requiring `gh` or Azure CLI. Only confirmed PR delivery and Hub completion mark the request **Implemented**, with a factual summary and PR link in history. It is not Closed, merged or deployed.

## Recovery

Attempt state lives outside the implementation worktree in `.github/graphify-review/output/request-implementations/<attempt>/`; verifier artifacts are under `output/request-verifications/<run>/`. Keep these files. Use the original trusted kit/interpreter and the saved state, not copies modified in the feature worktree.

```text
<review-python> <kit-scripts>/implement_requests.py fail --repository <original-checkout> --state <state.json> --summary-file <reason.txt>
<review-python> <kit-scripts>/implement_requests.py sync --repository <original-checkout> --state <state.json>
```

`fail` releases a still-valid claim, records the reason and leaves the request Specified; branches/worktrees are retained. Expired/cancelled claims cannot be completed; a new claim requires current approval and any branch collision must be reviewed manually. There is no automatic destructive cleanup or resume-overwrite. Failed attempts increment the revision so stale queued state cannot win.

For `completion_pending`, use **sync**, not failure: the same saved completion body is retried idempotently. Never alter it or create a new attempt/PR to work around a network error. If Azure PR creation has an uncertain result, retry `deliver` with the same saved report/summary to find the existing PR; do not clear `pr_submission_started` or blindly create another. A cancelled/edited request wins over an in-flight worker even if a remote PR already exists; inspect that PR manually.

## Hub API and deployment boundaries

`GET /api/v1/requests?repository_external_id=...&status=specified&ids=...` uses the existing 50-item pagination. `GET /api/v1/requests/<UUID>/implementation` returns context including `display_id`, `specification_digest`, accepted analysis and clone URL. Read scope is required.

`POST` to that implementation endpoint requires both request read/implement scopes:

- Claim: `action=claim`, `revision`, client-generated UUID `request_id`, and `repository_external_id`. Returns `attempt_id`, expiry and frozen-context digest (201 new, 200 identical active retry).
- Complete: `action=complete`, `attempt_id`, `repository_external_id`, and `completion` containing exactly `outcome`, `comment`, `report`, `pull_request`, `commit_sha`, `base_branch`. Success needs the clean matching `satisfied` report and matching repository PR; failure uses `report=null`. The body is limited to 256 KiB; comments to 8,000 characters.

Claims/completions are workspace-serialized, quota-accounted and audited. Invalid evidence is 400; insufficient authority/write entitlement 403; inaccessible project/claim 404; stale revision/claim 409; rate limit 429. Completed identical retries return their original result without repeating lifecycle changes. Human acceptance/cancellation remains UI-only. The Hub does not execute builds or independently contact Git hosting to attest a client report; use narrowly scoped trusted worker tokens. PostgreSQL RLS/reference/concurrency tests must run with a non-superuser/non-BYPASSRLS role before production.
