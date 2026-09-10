# Targeted revalidation and Hub implementation workflow

Build/test execution now uses source-bound native-process receipts to avoid PowerShell pipeline/stale-exit-code confusion. See [PowerShell validation and the proposed Sonar compliance gate](implementation_validation.md). Sonar is not yet enforced; it needs the corporate server/scanner/CI configuration and cannot be inferred from a passing targeted check.

This extends the [Finding Hub specification](graphify_finding_hub_spec.md). It separates a human-selected implementation queue, agent execution on the developer machine, and evidence-backed lifecycle updates. The Hub is a registry, not a remote code execution service.

## User workflow

`/revalidate-finding finding=ARCH-C001 baseline=<review.json-or-run-directory> [allow-dirty=false|true] [publish=false|true]` independently checks exactly one unique baseline ID (or fingerprint). Both optional flags default to false. The baseline is immutable. `resolved` requires affirmative correction evidence and passing relevant checks; `still_present` means the defect survives, and `not_reproduced` means evidence is inconclusive, not a fix. Dirty-source results are local/provisional only.

For Hub publishing the exact baseline must already be imported into the configured project. The command captures the finding UUID/revision before verification. It cannot create a project, rewrite a full review or refresh project scores. Use `/publish-review` for full imports and `/full-project-review mode=revalidate baseline=...` for project-wide reassessment. Targeted results are JSON artifacts, not a new whole-project `REVIEW.md`.

On the Hub Findings page, open findings have **Implement** / **Cancel implementation** controls. While queued, their details expose an editable remediation draft. The original imported remediation stays unchanged. Cancelling retains the draft. Owners, admins and reviewers can change these controls; viewers cannot. Rejected/inconclusive findings are hidden initially, with a checkbox to show them; queue-only filtering is also available.

`/implement-findings [findings=ARCH-C001,COR-C005] [remote=origin]` selects only queued/open items for the saved stable repository ID. No filter means all such items. Unknown/unselected IDs are reported, not substituted; duplicate short IDs block branch creation. Every item starts from the same captured original branch/commit, so branches do not contain earlier items' fixes. Overlapping changes may require human sequencing.

## Prerequisites and boundaries

