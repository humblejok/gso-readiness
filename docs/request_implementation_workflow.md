# Implement accepted Hub requests

## Recommended: resume retained work (protocol v2)

Deploy the updated **Hub first**, then update the project's entire kit and open a fresh
VS Code chat. Protocol v2 needs no new database migration or token scope. Back up local
Git-ignored helper customizations and preserve the complete `output/` tree. Do not
delete state, worktrees, pending completions or evidence. New work is blocked before
claiming if the Hub lacks the required capabilities.

Use the same token actor and original checkout:

```text
/implement-requests resume=.github/graphify-review/output/request-implementations/<attempt>/state.json
```

For the blocked adoption in the October recovery report:

```text
/implement-requests resume=.github/graphify-review/output/request-implementations/d392275c-9649-40c1-b5bf-24180e669ce0/adoption/state.json
```

The path identifies a **local state file**, not a Hub claim/request UUID. Resume does
not rename the branch, rewrite evidence or create a replacement PR. It inspects the
current Hub state, approved specification, actual clean worktree, pinned commit,
configured destinations and existing provider PR, then either:

- Confirms/syncs an identical valid completion without another PR.
- Creates a same-actor successor under `<attempt>/resume/`, retaining the predecessor
  and its rejected completion. The actor's running predecessor is cancelled (or kept
  expired). The successor pins source/target branches, commit and existing PR and
  requires a new server-issued verification run. Lost responses reuse this identity.
- Stops for human review if scope, credentials, destinations or source changed; another
  worker owns the claim; a human cancelled/completed the request; uncommitted work has
  expired; or the existing PR is incompatible/ambiguous. No reset or overwrite occurs.

Use the **returned successor state path** thereafter. The manager directly invokes
Request Implementation Verifier for readiness and fresh committed verification, uses
native tests and configured Sonar checks, builds the source-bound envelope, then
delivers. The retained PR must remain open/active with the pinned source commit and
target (and no Azure auto-completion). A merged PR needs human reconciliation; resume
does not reopen it. Preserved handoff proposals are rechecked against current consumers.

Terminal equivalents (PowerShell; run each separately):

```powershell
& .\.graphify-review-venv\Scripts\python.exe .\.github\graphify-review\scripts\implement_requests.py resume --repository . --state "PATH_TO_RETAINED_STATE"
& .\.graphify-review-venv\Scripts\python.exe .\.github\graphify-review\scripts\implement_requests.py prepare-verification --repository . --state "RETURNED_SUCCESSOR_STATE"
```

The second command prepares inputs; **it does not perform verification**. Prefer the
slash command to orchestrate the real independent verifier, envelope build and delivery.
Never fabricate verifier output, relabel a source branch, edit pending evidence or
extend timestamps in Django. If the successor expires, resume its explicit state path.

The Hub details page shows claim IDs, server-clock expiry, branch agreements, retained
PRs, successor links and individual failed completion checks. `preflight` validates
evidence before a push/PR; completion repeats checks afterward to handle races. HTTP
400 is not a transient error: use the reported failed check, not blind sync retries.
Uncertain GitHub/Azure PR creation is checkpointed and looked up before any retry.

## Corrective adoption after a confirmed failure

For an already committed correction extending a failed attempt:

```text
/implement-requests adopt=<failed-state-path> worktree=<path> branch=<correction/branch> commit=<full-SHA>
```

The predecessor must be the same actor's confirmed failed attempt, with its recorded
commit and no PR. Scope must be unchanged except the failure's revision increment.
The separate worktree must be clean in the same repository and extend that commit
linearly without merges. Original branch/base/destination checks still apply. The
manager registers the explicit agreement, obtains fresh independent committed
verification, then creates the correction branch/PR. A `correction/` prefix alone
does not authorize completion. Normal requests retain `feature/REQ-<UUID>` branches.

Use **resume, not adopt**, if a PR or pending completion already exists. Legacy local
September adoption states are upgraded through a successor, never by changing their
saved payload or loosening all Hub branch checks. If you already completed the request
manually in the UI, preserve its files and stop; recovery cannot override that decision.

## Legacy narrower recovery: expired claim without PR

Update **both the Hub application and the project's kit**, restart the Hub application, and open a fresh VS Code chat. No database migration or additional token scope is needed. Use the same Hub token actor that acquired the expired claim.

```text
/implement-requests retry=<local-attempt-directory-ID>
```

