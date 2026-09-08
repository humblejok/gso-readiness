---
name: publish-review
description: Publish a selected review to the configured Finding Hub, creating its project if missing.
argument-hint: "envelope=path/to/import-envelope.json | review=path/to/review.json [repository-id=stable-id] [default-branch=main] [remediations=path/to/plans.json] [dry-run=true]"
agent: Review Publish Manager
---

Use the Review Publish Manager to publish the explicitly selected envelope or export and publish the selected previous review. Require exactly one of `envelope` or `review`; never guess the latest run. Use the saved Hub URL and a workspace credential from the terminal login helper, never credentials in chat. The Hub creates the project/repository inside the authenticated workspace if its stable external ID is missing. Do not create an account/workspace, rerun audits, rescore, edit dependencies or execute remediation instructions.

`dry-run=true` prepares/validates locally and shows the destination without reading credentials or sending anything. Publication is otherwise an explicit network write. On a retry reuse the exact envelope from the first attempt, including its remediation plans and run ID.