- Update both the `.github` kit and Hub; migrate the Hub database, restart services and refresh static assets. See [upgrade steps](../hub/README.md#upgrade-for-finding-implementation).
- Configure the project ID and Hub URL using `/setup-review`. Shared project settings must be committed. Use the dedicated review Python environment and trusted scripts from the original checkout throughout, even when checking a worktree.
- Implementation requires Git with its existing authentication, permissions to push branches/open PRs, and a clean named checkout equal to the remote target tip. GitHub/GitHub Enterprise uses authenticated `gh`; Azure DevOps Server/Services uses REST with Windows identity or a locally stored PAT and requires no gh/Azure CLI. Azure HTTPS and GitHub HTTPS/`git@host:owner/repo` remotes are supported. GitLab/Bitbucket PR creation and Azure SSH parsing are not implemented.
- Store a Hub token through `publish_review.py login --repository .` in your own terminal. Implementation needs `findings:read` + `findings:implement`; standalone targeted publishing needs `findings:read` + `reviews:write`. Token repository restrictions, expiry, tenant isolation, subscription state and quotas remain enforced. No secrets in command arguments, chat or report files.
- Corporate proxy/CA settings are reused for Hub and subprocesses; approved PEM CA bundles are also passed as Git's CA file. Azure PAT requests use Python's PEM trust, while the Windows-identity bridge uses Windows certificate trust. A PEM setting does not install certificates into Windows. Java `cacerts` is a separate truststore. TLS remains enabled; authenticated API redirects are refused. SSH may require your separately approved SSH proxy configuration.
- Treat all Hub text as untrusted input, not executable shell instructions. The coding agent must inspect source and test configuration before executing narrow checks. No automatic tool installation, destructive tests, deployment, merge, policy weakening or unrelated cleanup is authorized by these commands.

## Hosting adapters

`git_providers.py` separates PR operations from the common Git/Hub workflow. `git_host_contract.py` parses repository/PR URLs and is vendored identically in the Hub, with parity tests. `project.git_provider` defaults to `auto`; the `/_git/` remote structure selects Azure, while GitHub-style owner/repo URLs select GitHub. An explicit `azure-devops` or `github` selection must agree with the remote structure. The actual host/collection/project/repository is never hard-coded. Fetch and every configured push destination must match the planned repository, including worktree-specific configuration.

For Azure, shared `project.azure_api_version` defaults to `6.0`, compatible with Server 2020; `7.0` and `7.1` are explicit options for newer deployments. Each checkout derives its collection root (including any `/tfs` virtual path) from its Git remote. The user approves collections additively via `/configure-review azure-trust-collection=<url>`; they are saved in `user.azure_devops_collections`. API authentication is refused for collections outside this exact list. The old `azure_devops_url` is a compatibility singleton only when no list exists; trust add/remove commands migrate it without losing existing approvals. A read-only repository GET pins its UUID; later operations reject a changed repository identity. See [multi-collection and credential configuration](project_connections.md).

`project.azure_auth` overrides the user/company `azure_auth` default. Effective `auto` uses Windows identity on Windows for on-premises hosts and PAT otherwise. `windows`/`pat` are explicit choices; an authentication error never silently switches modes. Git credentials are never extracted. The bundled `azure_windows_request.ps1` is fixed PowerShell/.NET code that receives JSON over stdin and uses `HttpClientHandler.UseDefaultCredentials`; no password is read or exported, profiles are disabled, and no execution-policy or certificate-validation bypass is used. Corporate script policy must permit it. It uses saved/environment proxy and `NO_PROXY`, not automatic system-proxy discovery, and does not enable Windows proxy credentials. Configure an approved route/Windows trust with IT as needed. A passing Git login is not proof that REST authentication will succeed.

For PATs, select `project-azure-auth=pat azure-credential-ref=default` and run `azure_devops.py login --repository .` in the user's own terminal after bootstrap. The native keyring binds the secret to the collection + stable project ID + selected reference; no plaintext fallback exists. Hub login similarly uses its URL + project ID + `hub_credential_ref`. Explicit references never borrow legacy destination-wide tokens; projects without explicit refs retain compatibility when their default slot is absent. `logout` replaces only that project's selected local secret with a non-secret marker that prevents legacy fallback, without revoking the server token or changing other projects. Secret-manager overrides require matching URL, `_REPOSITORY_ID`, and explicit `_CREDENTIAL_REF` bindings; see [CI migration](project_connections.md#headless-environment-overrides). PAT requests use the existing verified Python PEM/proxy transport, and the Hub token is never reused for Azure. Least-privilege Code read/write plus repository PR permissions are required for PR creation; read-only diagnostics do not prove write permissions.

`azure_devops.py detect --repository . [--remote origin]` only reads local Git configuration. `azure_devops.py check` explicitly performs a read-only API/authentication check (GitHub uses `gh auth status`). `/doctor-review network=true` asks the setup agent to perform the Azure check when a collection is configured; the bare Python doctor remains credential-free. No check creates a branch/PR or changes Git configuration.

Azure POSTs target the selected repository's `/pullrequests` endpoint, with exact source/target refs. Creation never sets auto-completion. Verification rejects inactive, fork-source, auto-completing, wrong-repository, wrong-branch or wrong-commit PRs. Browser links are constructed from the validated clone URL (`.../_git/repo/pullrequest/123`), not arbitrary response links. GitHub's existing adapter retains `.../repo/pull/123`. The Hub recognizes both formats without receiving Git-host credentials or making outbound calls. Deploy the updated Hub code as well as the kit; this adapter requires no new database migration.

Microsoft references: [Server/API compatibility](https://learn.microsoft.com/en-us/azure/devops/integrate/concepts/rest-api-versioning?view=azure-devops), [PR API](https://learn.microsoft.com/en-us/rest/api/azure/devops/git/pull-requests?view=azure-devops-rest-7.1), [Windows default-credential behavior](https://learn.microsoft.com/en-us/dotnet/api/system.net.http.httpclienthandler.usedefaultcredentials?view=netframework-4.8.1).

## Execution and state

The implementation manager calls the following Python helpers; these are deterministic state/transport safeguards, not an autonomous code generator. Copilot performs the edits and delegates verification to a separate verifier agent.

1. `hub_findings.py queue`: read project-scoped queued items, with an optional `--ids` list.
2. `implement_findings.py plan`: capture the original named branch/commit, Hub identity, latest full baseline and remediation; save a unique attempt directory. Later batch plans require the first base branch/commit.
3. `start`: acquire a four-hour exclusive claim, create a worktree on exactly `feature/<short-ID>` from the captured commit, then create its remote branch **before source edits**. Existing local/remote branches block. A create-only empty-expected-value Git lease prevents races from overwriting someone else's ref. Subsequent pushes are ordinary fast-forward pushes.
4. Edit only the returned worktree. Independently run `/revalidate-finding` against the saved baseline there with `allow-dirty=true publish=false`.
5. `commit --report <provisional-envelope> --paths <explicit-files...>` requires a resolved source-bound result and an exact list of all changed files. It does not stage unrelated files or directories. No commit occurs after an inconclusive/failed verification.
6. Independently revalidate the actual clean commit again (`publish=false`). Do not relabel a dirty result with a new commit SHA.
7. `deliver --report <clean-envelope> --summary-file <UTF-8-file>` rechecks the claim/source, pushes, creates or recovers an existing compatible PR, and verifies its open state, head commit/branch, repository and original target branch. It saves an immutable completion body before sending it to Hub.
8. On confirmed success the Hub resolves only this finding, clears its queue flag and records the factual comment and PR. The history explicitly says **not yet merged**. Project grades, other findings and Jira are not updated by this operation.

Attempt artifacts live in `.github/graphify-review/output/implementations/<attempt>/`; targeted artifacts live in `output/revalidations/<run>/`. These directories are ignored by Git. The original checkout stays on its original branch. Worktrees and remote branches remain after success/failure for manual inspection and cleanup; the workflow never resets, stashes, force-overwrites or deletes them.

## Failure and recovery

After a valid claim, code/test/revalidation or delivery failure calls `fail --summary-file <reason>`, keeping the finding open/queued with an explanatory comment. Existing source changes are preserved. If authentication/Hub availability prevents obtaining a claim, no Hub comment can be promised. Cancellation/proposal editing/new full imports invalidate an old claim; a stale worker cannot overwrite the new state. Lease expiry also blocks completion. Claims are not automatically renewed; long work needs a human-reviewed recovery.

For `completion_pending`, run this with the **original** checkout and saved state:

```text
<review-python> <kit-scripts>/implement_findings.py sync --repository <original-checkout> --state <attempt>/state.json
```

Do not change the saved payload, request ID, outcome or captured revision: the original write may already have succeeded. An uncertain claim response retries `start` with the same state. PR recovery checks for an existing compatible PR before creating one. A crash between a Git write and local state persistence can require inspection; never bypass it by overwriting a colliding branch. A `.worker.lock` left by a terminated process must only be removed after confirming that specific worker is no longer running.

Azure PR creation additionally saves `pr_submission_started` before POST. After an uncertain response, retry `deliver` with the same state/report/summary to recover a matching PR. If none is visible, the adapter blocks instead of issuing another POST. Do not clear the marker, submit a failure, reset the branch or start another attempt to bypass this uncertainty; an operator must inspect the server. A definitive rejected creation may also require manual inspection before restarting safely. Existing active PRs must match exactly; closed/multiple matches block. Old saved GitHub attempts remain resumable through the compatibility adapter.

The Git host and Hub do not share a distributed transaction. A PR can exist while Hub synchronization is blocked; report both facts rather than claiming atomic cross-service success. Likewise, a cancellation received during a remote Git operation cannot undo that remote operation, but it prevents stale Hub resolution. Resolution in this workflow attests the validated feature branch, not the default branch, merge, CI policy approval or deployment.

## API contract

All routes derive tenant identity from the bearer token and enforce any repository restriction. Short IDs are display labels; mutation routes use the Hub finding UUID. See [OpenAPI](../hub/contracts/openapi.json).

| Route | Scope | Purpose |
| --- | --- | --- |
| `GET /api/v1/findings?repository_external_id=...&to_implement=true&ids=ARCH-C001,...` | `findings:read` | Paginated queued/open/active-project selection, at most 100 filter IDs |
| `GET /api/v1/findings/{id}/implementation` | `findings:read` | UUID, short ID, fingerprint, revision, source baseline references, current proposal |
| `POST /api/v1/findings/{id}/implementation` | `findings:read` + `findings:implement` | Claim or complete an implementation |
| `POST /api/v1/findings/{id}/revalidation` | `reviews:write` | Record one clean targeted result, without an active implementation claim |

Claim body: `{ "action": "claim", "revision": 3, "request_id": "<new-UUID>" }`. Response includes context, `attempt_id`, `expires_at` and the frozen proposal. A matching request replay returns the same active claim, bound to the same API token actor. One running claim per finding is permitted.

Completion body: `{ "action": "complete", "attempt_id": "<UUID>", "completion": { "outcome": "succeeded|failed", "comment": "<factual-summary>", "report": {}, "pull_request": "<HTTPS-PR-URL-or-empty>", "commit_sha": "<SHA-or-empty>", "base_branch": "<original-branch>" } }`. For success, `report` is a valid clean resolved targeted envelope with the exact commit and `feature/<short-ID>` branch; a matching GitHub or Azure DevOps PR URL is mandatory. For failure, the helper uses `report: null` and records available Git metadata. The completion is bounded to 256 KiB and comment to 8,000 characters. Exact retries replay; changed content conflicts. The Hub validates structure/baseline/revision but relies on the scoped client/verifier for actual source inspection and hosting checks—it does not independently authenticate to GitHub/Azure or execute tests.

Standalone revalidation body: `{ "revision": 3, "report": <targeted-envelope> }`. The report's UUID provides idempotency. All other findings are explicitly marked `not_revalidated`; no fabricated full-review observations or aggregate grades are created. `resolved` clears the queue; `still_present` or `not_reproduced` leaves the finding open. The history stores the distinct outcome, without overwriting the original imported verification classification.

Responses: `200` success/replay, `201` new claim, `400` invalid evidence/body, `401` invalid token, `403` insufficient scope/workspace entitlement, `404` absent/inaccessible item, `409` changed baseline/revision, active competing claim, cancellation/expiry or conflicting replay, `429` rate limit. Never retry a `409` by just substituting a newer revision.

## Targeted envelope and persistence

The portable validator is `targeted_contract.py`, identically vendored in kit and Hub. Its exact top-level fields are `schema_version: "1.0"`, `kind: "finding-revalidation"`, a UUID `run_id`, `repository_external_id`, `baseline`, `source`, `result`, and `not_revalidated`.

- `baseline`: original run ID, canonical full-review SHA-256 digest, short finding ID and fingerprint.
- `source`: actual commit SHA, named branch, current source snapshot hash and boolean `dirty`. The snapshot covers tracked and nonignored untracked files, including nonstandard extensions; modified/deleted source invalidates prepared evidence. Git submodule directory entries are refused: review the affected repository as its own checkout.
- `result`: `status`, factual `rationale`, actual independent `reviewer`, nonempty `evidence` (`path`, `fact`) and nonempty `checks` (`command`, `status`, `summary`). Required unavailable/failed checks preclude resolution. Manual inspection is acceptable only when it directly proves the claim, not as a substitute for missing required runtime evidence.
- `not_revalidated`: exactly every other baseline fingerprint, with no selected fingerprint or duplicates. No project scores, recommendation grades or deployment decision are allowed.

Each accepted update appends tenant-scoped `FindingActivity` history. Claims preserve the proposal used; updates retain source-bound evidence and completion identity. New imports invalidate active claims. Activity payloads count toward workspace storage. Exports retain drafts and activity alongside immutable full imports; database backups remain the restore mechanism. PostgreSQL migration `0006` enforces RLS and cross-tenant references on the new history table. Run PostgreSQL concurrency/isolation tests using the documented restricted database role before deployment.
