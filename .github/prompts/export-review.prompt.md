---
name: export-review
description: Export a previous full-project review as a validated Finding Hub JSON import envelope, without rerunning audits.
argument-hint: "review=path/to/review.json repository-id=stable-id default-branch=main [repository-name=name] [remediations=path/to/plans.json] [output=path/to/new-directory] [source-commit=original-sha] [source-branch=original-branch] [clone-url=https://host/repo.git]"
agent: Review Export Manager
---

Export the explicitly selected previous full-project review using the Review Export Manager.

Accept `review` as either a final `review.json` or its run directory. Ask for a missing run selection, stable repository ID, or default branch; never guess the most recent run or use current HEAD as historical source metadata. Preserve the complete authoritative review unchanged. Generate proposed, source-bound remediation plans when none were supplied, validate the envelope with the deterministic exporter, and return its local path and idempotency key. Do not upload, run audits, rescore, edit dependencies or execute remediation commands.
