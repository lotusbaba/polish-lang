from pathlib import Path
import pytest
from polish import compile_source, simulate
from polish.planner import plan

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT/'examples/radio-catalog.polishd').read_text()


def run(source=SOURCE, index=0):
    c = compile_source(source)
    assert c.ok, c.diagnostics
    return simulate(c.architecture, c.architecture.scenarios[index])


@pytest.mark.parametrize('index', range(4))
def test_examples(index):
    assert run(index=index).passed


def test_per_node_and_shared_cluster_math():
    result = run(index=1)
    assert result.budgets[0]['potential_per_instance'] == 8000
    assert result.budgets[0]['available_per_instance'] == 4800
    assert run(SOURCE.replace('instances = 3', 'instances = 6'), 1).budgets[0]['potential_per_instance'] == 8000
    shared = run(SOURCE.replace('pool_distribution = per_node', 'pool_distribution = cluster_even'), 1)
    assert shared.outcome == 'success'
    assert shared.budgets[0]['potential_per_instance'] == 2667


def test_instance_scoped_pool():
    r = run(SOURCE.replace('pool_scope = worker','pool_scope = instance'), 1)
    assert r.outcome == 'success'
    assert r.budgets[0]['potential_per_instance'] == 1000


def test_reused_pool_not_counted_twice():
    source = SOURCE.replace('on_miss = fallback on_error = fail\n      }','on_miss = fallback on_error = fail\n      }\n      writes StationCache { pool_size = 50 pool_scope = worker pool_distribution = per_node }')
    assert run(source).budgets[0]['potential_per_instance'] == 800


def test_multiple_services_aggregate():
    source = SOURCE.replace('operation load { cpu_ms = 2 }', 'workers_per_instance = 8 operation load { cpu_ms = 2 writes StationCache { pool_size = 10 pool_scope = worker pool_distribution = per_node } }')
    assert run(source, 2).budgets[0]['potential_per_instance'] == 960


def test_miss_and_error_are_separate():
    assert 'CatalogSource.load' not in run(index=0).reached
    assert 'CatalogSource.load' in run(index=2).reached
    assert 'CatalogSource.load' not in run(index=3).reached
    # Error results apply to every cache access in the scenario, including writes.
    recovered = run(SOURCE.replace('on_error = fail', 'on_error = fallback'), 3)
    assert recovered.outcome == 'success'
    assert 'CatalogSource.load' in recovered.reached
    assert run(SOURCE.replace('on_miss = fallback', 'on_miss = fail'), 2).error == 'CACHE_MISS'


def test_managed_cache_nodes():
    source = SOURCE.replace('provider = self_hosted product = memcached hosted_on CacheHosts', 'provider = aws product = elasticache_memcached nodes = 3')
    assert run(source, 1).budgets[0]['potential_per_instance'] == 8000
    assert run(source.replace('nodes = 3', ''), 1).error == 'SCALING_MODEL_INCOMPLETE'


@pytest.mark.parametrize('old,new', [
 ('pool_size = 50','pool_size = true'),
 ('pool_scope = worker','pool_scope = thread'),
 ('pool_distribution = per_node','pool_distribution = random'),
 ('workers_per_instance = 8','workers_per_instance = 0'),
 ('workers_per_instance = 8',''),
 ('pool_size = 50',''),
 ('reserved_connections_per_instance = 200','reserved_connections_per_instance = 5000'),
 ('reserved_connections_per_instance = 200','reserved_connections_per_instance = false'),
 ('hosted_on CacheHosts','hosted_on CacheHosts nodes = 3'),
 ('invokes CatalogSource.load',''),
 ('on_error = fail','on_error = ignore'),
])
def test_invalid_contracts(old,new):
    assert not compile_source(SOURCE.replace(old,new)).ok


def test_missing_limit_is_not_infinite():
    source = SOURCE.replace('max_connections_per_instance = 5000 reserved_connections_per_instance = 200','')
    assert run(source).error == 'SCALING_MODEL_INCOMPLETE'


def test_planner_finds_introduced_cache_budget_failure():
    before = SOURCE.split('scenario ')[0] + '''scenario "Catalog" {
      request { entry = CatalogAPI protocol = http path = "/stations" cache_result = hit }
      expect { outcome = success }
    }'''
    result = plan(before.replace('2..20','2'), before.replace('2..20','20'))
    assert result['comparisons'][0]['status'] == 'introduced'
    assert result['findings'][0]['code'] == 'CACHE_CONNECTION_BUDGET_EXCEEDED'
    assert result['findings'][0]['suggestions']
