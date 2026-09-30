from pathlib import Path
import pytest
from polish import compile_source, simulate

SOURCE = (Path(__file__).resolve().parents[1] / 'examples/delivery.polishd').read_text()

def run(source=SOURCE, index=0):
    compilation = compile_source(source)
    assert compilation.ok, compilation.diagnostics
    arch = compilation.architecture
    return simulate(arch, arch.scenarios[index])

@pytest.mark.parametrize('index', range(10))
def test_delivery_scenarios(index):
    assert run(index=index).passed

@pytest.mark.parametrize('old,new', [
    ('endpoint = website', 'endpoint = website tls = true'),
    ('endpoint = website', 'endpoint = invalid'),
    ('loaded_from CloudFront', 'loaded_from Images'),
    ('routes_to Assets', 'routes_to Images'),
    ('image_from Images', 'image_from Assets'),
    ('authorized = true', 'authorized = 1'),
    ('max_requests_rps = 100', 'max_requests_rps = false'),
    ('max_pulls_rps = 5', 'max_pulls_rps = 0'),
    ('image_pulls_rps = 5', 'image_pulls_rps = -1'),
    ('product = ecr', 'product = ecr kind = object_store'),
])
def test_validation(old, new):
    assert not compile_source(SOURCE.replace(old, new)).ok

def test_pull_denial():
    assert run(SOURCE.replace('authorized = true', 'authorized = false'), 8).error == 'IMAGE_PULL_DENIED'
    assert run(SOURCE.replace('{ authorized = true }', ''), 8).error == 'IMAGE_PULL_DENIED'

def test_missing_budgets():
    assert run(SOURCE.replace('max_requests_rps = 100', '')).error == 'SCALING_MODEL_INCOMPLETE'
    assert run(SOURCE.replace('max_pulls_rps = 5', ''), 8).error == 'SCALING_MODEL_INCOMPLETE'

def test_registry_is_not_a_website():
    assert run(SOURCE.replace('entry = Shop', 'entry = Images')).error == 'REGISTRY_NOT_WEB_ORIGIN'

def test_capacity_math():
    assert run().budgets[-1]['demand'] == 100
    assert run(index=9).budgets[-1]['demand'] == 6

def test_cdn_origin_tls():
    source = SOURCE.replace('routes_to Assets', 'routes_to Website')
    assert run(source).error == 'TLS_UNSUPPORTED'
    assert run(source.replace('limit_rps = 200', 'limit_rps = 200 upstream_protocol = http').replace('rate_rps = 100', '')).outcome == 'success'
