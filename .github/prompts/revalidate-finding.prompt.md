---
name: revalidate-finding
description: Recheck one baseline finding without reassessing other findings or project scores.
argument-hint: "finding=ARCH-C001 baseline=path/to/review.json [allow-dirty=false|true] [publish=false|true]"
agent: Finding Revalidation Manager
---

Revalidate exactly the specified finding against the current checkout using the Finding Revalidation Manager. Require an explicit baseline and unique short ID or fingerprint. Do not run a whole-project review, alter other findings, rescore, implement fixes or automatically publish. `publish=true` explicitly permits sending this targeted result to the configured Hub after verification, using the captured pre-verification Hub revision. A dirty-worktree result is provisional and cannot update Hub lifecycle. Use the dedicated targeted contract, never fabricate a full review/import envelope.
