"""Bounded, configuration-driven candidate search with source-preserving edits."""
from collections import deque
from importlib.resources import files
import json
import math
from pathlib import Path

from lark import Tree

from .compiler import compile_source, parser, value
from .configuration import load_config, ConfigurationError
from .planner import plan
from .recommendations import RecommendationError, recommend
from .rule_engine import run


def load_settings(config_dir):
    root = Path(config_dir) if config_dir else files('polish').joinpath('config')
    try:
        settings = json.loads(root.joinpath('generation.json').read_text())
        if set(settings) != {'max_candidates', 'max_rounds', 'max_edits', 'headroom_factors'}:
            raise ValueError()
        for key, maximum in [('max_candidates', 10), ('max_rounds', 10), ('max_edits', 32)]:
            if type(settings[key]) is not int or not 1 <= settings[key] <= maximum:
                raise ValueError()
        factors = settings['headroom_factors']
        if not isinstance(factors, list) or not 1 <= len(factors) <= 5 or any(
                type(f) not in (int, float) or not math.isfinite(f) or not 1 <= f <= 10 for f in factors):
            raise ValueError()
        return settings
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ConfigurationError('Invalid generation.json search settings') from exc


def validate_constraints(constraints):
    if constraints is None:
        return {'locked': [], 'bounds': []}
    if not isinstance(constraints, dict) or set(constraints) - {'locked', 'bounds'}:
        raise RecommendationError('Generation constraints accept locked and bounds lists')
    result = {'locked': constraints.get('locked', []), 'bounds': constraints.get('bounds', [])}
    for category, rows in result.items():
        if not isinstance(rows, list):
            raise RecommendationError('Generation constraints must be lists')
        seen = set()
        for row in rows:
            allowed = {'component', 'property'} | ({'min', 'max'} if category == 'bounds' else set())
            if not isinstance(row, dict) or set(row) - allowed or not all(isinstance(row.get(k), str) and row[k] for k in ('component', 'property')):
                raise RecommendationError('Invalid generation constraint')
            key = row['component'], row['property']
            if key in seen:
                raise RecommendationError('Duplicate generation constraint')
            seen.add(key)
            if category == 'bounds':
                values = [row[k] for k in ('min', 'max') if k in row]
                if not values or any(type(v) not in (int, float) or not math.isfinite(v) for v in values) or row.get('min', -math.inf) > row.get('max', math.inf):
                    raise RecommendationError('Invalid generation bounds')
    return result


def property_spans(source, config):
    tree = parser(tuple(config['components']), tuple(config['relationships'])).parse(source)
    spans = {}
    def visit(declaration, parent=None):
        name = str(declaration.children[1])
        qualified = parent + '.' + name if parent else name
        for child in declaration.children[2:]:
            if not isinstance(child, Tree):
                continue
            if child.data == 'declaration':
                visit(child, qualified)
            elif child.data == 'property':
                token = child.children[1]
                spans[qualified, str(child.children[0])] = (token.meta.start_pos, token.meta.end_pos, value(token))
    for statement in tree.children[0].children[1:]:
        if isinstance(statement, Tree) and statement.data == 'declaration':
            visit(statement)
    return spans


def apply_edits(source, edits, config, constraints):
    """Replace only existing declaration values, never scenario text or graph structure."""
    spans = property_spans(source, config)
    locked = {(r['component'], r['property']) for r in constraints['locked']}
    bounds = {(r['component'], r['property']): r for r in constraints['bounds']}
    replacements, changes, seen = [], [], set()
    if not isinstance(edits, list) or not edits:
        raise RecommendationError('Recipe must return a nonempty edit list')
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {'component', 'property', 'value', 'reason'}:
            raise RecommendationError('Invalid generated edit')
        if any(not isinstance(edit[k], str) or not edit[k] for k in ('component', 'property', 'reason')):
            raise RecommendationError('Generated edit names and reason must be nonempty strings')
        key = edit['component'], edit['property']
        if key in seen:
            raise RecommendationError('Recipe edits a property more than once')
        seen.add(key)
        if key not in spans:
            raise RecommendationError('Recipe target is not an explicitly declared property')
        start, end, old = spans[key]
        new = edit['value']
        if old == new:
            continue
        if key in locked:
            raise RecommendationError('Property is locked by generation constraints')
        values = new if isinstance(new, tuple) else (new,)
        if len(values) not in (1, 2) or any(type(v) is not int or v < 1 for v in values) or len(values) == 2 and values[0] > values[1]:
            raise RecommendationError('Generated capacity must be a positive integer or ordered range')
        limits = bounds.get(key, {})
        if any(v < limits.get('min', -math.inf) or v > limits.get('max', math.inf) for v in values):
            raise RecommendationError('Generated value violates generation bounds')
        replacement = '..'.join(str(v) for v in values)
        replacements.append((start, end, replacement))
        changes.append({**edit, 'before': old})
    if not changes:
        raise RecommendationError('Recipe makes no change')
    for start, end, replacement in sorted(replacements, reverse=True):
        source = source[:start] + replacement + source[end:]
    return source, changes


