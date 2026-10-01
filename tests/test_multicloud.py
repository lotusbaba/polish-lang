from pathlib import Path
import pytest
from polish import compile_source, simulate
from polish.planner import plan

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT/'examples/multicloud.polishd').read_text()


def run(source=SOURCE, index=0):
    c=compile_source(source)
    assert c.ok, c.diagnostics
    return simulate(c.architecture, c.architecture.scenarios[index])


@pytest.mark.parametrize('index', range(10))
def test_example(index):
    assert run(index=index).passed


def test_fanout_and_independent_backlog():
    result=run(index=5)
    backlogs={b['resource']:b['messages'] for b in result.budgets if b['kind']=='backlog'}
    assert backlogs == {'Indexing':60, 'Analytics':0}
    assert 'Updates' not in backlogs
    assert run(SOURCE.replace('consumer_rate_rps = 10 max_backlog = 50','consumer_rate_rps = 20 max_backlog = 50'),5).outcome=='success'


def test_kinesis_math_and_on_demand():
    result=run(index=3)
    budget=next(b for b in result.budgets if b['resource']=='Events' and b['kind']=='publish')
    assert budget['capacity']==100 and budget['demand']==101
    assert run(SOURCE.replace('shards = 2','shards = 3'),3).outcome=='success'
    demand=SOURCE.replace('capacity_mode = provisioned','capacity_mode = on_demand').replace('shards = 2 messages_per_shard_rps = 50','max_messages_rps = 100')
    assert run(demand,2).passed and run(demand,3).passed
    assert run(demand.replace('max_messages_rps = 100 consumer_rate_rps = 100','consumer_rate_rps = 100'),2).error=='SCALING_MODEL_INCOMPLETE'
    assert run(SOURCE.replace('messages_per_shard_rps = 50',''),2).error=='SCALING_MODEL_INCOMPLETE'


def test_cloudsql_connections_and_missing_capacity():
    assert run(SOURCE.replace('connection_pool_size = 20','connection_pool_size = 51'),8).error=='CONNECTION_BUDGET_EXCEEDED'
    assert run(SOURCE.replace('max_ops_rps = 50',''),8).error=='SCALING_MODEL_INCOMPLETE'
    assert run(SOURCE.replace('protocol = tls','protocol = tls').replace('product = cloudsql_postgres','product = cloudsql_postgres tls = false'),8).error=='TLS_UNSUPPORTED'


def test_memorystore_limits():
    assert run(SOURCE.replace('dataset_mib = 100','dataset_mib = 600'),6).error=='CACHE_CAPACITY_EXCEEDED'
    assert run(SOURCE.replace('max_connections_per_instance = 100','max_connections_per_instance = 19'),6).error=='CACHE_CONNECTION_BUDGET_EXCEEDED'


def test_sns_topic_limit_and_tls():
    assert run(SOURCE.replace('topic_type = standard max_messages_rps = 100','topic_type = standard max_messages_rps = 9')).error=='PLATFORM_CAPACITY_EXCEEDED'
    assert run(SOURCE.replace('product = sns','product = sns tls = false')).error=='TLS_UNSUPPORTED'


@pytest.mark.parametrize('old,new', [
 ('capacity_mode = provisioned','capacity_mode = on_demand'),
 ('shards = 2','shards = false'),
 ('shards = 2',''),
 ('shards = 2','shards = 2 max_messages_rps = 100'),
 ('delivery = pull','delivery = push'),
 ('delivers_to Indexing','delivers_to Emails'),
 ('publishes_to Updates','publishes_to Indexing'),
 ('publishes_to Notifications','consumes_from Notifications'),
 ('topic_type = standard','topic_type = unknown'),
 ('queue_type = standard','queue_type = fifo'),
 ('product = memorystore_redis nodes = 1','product = memorystore_redis nodes = 1 hosted_on Apps'),
 ('product = cloudsql_postgres','product = cloudsql_postgres instance_type = "m7i.large"'),
 ('kind = relational max_connections','kind = document max_connections'),
 ('kind = relational max_connections','kind = relational read_replicas = 1 replication = synchronous max_connections'),
])
def test_product_contracts(old,new):
    assert not compile_source(SOURCE.replace(old,new)).ok


def test_missing_and_multiple_subscription_topics():
    assert not compile_source(SOURCE.replace('delivers_to Indexing','')).ok
    second='topic Other { provider = gcp product = pubsub_topic delivers_to Indexing }'
    assert not compile_source(SOURCE.replace('architecture MultiCloud {','architecture MultiCloud { '+second)).ok


def test_plan_traverses_subscription_dependencies():
    before=SOURCE.split('scenario ')[0]+'''scenario "Publish" {
      request { entry = Catalog protocol = http method = POST path = "/publish" rate_rps = 10 window_seconds = 6 }
      expect { outcome = success }
    }'''
    result=plan(before,before.replace('consumer_rate_rps = 10 max_backlog = 50','consumer_rate_rps = 0 max_backlog = 50'))
    assert result['comparisons'][0]['status']=='introduced'
    assert {'Updates','Catalog.publish','Analytics'} <= {n['component'] for n in result['impact']}


def test_subscription_consumption():
    source=SOURCE.replace('path = "/publish" rate_rps = 10','path = "/consume" rate_rps = 10',1)
    result=run(source,4)
    assert result.outcome=='success'
    assert next(b for b in result.budgets if b['kind']=='consume')['demand']==10


def test_cloudsql_replica_consistency_and_writes():
    source=SOURCE.replace('kind = relational max_connections','kind = relational read_replicas = 1 max_connections')
    source=source.replace('protocol = tls }','protocol = tls read_from = replica consistency = strong }')
    assert run(source,8).error=='CONSISTENCY_UNSUPPORTED'
    assert run(source.replace('consistency = strong','consistency = eventual'),8).outcome=='success'
    source=SOURCE.replace('reads CatalogDB.Stations','writes CatalogDB.Stations')
    assert run(source,8).outcome=='success'


def test_sns_fifo_to_sqs_fifo():
    source=SOURCE.replace('topic_type = standard','topic_type = fifo').replace('queue_type = standard','queue_type = fifo')
    assert run(source).passed
