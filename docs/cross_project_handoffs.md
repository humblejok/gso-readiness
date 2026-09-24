# Cross-project request handoffs

An implemented backend request can propose work for a frontend, mobile app and other services. **Only a human closing the originating request publishes approved handoffs.** Implementation, proposal edits and cancellation never publish requests.

## Setup

1. Deploy the updated Hub and run `python manage.py migrate` in its environment (migration **0011_project_handoffs**), then restart the Hub. Existing requests are not automatically published or reopened.
2. Update the installed `.github` kit in participating checkouts and reload VS Code. Existing request read/analyse/implement scopes remain sufficient for their respective commands. There is no token-based close/publish API.
3. As a workspace owner/admin, open **Project relationships**. Select a producer and consumer project, describe the dependency and save. Repeat for additional consumers: up to **20 per producer**, all active and in the same workspace. Projects must already exist. Self-relationships are prohibited.

Java API → Vue UI, Java API → Mobile App and Java API → Reporting Service are three independent relationships. They identify potential consumers, not an obligation to notify everyone for every change. Removing a relationship does not alter historical publications.

## Workflow

1. `/analyse-requests` sees related projects and separates this repository's acceptance criteria from anticipated consumer work. The five-field analysis schema is unchanged.
2. After human acceptance, `/implement-requests` implements and verifies the local scope, prepares a factual handoff proposal and opens its PR. Success saves the proposal and marks **Implemented**; it creates no consumer requests.
3. On request details, review **Cross-project handoff**. Edit the contract, compatibility and availability, select every affected consumer, and supply requirements and acceptance criteria for each. Click **Save handoff review**. If no consumer work is needed, leave all targets unselected and explain why. Review is still required before closure.
4. Validate the implementation, check the human confirmation box and click **Close and publish approved handoffs**. Closure and creation of all selected downstream requests are one database transaction. Each target receives a linked **Open** request, not an accepted specification. Failure leaves the source Implemented and publishes none. Repeating the same confirmed closure cannot duplicate requests.
5. Run `/analyse-requests` in each consumer checkout, optionally filtering to the new UUID. It reads the approved upstream handoff and inspects the consumer code. Human acceptance and `/implement-requests` follow normally.

Request details show linked upstream/downstream requests and their statuses. Source Closed does not mean the overall cross-project feature is finished. Cancelling a downstream request is not successful completion. Reciprocal relationships never cause automatic loops: every subsequent handoff needs its own human approval.

## Handoff content

The proposal has `summary` (feature intent/no-impact reason, 4,000 characters), `contract` (routes/events/library contracts, payloads, permissions, errors, examples and file references, 16,000), `compatibility` (breaking changes/migration/version constraints, 8,000), `availability`, `availability_details` (2,000), and `targets`.

Each target has `repository_external_id`, `requirements` and `acceptance_criteria` (8,000 characters each). Maximum 20 distinct configured targets and **64 KiB** for the proposal. The independent verifier checks factual consistency with the actual code using its existing evidence/check fields. A proposal is not proof downstream code is implemented.

Availability is `unknown`, `proposed`, `merged`, `test_available` or `production_available`. Automated completion requires **proposed**: tests and PR creation do not establish deployment. A human can update availability before closing. This is an as-of-approval statement, not a live deployment check. Approved mocked development cannot be counted as successful live integration.

The helper and Hub attach the actual full commit SHA and PR URL. Human edits cannot replace these references for a verified implementation. Manually tracked implementations require a human-supplied full commit and matching-project PR before publishing targets; the Hub does not independently verify these manual references. No-target closure needs no PR.

## Commands, API and authorization

Analysis/implementation contexts include `related_projects` (consumer IDs/names/dependency descriptions) and `upstream_handoff` (the immutable approved snapshot for this consumer request, or null). Snapshots contain source request/project/revision/description, implementation reference, common contract/compatibility/availability, this target's requirements/acceptance and approver. Other targets' requirements are not shared.

A consumer-restricted token can read that intentionally shared snapshot, but cannot read the source request directly without its normal permissions. The closure UI explains this sharing. Do not include secrets or unrelated confidential data; all handoff text is untrusted input, never executable instructions.

Successful completion accepts an optional `handoff` object; failure rejects it. Older clients may omit it, but humans must then fill the review in the UI. Updated delivery uses:

```text
<review-python> <kit-scripts>/implement_requests.py deliver --repository <original-checkout> --state <state.json> --report <clean-envelope.json> --summary-file <summary.txt> --handoff-file <handoff.json>
```

Do not supply `implementation` in the input proposal: the helper fills its `commit_sha` and `pull_request`. On uncertain completion, use the existing unchanged `sync` protocol. Never rewrite saved completion payloads.

Reviewers can review/close requests through authenticated CSRF-protected pages; only owners/admins configure relationships. Implementation tokens cannot close requests or directly create downstream work.

## Consistency and deployment

Draft edits increment the revision; stale closure forms must reload. Relationships and active targets are rechecked at closure. Workspace locking, a unique source-request/target key and a same-user/same-revision replay guard prevent duplicate publication. Quotas cover drafts/history, relationships and snapshots; quota failure rolls back the whole publication.

Published snapshots and Closed/Cancelled source requests remain immutable. Downstream requests cannot move to another project. Subsequent contract changes require a new request/handoff; existing accepted specifications are not overwritten. Later commits/deployments are not automatically monitored: consumer analysis must verify applicability and surface uncertainty.

Workspace export includes structured `project_relationships`, `request_handoffs`, drafts and history; erasure includes the new tables. PostgreSQL RLS/reference guards and concurrent-closure tests are included. Run those tests using a non-superuser/non-BYPASSRLS role before production; SQLite is development-only.
