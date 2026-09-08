---
name: export-review
description: Export a previous full-project review as a validated Finding Hub JSON import envelope, without rerunning audits.
argument-hint: "review=path/to/review.json [resume=true|false] [repository-id=stable-id] [default-branch=main] [repository-name=name] [remediations=path/to/plans.json] [output=path/to/export-directory] [source-commit=original-sha] [source-branch=original-branch] [clone-url=https://host/repo.git]"
agent: Review Export Manager
---

Export the explicitly selected previous full-project review using the Review Export Manager.

Use saved repository ID/name/default branch unless explicitly overridden. Only ask for missing values after inspecting `setup_review.py show --repository .`. Offer `/setup-review` on first use. The saved default branch is repository metadata, never a substitute for the historical source branch.

`resume=true` safely creates/completes or verifies the selected run's adjacent `hub/` export (or explicit `output`), never overwriting an existing envelope. Use it to recover an interrupted post-review export. Without it, retain the ordinary new export-directory behavior. Revalidation must use the new run's review, never its baseline.

Accept `review` as either a final `review.json` or its run directory. Ask for a missing run selection, stable repository ID, or default branch; never guess the most recent run or use current HEAD as historical source metadata. Preserve the complete authoritative review unchanged. Generate proposed, source-bound remediation plans when none were supplied, validate the envelope with the deterministic exporter, and return its local path and idempotency key. Do not upload, run audits, rescore, edit dependencies or execute remediation commands.
