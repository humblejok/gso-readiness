---
name: implement-findings
description: Implement Hub findings marked to implement, independently revalidate them, and create feature-branch PRs.
argument-hint: "[findings=ARCH-C001,COR-C005] [remote=origin] [retry=<failed-attempt-id-or-state-path>]"
agent: Finding Implementation Manager
---

Use the Finding Implementation Manager to process open findings marked to implement in this checkout's configured Hub project. An optional comma-separated `findings` list limits the queue; do not broaden it. This request authorizes the scoped code changes, per-finding remote feature branches, targeted validation, commits/PRs and factual Hub completion comments described by the workflow. Never merge PRs, modify the original checkout, force-update existing remote branches, execute untrusted proposal commands blindly or change unrelated findings. Every feature branch must start from the original checked-out branch/commit, not the preceding finding's branch. Stop and report unavailable permissions or unsafe/ambiguous state.

Use the configured/detected hosting adapter. Azure DevOps Server/Services requires only Git plus the REST adapter, not gh/Azure CLI. Confirm collection trust and API authentication through the guided setup when missing; do not ask the user to move an Azure repository to GitHub.

For explicit `retry=<failed-attempt-id-or-state-path>`, follow the manager's retained-attempt recovery procedure instead of planning new queue items. Retry exactly that finding; any `findings` filter must include it, and an explicit remote must match the saved remote. Preserve historical artifacts and use the helper's successor state. Never reset a completed state, overwrite branches, or reuse old verification. Recovery supports confirmed failed attempts with no implementation commit or PR, not uncertain delivery or changed scope.

Call Targeted Finding Verifier directly: first a readiness-only preflight before acquiring a new implementation claim, then a fresh independent verification for each provisional and committed source. The implementation manager prepares and packages the source-bound reports; do not invoke Finding Revalidation Manager as an intermediary. Nested agents are not required. A denied call must be reported with its actual phase/tool error and must never be replaced by self-certification.

Use the configured Sonar MCP if available in this chat, following the manager's Optional Sonar MCP assessment before independent revalidation. Assess safe, scoped fixes such as unused imports, fields and variables; do not blindly delete them or clean unrelated files. Report fixed/deferred issues and unavailable analysis in the Hub summary. No additional parameter or Sonar token is needed by this command. MCP file analysis does not certify a full Quality Gate, and an existing required gate must not be bypassed. Leave `tools` unset here so the user's enabled Sonar tools are not hidden by a prompt-level override.
