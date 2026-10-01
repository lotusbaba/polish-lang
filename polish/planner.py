"""Conservative dependency impact and scenario-backed change advice."""
from collections import deque
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path
import json

from .compiler import compile_source
from .configuration import ConfigurationError
from .simulator import simulate


def semantic(value):
    data = asdict(value)
    data.pop('line', None)
    return data


def graph_diff(before, after):
    changes = []
    for name in sorted(before.nodes.keys() | after.nodes.keys()):
        old = semantic(before.nodes[name]) if name in before.nodes else None
        new = semantic(after.nodes[name]) if name in after.nodes else None
        if old != new:
            changes.append(dict(component=name, before=old, after=new))
    def edges(arch):
        return {json.dumps(semantic(e), sort_keys=True): semantic(e) for e in arch.edges}
    old, new = edges(before), edges(after)
    return dict(components=changes, removed_edges=[old[k] for k in sorted(old.keys()-new.keys())],
                added_edges=[new[k] for k in sorted(new.keys()-old.keys())])


def impact(before, after, changes):
    adjacency = {}
    def link(source, target, kind):
        adjacency.setdefault(source, set()).add((target, kind))
        adjacency.setdefault(target, set()).add((source, 'reverse:' + kind))
    for arch in (before, after):
        for edge in arch.edges:
            link(edge.source, edge.target, edge.kind)
            if 'via' in edge.properties:
                link(edge.source, edge.properties['via'], 'via')
        for node in arch.nodes.values():
            if node.parent:
                link(node.parent, node.name, 'contains')
    seeds = {c['component'] for c in changes['components']}
    for edge in changes['removed_edges'] + changes['added_edges']:
        seeds.update((edge['source'], edge['target']))
        if 'via' in edge['properties']:
            seeds.add(edge['properties']['via'])
    paths = {name: [] for name in sorted(seeds)}
    queue = deque(sorted(seeds))
    while queue:
        source = queue.popleft()
        for target, kind in sorted(adjacency.get(source, ())):
            if target not in paths:
                paths[target] = paths[source] + [dict(source=source, relation=kind, target=target)]
                queue.append(target)
    return [dict(component=name, changed=name in seeds, path=path) for name, path in sorted(paths.items())]


def load_rules(directory):
    root = Path(directory) if directory else files('polish').joinpath('config')
    try:
        rules = json.loads(root.joinpath('decisions.json').read_text())['rules']
        if not isinstance(rules, dict):
            raise ValueError('rules must be an object')
        for code, rule in rules.items():
            if not isinstance(rule, dict) or set(rule) != {'suggestions'} or not isinstance(rule['suggestions'], list) or not all(isinstance(s, str) for s in rule['suggestions']):
                raise ValueError(f'Invalid decision rule {code}')
        return rules
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ConfigurationError(f'Invalid decisions.json: {exc}') from exc


def scenario_source(scenario, name):
    def literal(value):
        return json.dumps(value)
    request = ' '.join(f'{k} = {literal(v)}' for k, v in scenario.request.items())
    expected = ' '.join(f'{k} = {literal(v)}' for k, v in scenario.expected.items())
    assertions = ' '.join(f'{k} {v}' for k, v in scenario.assertions)
    return f'\nscenario {literal(name)} {{ request {{ {request} }} expect {{ {expected} {assertions} }} }}'


def plan(before_source, after_source, *, config_dir=None, scenario_name=None):
    rules = load_rules(config_dir)
    before = compile_source(before_source, config_dir=config_dir)
    after = compile_source(after_source, config_dir=config_dir)
    report = dict(ok=False, diagnostics=[], changes=None, impact=[], comparisons=[], findings=[],
                  limitations=['Impact paths indicate possible effects, not proven failures.',
                               'Suggestions are alternatives for review; no candidate edits are applied or validated.',
                               'Simulations stop at the first runtime failure; further bottlenecks may remain.'])
    for label, compilation in [('before', before), ('after', after)]:
        for diagnostic in compilation.diagnostics:
            report['diagnostics'].append(dict(side=label, **diagnostic.to_dict()))
            report['findings'].append(dict(category='required', side=label, code=diagnostic.code,
                                           evidence=diagnostic.message, suggestions=rules.get(diagnostic.code, {}).get('suggestions', [])))
    if not before.ok or not after.ok:
        return report
    report['changes'] = graph_diff(before.architecture, after.architecture)
    report['impact'] = impact(before.architecture, after.architecture, report['changes'])
    scenarios = {}
    for label, arch in [('before', before.architecture), ('after', after.architecture)]:
        for scenario in arch.scenarios:
            if scenario_name is None or scenario.name == scenario_name:
                key = json.dumps(semantic(scenario), sort_keys=True)
                scenarios.setdefault(key, (label, scenario))
    names = {s.name for a in (before.architecture, after.architecture) for s in a.scenarios}
    evaluation_name = "__polish_plan_evaluation__"
    while evaluation_name in names:
        evaluation_name += "_"
    for origin, scenario in scenarios.values():
        results = []
        for source in (before_source, after_source):
            checked = compile_source(source + scenario_source(scenario, evaluation_name), config_dir=config_dir)
            if not checked.ok:
                results.append(dict(passed=False, outcome='unavailable', error='SCENARIO_INCOMPATIBLE',
                                    diagnostics=[d.to_dict() for d in checked.diagnostics]))
            else:
                result = simulate(checked.architecture, checked.architecture.scenarios[-1]).to_dict()
                result["scenario"] = scenario.name
                results.append(result)
        old, new = results
        status = ('unchanged_pass' if old['passed'] else 'resolved') if new['passed'] else ('introduced' if old['passed'] else 'persists')
        report['comparisons'].append(dict(scenario=scenario.name, origin=origin, request=scenario.request,
                                          status=status, before=old, after=new))
        if not new['passed']:
            code = new.get('error') or 'ASSERTION_FAILED'
            report['findings'].append(dict(category='needs_information' if code == 'SCALING_MODEL_INCOMPLETE' else 'required',
                scenario=scenario.name, status=status, code=code,
                evidence=new.get('trace', [])[-1:] + new.get('failures', []) + [d['message'] for d in new.get('diagnostics', [])],
                budgets=new.get('budgets', []), capacity=new.get('capacity', []),
                suggestions=rules.get(code, {}).get('suggestions', ['Review this scenario and its failing requirement.'])))
    observed = {name for comparison in report['comparisons'] for name in comparison['after'].get('reached', [])}
    unobserved = sorted({n['component'] for n in report['impact'] if n['component'] in after.architecture.nodes} - observed)
    if scenarios and unobserved:
        report['findings'].append(dict(category='needs_information', code='UNOBSERVED_IMPACT',
            components=unobserved, evidence='These potentially affected components were not reached by the proposed-design runs. Add scenarios or resolve earlier failures to evaluate them.'))
    if not scenarios:
        report['findings'].append(dict(category='needs_information', code='NO_SCENARIOS',
                                      evidence='No matching scenarios; add workloads and expected behavior to evaluate the proposal.'))
    report['ok'] = bool(scenarios) and all(c['after']['passed'] for c in report['comparisons'])
    return report
