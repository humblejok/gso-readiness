---
name: standards-mapping
description: Map verified findings to CWE, OWASP Top 10, OWASP ASVS, or NIST SSDF only when evidence supports the mapping.
user-invocable: false
---

# Standards Mapping

Standards are classification metadata, not evidence of a defect and not a reason to increase severity.

Prefer deterministic mappings already supplied by scanner output (`cwe`, `rule_id`, or equivalent). Use `.github/graphify-review/scripts/map_standards.py` with `mappings.json` for known rule mappings.

An LLM-assisted mapping is allowed only when deterministic metadata is absent and the verified behavior maps specifically to a standard. Mark uncertain mappings and require Finding Verifier confirmation. Do not attach a standard merely because a finding is in the security category.

Supported frameworks and identifier formats:

- CWE: `CWE-798`
- OWASP Top 10: `A05:2021`
- OWASP ASVS: `V2.1.1`
- NIST SSDF: `PW.1.1`

Output objects contain `framework`, `id`, and `title`. The final validator rejects malformed identifiers.
