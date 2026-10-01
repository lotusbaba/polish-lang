import pytest
from polish.choice_evaluation import wilson_interval,evaluate_choices


def evidence(sampling='independent_held_out'):
    return dict(model='test',population='Fixture population for interval arithmetic tests',sampling=sampling,
                choices={'a':'A','b':'B'},records=[dict(id=str(i),selected='a',acceptable=['a'] if i<80 else ['b']) for i in range(100)])


def test_wilson_reference_counts_and_zero():
    interval=wilson_interval(80,100)
    assert interval['lower'] == pytest.approx(.711170835,abs=1e-8)
    assert interval['upper'] == pytest.approx(.866633066,abs=1e-8)
    assert wilson_interval(0,0) is None
    assert wilson_interval(0,10)['lower'] == 0
    assert wilson_interval(10,10)['upper'] == 1
    with pytest.raises(ValueError): wilson_interval(2,1)


def test_evaluation_per_choice_not_request_probability():
    result=evaluate_choices(evidence(),model='test',choices={'a':'A','b':'B'})
    assert result['choices']['a']['accuracy'] == .8
    assert result['choices']['a']['confidence_interval']['level'] == .95
    assert result['choices']['b']['confidence_interval'] is None


def test_illustrative_cases_do_not_produce_inferential_intervals():
    result=evaluate_choices(evidence('illustrative'),model='test',choices={'a':'A','b':'B'})
    assert result['choices']['a']['confidence_interval'] is None


@pytest.mark.parametrize('change', ['duplicate','model','meaning'])
def test_reject_mismatched_or_duplicate_evidence(change):
    data=evidence()
    if change=='duplicate': data['records'].append(data['records'][0])
    if change=='model': data['model']='different'
    if change=='meaning': data['choices']['a']='Other meaning'
    with pytest.raises(ValueError): evaluate_choices(data,model='test',choices={'a':'A','b':'B'})
