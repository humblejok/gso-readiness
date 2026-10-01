---
name: implement-requests
description: Implement user-specified Hub requests on isolated branches, verify independently and create PRs.
argument-hint: "[requests=<request-UUID>,<request-UUID>] [remote=origin] [resume=<state-path>] [adopt=<failed-state-path> worktree=<path> branch=<correction/branch> commit=<full-SHA>] [retry=<expired-attempt-ID-or-state-path>]"
agent: Request Implementation Manager
---

Prefer `resume=<state-path>` for interrupted retained work, including expired claims, existing PRs and legacy correction adoptions blocked at completion. Process only that attempt; never widen to the queue. `resume`, `adopt` and `retry` are mutually exclusive. Follow the manager's protocol-v2 recovery instructions and direct verifier calls. If a `requests` filter or explicit remote is present it must match the saved request. Never rewrite a pending completion or relabel its source branch.

For explicit `adopt=<failed-state-path> worktree=<path> branch=<correction/branch> commit=<full-SHA>`, register an already committed correction extending the confirmed failed attempt. Require the request UUID (from the saved state, checked against any supplied filter). Both Hub and kit must support protocol v2; no locally invented helper modifications. Adoption does not authorize edits, new product scope, rewriting old history or bypassing independent verification.

Implement Specified requests assigned to this checkout's configured Hub project. With no filter use all returned Specified requests; the optional comma-separated `requests` UUID filter must never widen. Use only the user-accepted specification. Follow Request Implementation Manager for readiness, claims, isolated feature branches, optional Sonar, direct independent verification before/after committing, PRs and Hub completion. Never merge, close requests, rewrite approved specifications, self-certify, or invoke a revalidation manager as an intermediary. No model override. Leave tools unset here so configured Sonar MCP tools enabled in the chat remain available.

For `retry`, process only the saved request attempt under `output/request-implementations/<attempt-ID>/state.json` or the supplied state path. Follow the manager's expired-request recovery procedure, not normal queue/plan. Any requests filter or explicit remote must match the saved attempt. This supports an expired claim and a clean retained implementation commit without a PR submission or pending completion. Acquire a successor claim, obtain fresh committed verification, and deliver the existing commit without edits, amendments or another commit. Do not reuse findings recovery instructions, rewrite state, or treat the request UUID or Hub claim UUID as the local attempt-directory ID.
