---
name: graphify-evidence
description: Normalize and interpret Graphify structural output as evidence for code review without confusing graph metrics with defects.
user-invocable: false
---

# Graphify Evidence

Use this skill whenever a review relies on Graphify output.

## Evidence hierarchy

Treat evidence in this order:
1. Direct source code and configuration.
2. Deterministic test/tool output produced in the current review.
3. Normalized Graphify structural facts.
4. Model inference.

Graph facts are observations. They are not conclusions. Examples:
- a cycle exists -> fact
- the cycle causes harmful architectural coupling -> inference requiring source inspection
- fan-out is high -> fact relative to the graph
- a component has too many responsibilities -> inference requiring code inspection

## Normalized graph

The canonical structural file is `<run-dir>/evidence/project-graph.json`. The evidence-first pipeline combines it with `<run-dir>/evidence/profile.json` to build `<run-dir>/evidence/normalized-evidence.json`; specialists must not start before it exists and its execution/completion states are checked.

Create it with:

```bash
<review-python> .github/skills/graphify-evidence/scripts/graphify_adapter.py \
  --input <graphify-export> \
  --output <run-dir>/evidence/project-graph.json
```

The adapter intentionally preserves uncertain mappings as metadata rather than inventing semantics.

Build the full evidence envelope with:

```bash
<review-python> .github/graphify-review/scripts/build_evidence.py \
  --repository . \
  --graph <run-dir>/evidence/project-graph.json \
  --profile-file <run-dir>/evidence/profile.json \
  --tool-results <run-dir>/evidence/tool-results.json \
  --output <run-dir>/evidence/normalized-evidence.json
```

## Referencing evidence

Use stable evidence references when possible:
- `graphify` evidence with stable `source_id`, node IDs, and/or edge IDs
- `source:<relative-path>:<start>-<end>`
- `test:<command-or-test-id>`
- `tool:<tool-name>:<result-id>`

If line numbers cannot be established reliably, cite the file and symbol instead of inventing line numbers.

## Review discipline

- Inspect both sides of a dependency before judging it.
- Identify generated/vendor files and avoid treating them as authored architecture.
- If Graphify output contradicts the current source tree, record the graph as potentially stale.
- When a graph relation's semantics are unknown, use the generic relation type and do not rename it to `call`, `import`, or `dependency` without evidence.
