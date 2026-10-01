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
    from .rule_engine import RuleError
    try:
        return _compile_source(source, config_dir=config_dir)
    except RuleError as exc:
        return Compilation(None, [Diagnostic('E_CONFIG', str(exc))])


def _compile_source(source: str, *, config_dir=None) -> Compilation:
    diagnostics: list[Diagnostic] = []
    arch = None

    def error(rule, line=1, column=1, subject=None, **values):
        code, message = render_error(config["errors"], rule, architecture=arch, subject=subject, **values)
        from .diagnostics import diagnostic_context
        diagnostics.append(Diagnostic(code, message, line, column, diagnostic_context(arch, rule, values, subject)))

    try:
        config = load_config(config_dir)
    except ConfigurationError as exc:
        return Compilation(None, [Diagnostic("E_CONFIG", str(exc))])
    PARENTS = {k: set(v["parents"]) for k, v in config["components"].items() if v["parents"]}
    PROPERTIES = {k: set(v["properties"]) for k, v in config["components"].items()}
    RELATIONS = {k: (set(v["sources"]), set(v["targets"])) for k, v in config["relationships"].items()}
    try:
        root = parser(tuple(PROPERTIES), tuple(RELATIONS)).parse(source)
    except UnexpectedInput as exc:
        error('E_SYNTAX', line=exc.line, column=exc.column, context=exc.get_context(source).strip())
        return Compilation(None, diagnostics)

    arch_tree, *scenario_trees = root.children
    arch = Architecture(str(arch_tree.children[0]), errors=config["errors"], aws=config["aws"], cloud=config["cloud"], diagnostic_details=config["diagnostic_details"], component_definitions=config["components"], model_inputs=config["model_inputs"], rules=config["rules"])
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
    from .rule_engine import run
    run(arch, 'compiler_contracts', 'validate_architecture', arch, error, config)

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
        run(arch, 'compiler_contracts', 'validate_scenario', arch, error, config, scenario)
    from .scaling import validate_scaling
    validate_scaling(arch, error)
    from .cache_connections import validate_cache_connections
    validate_cache_connections(arch, error)
    from .runtime_resources import validate_runtime
    validate_runtime(arch, error)
    return Compilation(arch, diagnostics)
