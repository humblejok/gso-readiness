---
name: production-readiness
description: Classify evidence-backed production consequences under policy while leaving scores and release state to deterministic code.
user-invocable: false
---

# Production Readiness

Read `.github/graphify-review/policy.yaml`, [Production Policy](production-policy.md), and [Severity and Release Rules](severity-rules.md).

Keep observed facts, verified defects, severity, technical-quality impact, likelihood, production impact, remediation priority, and recommendations separate. Missing evidence lowers assessment confidence; it does not prove poor quality. Graphify anomalies and framework profile rules require source confirmation before becoming findings.

Production impact values are `blocker`, `required_before_production`, `post_release`, and `none`. Classify them only after verification. Do not calculate a numeric score or choose the release recommendation; `calculate_score.py` owns both.

Accepted risks are explicit governance decisions. Active acceptance may override release impact according to policy, but the technical penalty remains. Expired acceptance has no waiver effect and remains visible.

Follow deployment policy. When `containers_required: false`, absence of Docker/Kubernetes assets is not a defect.

Named CVEs require structured scanner/advisory evidence. Without SCA, status is unknown—not clean or vulnerable.
