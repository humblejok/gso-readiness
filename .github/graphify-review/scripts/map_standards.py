#!/usr/bin/env python3
"""Apply deterministic standards mappings from scanner rule/CWE evidence."""
from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any


def map_findings(findings: list[dict[str, Any]], mappings: dict[str, Any]) -> list[dict[str, Any]]:
    output = deepcopy(findings)
    by_rule = mappings.get("rule_mappings", {})
    by_cwe = mappings.get("cwe_mappings", {})
    for finding in output:
        standards = list(finding.get("standards", []))
        seen = {(item.get("framework"), item.get("id")) for item in standards if isinstance(item, dict)}
        for evidence in finding.get("evidence", []):
            if not isinstance(evidence, dict):
                continue
            keys = []
            if evidence.get("rule_id"):
                keys.extend(by_rule.get(str(evidence["rule_id"]), []))
            cwe = evidence.get("cwe")
            if cwe:
                normalized = str(cwe).upper()
                normalized = normalized if normalized.startswith("CWE-") else f"CWE-{normalized}"
                keys.extend(by_cwe.get(normalized, [{"framework": "CWE", "id": normalized, "title": "Scanner-provided CWE mapping"}]))
            for item in keys:
                identity = (item.get("framework"), item.get("id"))
                if identity not in seen:
                    standards.append(item)
                    seen.add(identity)
        finding["standards"] = standards
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--findings", required=True)
    parser.add_argument("--mappings", default=str(Path(__file__).resolve().parents[2] / "skills" / "standards-mapping" / "mappings.json"))
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    raw = json.loads(Path(args.findings).read_text(encoding="utf-8"))
    findings = raw.get("findings") if isinstance(raw, dict) else raw
    if not isinstance(findings, list):
        raise SystemExit("findings input must be an array or object with findings[]")
    mapped = map_findings(findings, json.loads(Path(args.mappings).read_text(encoding="utf-8")))
    payload: Any = {**raw, "findings": mapped} if isinstance(raw, dict) else mapped
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