The ID is the directory under `.github/graphify-review/output/request-implementations/`, not the request UUID or the `attempt_id` field inside `state.json`. An explicit path to that saved state also works. Do not edit state, extend timestamps in Django, delete branches/worktrees, push manually, or repeat ordinary plan/start to bypass a branch collision.

Recovery requires a request still **Specified**, the same approved revision/specification, a Hub-confirmed expired claim, and a clean retained worktree at the recorded single non-merge implementation commit directly on the original base. The original checkout and remote target must still be at that base. The remote feature branch can be at the base (implementation not pushed) or at the recorded commit. Any existing PR, uncertain PR submission, pending completion, changed credentials/actor/scope, divergent branches or dirty source stops recovery. Installing a newer kit must not silently change these captured-base requirements; recovery never resets the original checkout to undo an update.

The manager performs readiness, then uses the trusted review Python environment for:

1. `implement_requests.py retry --repository <original-checkout> --state <expired-state>`: verifies ownership/expiry and writes a successor under `<expired-attempt>/retry/`, preserving old artifacts.
2. `implement_requests.py start --repository <original-checkout> --state <successor-state>`: atomically acquires a new four-hour claim and resumes at `committed` with the existing SHA. Repeat this same state/client request ID after an uncertain response.
3. `implement_requests.py prepare-verification --repository <original-checkout> --state <successor-state>`: prepares fresh committed verification. The manager directly invokes Request Implementation Verifier and packages its actual output with `verify_request.py build`. Historical reports, missing output and failed/unavailable required checks cannot authorize delivery.
4. `implement_requests.py deliver --repository <original-checkout> --state <successor-state> --report <fresh-envelope> --summary-file <new-summary> [--handoff-file <new-proposal>]`: pushes the retained commit, creates/verifies the PR, then marks the request Implemented. It does not merge or Close it.

No new commit or provisional verification is needed for this unchanged retained commit. Rerun required checks, use read-only Sonar assessment with honest limitations, and review handoff drafts against current related projects. If corrections are needed, stop for a separately agreed code-change workflow. Failure of the new active claim uses normal failure handling; pending completion must be synced unchanged. If the successor itself expires before delivery, retry its explicit state path.

Read-only claim inspection is available at `GET /api/v1/requests/{request-UUID}/implementation?attempt_id={Hub-claim-UUID}`. The returned `claim` has status, `expires_at`, server-computed `expired`, revision, specification digest and `owned_by_caller`. It never renews the claim. POST action `recover` accepts `revision`, a new client `request_id`, `repository_external_id` and `predecessor_attempt_id`. It checks expiry, actor and approved scope atomically under the workspace lock; a competing active worker blocks it. The predecessor remains in history, and the successor stores `recovery_of`. Existing implementation scopes apply: `requests:read` and `requests:implement` (GET needs only read).

`/implement-requests` implements user-approved bug/feature specifications using the selected VS Code model. It shares the findings workflow's Git/PR engine and quality policy, but has its own request claims, evidence and lifecycle. It does not resolve findings or recalculate review grades.

## Setup and usage

Cross-project extension: deploy migration **0011_project_handoffs**, configure consumer relationships and update the kit. The command now saves a handoff proposal with delivery; **only human closure publishes** linked Open requests. Reviewers must save the handoff review (or no-impact reason) before closing. See [cross-project handoffs](cross_project_handoffs.md).

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

### Verification artifact handoff

`verify_request.py build` prints a command receipt such as:

```json
{
  "artifact_kind": "request-verification-build-result",
  "status": "verified",
  "outcome": "satisfied",
  "envelope": "/path/to/request-verification-envelope.json"
}
```

This is successful packaging output, **not** the verifier's evidence. The original `verification.json` contains `status=satisfied|not_satisfied|inconclusive` plus `rationale`, `reviewer`, `evidence`, `checks`, and `acceptance`. The generated envelope binds that evidence to the request and source; its verdict is `result.status`. Commit/deliver take the **envelope file path** as `--report`, not the receipt or raw verifier file. Older kits' receipts lack `artifact_kind` but retain the same meaning.

Wrong-artifact inputs now produce `error_code=request_artifact_mismatch` in the local helpers, with instructions identifying the required artifact. They remain rejected; no status conversion, missing-evidence fabrication or automatic path following is performed. A still-active attempt gets one bounded handoff-recovery attempt using genuine verifier evidence/the generated envelope and all normal source/claim checks before being reported as failed. Updating the kit does not reopen an already released claim or change an immutable completion. Preserve that attempt's state, worktree and branch for separate recovery.

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
