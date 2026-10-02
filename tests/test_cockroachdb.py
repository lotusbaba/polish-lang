import json
from pathlib import Path
import shutil
import pytest
from polish import compile_source, simulate
from polish.planner import plan

ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/'examples/cockroachdb.polishd').read_text()


def run(source=SOURCE,index=0,config_dir=None):
    c=compile_source(source,config_dir=config_dir)
    assert c.ok,c.diagnostics
    return simulate(c.architecture,c.architecture.scenarios[index])


@pytest.mark.parametrize('index',range(5))
def test_scenarios(index): assert run(index=index).passed


def test_defaults_and_quorum():
    c=compile_source(SOURCE)
    p=c.architecture.nodes['Store'].properties
    assert p['replication']=='synchronous' and p['write_ack']=='quorum'
    assert p['durability']=='replicated'
    assert any('acknowledgements=3/5' in t for t in run(index=1).trace)


@pytest.mark.parametrize('old,new',[
 ('nodes = 5','nodes = 4'),('nodes = 5','nodes = false'),('nodes = 5',''),
 ('regions = 3','regions = 2'),('regions = 3','regions = false'),
 ('replication_factor = 5','replication_factor = 3'),
 ('survival_goal = region','survival_goal = unknown'),
 ('isolation_level = serializable','isolation_level = repeatable_read'),
 ('kind = relational hosted_on Cluster','kind = document hosted_on Cluster'),
 ('product = cockroachdb_cloud','product = cockroachdb_cloud tls = false'),
 ('product = cockroachdb_cloud','product = cockroachdb_cloud nodes = 3'),
 ('product = cockroachdb_cloud','product = cockroachdb_cloud hosted_on Apps'),
 ('product = cockroachdb\n','product = cockroachdb engine = postgres\n'),
 ('product = cockroachdb\n','product = cockroachdb instance_type = "m7i.large"\n'),
 ('kind = relational hosted_on Cluster','kind = relational hosted_on Cluster replication = asynchronous'),
 ('kind = relational hosted_on Cluster','kind = relational hosted_on Cluster write_ack = primary'),
 ('kind = relational hosted_on Cluster','kind = relational hosted_on Cluster read_replicas = 1'),
 ('kind = relational hosted_on Cloud','kind = relational hosted_on Cloud tls = false'),
 ('protocol = tls consistency = strong','protocol = tls consistency = strong read_from = replica'),
 ('protocol = tls consistency = strong','protocol = tls consistency = strong cpu_ms = 1'),
 ('backups = true','backups = false'),
])
def test_invalid_contracts(old,new):
    assert old in SOURCE
    assert not compile_source(SOURCE.replace(old,new)).ok


def test_missing_ops_budget_and_overall_not_per_node_capacity():
    assert run(SOURCE.replace('nodes = 5 max_ops_rps = 100','nodes = 5')).error=='SCALING_MODEL_INCOMPLETE'
    assert run(SOURCE.replace('nodes = 5','nodes = 10'),2).error=='PLATFORM_CAPACITY_EXCEEDED'
    assert run(SOURCE.replace('connection_pool_size = 20','connection_pool_size = 46')).error=='CONNECTION_BUDGET_EXCEEDED'


def test_zone_goal_and_custom_config(tmp_path):
    zone=SOURCE.replace('regions = 3 survival_goal = region replication_factor = 5','regions = 1 survival_goal = zone replication_factor = 3')
    assert run(zone).passed
    config=tmp_path/'config';shutil.copytree(ROOT/'polish/config',config)
    p=config/'vendors/cockroachdb/databases.json';d=json.loads(p.read_text())
    d['products']['cockroachdb']['survival_goals']['zone']['min_replicas']=5
    p.write_text(json.dumps(d))
    assert not compile_source(zone,config_dir=config).ok


def test_bad_vendor_contract_configuration(tmp_path):
    config=tmp_path/'config';shutil.copytree(ROOT/'polish/config',config)
    p=config/'vendors/cockroachdb/databases.json';d=json.loads(p.read_text())
    d['products']['cockroachdb']['survival_goals']['region']['min_regions']=False
    p.write_text(json.dumps(d))
    result=compile_source(SOURCE,config_dir=config)
    assert not result.ok
    assert any(d.code=='E_CONFIG' for d in result.diagnostics)


def test_legacy_provider_still_works_and_new_settings_are_scoped():
    legacy=SOURCE.replace('provider = cockroachdb product = cockroachdb\n','provider = self_hosted product = cockroachdb\n').replace('regions = 3 survival_goal = region replication_factor = 5','replication_factor = 5 replication = synchronous write_ack = quorum durability = replicated').replace('    isolation_level = serializable\n','')
    assert run(legacy).passed
    assert not compile_source(legacy.replace('replication_factor = 5','regions = 3 replication_factor = 5')).ok


def test_plan_reports_introduced_capacity_failure():
    report=plan(SOURCE,SOURCE.replace('max_ops_rps = 100','max_ops_rps = 40'),scenario_name='Strong SQL read within declared capacity')
    assert report['comparisons'][0]['status']=='introduced'
    assert report['comparisons'][0]['after']['error']=='PLATFORM_CAPACITY_EXCEEDED'


def test_default_read_is_strong_and_uses_distributed_endpoint():
    result=run(SOURCE.replace(' consistency = strong',''))
    assert any('distributed SQL endpoint, consistency=strong' in t for t in result.trace)


def test_cloud_plaintext_protocol_is_rejected():
    source=SOURCE.replace('reads Managed.Items { protocol = tls','reads Managed.Items { protocol = tcp')
    assert not compile_source(source).ok
