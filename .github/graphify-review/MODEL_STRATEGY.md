# Model strategy

The production-readiness pipeline uses the task-complexity routes in `policy.yaml`. Small models perform bounded evidence work; stronger models handle cross-component judgment, adversarial verification, production-impact classification, and synthesis. Deterministic scripts—not models—own confidence, scoring, hard gates, and release state.

| Stage | Pinned model | Fallbacks | Reason |
|---|---|---|---|
| Evidence scoping | GPT-5.6 Luna | none | Structured extraction, Graphify prioritization, manifest/config inventory |
| Maintainability | GPT-5.6 Luna | none | Local evidence-constrained review |
| Security triage | GPT-5.6 Luna | none | Candidate generation and deterministic-tool interpretation; verification follows |
| Testing review | GPT-5.6 Luna | none | Test inventory, safe execution, high-risk path inspection |
| Deployability | GPT-5.6 Luna | none | Mostly configuration/runtime/server deployment inspection |
| Architecture | GPT-5.6 Terra | none | Cross-component structural judgment |
| Correctness | GPT-5.6 Terra | none | Cross-file execution-path reasoning |
| Finding verification | GPT-5.6 Terra | none | Adversarial precision gate |
| Production readiness | GPT-5.6 Terra | none | Production-impact classification and missing-evidence interpretation |
| Final synthesis | GPT-5.6 Sol | none | Reconcile evidence, map IDs, produce schema-valid final JSON |

## Lower-cost mode

For lower latency/cost, change both the single `models.routing` preference and matching agent declaration as a versioned review-kit change.

Keep the final synthesizer on Sol/Terra/Sonnet for a report intended as an assessment rather than review hints.

## Higher-assurance mode

Use Terra/Sonnet for all specialists, Sol/Terra for verification and production readiness, and Sol for synthesis. This is appropriate for small but high-risk repositories.

## Model ownership

Agents own model execution. Policy owns preferred routes. Skills do not execute independently; they provide methodology and routing rules to the running agent. Record agent/model/role/task complexity in the manifest without hidden reasoning.

Model availability depends on the GitHub Copilot plan and organization policy. Strict runs stop instead of silently falling back to another model. Pinning reduces variability but cannot make fresh LLM discovery deterministic; baseline revalidation provides continuity.
