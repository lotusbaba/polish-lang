import json
from pathlib import Path
import shutil
import pytest

from polish import compile_source
from polish.cli import main
from polish.configuration import ConfigurationError
from polish.planner import plan, graph_diff

ROOT = Path(__file__).resolve().parents[1]
BEFORE = (ROOT/'examples/plan-before.polishd').read_text()
AFTER = (ROOT/'examples/plan-after.polishd').read_text()


def test_scale_change_exposes_database_budget():
    result = plan(BEFORE, AFTER)
    assert not result['ok']
    comparison = result['comparisons'][0]
    assert comparison['status'] == 'introduced'
    assert comparison['before']['passed']
    assert comparison['after']['error'] == 'CONNECTION_BUDGET_EXCEEDED'
    budget = comparison['after']['budgets'][0]
    assert budget['potential_per_instance'] == 400
    assert budget['available_per_instance'] == 300
    assert result['findings'][0]['suggestions']
    assert {'Orders', 'Orders.Items', 'Storage', 'Api.buy'} <= {x['component'] for x in result['impact']}


def test_related_fix_resolves_failure():
    result = plan(AFTER, AFTER.replace('connection_pool_size = 50', 'connection_pool_size = 30'))
    assert result['ok']
    assert result['comparisons'][0]['status'] == 'resolved'


def test_existing_failure_and_expected_failure_are_distinct():
    assert plan(AFTER, AFTER)['comparisons'][0]['status'] == 'persists'
    expected = AFTER.replace('outcome = success', 'outcome = error error = CONNECTION_BUDGET_EXCEEDED')
    assert plan(expected, expected)['ok']


def test_original_requirements_cannot_be_deleted_or_weakened():
    deleted = AFTER.split('scenario ')[0]
    assert not plan(BEFORE, deleted)['ok']
    weakened = AFTER.replace('outcome = success', 'outcome = error error = CONNECTION_BUDGET_EXCEEDED')
    result = plan(BEFORE, weakened)
    assert len(result['comparisons']) == 2
    assert not result['ok']


def test_future_workload_is_run_on_both_designs():
    future = BEFORE.replace('rate_rps = 10', 'rate_rps = 2000')
    result = plan(BEFORE, future)
    assert len(result['comparisons']) == 2
    assert result['comparisons'][1]['before']['error'] == 'CAPACITY_EXCEEDED'
    assert result['comparisons'][1]['after']['error'] == 'CAPACITY_EXCEEDED'


def test_line_shifts_do_not_change_graph():
    result = plan(BEFORE, '\n\n'+BEFORE)
    assert result['changes'] == {'components': [], 'removed_edges': [], 'added_edges': []}
    assert result['impact'] == []
    assert result['ok']


def test_removed_request_component_is_not_a_crash():
    renamed = AFTER.replace('Api', 'NewApi')
    result = plan(BEFORE, renamed)
    assert result['comparisons'][0]['after']['error'] == 'SCENARIO_INCOMPATIBLE'
    assert not result['ok']


def test_edge_options_and_containment_are_compared():
    result = plan(BEFORE, BEFORE.replace('cpu_ms = 1 }', 'cpu_ms = 2 }'))
    assert result['changes']['added_edges']
    assert 'Storage' in {n['component'] for n in result['impact']}


def test_incoming_filters_relationships():
    arch = compile_source(BEFORE).architecture
    assert arch.incoming('Apps', 'hosted_on')[0].source == 'Api'
    assert arch.incoming('Apps', 'reads') == []


def test_no_scenarios_and_unknown_filter():
    assert not plan(BEFORE, AFTER, scenario_name='missing')['ok']
    result = plan(BEFORE.split('scenario ')[0], AFTER.split('scenario ')[0])
    assert result['findings'][0]['code'] == 'NO_SCENARIOS'


def test_invalid_proposal_explains_compile_error():
    result = plan(BEFORE, AFTER.replace('hosted_on Apps', 'hosted_on Orders'))
    assert not result['ok']
    assert result['diagnostics'][0]['side'] == 'after'
    assert result['comparisons'] == []


def test_configurable_advice_and_invalid_rules(tmp_path):
    config = tmp_path/'config'
    shutil.copytree(ROOT/'polish/config', config)
    rules = {'rules': {'CONNECTION_BUDGET_EXCEEDED': {'suggestions': ['Check the pools.']}}}
    path = config/'decisions.json'
    path.write_text(json.dumps(rules))
    assert plan(BEFORE, AFTER, config_dir=config)['findings'][0]['suggestions'] == ['Check the pools.']
    path.write_text('{"rules": []}')
    with pytest.raises(ConfigurationError):
        plan(BEFORE, AFTER, config_dir=config)


def test_cli_json(capsys):
    assert main(['plan', str(ROOT/'examples/plan-before.polishd'), '--proposed', str(ROOT/'examples/plan-after.polishd'), '--json']) == 1
    assert json.loads(capsys.readouterr().out)['comparisons'][0]['status'] == 'introduced'


def test_cycles_terminate_and_disconnected_nodes_are_excluded():
    extra = 'cdn A { routes_to B } cdn B { routes_to A } artifact_store Unrelated { kind = object_store }'
    before = BEFORE.replace('architecture Checkout {', 'architecture Checkout { '+extra)
    after = before.replace('cdn A {', 'cdn A { tls = true')
    result = plan(before, after)
    assert {n['component'] for n in result['impact']} == {'A', 'B'}
    assert result['findings'][-1]['code'] == 'UNOBSERVED_IMPACT'


def test_nlb_path_rule_advice():
    extra = 'load_balancer Edge { provider = aws product = alb route "/api/*" to Apps }'
    before = BEFORE.replace('architecture Checkout {', 'architecture Checkout { '+extra)
    result = plan(before, before.replace('product = alb', 'product = nlb'))
    assert any('NLB' in s for f in result['findings'] for s in f.get('suggestions', []))


def test_missing_model_is_reported_as_information():
    result = plan(BEFORE, BEFORE.replace('path = "/buy" cpu_ms = 1', 'path = "/buy"'))
    assert result['findings'][0]['category'] == 'needs_information'
