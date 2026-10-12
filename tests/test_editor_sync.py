from copy import deepcopy

import pytest

from polish.editor import STARTER, analyze
from polish.editor_sync import apply_canvas, CanvasEditError


def model(source):
    graph = analyze(source)['graph']
    return dict(nodes=[dict(id=n['name'], original=n['name'], name=n['name'], kind=n['kind']) for n in graph['nodes']],
                edges=[dict(original=i, source=e['source'], target=e['target'], kind=e['kind'], pattern=e['pattern']) for i, e in enumerate(graph['edges'])])


def test_noop_preserves_source_exactly():
    assert apply_canvas(STARTER, **model(STARTER)) == STARTER


def test_rename_updates_descendants_and_scenarios_without_touching_comments_or_strings():
    source = STARTER.replace('path = "/health"', 'path = "/API"') + '\n// API must stay in this comment\n'
    scene = model(source)
    scene['nodes'][1]['name'] = 'Gateway'
    result = apply_canvas(source, **scene)
    assert 'service Gateway' in result and 'entry = Gateway' in result
    assert 'reaches Gateway.health' in result
    assert '// API must stay' in result and 'path = "/API"' in result
    assert analyze(result, run_scenarios=True)['ok']


def test_scoped_rename_and_duplicate_short_names():
    source = '''architecture Test {
      compute_pool Host { instances = 1 }
      service A { hosted_on Host operation get {} }
      service B { hosted_on Host operation get { invokes A.get } }
    }'''
    scene = model(source)
    next(n for n in scene['nodes'] if n['name'] == 'A.get')['name'] = 'A.read'
    result = apply_canvas(source, **scene)
    assert 'operation read' in result and 'invokes A.read' in result
    assert 'operation get' in result and analyze(result)['ok']


def test_add_component_and_reconnect_preserves_edge_options():
    source = STARTER.replace('hosted_on Servers', 'hosted_on Servers')
    scene = model(source)
    scene['nodes'].append(dict(id='new', name='MoreServers', kind='compute_pool', body='instances = 3'))
    scene['edges'][0]['target'] = 'new'
    result = apply_canvas(source, **scene)
    assert 'API hosted_on MoreServers' in result and 'compute_pool MoreServers { instances = 3 }' in result
    assert analyze(result, run_scenarios=True)['ok']


def test_delete_container_cascades_children_but_does_not_remove_scenarios():
    scene = model(STARTER)
    scene['nodes'] = [n for n in scene['nodes'] if n['name'] != 'API']
    scene['edges'] = []
    result = apply_canvas(STARTER, **scene)
    assert 'service API' not in result and 'operation health' not in result
    assert 'scenario "Health check"' in result
    assert not analyze(result)['ok']


def test_deleting_optional_relationship_and_isolated_component():
    source = 'architecture Test { compute_pool Host { instances = 1 } role Admin role User }'
    scene = model(source)
    scene['nodes'] = [n for n in scene['nodes'] if n['name'] != 'User']
    result = apply_canvas(source, **scene)
    assert 'role User' not in result and analyze(result)['ok']


def test_reject_invalid_metadata_and_body_injection():
    scene = model(STARTER)
    scene['nodes'][0]['name'] = 'Bad name'
    with pytest.raises(CanvasEditError): apply_canvas(STARTER, **scene)
    scene = model(STARTER)
    scene['nodes'].append(dict(id='new', name='Bad', kind='role', body='} role Injected {'))
    with pytest.raises(CanvasEditError): apply_canvas(STARTER, **scene)


def test_new_nested_component_in_existing_parent():
    scene = model(STARTER)
    scene['nodes'].append(dict(id='new', name='API.ping', kind='operation', body='method = GET path = "/ping"'))
    result = apply_canvas(STARTER, **scene)
    assert 'API.ping' in {n['name'] for n in analyze(result)['graph']['nodes']}


def test_retarget_plain_and_path_routes():
    source = '''architecture Test {
      cdn Edge { route "/old/*" to First }
      gateway Gateway { routes_to First }
      compute_pool First { instances = 1 }
      compute_pool Second { instances = 2 }
    }'''
    scene = model(source)
    scene['edges'][0].update(target='Second', pattern='/new/*')
    scene['edges'][1]['target'] = 'Second'
    result = apply_canvas(source, **scene)
    assert 'route "/new/*" to Second' in result
    assert 'Gateway routes_to Second' in result and analyze(result)['ok']


def test_bare_parent_accepts_multiple_new_nested_nodes():
    source = 'architecture Test { frontend Site { rendering = static loaded_from Assets } artifact_store Assets { kind = object_store } }'
    scene = model(source)
    scene['nodes'].extend([dict(id='a', name='Site.One', kind='page'), dict(id='b', name='Site.Two', kind='page')])
    assert analyze(apply_canvas(source, **scene))['ok']


def test_changed_edge_keeps_options_and_comments_on_untouched_statements():
    source = '''architecture Test {
      compute_pool Host { instances = 1 }
      service A { hosted_on Host operation get { invokes B.get { protocol = http } } }
      service B { hosted_on Host operation get {} operation post {} }
    }'''
    scene = model(source)
    edge = next(e for e in scene['edges'] if e['kind'] == 'invokes')
    edge['target'] = 'B.post'
    result = apply_canvas(source, **scene)
    assert 'A.get invokes B.post { protocol = http }' in result and analyze(result)['ok']
