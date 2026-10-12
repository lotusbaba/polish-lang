"""Lossless source patches for semantic canvas edits (never serialize runtime defaults)."""
import json
import re

from lark import Tree

from .compiler import compile_source, parser, reference
from .configuration import load_config


class CanvasEditError(ValueError):
    pass


def apply_canvas(source, nodes, edges, *, config_dir=None):
    """Return a candidate source. The caller compiles it before accepting it.

    Existing nodes/edges carry their original names/indexes. New nodes supply a
    declaration body. Untouched comments, strings, scenarios, and formatting stay
    verbatim; renames patch resolved references rather than text occurrences.
    """
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise CanvasEditError('Canvas nodes and relationships must be lists')
    compiled = compile_source(source, config_dir=config_dir)
    if not compiled.ok:
        raise CanvasEditError('Check & draw a valid specification before editing the canvas')
    arch = compiled.architecture
    config = load_config(config_dir)
    parse = parser(tuple(config['components']), tuple(config['relationships']))
    root = parse.parse(source)
    arch_tree = root.children[0]
    declarations, relations, refs = {}, [], []

    def walk(tree, scope=None, context=None):
        if tree.data == 'declaration':
            short = str(tree.children[1])
            scope = f'{scope}.{short}' if scope else short
            declarations[scope] = tree
        if tree.data in ('relation', 'route'):
            relations.append((tree, scope))
        if tree.data == 'property':
            # Only these properties contain component references in the language.
            key, val = tree.children
            if str(key) in {'entry', 'destination', 'actor', 'action', 'via'}:
                if val.data == 'symbol':
                    refs.append((val.children[0], scope, False))
                elif val.data == 'string':
                    refs.append((val.children[0], scope, True))
            return
        if tree.data == 'reference' and context in {'relation', 'route', 'role_requirement', 'assertion'}:
            refs.append((tree, scope, False))
            return
        for child in tree.children:
            if isinstance(child, Tree):
                walk(child, scope, tree.data)

    walk(arch_tree)
    for scenario in root.children[1:]:
        walk(scenario)
    # Compiler gathers statements before resolving edges, in this same preorder.
    if len(relations) != len(arch.edges):
        raise CanvasEditError('Cannot match the diagram relationships to this source')

    by_id, existing, names = {}, {}, set()
    for node in nodes:
        if not isinstance(node, dict):
            raise CanvasEditError('Invalid canvas component')
        identity, name, kind = node.get('id'), node.get('name'), node.get('kind')
        if not isinstance(identity, str) or identity in by_id:
            raise CanvasEditError('Canvas component IDs must be unique')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*', name, re.ASCII):
            raise CanvasEditError('Use a Polish component name, for example API or API.get')
        if kind not in config['components']:
            raise CanvasEditError(f'Unknown component kind: {kind}')
        original = node.get('original')
        if original is not None:
            if original not in arch.nodes or original in existing:
                raise CanvasEditError('Unknown or duplicated architecture component; use Check & draw')
            existing[original] = node
        by_id[identity] = dict(node)

    # Renaming a parent also renames descendants whose labels were not edited.
    renamed = {}
    for original in sorted(existing, key=lambda n: n.count('.')):
        item = existing[original]
        parent = arch.nodes[original].parent
        if parent and parent not in renamed:
            continue  # Deleting a container deletes its children.
        new_parent = renamed.get(parent)
        requested = item['name']
        if parent:
            if requested.rpartition('.')[0] not in {parent, new_parent}:
                raise CanvasEditError('Change containment in the source pane; canvas renames cannot move components')
            short = requested.rsplit('.', 1)[-1]
            requested = new_parent + '.' + short
        elif '.' in requested:
            raise CanvasEditError('Change containment in the source pane; canvas renames cannot move components')
        renamed[original] = requested
        by_id[item['id']]['name'] = requested

    deleted = set(arch.nodes) - set(renamed)
    for item in by_id.values():
        if item.get('original') in deleted:
            continue
        if item['name'] in names:
            raise CanvasEditError(f'Duplicate component: {item["name"]}')
        names.add(item['name'])

    patches, insertions = [], {}
    def patch(item, text):
        meta = item.meta if isinstance(item, Tree) else item
        patches.append((meta.start_pos, meta.end_pos, text))

    def insert(container, text):
        tree = declarations.get(container) if container else arch_tree
        if tree is None:
            raise CanvasEditError('Create the parent component before adding a nested component')
        position = tree.meta.end_pos
        if source[position - 1] == '}':
            insertions.setdefault(position - 1, []).append('\n  ' + text + '\n')
        else:
            insertions.setdefault(position, []).append('\n  ' + text + '\n')

    for original, tree in declarations.items():
        if original in deleted:
            if arch.nodes[original].parent not in deleted:
                patch(tree, '')
            continue
        item = existing[original]
        if renamed[original] != original:
            patch(tree.children[1], renamed[original].rsplit('.', 1)[-1])
        if item['kind'] != arch.nodes[original].kind:
            patch(tree.children[0], item['kind'])

    for item in by_id.values():
        if item.get('original') is not None:
            continue
        name, kind = item['name'], item['kind']
        parent, _, short = name.rpartition('.')
        old_parent = next((old for old, new in renamed.items() if new == parent), None)
        if parent and old_parent is None:
            raise CanvasEditError(f'Unknown parent: {parent}')
        body = item.get('body', '')
        if not isinstance(body, str):
            raise CanvasEditError('Component properties must be Polish source text')
        declaration = f'{kind} {short} {{ {body} }}'
        # Prevent a label from breaking out of its declaration or adding scenarios.
        try:
            check = parse.parse('architecture Canvas { ' + declaration + ' }')
            items = check.children[0].children[1:]
            if len(check.children) != 1 or len(items) != 1 or items[0].data != 'declaration':
                raise ValueError()
        except Exception as exc:
            raise CanvasEditError('Use a single component declaration in the shape label') from exc
        insert(old_parent, declaration)

    used_edges = set()
    for edge in edges:
        if not isinstance(edge, dict):
            raise CanvasEditError('Invalid canvas relationship')
        source_node, target_node = by_id.get(edge.get('source')), by_id.get(edge.get('target'))
        if not source_node or not target_node:
            raise CanvasEditError('Bind both ends of each architecture arrow to component shapes')
        if source_node.get('original') in deleted or target_node.get('original') in deleted:
            continue
        kind = edge.get('kind')
        if kind not in config['relationships']:
            raise CanvasEditError(f'Unknown relationship: {kind}')
        src, dst = source_node['name'], target_node['name']
        index = edge.get('original')
        if index is not None:
            if type(index) is not int or not 0 <= index < len(relations) or index in used_edges:
                raise CanvasEditError('Unknown or duplicated relationship; use Check & draw')
            used_edges.add(index)
            old = arch.edges[index]
            tree, scope = relations[index]
            if (kind, src, dst, edge.get('pattern')) == (old.kind, renamed.get(old.source), renamed.get(old.target), old.pattern):
                continue  # Reference patches below handle names without rewriting options.
            if kind == 'routes_to' and edge.get('pattern') is not None:
                if src != renamed.get(scope):
                    raise CanvasEditError('Move path routes between components in the source pane')
                pattern = edge.get('pattern')
                if not isinstance(pattern, str):
                    raise CanvasEditError('A routes_to arrow needs a path, for example routes_to /api/*')
                patch(tree, f'route {json.dumps(pattern)} to {dst}')
            else:
                options = next((c for c in tree.children if isinstance(c, Tree) and c.data == 'edge_options'), None)
                suffix = ''
                if options:
                    suffix = source[options.meta.start_pos:options.meta.end_pos]
                    replacements = []
                    for ref, ref_scope, quoted in refs:
                        meta = ref.meta if isinstance(ref, Tree) else ref
                        if options.meta.start_pos <= meta.start_pos < options.meta.end_pos:
                            raw = json.loads(str(ref)) if quoted else reference(ref)
                            resolved = arch.resolve(raw, ref_scope)
                            if resolved in renamed and renamed[resolved] != resolved:
                                replacements.append((meta.start_pos - options.meta.start_pos, meta.end_pos - options.meta.start_pos,
                                                     json.dumps(renamed[resolved]) if quoted else renamed[resolved]))
                    for start, end, text in sorted(replacements, reverse=True):
                        suffix = suffix[:start] + text + suffix[end:]
                    suffix = ' ' + suffix
                patch(tree, f'{src} {kind} {dst}{suffix}')
        elif kind == 'routes_to' and edge.get('pattern') is not None:
            pattern = edge.get('pattern')
            if not isinstance(pattern, str):
                raise CanvasEditError('A routes_to arrow needs a path')
            container = source_node.get('original')
            if not container:
                raise CanvasEditError('Create the component before adding its path routes')
            insert(container, f'route {json.dumps(pattern)} to {dst}')
        else:
            insert(None, f'{src} {kind} {dst}')

    for index, (tree, scope) in enumerate(relations):
        if index not in used_edges and scope not in deleted:
            patch(tree, '')

    # Resolve against the original scopes, and emit full names so renames cannot
    # accidentally bind to a same-named sibling or a newly introduced component.
    for item, scope, quoted in refs:
        if scope in deleted:
            continue
        raw = json.loads(str(item)) if quoted else reference(item)
        resolved = arch.resolve(raw, scope)
        if resolved in renamed and renamed[resolved] != resolved:
            patch(item, json.dumps(renamed[resolved]) if quoted else renamed[resolved])

    # A whole removed/replaced declaration or edge wins over patches inside it.
    kept = []
    for start, end, text in sorted(patches, key=lambda p: (p[0], -p[1])):
        if kept and start < kept[-1][1]:
            continue
        kept.append((start, end, text))
    bare_ends = {t.meta.end_pos for t in declarations.values() if source[t.meta.end_pos - 1] != '}'}
    for pos, texts in insertions.items():
        if pos in bare_ends:
            texts = [' {' + ''.join(texts) + '}']
        if not any(start <= pos < end for start, end, _ in kept):
            kept.append((pos, pos, ''.join(texts)))
    for start, end, text in sorted(kept, reverse=True):
        source = source[:start] + text + source[end:]
    return source
