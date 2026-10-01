import json
from pathlib import Path
import pytest
from polish.recommendations import recommend, load_candidates, JevDecisionModel, RecommendationError
from polish.cli import main

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / 'examples/recommendations'


class FakeModel:
    def __init__(self): self.calls = []
    def choose(self, state, choices):
        self.calls.append((state, choices))
        return dict(selected=next(iter(choices)), probabilities={c: 1/len(choices) for c in choices}, model='test-double')


def run(name='connection-pools', model=None, candidates=None, **kwargs):
    directory = EXAMPLES / name
    return recommend((directory/'before.polishd').read_text(), (directory/'proposed.polishd').read_text(),
                     candidates or load_candidates(directory/'candidates.json'), objective='Preserve current instance count', model=model, **kwargs)


@pytest.mark.parametrize('name', ['connection-pools','cpu-capacity','stream-retention'])
def test_examples_filter_failed_choices_and_preserve_original_failure(name):
    model = FakeModel()
    report = run(name, model)
    assert not report['ok']
    assert report['comparisons'][0]['status'] == 'introduced'
    advice = report['recommendations']
    assert advice['status'] == 'selected'
    assert advice['model_calls'] == 1
    assert len(model.calls) == 1
    assert len(model.calls[0][1]) == 2
    assert 'unchanged' not in model.calls[0][1]
    assert advice['confidence_interval'] is None
    assert advice['selection'] == 'model'


def test_single_eligible_choice_is_not_a_model_inference():
    candidates = load_candidates(EXAMPLES/'connection-pools/candidates.json')
    model = FakeModel()
    advice = run(model=model,candidates=candidates[:1]+candidates[-1:])['recommendations']
    assert advice['selection'] == 'deterministic_sole_candidate'
    assert advice['probabilities'] is None
    assert advice['model_calls'] == 0
    assert not model.calls


def test_weakening_candidate_expectations_does_not_hide_baseline_failure():
    candidates = load_candidates(EXAMPLES/'connection-pools/candidates.json')[-1:]
    candidates[0]['source'] = candidates[0]['source'].replace('outcome = success', 'outcome = error error = CONNECTION_BUDGET_EXCEEDED')
    advice = run(candidates=candidates)['recommendations']
    assert advice['status'] == 'no_valid_candidates'


def test_proposal_requirements_are_also_preserved():
    d=EXAMPLES/'connection-pools'
    before=(d/'before.polishd').read_text()
    proposed=(d/'proposed.polishd').read_text().replace('rate_rps = 10','rate_rps = 10000')
    report=recommend(before,proposed,load_candidates(d/'candidates.json'),objective='Keep capacity')
    assert report['recommendations']['status'] == 'no_valid_candidates'


@pytest.mark.parametrize('bad', [
    dict(selected='unchanged',probabilities={'smaller-pools':.5,'fewer-instances':.5},model='fake'),
    dict(selected='smaller-pools',probabilities={'smaller-pools':1},model='fake'),
    dict(selected='smaller-pools',probabilities={'smaller-pools':float('nan'),'fewer-instances':0},model='fake'),
    dict(selected='smaller-pools',probabilities={'smaller-pools':1,'fewer-instances':1},model='fake'),
])
def test_invalid_provider_answers_do_not_become_recommendations(bad):
    class BadModel:
        def choose(self,*args): return bad
    report=run(model=BadModel())
    assert not report['ok']
    assert report['recommendations']['status'] == 'model_error'
    assert report['recommendations']['selected'] is None


def test_offline_cli_reports_candidates_and_keeps_failure_exit(capsys):
    d=EXAMPLES/'connection-pools'
    code=main(['plan',str(d/'before.polishd'),'--proposed',str(d/'proposed.polishd'),
               '--candidates',str(d/'candidates.json'),'--objective','Keep eight instances','--json'])
    report=json.loads(capsys.readouterr().out)
    assert code == 1
    assert report['recommendations']['status'] == 'awaiting_model'


def test_jev_transport_contract_and_no_error_body_leak():
    httpx=pytest.importorskip('httpx')
    def handler(request):
        payload=json.loads(request.content)
        assert request.url == 'https://jev-ai.pro/api/v1/systemone'
        assert request.headers['Authorization'] == 'Bearer secret-test-key'
        assert payload['questions']['recommendation']['criteria'] == {'a':'A','b':'B'}
        return httpx.Response(200,json={'model':'jev-test','answers':{'recommendation':{
            'type':'choice','choice':'a','probabilities':{'a':.8,'b':.2}}}})
    model=JevDecisionModel(key='secret-test-key',transport=httpx.MockTransport(handler))
    assert model.choose({'objective':'A'}, {'a':'A','b':'B'})['probabilities']['a'] == .8
    model.transport=httpx.MockTransport(lambda request:httpx.Response(401,text='secret-response'))
    with pytest.raises(RecommendationError,match='HTTP 401') as err:
        model.choose({}, {'a':'A','b':'B'})
    assert 'secret' not in str(err.value)


def test_invalid_manifest_is_reported(tmp_path):
    manifest=tmp_path/'candidates.json'
    manifest.write_text('[{"id":"a","description":"A","file":"missing"}]')
    with pytest.raises(RecommendationError): load_candidates(manifest)


def test_matching_evaluation_is_separate_from_request_probability():
    candidates=load_candidates(EXAMPLES/'connection-pools/candidates.json')
    choices={c['id']:c['description'] for c in candidates[:2]}
    evidence=dict(model='test-double',population='Synthetic unit-test population',
                  sampling='independent_held_out',choices=choices,
                  records=[dict(id=str(i),selected='smaller-pools',acceptable=['smaller-pools']) for i in range(5)])
    advice=run(model=FakeModel(),evaluation=evidence)['recommendations']
    assert advice['confidence_interval'] is None
    assert advice['probabilities']['smaller-pools'] == .5
    stats=advice['choice_evaluation']['choices']['smaller-pools']
    assert stats['accuracy'] == 1
    assert stats['confidence_interval']['lower'] < 1
    evidence['model']='different'
    advice=run(model=FakeModel(),evaluation=evidence)['recommendations']
    assert 'evaluation_error' in advice
    assert 'choice_evaluation' not in advice
    assert advice['status'] == 'selected'
