"""Parse and type-check a Polish architecture without executing user code."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib.resources import files

from lark import Lark, Tree, UnexpectedInput

from .configuration import load_config, ConfigurationError
from .errors import render_error
from .model import Architecture, Compilation, Diagnostic, Edge, Field, Node, Scenario


@lru_cache(maxsize=8)
def parser(kinds, relations) -> Lark:
    grammar = files("polish").joinpath("grammar.lark").read_text()
    grammar = grammar.replace("__COMPONENT_KINDS__", "|".join(re.escape(k) for k in kinds))
    grammar = grammar.replace("__RELATION_KINDS__", "|".join(re.escape(k) for k in relations))
    return Lark(grammar,
                parser="lalr", propagate_positions=True, maybe_placeholders=False)


def reference(tree: Tree) -> str:
    return ".".join(map(str, tree.children))


def value(tree: Tree):
    if tree.data == "string":
        return json.loads(str(tree.children[0]))
    if tree.data == "number":
        raw = str(tree.children[0])
        return float(raw) if any(c in raw.lower() for c in ".e") else int(raw)
    if tree.data == "interval":
        return tuple(int(part.strip()) for part in str(tree.children[0]).split(".."))
    if tree.data in ("true", "false"):
        return tree.data == "true"
    return reference(tree.children[0])


def compile_source(source: str, *, config_dir=None) -> Compilation:
    diagnostics: list[Diagnostic] = []

    def error(rule, line=1, column=1, **values):
        code, message = render_error(config["errors"], rule, **values)
        diagnostics.append(Diagnostic(code, message, line, column))

    try:
        config = load_config(config_dir)
    except ConfigurationError as exc:
        return Compilation(None, [Diagnostic("E_CONFIG", str(exc))])
    PARENTS = {k: set(v["parents"]) for k, v in config["components"].items() if v["parents"]}
    PROPERTIES = {k: set(v["properties"]) for k, v in config["components"].items()}
    RELATIONS = {k: (set(v["sources"]), set(v["targets"])) for k, v in config["relationships"].items()}
    NETWORK = set(config["network"])
    BOOLS = set(config["booleans"])
    METHODS = set(config["methods"])
    try:
        root = parser(tuple(PROPERTIES), tuple(RELATIONS)).parse(source)
    except UnexpectedInput as exc:
        error('E_SYNTAX', line=exc.line, column=exc.column, context=exc.get_context(source).strip())
        return Compilation(None, diagnostics)

    arch_tree, *scenario_trees = root.children
    arch = Architecture(str(arch_tree.children[0]), errors=config["errors"], aws=config["aws"], cloud=config["cloud"])
    pending: list[tuple[Tree, str | None]] = []

    def properties(items):
        result = {}
        for item in items:
            if item.data != "property":
                continue
            key = str(item.children[0])
            if key in result:
                error('E_DUPLICATE_PROPERTY', line=item.meta.line, key=key)
            result[key] = value(item.children[1])
        return result

    def declarations(items, parent=None):
        for item in items:
            if item.data != "declaration":
                pending.append((item, parent))
                continue
            kind, short, *body = item.children
            name = f"{parent}.{short}" if parent else str(short)
            if name in arch.nodes:
                error('E_DUPLICATE_NAME', line=item.meta.line, name=name)
                continue
            node = Node(name, str(kind), parent, item.meta.line, properties(body))
            arch.nodes[name] = node
            parent_kind = arch.nodes[parent].kind if parent else None
            if (kind in PARENTS and parent_kind not in PARENTS[kind]) or (kind not in PARENTS and parent):
                error('E_CONTAINMENT', line=node.line, kind=kind, parent_kind_or_architecture=parent_kind or 'architecture')
            declarations(body, name)

    declarations(arch_tree.children[1:])
    for item, scope in pending:
        line = item.meta.line
        node = arch.nodes.get(scope)
        if item.data == "property":
            if not node:
                error('E_PROPERTY', line=line)
        elif item.data == "relation":
            parts = list(item.children)
            options = properties(parts.pop().children) if isinstance(parts[-1], Tree) and parts[-1].data == "edge_options" else {}
            raw_source, kind, raw_target = ((reference(parts[0]), str(parts[1]), reference(parts[2]))
                                           if len(parts) == 3 else (scope, str(parts[0]), reference(parts[1])))
            src = arch.resolve(raw_source, scope) if len(parts) == 3 else scope
            dst = arch.resolve(raw_target, scope)
            if not src or not dst:
                error('E_REFERENCE_3', line=line, raw_source=raw_source, raw_target=raw_target)
            else:
                if "via" in options:
                    via = arch.resolve(str(options["via"]), scope)
                    if not via:
                        error('E_REFERENCE_5', line=line, options_via=options['via'])
                    else:
                        options["via"] = via
                arch.edges.append(Edge(src, kind, dst, line, properties=options))
        elif item.data == "route":
            dst = arch.resolve(reference(item.children[1]), scope)
            if not scope or not dst:
                error('E_REFERENCE_4', line=line)
            else:
                pattern = json.loads(str(item.children[0]))
                arch.edges.append(Edge(scope, "routes_to", dst, line, pattern))
        elif item.data in {"role_requirement", "auth_requirement"}:
            if not node or node.kind not in {"frontend", "page", "service", "operation"}:
                error('E_REQUIREMENT', line=line)
            elif item.data == "auth_requirement":
                node.requirements.append("authenticated")
            else:
                role = arch.resolve(reference(item.children[0]), scope)
                if not role or arch.nodes[role].kind != "role":
                    error('E_ROLE_2', line=line)
                else:
                    node.requirements.append(role)
        elif item.data == "field":
            if not node or node.kind not in {"table", "document", "collection"}:
                error('E_FIELD', line=line)
            else:
                name, field_type = map(str, item.children[:2])
                if name in node.fields:
                    error('E_DUPLICATE_FIELD', line=line, name=name)
                if field_type not in config["field_types"]:
                    error('E_FIELD_TYPE', line=line, field_type=field_type)
                node.fields[name] = Field(field_type, len(item.children) == 3)

    from .vendors import prepare_products
    prepare_products(arch, error, config)
    seen_edges = set()
    for edge in arch.edges:
        src, dst = arch.nodes[edge.source], arch.nodes[edge.target]
        allowed_src, allowed_dst = RELATIONS[edge.kind]
        if src.kind not in allowed_src or dst.kind not in allowed_dst:
            error('E_RELATION_TYPE', line=edge.line, src_kind=src.kind, edge_kind=edge.kind, dst_kind=dst.kind)
        key = (edge.source, edge.kind, edge.target, edge.pattern)
        if key in seen_edges:
            error('E_DUPLICATE_RELATION', line=edge.line, edge_source=edge.source, edge_target=edge.target)
        seen_edges.add(key)
        if edge.kind == "hosted_on":
            expected = "compute_pool" if src.kind == "gateway" else config["hosting"].get(src.kind)
            if dst.kind != expected:
                error('E_HOST_TYPE', line=edge.line, src_kind=src.kind, expected=expected)
        if edge.pattern is not None and not edge.pattern.startswith("/"):
            error('E_ROUTE', line=edge.line)

    for node in arch.nodes.values():
        p = node.properties
        for key, val in p.items():
            product_properties = arch.cloud.get(p.get("provider"), {}).get("products", {}).get(p.get("product"), {}).get("properties", [])
            if key not in PROPERTIES[node.kind] and key not in product_properties:
                error('E_PROPERTY_2', line=node.line, key=key, node_kind=node.kind, node_name=node.name)
            if key in BOOLS and type(val) is not bool:
                error('E_PROPERTY_TYPE', line=node.line, key=key)
            if key in config["nonnegative_integers"] and (type(val) is not int or val < 0):
                error('E_SCALE', line=node.line, key=key)
            if key == "partitions" and val != "managed" and (type(val) is not int or val < 1):
                error('E_SCALE_2', line=node.line)
            if key == "instances":
                bounds = val if isinstance(val, tuple) else (val, val)
                if any(type(x) is not int or x < 1 for x in bounds) or bounds[0] > bounds[1]:
                    error('E_SCALE_3', line=node.line)
            enums = {**config["enums"], "method": METHODS}
            if key in enums and (not isinstance(val, str) or val not in enums[key]):
                error('E_PROPERTY_VALUE', line=node.line, key=key, val=val)
        if "path" in p and (not isinstance(p["path"], str) or not p["path"].startswith("/")):
            error('E_ROUTE_2', line=node.line)
        if node.kind == "operation" and (("path" in p) != ("method" in p)):
            error('E_ENDPOINT', line=node.line)
        if p.get("tls_termination") is True and p.get("tls") is False:
            error('E_TLS_CONFIG', line=node.line)
        product = arch.cloud.get(p.get("provider"), {}).get("products", {}).get(p.get("product"), {})
        if node.kind in config["hosting"] and not product.get("managed") and len(arch.outgoing(node.name, "hosted_on")) != 1:
            error('E_HOST_COUNT', line=node.line, node_name=node.name)
        if node.kind in {"frontend", "page"}:
            sources = arch.outgoing(node.name, "loaded_from")
            inherited = arch.outgoing(node.parent, "loaded_from") if node.parent else []
            effective = sources or inherited
            rendering = p.get("rendering", arch.nodes[node.parent].properties.get("rendering") if node.parent else None)
            if len(effective) != 1:
                error('E_LOAD_COUNT', line=node.line, node_name=node.name)
            if rendering not in config["loading"]:
                error('E_RENDERING', line=node.line, node_name=node.name)
            elif len(effective) == 1:
                source_kind = arch.nodes[effective[0].target].kind
                allowed = config["loading"][rendering]
                if source_kind not in allowed:
                    error('E_LOAD_TYPE', line=node.line, rendering=rendering, source_kind=source_kind)
        if node.kind == "artifact_store" and p.get("kind") not in config["artifact_kinds"]:
            error('E_STORAGE_KIND', line=node.line)
        if node.kind == "database":
            kind = p.get("kind")
            if kind not in config["database_kinds"]:
                error('E_DATABASE_KIND', line=node.line, kinds=', '.join(config['database_kinds']))
            for child in arch.nodes.values():
                if child.parent == node.name and child.kind not in config["database_kinds"].get(kind, []):
                    error('E_DATA_MODEL', line=child.line, child_kind=child.kind, kind=kind)
            hosts = arch.outgoing(node.name, "hosted_on")
            if len(hosts) == 1:
                hp = arch.nodes[hosts[0].target].properties
                replicas, available = p.get("read_replicas", 0), hp.get("replicas")
                if type(replicas) is int and type(available) is int and replicas > available:
                    error('E_CAPACITY', line=node.line, node_name=node.name, replicas=replicas, available=available)
                partitions, capacity = p.get("partitions"), hp.get("partitions")
                if type(partitions) is int and type(capacity) is int and partitions > capacity:
                    error('E_CAPACITY_2', line=node.line, node_name=node.name)
        routes = arch.outgoing(node.name, "routes_to")
        patterns = [e.pattern for e in routes]
        if len(patterns) != len(set(patterns)):
            error('E_AMBIGUOUS_ROUTE', line=node.line, node_name=node.name)

    from .storage import validate_storage
    validate_storage(arch, error, config)

    for item in scenario_trees:
        name = json.loads(str(item.children[0]))
        req = properties(item.children[1].children)
        exp = properties(item.children[2].children)
        assertions = [(str(a.children[0]), reference(a.children[1])) for a in item.children[2].children if a.data == "assertion"]
        scenario = Scenario(name, req, exp, assertions, item.meta.line)
        if any(s.name == name for s in arch.scenarios):
            error('E_DUPLICATE_SCENARIO', line=scenario.line, name=name)
        arch.scenarios.append(scenario)
        for key in req.keys() - {"entry", "protocol", "actor", "authenticated", "method", "path", "destination", "action", "via", "rate_rps", "scale", "cache_result", "burst_requests", "query_depth", "query_cost", "window_seconds", "image_pulls_rps", "concurrent_connections", "consumer_lag_seconds"}:
            error('E_SCENARIO', line=scenario.line, key=key)
        for key in exp.keys() - {"outcome", "error"}:
            error('E_SCENARIO_2', line=scenario.line, key=key)
        if exp.get("outcome") not in {"success", "denied", "error"}:
            error('E_SCENARIO_3', line=scenario.line)
        for key in ("entry", "destination", "actor", "action", "via"):
            if key in req and (not isinstance(req[key], str) or req[key] not in arch.nodes):
                error('E_REFERENCE', line=scenario.line, key=key, req_key=req[key])
        if "entry" not in req:
            error('E_SCENARIO_4', line=scenario.line)
        elif req["entry"] in arch.nodes and arch.nodes[req["entry"]].kind not in NETWORK | {"page", "frontend", "service", "operation", "artifact_store", "compute_pool"}:
            error('E_SCENARIO_10', line=scenario.line)
        if req.get("actor") in arch.nodes and arch.nodes[req["actor"]].kind != "role":
            error('E_ROLE', line=scenario.line)
        if "destination" in req and req.get("destination") in arch.nodes:
            entry = arch.nodes.get(req.get("entry"))
            if arch.nodes[req["destination"]].kind != "page" or not entry or entry.kind != "page":
                error('E_SCENARIO_11', line=scenario.line)
        if ("action" in req) != ("via" in req):
            error('E_SCENARIO_5', line=scenario.line)
        if "action" in req:
            entry = arch.nodes.get(req.get("entry"))
            action = arch.nodes.get(req["action"])
            via = arch.nodes.get(req.get("via"))
            if not entry or entry.kind not in {"page", "frontend"} or "destination" in req:
                error('E_SCENARIO_12', line=scenario.line)
            if action and action.kind != "operation":
                error('E_SCENARIO_13', line=scenario.line)
            if via and via.kind not in NETWORK | {"service", "compute_pool"}:
                error('E_SCENARIO_14', line=scenario.line)
        if req.get("protocol", "https") not in {"http", "https"}:
            error('E_SCENARIO_6', line=scenario.line)
        if req.get("method", "GET") not in METHODS:
            error('E_SCENARIO_7', line=scenario.line)
        if not isinstance(req.get("path", "/"), str) or not req.get("path", "/").startswith("/"):
            error('E_SCENARIO_8', line=scenario.line)
        if "authenticated" in req and type(req["authenticated"]) is not bool:
            error('E_SCENARIO_9', line=scenario.line)
        for _, target in assertions:
            if target not in arch.nodes:
                error('E_REFERENCE_2', line=scenario.line, target=target)
    from .scaling import validate_scaling
    validate_scaling(arch, error)
    from .cache_connections import validate_cache_connections
    validate_cache_connections(arch, error)
    from .runtime_resources import validate_runtime
    validate_runtime(arch, error)
    return Compilation(arch, diagnostics)
