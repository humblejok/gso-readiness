# Graphify review runtime

Original concept and project creator: **De Jonckheere Stéphane (humblejok)**. Copyright © 2026 De Jonckheere Stéphane (humblejok).

This kit is licensed under [GNU AGPL-3.0-only](LICENSE), without warranty. Retain [NOTICE](NOTICE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) with redistributions. These documents also cover the kit's supplied sibling agent, prompt, and skill files; unrelated files in the host repository keep their own licenses.

This directory contains the policy, strict v2 schemas, deterministic scripts, examples, tests, viewer, and generated-output location for the VS Code Graphify production review kit.

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
- `schema/evidence.schema.json`: normalized evidence and completion contract
- `schema/finding.schema.json`: verified finding contract
- `schema/review.schema.json`: authoritative final JSON contract
- `scripts/calculate_score.py`: only category score, weighted/weakest aggregation, profile-cap, and release-state authority
- `scripts/calculate_confidence.py`: assessment coverage authority
- `scripts/validate_review.py`: final semantic and deterministic enforcement

Generated output is stored in unique `output/runs/<run-id>/` directories and normally ignored using `gitignore.snippet`; archive the complete run directory when reviews are governance records.
