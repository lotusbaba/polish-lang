import ast
import json
from pathlib import Path
import shutil

import pytest

from polish import compile_source, simulate

ROOT = Path(__file__).resolve().parents[1]
PROCESSING = (ROOT / 'examples/aws-processing.polishd').read_text()
PLATFORMS = (ROOT / 'examples/platforms.polishd').read_text()


@pytest.fixture
def config(tmp_path):
    target = tmp_path / 'config'
    shutil.copytree(ROOT / 'polish/config', target)
    return target


def edit(config, file, fn):
    path = config / file
    data = json.loads(path.read_text())
    fn(data)
    path.write_text(json.dumps(data))


def run(config, source, index):
    compilation = compile_source(source, config_dir=config)
    assert compilation.ok, compilation.diagnostics
    return simulate(compilation.architecture, compilation.architecture.scenarios[index])


def test_product_consumption_field_changes_backlog(config):
    def update(data):
        product = data['products']['sqs']
        product['properties'].append('drain_rps')
        product['property_types']['drain_rps'] = 'nonnegative'
        product['model_inputs'] = {'message_backlog': {
            'background_consumption': {'scope': 'node', 'field': 'drain_rps', 'default': 0}}}
    edit(config, 'vendors/aws/messaging.json', update)
    source = PROCESSING.replace('consumer_rate_rps = 5', 'drain_rps = 9')
    result = run(config, source, 1)
    assert result.outcome == 'success'
    assert next(b for b in result.budgets if b['resource'] == 'Jobs' and b['kind'] == 'backlog')['messages'] == 5
    assert run(config, source.replace('drain_rps = 9', 'drain_rps = 5'), 1).error == 'PLATFORM_CAPACITY_EXCEEDED'


def test_rate_policy_product_overrides_component(config):
    def product(data):
        p = data['products']['api_gateway']
        p['properties'].append('requests_per_second')
        p['property_types']['requests_per_second'] = 'positive'
        p['model_inputs'] = {'rate_policy': {'limit': {
            'scope': 'node', 'field': 'requests_per_second', 'default': None}}}
    # Locate the product without coupling the test to the vendor manifest layout.
    path = next(p for p in (config / 'vendors/aws').glob('*.json') if 'api_gateway' in json.loads(p.read_text()).get('products', {}))
    edit(config, path.relative_to(config), product)
    edit(config, 'network.json', lambda d: d['components']['gateway'].update(model_inputs={
        'rate_policy': {'limit': {'scope': 'node', 'field': 'limit_rps', 'default': None}}}))
    result = run(config, PLATFORMS.replace('limit_rps = 500', 'requests_per_second = 40'), 0)
    assert result.error == 'RATE_LIMITED'
    assert result.diagnostics[0]['context']['object'] == 'AwsGateway'
    assert result.diagnostics[0]['context']['parameters']['limit'] == 40


def test_configured_backlog_window_default(config):
    edit(config, 'model_inputs.json', lambda d: d['model_inputs']['message_backlog']['window'].update(default=10))
    result = run(config, PROCESSING.replace('window_seconds = 5', ''), 1)
    assert next(b for b in result.budgets if b['resource'] == 'Jobs' and b['kind'] == 'backlog')['messages'] == 50


def test_missing_remapped_required_value_is_diagnostic(config):
    edit(config, 'model_inputs.json', lambda d: d['model_inputs']['batch_capacity']['count'].update(field='missing_workers'))
    result = run(config, PROCESSING, 4)
    assert result.error == 'SCALING_MODEL_INCOMPLETE'
    assert 'missing_workers' in result.diagnostics[0]['message']


@pytest.mark.parametrize('binding', [
    {'scope': 'edge', 'field': 'rate', 'default': 0},
    {'scope': 'node', 'field': 'rate', 'default': -1},
    {'scope': 'node', 'field': 'rate', 'default': True},
    {'scope': 'node', 'field': 'rate', 'default': None},
    {'scope': 'node', 'field': 'rate', 'default': float('inf')},
])
def test_bad_model_binding_rejected(config, binding):
    edit(config, 'model_inputs.json', lambda d: d['model_inputs']['message_backlog'].update(background_consumption=binding))
    result = compile_source(PROCESSING, config_dir=config)
    assert result.diagnostics[0].code == 'E_CONFIG'


def test_platform_calculations_do_not_embed_numeric_dsl_fields():
    fields = {'rate_rps', 'limit_rps', 'consumer_rate_rps', 'window_seconds', 'max_backlog',
              'burst_requests', 'max_query_depth', 'max_query_cost', 'query_depth', 'query_cost',
              'capacity_mib', 'dataset_mib', 'max_ops_rps', 'worker_vcpus', 'workers', 'cpu_utilization'}
    tree = ast.parse((ROOT / 'polish/platforms.py').read_text())
    assert not fields.intersection(n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str))


def test_traffic_reads_configured_request_field(config):
    edit(config, 'model_inputs.json', lambda d: d['model_inputs']['traffic']['rate'].update(field='burst_requests'))
    source = PROCESSING.replace('rate_rps = 5 window_seconds = 10', 'burst_requests = 101 window_seconds = 10')
    result = run(config, source, 0)
    assert result.error == 'PLATFORM_CAPACITY_EXCEEDED'
    assert next(b for b in result.budgets if b['resource'] == 'Events' and b['kind'] == 'publish')['demand'] == 101