def generate_candidates(before_source, proposed_source, *, config_dir=None, constraints=None):
    config = load_config(config_dir)
    settings = load_settings(config_dir)
    constraints = validate_constraints(constraints)
    compiled = compile_source(proposed_source, config_dir=config_dir)
    initial = plan(before_source, proposed_source, config_dir=config_dir)
    result = {'candidates': [], 'skipped': [], 'settings': settings, 'constraints': constraints,
              'status': 'no_candidates', 'truncated': False}
    if not compiled.ok or initial['changes'] is None:
        result['status'] = 'invalid_architecture'
        return result
    spans = property_spans(proposed_source, config)
    for row in constraints['locked'] + constraints['bounds']:
        if (row['component'], row['property']) not in spans:
            raise RecommendationError('Constraint names an unknown or implicit property')
    if initial['ok']:
        result['status'] = 'already_satisfied'
        return result
    queue = deque([(proposed_source, compiled.architecture, initial, [], 0)])
    seen = {proposed_source}
    while queue and len(result['candidates']) < settings['max_candidates']:
        source, arch, report, history, depth = queue.popleft()
        if depth >= settings['max_rounds']:
            result['truncated'] = True
            continue
        proposals = run(arch, 'generation', 'propose', arch, report, settings)
        if not isinstance(proposals, list):
            raise RecommendationError('Generation recipe must return a list of edit batches')
        for edits in proposals:
            if len(result['candidates']) >= settings['max_candidates']:
                result['truncated'] = True
                break
            try:
                changed, changes = apply_edits(source, edits, config, constraints)
            except RecommendationError as exc:
                result['skipped'].append(str(exc))
                continue
            if changed in seen:
                continue
            seen.add(changed)
            history_next = history + changes
            if len(history_next) > settings['max_edits']:
                result['truncated'] = True
                continue
            checked = plan(before_source, changed, config_dir=config_dir)
            candidate = dict(id=f'generated-{len(result["candidates"])+1:02d}',
                description='; '.join(f"{c['component']}.{c['property']}: {c['before']} → {c['value']}. {c['reason']}" for c in history_next),
                source=changed, edits=history_next, eligible=checked['ok'], validation=checked)
            result['candidates'].append(candidate)
            if not checked['ok'] and checked['changes'] is not None:
                next_arch = compile_source(changed, config_dir=config_dir).architecture
                queue.append((changed, next_arch, checked, history_next, depth+1))
    if queue:
        result['truncated'] = True
    if result['candidates']:
        result['status'] = 'generated'
    return result


def export_candidates(generation, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    manifest = []
    for candidate in generation['candidates']:
        filename = candidate['id'] + '.polishd'
        (directory / filename).write_text(candidate['source'], encoding='utf-8')
        manifest.append({k: candidate[k] for k in ('id', 'description')} | {'file': filename})
    (directory / 'candidates.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    (directory / 'generation.json').write_text(json.dumps(generation, indent=2)+'\n', encoding='utf-8')


def recommend_generated(before_source, proposed_source, *, objective, model=None, config_dir=None,
                        scenario_name=None, evaluation=None, constraints=None):
    if not isinstance(objective, str) or not objective.strip():
        raise RecommendationError('A nonempty recommendation objective is required')
    generation = generate_candidates(before_source, proposed_source, config_dir=config_dir, constraints=constraints)
    if generation['candidates']:
        report = recommend(before_source, proposed_source, generation['candidates'], objective=objective,
                           model=model, config_dir=config_dir, scenario_name=scenario_name, evaluation=evaluation)
    else:
        report = plan(before_source, proposed_source, config_dir=config_dir, scenario_name=scenario_name)
    report['generation'] = generation
    return report
