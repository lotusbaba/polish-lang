import json
from pathlib import Path
import shutil

import pytest

from polish.candidate_generation import generate_candidates, recommend_generated, apply_edits
from polish.configuration import load_config
from polish.recommendations import RecommendationError
from polish.cli import main

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT/'examples/recommendations'


def sources(name):
    d = EXAMPLES/name
    return (d/'before.polishd').read_text(), (d/'proposed.polishd').read_text()


@pytest.mark.parametrize('name,field', [('cpu-capacity','instances'), ('connection-pools','connection_pool_size'), ('stream-retention','retention_messages')])
def test_generate_valid_candidates_without_changing_requirements(name, field):
    before, proposed = sources(name)
    result = generate_candidates(before, proposed)
    assert result['status'] == 'generated'
    assert len(result['candidates']) >= 2
    assert all(c['eligible'] for c in result['candidates'])
    for candidate in result['candidates']:
        assert all(e['property'] == field for e in candidate['edits'])
        assert candidate['source'].split('scenario ')[1:] == proposed.split('scenario ')[1:]


def test_downstream_failure_produces_combined_remedy():
    before, proposed = sources('connection-pools')
    # One host passes pools but lacks CPU. Scaling to two creates a connection failure.
    proposed = proposed.replace('instances = 8', 'instances = 1').replace('connection_pool_size = 50','connection_pool_size = 200').replace('cpu_ms = 1\n', 'cpu_ms = 200\n')
    result = generate_candidates(before, proposed)
    assert any(not c['eligible'] for c in result['candidates'])
    assert any(c['eligible'] and {e['property'] for e in c['edits']} == {'instances','connection_pool_size'} for c in result['candidates'])


def test_locks_and_bounds_are_enforced():
    before, proposed = sources('cpu-capacity')
    locked = generate_candidates(before, proposed, constraints={'locked':[{'component':'Apps','property':'instances'}]})
    assert not locked['candidates']
    limited = generate_candidates(before, proposed, constraints={'bounds':[{'component':'Apps','property':'instances','max':2}]})
    assert len(limited['candidates']) == 1
    assert limited['candidates'][0]['edits'][0]['value'] == 2
    with pytest.raises(RecommendationError):
        generate_candidates(before, proposed, constraints={'locked':[{'component':'Typo','property':'instances'}]})


def test_expected_failures_are_not_repaired():
    before, proposed = sources('cpu-capacity')
    expected = proposed.replace('outcome = success reaches API.calculate', 'outcome = error error = CAPACITY_EXCEEDED')
    assert generate_candidates(expected, expected)['status'] == 'already_satisfied'


def test_nested_property_edit_preserves_comments_and_unrelated_values():
    before, proposed = sources('connection-pools')
    proposed = proposed.replace('connection_pool_size = 50', 'connection_pool_size = 50 // leave comment')
    changed, edits = apply_edits(proposed, [{'component':'Api','property':'connection_pool_size','value':37,'reason':'test'}],load_config(),{'locked':[],'bounds':[]})
    assert changed == proposed.replace('connection_pool_size = 50', 'connection_pool_size = 37')
    assert edits[0]['before'] == 50


def test_model_only_gets_passing_generated_candidates():
    class Model:
        def choose(self, state, choices):
            assert all(c['eligible'] for c in state['eligible_candidates'])
            return dict(selected=next(iter(choices)), probabilities={k:1/len(choices) for k in choices},model='fake')
    report = recommend_generated(*sources('cpu-capacity'), objective='CPU headroom',model=Model())
    assert not report['ok']
    assert report['recommendations']['selection'] == 'model'


def test_search_limit_is_reported_and_recipe_is_configurable(tmp_path):
    target = tmp_path/'config'
    shutil.copytree(ROOT/'polish/config',target)
    settings = target/'generation.json'
    data = json.loads(settings.read_text());data['max_candidates']=1;settings.write_text(json.dumps(data))
    result = generate_candidates(*sources('cpu-capacity'),config_dir=target)
    assert len(result['candidates']) == 1 and result['truncated']
    recipe = target/'rules/generation.rules'
    recipe.write_text('def propose(arch, report, settings):\n    return []\n')
    assert not generate_candidates(*sources('cpu-capacity'),config_dir=target)['candidates']


def test_cli_exports_candidates_and_refuses_overwrite(tmp_path,capsys):
    d = EXAMPLES/'cpu-capacity';out=tmp_path/'generated'
    args=['plan',str(d/'before.polishd'),'--proposed',str(d/'proposed.polishd'),'--generate-candidates',str(out),'--objective','CPU headroom','--json']
    assert main(args) == 1
    result=json.loads(capsys.readouterr().out)
    assert result['recommendations']['status']=='awaiting_model'
    manifest=json.loads((out/'candidates.json').read_text())
    assert len(manifest)==2 and (out/manifest[0]['file']).exists()
    assert main(args)==1
    assert 'already exists' in capsys.readouterr().out


def test_missing_measurements_are_not_invented():
    before, proposed = sources('cpu-capacity')
    result = generate_candidates(before, proposed.replace('cpu_ms = 10', ''))
    assert result['status'] == 'no_candidates'
    assert not result['candidates']


def test_instance_ranges_preserve_order_and_maximum():
    before, proposed = sources('cpu-capacity')
    result = generate_candidates(before, proposed.replace('instances = 1', 'instances = 1..4'))
    assert result['candidates']
    assert result['candidates'][0]['edits'][0]['value'] == (2, 4)
    assert result['candidates'][0]['eligible']


def test_scenario_filter_does_not_hide_generation_requirements():
    before, proposed = sources('cpu-capacity')
    report = recommend_generated(before, proposed, objective='Headroom', scenario_name='missing')
    assert not report['ok']
    assert report['generation']['candidates']
    assert all(c['eligible'] for c in report['recommendations']['candidates'])


def test_zero_candidate_search_does_not_call_model():
    class Never:
        def choose(self, *args):
            raise AssertionError('No valid candidates should mean no model call')
    before, proposed = sources('cpu-capacity')
    report = recommend_generated(before, proposed, objective='Keep size', model=Never(),
        constraints={'locked':[{'component':'Apps','property':'instances'}]})
    assert report['generation']['status'] == 'no_candidates'
    assert not report['ok']
