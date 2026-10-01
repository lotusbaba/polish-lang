"""Config owns executable model semantics; the host interpreter stays bounded."""
import json
import shutil
from pathlib import Path

import pytest

from polish import compile_source, simulate
from polish.rule_engine import parse_rules, RuleError

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'examples/aws-processing.polishd').read_text()


@pytest.fixture
def config(tmp_path):
    target = tmp_path / 'config'
    shutil.copytree(ROOT / 'polish/config', target)
    return target


def change(config, module, before, after):
    p = config / 'rules' / (module + '.rules')
    source = p.read_text()
    assert before in source
    p.write_text(source.replace(before, after))


def execute(config, source=SOURCE, index=1):
    compiled = compile_source(source, config_dir=config)
    assert compiled.ok, compiled.diagnostics
    return simulate(compiled.architecture, compiled.architecture.scenarios[index])


def test_backlog_formula_is_loaded_from_config(config):
    assert execute(config).error == 'PLATFORM_CAPACITY_EXCEEDED'
    change(config, 'platforms', 'backlog = max(0, arrivals - consumed) * window',
           'backlog = max(0, arrivals - consumed * 2) * window')
    result = execute(config)
    assert result.outcome == 'success'
    assert next(b for b in result.budgets if b['resource'] == 'Jobs' and b['kind'] == 'backlog')['messages'] == 0


def test_cpu_formula_is_loaded_from_config(config):
    source = (ROOT / 'examples/scaling.polishd').read_text()
    change(config, 'scaling', "per_instance = profile['vcpus'] * 1000 * p.get('cpu_utilization', 0.7)",
           "per_instance = profile['vcpus'] * 100 * p.get('cpu_utilization', 0.7)")
    compiled = compile_source(source, config_dir=config)
    assert compiled.ok
    default = compile_source(source)
    before = simulate(default.architecture, default.architecture.scenarios[0])
    after = simulate(compiled.architecture, compiled.architecture.scenarios[0])
    assert after.capacity[0]['capacity_cpu_ms_per_second'] == before.capacity[0]['capacity_cpu_ms_per_second'] / 10


def test_product_validation_condition_is_configured(config):
    source = SOURCE.replace('replication_factor = 3', 'replication_factor = 4')
    assert not compile_source(source, config_dir=config).ok
    change(config, 'vendors', "p['replication_factor'] > p['brokers']", "p['replication_factor'] > p['brokers'] + 1")
    assert compile_source(source, config_dir=config).ok


def test_unknown_request_field_can_be_declared_in_config(config):
    p = config / 'language.json'
    data = json.loads(p.read_text())
    data['request_properties'].append('background_jobs')
    p.write_text(json.dumps(data))
    source = SOURCE.replace('rate_rps = 5 window_seconds', 'rate_rps = 5 background_jobs = 2 window_seconds')
    assert compile_source(source, config_dir=config).ok
    assert not compile_source(source).ok


@pytest.mark.parametrize('source', [
    'import os', 'from pathlib import Path', 'def f():\n return (1).__class__',
    'def f():\n return lambda: 1', 'def f():\n global x',
    'def f():\n return [x async for x in xs]',
])
def test_unsupported_language_features_rejected(source):
    with pytest.raises(RuleError):
        parse_rules(source)


def test_missing_rule_file_is_config_error(config):
    (config / 'rules/platforms.rules').unlink()
    assert compile_source(SOURCE, config_dir=config).diagnostics[0].code == 'E_CONFIG'


@pytest.mark.parametrize('body', [
    "return open('/tmp/polish-rule-must-not-exist', 'w')",
    "return eval('1 + 1')",
    'while True:\n    pass',
    "return 'x' * 1000000000",
])
def test_execution_is_restricted_and_bounded(config, body):
    change(config, 'platforms', "inputs = self.inputs('message_backlog', node)",
           "inputs = self.inputs('message_backlog', node)\n        " + body.replace('\n', '\n        '))
    result = execute(config)
    assert result.error == 'E_CONFIG'
    assert not result.passed


def test_rule_signatures_cannot_silently_break_engine_hooks(config):
    change(config, 'platforms', 'def policy(self, node):', 'def policy(self):')
    assert compile_source(SOURCE, config_dir=config).diagnostics[0].code == 'E_CONFIG'


def test_engine_adapters_do_not_embed_model_vocabulary():
    fields = ('rate_rps', 'consumer_rate_rps', 'rate_policy', 'burst_limit', 'worker_vcpus', 'consumer_lag_seconds')
    for module in ('platforms','scaling','budgets','cache_connections','runtime_resources','cloud_products','storage','simulator','compiler'):
        source = (ROOT / 'polish' / (module + '.py')).read_text()
        assert not any(field in source for field in fields), module


def test_vendor_metadata_validation_is_configured(config):
    p = config / 'vendors/aws/dynamodb.json'
    product_data = json.loads(p.read_text())
    product_data['products']['dynamodb']['custom_chunk'] = 0
    p.write_text(json.dumps(product_data))
    p = config / 'vendor_schema.json'
    schema = json.loads(p.read_text())
    schema['vendor_schema']['numeric_fields']['custom_chunk'] = 'positive_integer'
    p.write_text(json.dumps(schema))
    assert compile_source(SOURCE, config_dir=config).diagnostics[0].code == 'E_CONFIG'
