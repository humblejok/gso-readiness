#!/usr/bin/env python3
"""Normalize heterogeneous Graphify-style exports into a conservative review graph.

Supported inputs:
- JSON (native)
- GraphML (stdlib)
- YAML/YML when PyYAML is installed

The adapter is intentionally conservative: unknown fields are retained as attributes,
and unknown relationship semantics are not upgraded into stronger meanings.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

NODE_GROUPS = {
    "nodes": "unknown",
    "vertices": "unknown",
    "entities": "entity",
    "files": "file",
    "modules": "module",
    "packages": "package",
    "classes": "class",
    "functions": "function",
    "methods": "method",
    "symbols": "symbol",
    "components": "component",
}
EDGE_GROUPS = {
    "edges": "unknown",
    "links": "unknown",
    "relationships": "unknown",
    "dependencies": "dependency",
    "calls": "call",
    "imports": "import",
    "references": "reference",
    "uses": "use",
}
SOURCE_KEYS = ("source", "from", "src", "origin", "caller", "dependent", "start")
TARGET_KEYS = ("target", "to", "dst", "destination", "callee", "dependency", "end")
ID_KEYS = ("id", "uid", "key", "qualified_name", "full_name", "name", "path")
NAME_KEYS = ("name", "qualified_name", "full_name", "label", "path", "id")
PATH_KEYS = ("path", "file", "filename", "source_file", "module_path")
TYPE_KEYS = ("type", "kind", "node_type", "entity_type", "category")


def load_input(path: Path):
    suffix = path.suffix.lower()
    if suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8")), "json"
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise SystemExit("YAML input requires PyYAML: pip install pyyaml") from exc
        return yaml.safe_load(path.read_text(encoding="utf-8")), "yaml"
    if suffix in {".graphml", ".xml"}:
        return load_graphml(path), "graphml"
    raise SystemExit(f"Unsupported input format: {suffix}. Use JSON, GraphML, or YAML.")


def load_graphml(path: Path):
    tree = ET.parse(path)
    root = tree.getroot()
    ns = {"g": "http://graphml.graphdrawing.org/xmlns"}
    keys = {}
    for key in root.findall("g:key", ns):
        keys[key.attrib.get("id", "")] = key.attrib.get("attr.name", key.attrib.get("id", ""))

    def data_map(el):
        out = {}
        for d in el.findall("g:data", ns):
            out[keys.get(d.attrib.get("key", ""), d.attrib.get("key", "data"))] = d.text
        return out

    graph = root.find("g:graph", ns)
    if graph is None:
        return {"nodes": [], "edges": []}
    nodes = []
    for n in graph.findall("g:node", ns):
        item = {"id": n.attrib.get("id")}
        item.update(data_map(n))
        nodes.append(item)
    edges = []
    for e in graph.findall("g:edge", ns):
        item = {
            "id": e.attrib.get("id"),
            "source": e.attrib.get("source"),
            "target": e.attrib.get("target"),
        }
        item.update(data_map(e))
        edges.append(item)
    return {"nodes": nodes, "edges": edges}


def json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [json_safe(v) for v in value[:500]]
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in list(value.items())[:500]}
    return str(value)


def first_value(item, keys):
    if not isinstance(item, dict):
        return None
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return None


def normalize_node(item, default_type, index, group_name):
    if not isinstance(item, dict):
        raw_id = str(item)
        return {
            "id": raw_id,
            "type": default_type,
            "name": raw_id,
            "path": None,
            "symbol": None,
            "attributes": {"raw_value": json_safe(item), "source_group": group_name},
        }
    raw_id = first_value(item, ID_KEYS)
    if raw_id is None:
        raw_id = f"{group_name}:{index}"
    node_type = first_value(item, TYPE_KEYS) or default_type
    name = first_value(item, NAME_KEYS) or str(raw_id)
    path = first_value(item, PATH_KEYS)
    symbol = item.get("symbol") or item.get("signature")
    return {
        "id": str(raw_id),
        "type": str(node_type),
        "name": str(name),
        "path": str(path) if path is not None else None,
        "symbol": str(symbol) if symbol is not None else None,
        "attributes": {**{k: json_safe(v) for k, v in item.items()}, "source_group": group_name},
    }


def iter_candidate_containers(root):
    if isinstance(root, dict):
        yield "root", root
        graph = root.get("graph")
        if isinstance(graph, dict):
            yield "graph", graph
        data = root.get("data")
        if isinstance(data, dict):
            yield "data", data


def collect_nodes(root):
    nodes = []
    seen_groups = set()
    for prefix, container in iter_candidate_containers(root):
        for group, default_type in NODE_GROUPS.items():
            value = container.get(group)
            if isinstance(value, list):
                marker = (id(value), group)
                if marker in seen_groups:
                    continue
                seen_groups.add(marker)
                for i, item in enumerate(value):
                    nodes.append(normalize_node(item, default_type, i, f"{prefix}.{group}"))
    if not nodes and isinstance(root, list):
        for i, item in enumerate(root):
            nodes.append(normalize_node(item, "unknown", i, "root"))

    # Ensure unique IDs while retaining original as alias.
    counts = Counter(n["id"] for n in nodes)
    running = defaultdict(int)
    for n in nodes:
        original = n["id"]
        if counts[original] > 1:
            running[original] += 1
            n["attributes"]["original_id"] = original
            n["id"] = f"{original}#{running[original]}"
    return nodes


def endpoint_value(edge, keys):
    value = first_value(edge, keys)
    if isinstance(value, dict):
        return first_value(value, ID_KEYS)
    return value


def normalize_edge(item, default_type, index, group_name):
    if not isinstance(item, dict):
        return None
    source = endpoint_value(item, SOURCE_KEYS)
    target = endpoint_value(item, TARGET_KEYS)
    if source is None or target is None:
        return None
    edge_type = first_value(item, TYPE_KEYS) or default_type
    edge_id = item.get("id") or f"{group_name}:{index}:{source}->{target}"
    return {
        "id": str(edge_id),
        "source": str(source),
        "target": str(target),
        "type": str(edge_type),
        "attributes": {**{k: json_safe(v) for k, v in item.items()}, "source_group": group_name},
    }


def collect_edges(root, nodes):
    edges = []
    seen_groups = set()
    for prefix, container in iter_candidate_containers(root):
        for group, default_type in EDGE_GROUPS.items():
            value = container.get(group)
            if isinstance(value, list):
                marker = (id(value), group)
                if marker in seen_groups:
                    continue
                seen_groups.add(marker)
                for i, item in enumerate(value):
                    edge = normalize_edge(item, default_type, i, f"{prefix}.{group}")
                    if edge:
                        edges.append(edge)

    # Discover adjacency lists embedded in nodes.
    raw_node_lookup = []
    for prefix, container in iter_candidate_containers(root):
        for group in NODE_GROUPS:
            value = container.get(group)
            if isinstance(value, list):
                raw_node_lookup.extend((f"{prefix}.{group}", i, item) for i, item in enumerate(value) if isinstance(item, dict))

    for group_name, i, item in raw_node_lookup:
        src = first_value(item, ID_KEYS)
        if src is None:
            continue
        for rel_key, rel_type in (("dependencies", "dependency"), ("calls", "call"), ("imports", "import"), ("references", "reference"), ("uses", "use")):
            rels = item.get(rel_key)
            if not isinstance(rels, list):
                continue
            for j, rel in enumerate(rels):
                if isinstance(rel, dict):
                    tgt = first_value(rel, TARGET_KEYS) or first_value(rel, ID_KEYS)
                else:
                    tgt = rel
                if tgt is None:
                    continue
                edges.append({
                    "id": f"{group_name}:{i}:{rel_key}:{j}:{src}->{tgt}",
                    "source": str(src),
                    "target": str(tgt),
                    "type": rel_type,
                    "attributes": {"source_group": group_name, "embedded_relation": rel_key, "raw": json_safe(rel)},
                })
    return edges


def resolve_endpoints(nodes, edges):
    aliases = defaultdict(list)
    for n in nodes:
        aliases[n["id"]].append(n["id"])
        for key in ("original_id", "name", "qualified_name", "full_name", "path"):
            value = n["attributes"].get(key)
            if value not in (None, ""):
                aliases[str(value)].append(n["id"])
        aliases[n["name"]].append(n["id"])
        if n.get("path"):
            aliases[n["path"]].append(n["id"])

    unresolved = []
    for e in edges:
        for side in ("source", "target"):
            raw = e[side]
            options = list(dict.fromkeys(aliases.get(raw, [])))
            if len(options) == 1:
                e[side] = options[0]
            elif raw not in {n["id"] for n in nodes}:
                unresolved.append({"edge_id": e["id"], "endpoint": side, "value": raw, "candidate_ids": options})
    return unresolved


def tarjan_scc(nodes, edges):
    node_ids = {n["id"] for n in nodes}
    adj = defaultdict(list)
    self_loops = []
    for e in edges:
        if e["source"] in node_ids and e["target"] in node_ids:
            adj[e["source"]].append(e["target"])
            if e["source"] == e["target"]:
                self_loops.append(e["id"])

    index = 0
    stack = []
    on_stack = set()
    indices = {}
    low = {}
    sccs = []

    def strongconnect(v):
        nonlocal index
        indices[v] = index
        low[v] = index
        index += 1
        stack.append(v)
        on_stack.add(v)
        for w in adj.get(v, []):
            if w not in indices:
                strongconnect(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], indices[w])
        if low[v] == indices[v]:
            comp = []
            while True:
                w = stack.pop()
                on_stack.remove(w)
                comp.append(w)
                if w == v:
                    break
            sccs.append(comp)

    for n in node_ids:
        if n not in indices:
            strongconnect(n)

    cyclic = [sorted(c) for c in sccs if len(c) > 1]
    # Single-node SCCs with self-loop are also cycles.
    self_loop_nodes = sorted({e["source"] for e in edges if e["source"] == e["target"] and e["source"] in node_ids})
    cyclic.extend([[n] for n in self_loop_nodes])
    cyclic.sort(key=lambda c: (-len(c), c))
    return cyclic, self_loops, sccs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Graphify export path")
    parser.add_argument("--output", required=True, help="Canonical JSON output path")
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        raise SystemExit(f"Input does not exist: {input_path}")
    root, fmt = load_input(input_path)
    nodes = collect_nodes(root)
    edges = collect_edges(root, nodes)
    unresolved = resolve_endpoints(nodes, edges)
    cycles, self_loops, sccs = tarjan_scc(nodes, edges)

    warnings = []
    if not nodes:
        warnings.append("No recognizable node collection was found; inspect the source export and adapt NODE_GROUPS/mapping rules.")
    if not edges:
        warnings.append("No recognizable edge collection was found; structural relationship analysis will be limited.")
    if unresolved:
        warnings.append(f"{len(unresolved)} edge endpoints could not be mapped unambiguously to normalized nodes.")

    top_keys = sorted(root.keys()) if isinstance(root, dict) else []
    node_types = Counter(n["type"] for n in nodes)
    edge_types = Counter(e["type"] for e in edges)

    payload = {
        "schema_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "path": os.path.relpath(input_path, Path.cwd()),
            "format": fmt,
            "top_level_keys": top_keys,
            "warnings": warnings,
        },
        "nodes": nodes,
        "edges": edges,
        "derived": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "node_types": dict(sorted(node_types.items())),
            "edge_types": dict(sorted(edge_types.items())),
            "cycles": [
                {"id": f"cycle:{i+1}", "nodes": comp, "size": len(comp)}
                for i, comp in enumerate(cycles)
            ],
            "strongly_connected_component_count": len(sccs),
            "self_loop_edge_ids": self_loops,
            "unresolved_edge_endpoints": unresolved,
        },
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {output_path} ({len(nodes)} nodes, {len(edges)} edges, {len(cycles)} cyclic components)")
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)


if __name__ == "__main__":
    main()
