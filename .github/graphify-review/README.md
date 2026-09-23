# Graphify review runtime

Original concept and project creator: **De Jonckheere Stéphane (humblejok)**. Copyright © 2026 De Jonckheere Stéphane (humblejok).

This kit is licensed under [GNU AGPL-3.0-only](LICENSE), without warranty. Retain [NOTICE](NOTICE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) with redistributions. These documents also cover the kit's supplied sibling agent, prompt, and skill files; unrelated files in the host repository keep their own licenses.

This directory contains the policy, strict v2 schemas, deterministic scripts, examples, tests, viewer, and generated-output location for the VS Code Graphify production review kit.

## Analyze Hub bug and feature requests

Run `/analyse-requests` or `/analyse-requests requests=<request-UUID>,<request-UUID>`. The currently selected model analyzes Open requests assigned to this checkout's configured Hub project, asks clarifying questions, and writes implementation specifications covering backend/frontend scope and new/changed/breaking interfaces. No subagents, application edits, branches, PRs or finding claims are involved. Blocking questions leave requests Open; successful submission marks them Analyzed. Review/edit the result in the Hub and click **Accept analysis** to mark it Specified. The creator may cancel until implementation.

Requires the updated Hub with migration **0009**, an assigned Project on each request, and token scopes **requests:read** and **requests:analyse**. Personal tech-lead tokens also need the saved workspace ID. Existing credentials/proxy/CA setup is reused; never paste secrets in chat. Existing unassigned requests are not included. UUIDs are shown on request details. The helper `scripts/hub_requests.py` saves source-bound state/analysis/submission artifacts under `output/request-analyses/`. Stale revisions/cancellation/source changes block submission; uncertain writes must retry the identical saved payload. No analysis token can accept or cancel a request.

### Implement accepted requests

Run `/implement-requests [requests=<request-UUID>,<request-UUID>] [remote=origin]` after human acceptance. Requires Hub migration **0010**, **requests:read** + **requests:implement**, and a clean authenticated checkout. Request Implementation Manager directly calls Request Implementation Verifier (readiness, provisional and committed phases), reuses the findings workflow's native build/Maven, optional Sonar MCP and GitHub/Azure PR safeguards, and implements the frozen approved specification. No nested manager is used. Each `feature/REQ-<UUID>` branch starts at the original base commit; only verified PR creation and Hub synchronization mark the request Implemented. Failure keeps it Specified, preserving work and recording a reason. See [request implementation and recovery](../../docs/request_implementation_workflow.md). State and evidence stay under `output/request-implementations/` and `output/request-verifications/`; they are not finding-import envelopes.

Start with [`policy.yaml`](policy.yaml), then follow the manager lifecycle in `../agents/review-manager.agent.md`. The root repository `README.md` documents commands and output contracts.

Important authorities:

- `requirements.txt` and `scripts/bootstrap_environment.py`: pinned Graphify runtime and safe first-call/manual setup
- `policy.yaml`: environment assumptions, category and weakest-category weights, penalties, gates, confidence, verification, accepted risks, and model routing
- `profiles/*.yaml` and `profiles/profile.schema.json`: framework evidence, audit, rule, and category-cap contracts
- `scripts/resolve_profile.py`: strict alias normalization, auto-detection, validation, and profile hashing
- `scripts/reproducibility.py`: source snapshots, clean-worktree enforcement, run modes, and versioned directories
- `scripts/run_audits.py`: deterministic native/JFrog audit selection, target coverage, sanitized findings, and output provenance
- `scripts/vulnerability_upgrades.py`: independent Xray upgrade checks, exact Artifactory availability decisions, conservative direct-manifest edits, and recoverable backups
- `scripts/aspnet_checks.py`: mandatory ASP.NET investigation anchors
- `scripts/spring_checks.py`: mandatory Spring Boot investigation anchors
- `scripts/finding_identity.py`: stable fingerprints and baseline reconciliation
- `scripts/render_review.py`: deterministic Markdown rendering
- `scripts/rescore_review.py`: model-free baseline rescoring
- `scripts/revalidate_finding.py` and `scripts/targeted_contract.py`: immutable single-finding verification, with every other baseline finding explicitly not revalidated and no aggregate scores
- `scripts/hub_findings.py` and `scripts/implement_findings.py`: scoped Hub queue, exclusive implementation claims, isolated feature-branch worktrees, explicit commits, PR validation and idempotent completion
- `schema/evidence.schema.json`: normalized evidence and completion contract
- `schema/finding.schema.json`: verified finding contract
- `schema/review.schema.json`: authoritative final JSON contract
- `scripts/calculate_score.py`: only category score, weighted/weakest aggregation, profile-cap, and release-state authority
- `scripts/calculate_confidence.py`: assessment coverage authority
- `scripts/validate_review.py`: final semantic and deterministic enforcement

Generated output is stored in unique `output/runs/<run-id>/` directories and normally ignored using `gitignore.snippet`; archive the complete run directory when reviews are governance records.

## Single-finding commands

