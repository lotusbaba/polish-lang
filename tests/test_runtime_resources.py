from pathlib import Path
import pytest
from polish import compile_source, simulate
from polish.planner import plan

ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/'examples/streaming-runtime.polishd').read_text()


def run(source=SOURCE,index=0):
    c=compile_source(source)
    assert c.ok,c.diagnostics
    return simulate(c.architecture,c.architecture.scenarios[index])


@pytest.mark.parametrize('index',range(8))
def test_examples(index):
    assert run(index=index).passed


def test_shared_stream_connection_budget_and_fanout():
    result=run(index=1)
    record=next(b for b in result.budgets if b['kind']=='stream_connections')
    assert record['demand']==100 and record['capacity']==90
    assert not any(b['kind']=='backlog' for b in result.budgets)
    result=run()
    assert next(b for b in result.budgets if b['kind']=='broadcast_deliveries')['demand']==320


def test_lag_and_proxy_limits():
    assert run(index=2).error=='STREAM_RETENTION_EXCEEDED'
    assert run(SOURCE.replace('response_buffering = false','response_buffering = true')).error=='STREAMING_UNSUPPORTED'
    assert run(SOURCE.replace('supports_streaming = true','supports_streaming = false')).error=='STREAMING_UNSUPPORTED'
    assert run(SOURCE.replace('max_client_connections = 1000','max_client_connections = 39')).error=='STREAM_CONNECTIONS_EXCEEDED'


def test_stream_distribution_is_not_inferred():
    assert run(SOURCE.replace('source_rate_rps = 8','source_rate_rps = 100')).error=='STREAM_RETENTION_EXCEEDED'
    assert run(SOURCE.replace('max_stream_connections = 100','max_stream_connections = 30')).error=='STREAM_CONNECTIONS_EXCEEDED'
    assert run(SOURCE.replace('max_messages_rps = 1000','max_messages_rps = 100')).error=='PLATFORM_CAPACITY_EXCEEDED'


def test_missing_stream_inputs():
    assert run(SOURCE.replace('max_stream_connections = 100 reserved_stream_connections = 10','')).error=='SCALING_MODEL_INCOMPLETE'
    assert run(SOURCE.replace('source_rate_rps = 8','')).error=='SCALING_MODEL_INCOMPLETE'
    assert run(SOURCE.replace('max_messages_rps = 1000','')).error=='SCALING_MODEL_INCOMPLETE'


def test_no_fake_request_stream_success():
    source=SOURCE.replace('path = "/live" concurrent_connections', 'path = "/file" concurrent_connections',1)
    assert run(source).error=='SCALING_MODEL_INCOMPLETE'


def test_volume_and_external_capacity():
    assert run(SOURCE.replace('used_mib = 100','used_mib = 1001'),3).error=='PLATFORM_CAPACITY_EXCEEDED'
    assert run(SOURCE.replace('max_ops_rps = 100','max_ops_rps = 9'),3).error=='PLATFORM_CAPACITY_EXCEEDED'
    assert run(SOURCE.replace('Speech { tls = true','Speech { tls = false'),7).error=='TLS_UNSUPPORTED'
    assert run(SOURCE.replace('Speech { tls = true max_requests_rps = 100','Speech { tls = true max_requests_rps = 9'),7).error=='PLATFORM_CAPACITY_EXCEEDED'


def test_localstack_differs_from_aws_transport():
    assert any('HTTP accepted by Jobs' in s for s in run(index=5).trace)
    assert any('HTTP accepted by Objects' in s for s in run(index=6).trace)
    assert not compile_source(SOURCE.replace('reads Objects','reads Objects { protocol = https }')).ok


@pytest.mark.parametrize('old,new',[
 ('mounts Media { access = read_only }',''),
 ('access = read_only','access = invalid'),
 ('access = read_only',''),
 ('RedisHost { instances = 1','RedisHost { instances = 2'),
 ('reserved_stream_connections = 10','reserved_stream_connections = 100'),
 ('concurrent_connections = 40','concurrent_connections = true'),
 ('concurrent_connections = 40','concurrent_connections = 1.5'),
 ('consumer_lag_seconds = 10','consumer_lag_seconds = -1'),
 ('concurrent_connections = 40',''),
 ('consumes_from Audio','consumes_from Audio { connections_per_client = 0 }'),
 ('reads Media','reads Media { cpu_ms = 1 }'),
 ('capacity_mib = 1000','capacity_mib = -1'),
])
def test_invalid_contracts(old,new):
    assert not compile_source(SOURCE.replace(old,new)).ok


def test_planner_detects_streaming_regression():
    result=plan(SOURCE,SOURCE.replace('response_buffering = false','response_buffering = true'),scenario_name='Independent stream readers fit')
    assert result['comparisons'][0]['status']=='introduced'
    assert result['findings'][0]['code']=='STREAMING_UNSUPPORTED'


def test_http_search_cluster():
    source='''architecture Search {
      compute_pool Apps { instances = 1 }
      service Api { hosted_on Apps operation search { method = GET path = "/" reads Logs.Entries { protocol = http } } }
      database Logs { kind = document hosted_on Search collection Entries }
      database_cluster Search { provider = self_hosted product = opensearch }
    } scenario "Search" { request { entry = Api protocol = http } expect { outcome = success accesses Logs.Entries } }'''
    assert run(source).passed
