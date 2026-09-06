---
name: model-routing
description: Route review work by deterministic task-complexity factors and record agent/model roles in the reproducibility manifest.
user-invocable: false
---

# Model Routing

Policy under `models.routing` owns one exact model per tier; agents own execution. Skills do not execute models. When `exact_model_required` is true, stop if that model is unavailable instead of selecting a fallback.

Classify each work item as `trivial`, `normal`, `complex`, or `critical`. Prefer deterministic factors:

- file count and Graphify subgraph size;
- dependency-edge count and cross-module behavior;
- security sensitivity and candidate severity;
- whether the result may create a production blocker.

Use the agent default when factors do not justify another tier. A bounded local review normally remains `normal`; cross-module architecture/correctness and independent verification are normally `complex`; synthesis or release-critical adjudication may be `critical`. Do not silently route all work to the most expensive tier.

Record each executed route in the manifest:

```json
{
  "agent": "architecture-reviewer",
  "model": "GPT-5.6 Terra",
  "role": "primary_reviewer",
  "task_complexity": "complex"
}
```

Record model identity and role only. Never persist hidden reasoning or chain-of-thought.