```text
/revalidate-finding finding=ARCH-C001 baseline=path/to/review.json
/revalidate-finding finding=ARCH-C001 baseline=path/to/review.json publish=true
/implement-findings findings=ARCH-C001,COR-C005
```

Revalidation requires an explicit baseline and clean named checkout; `allow-dirty=true` is only a local provisional check. It creates `output/revalidations/<run>/revalidation-envelope.json`, not a replacement full review or score. Publishing requires the baseline already in the configured Hub and a token with `findings:read` + `reviews:write`.

Implementation selects only open items marked **Implement** in the configured Hub project; omit `findings=` to select all queued items. The Hub must be upgraded/migrated to support the implementation API. Use Git with its existing authentication, a clean original checkout matching its remote tip, and a Hub token with `findings:read` + `findings:implement`, stored via `scripts/publish_review.py login` in your terminal. GitHub additionally needs authenticated `gh`; Azure DevOps uses REST instead, with no gh/Azure CLI requirement. Shared project settings must be committed.

For Azure DevOps Server 2020, use `/configure-review git-provider=azure-devops azure-api-version=6.0 azure-trust-collection=https://ado.example.invalid/Collection project-azure-auth=windows`, replacing the placeholder with the confirmed collection URL from the remote. Approvals are additive: approving Collection B does not replace Collection A. Each checkout selects its collection from its own Git remote, checked against `user.azure_devops_collections`. A legacy `azure_devops_url` still works when no list is present; adding/removing trust migrates it safely. `/setup-review` also guides this. Then `/doctor-review network=true` explicitly checks API access, or run `scripts/azure_devops.py check --repository .` with review Python. Windows uses the current identity and Windows certificate trust via the bundled PowerShell bridge; existing Git credentials remain untouched. The supplied script must be permitted by corporate policy. No TLS or execution-policy bypass is used. API proxy settings come from saved/environment proxy and NO_PROXY; Windows proxy credentials are not automatically enabled.

For project-specific tokens, use `/configure-review hub-credential-ref=default azure-credential-ref=default`, then run `scripts/publish_review.py login --repository .` in your own terminal. Only for PAT authentication, select `project-azure-auth=pat` and run `scripts/azure_devops.py login --repository .`. Tokens are stored by service destination + stable project ID + reference in native OS storage, so another project's login does not replace them. Explicit refs forbid legacy destination-wide fallback. `logout` replaces only the selected project secret with a non-secret marker preventing fallback, without revoking server tokens or affecting others. Never paste credentials into chat/settings. Environment overrides require matching URL and `_REPOSITORY_ID`, plus `_CREDENTIAL_REF` for explicit refs (prefixes `FINDING_HUB_TOKEN` / `GRAPHIFY_AZURE_PAT`). Finish implementation claims before changing their token/reference. Multi-collection/project-token support needs only the updated kit; the Hub already supporting Azure PR URLs needs no further update/migration for this enhancement.

Each item uses an isolated worktree and remote `feature/<short-ID>` branch from the original base commit. Independent targeted verification runs before and after committing. Successful PR creation resolves/unqueues only that finding and adds a summary; failure keeps it open/queued with a reason when the claim remains valid. **PRs are not merged or deployed**, and project grades/Jira stay unchanged. Existing branches are never overwritten. Cancellation, proposal edits and newer imported reviews invalidate workers; claims expire after four hours.

The implementation manager calls **Targeted Finding Verifier directly**, with no intervening revalidation manager or nested agents. It performs a readiness-only invocation before new claims, then prepares/packages separate provisional and committed verification runs via `revalidate_finding.py`. The verifier remains read/search/execute-only and cannot delegate. A denied call stops progress and reports the actual phase and available tool error; there is no self-certification fallback. Update the kit and use a fresh VS Code chat; leave agent/runSubagent enabled alongside the core tools. This does not require `chat.subagents.allowInvocationsFromSubagents` or the obsolete `chat.customAgentInSubagent.enabled` setting. Standalone `/revalidate-finding` still uses its manager with a single direct verifier call. Source-bound envelopes are validated JSON, not cryptographically signed attestations.

Artifacts and worktrees remain in `output/implementations/<attempt>/`. For an uncertain `completion_pending` Hub write, retry `scripts/implement_findings.py sync --repository <original-checkout> --state <attempt>/state.json` with the exact saved completion. Never substitute a failure or rewrite the request. Do not delete branches/worktrees or stale worker locks without first inspecting whether work is still active.

`/implement-findings` optionally uses Sonar MCP tools enabled in VS Code chat. Its manager inherits the chat tool selection, allowing any configured server name; independent verifiers remain restricted. Enable core agent/read/search/edit/execute tools as well. Sonar analysis must target the actual feature worktree. Safe unused imports/fields/variables in edited files may be corrected in up to three rounds before independent verification; uncertain or unrelated cleanup is deferred. The agent writes `sonar-assessment.md` beside the attempt state and summarizes fixed/deferred issues or unavailable analysis in the Hub comment. No extra token or parameter is required. This is supplementary file analysis, not an enforced server Quality Gate; existing mandatory checks are never bypassed.
