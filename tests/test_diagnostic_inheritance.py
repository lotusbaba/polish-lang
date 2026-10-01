import ast
import json
from pathlib import Path
import shutil
import pytest
from polish import compile_source, simulate
from polish.configuration import load_config
from polish.planner import plan

ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/'examples/aws-processing.polishd').read_text()


@pytest.fixture
def config(tmp_path):
    target=tmp_path/'config'
    shutil.copytree(ROOT/'polish/config',target)
    return target


def edit(config,file,fn):
    p=config/file;d=json.loads(p.read_text());fn(d);p.write_text(json.dumps(d))


def run(config=None,source=None,index=4):
    c=compile_source(source or SOURCE.replace('worker_vcpus = 2',''),config_dir=config)
    assert c.ok,c.diagnostics
    return simulate(c.architecture,c.architecture.scenarios[index])


def test_missing_inputs_are_structured_and_precise():
    r=run()
    ctx=r.diagnostics[0]['context']
    assert ctx['template_origin']=='product'
    assert ctx['component']=='batch_cluster' and ctx['product']=='emr'
    assert ctx['missing_fields']==['worker_vcpus']
    assert 'submits_to.job_cpu_ms' not in r.trace[-1]
    r=run(source=SOURCE.replace('worker_vcpus = 2','').replace('job_cpu_ms = 1000',''))
    assert r.diagnostics[0]['context']['missing_fields']==['worker_vcpus','submits_to.job_cpu_ms']


def test_product_component_global_precedence(config):
    def msg(owner,text):owner.setdefault('diagnostics',{})['BATCH_CPU_INPUTS']={'message':text}
    edit(config,'hosts.json',lambda d:msg(d['components']['batch_cluster'],'CLASS {name}: {missing_fields}'))
    edit(config,'vendors/aws/messaging.json',lambda d:msg(d['products']['emr'],'PRODUCT {name}: {missing_fields}'))
    assert 'PRODUCT Analytics: worker_vcpus' in run(config).trace[-1]
    edit(config,'vendors/aws/messaging.json',lambda d:d['products']['emr'].pop('diagnostics'))
    assert 'CLASS Analytics: worker_vcpus' in run(config).trace[-1]
    edit(config,'hosts.json',lambda d:d['components']['batch_cluster'].pop('diagnostics'))
    edit(config,'diagnostics.json',lambda d:d['diagnostic_details']['BATCH_CPU_INPUTS'].update(message='GLOBAL {component}: {missing_fields}'))
    assert 'GLOBAL batch_cluster: worker_vcpus' in run(config).trace[-1]


def test_input_labels_come_from_contract(config):
    edit(config,'diagnostics.json',lambda d:d['diagnostic_details']['BATCH_CPU_INPUTS']['inputs']['worker_cpu'].update(label='CPU cores per worker'))
    assert 'CPU cores per worker' in run(config).trace[-1]


def test_bound_batch_field_drives_calculation(config):
    edit(config,'diagnostics.json',lambda d:d['diagnostic_details']['BATCH_CPU_INPUTS']['inputs']['worker_cpu'].update(field='cores',label='cores'))
    def schema(d):
        p=d['products']['emr'];p['properties'].append('cores');p['property_types']['cores']='positive_integer'
    edit(config,'vendors/aws/messaging.json',schema)
    assert run(config,SOURCE.replace('worker_vcpus = 2','cores = 2')).passed
    r=run(config,SOURCE.replace('worker_vcpus = 2','cores = 2'),5)
    assert r.error=='PLATFORM_CAPACITY_EXCEEDED'
    assert next(b for b in r.budgets if b['kind']=='batch_cpu')['capacity']==2800


def test_compile_product_override(config):
    edit(config,'vendors/aws/messaging.json',lambda d:d['products']['msk'].setdefault('diagnostics',{}).update(VENDORS_MSK_REPLICATION_FACTOR_CANNOT_EXCEED_BROKERS={'message':'{product} {name} needs more brokers.'}))
    c=compile_source(SOURCE.replace('replication_factor = 3','replication_factor = 4'),config_dir=config)
    assert not c.ok
    assert 'msk Events needs more brokers' in c.diagnostics[0].message
    assert c.diagnostics[0].context['template_origin']=='product'


def test_existing_error_catalog_supports_product_override(config):
    edit(config,'vendors/aws/messaging.json',lambda d:d['products']['msk'].setdefault('diagnostics',{}).update(TLS_UNSUPPORTED={'message':'{provider}/{product} {node_name} needs TLS.'}))
    r=run(config,SOURCE.replace('provider = aws product = msk','provider = aws product = msk tls = false'),0)
    assert r.error=='TLS_UNSUPPORTED'
    assert 'aws/msk Events needs TLS' in r.trace[-1]


@pytest.mark.parametrize('override',[
 {'message':'{unknown}'}, {'message':'{name.__class__}'}, {'message':'{name:>20}'},
 {'message':4}, {'code':'NEW','message':'bad'}, [],
])
def test_invalid_override_is_config_error(config,override):
    edit(config,'vendors/aws/messaging.json',lambda d:d['products']['emr']['diagnostics'].update(BATCH_CPU_INPUTS=override))
    c=compile_source(SOURCE,config_dir=config)
    assert not c.ok and c.diagnostics[0].code=='E_CONFIG'


def test_missing_definition_rejected(config):
    edit(config,'diagnostics.json',lambda d:d['diagnostic_details'].pop('BATCH_CPU_INPUTS'))
    assert compile_source(SOURCE,config_dir=config).diagnostics[0].code=='E_CONFIG'


def test_planner_uses_stable_rule_with_custom_error_code(config):
    edit(config,'errors.json',lambda d:d['errors']['SCALING_MODEL_INCOMPLETE'].update(code='MISSING_MODEL_DATA'))
    report=plan(SOURCE,SOURCE.replace('worker_vcpus = 2',''),config_dir=config,scenario_name='EMR CPU budget fits')
    assert report['findings'][0]['category']=='needs_information'
    assert report['findings'][0]['diagnostics'][0]['context']['product']=='emr'


def test_no_inline_model_detail_messages_and_all_calls_defined():
    catalog=load_config()['diagnostic_details']
    for path in [*(ROOT/'polish').glob('*.py'), *(ROOT/'polish/config/rules').glob('*.rules')]:
        tree=ast.parse(path.read_text())
        for call in (n for n in ast.walk(tree) if isinstance(n,ast.Call)):
            if isinstance(call.func,ast.Name) and call.func.id=='describe' and isinstance(call.args[1],ast.Constant):
                definition=catalog[call.args[1].value]
                params={kw.arg for kw in call.keywords if not kw.arg.startswith('_')}
                assert params==set(definition['parameters']), (path,call.lineno)
            for kw in call.keywords:
                if kw.arg=='detail':
                    assert not isinstance(kw.value,(ast.Constant,ast.JoinedStr)),(path,call.lineno)


def test_product_input_binding_overrides_component(config):
    binding = {'scope': 'node', 'field': 'cores', 'label': 'EMR worker cores'}
    def product(d):
        p = d['products']['emr']
        p['properties'].append('cores')
        p['property_types']['cores'] = 'positive_integer'
        p['diagnostics']['BATCH_CPU_INPUTS']['inputs'] = {'worker_cpu': binding}
    edit(config, 'vendors/aws/messaging.json', product)
    edit(config, 'hosts.json', lambda d: d['components']['batch_cluster']['diagnostics']['BATCH_CPU_INPUTS'].update(
        inputs={'worker_cpu': {'scope': 'node', 'field': 'worker_vcpus', 'label': 'class cores'}}))
    assert run(config, SOURCE.replace('worker_vcpus = 2', 'cores = 2')).passed
    assert run(config).diagnostics[0]['context']['missing_fields'] == ['EMR worker cores']
    overloaded = run(config, SOURCE.replace('worker_vcpus = 2', 'cores = 2'), 5)
    assert next(b for b in overloaded.budgets if b['kind'] == 'batch_cpu')['capacity'] == 2800


@pytest.mark.parametrize('binding', [
    {'scope': 'edge', 'field': 'cores', 'label': 'cores'},
    {'scope': 'unknown', 'field': 'cores', 'label': 'cores'},
    {'scope': 'node', 'field': 'cores'},
    {'scope': 'node', 'field': '', 'label': 'cores'},
])
def test_invalid_input_override_rejected(config, binding):
    edit(config, 'vendors/aws/messaging.json', lambda d: d['products']['emr']['diagnostics']['BATCH_CPU_INPUTS'].update(inputs={'worker_cpu': binding}))
    assert compile_source(SOURCE, config_dir=config).diagnostics[0].code == 'E_CONFIG'


def test_required_input_contract_cannot_be_removed(config):
    edit(config, 'diagnostics.json', lambda d: d['diagnostic_details']['BATCH_CPU_INPUTS']['inputs'].pop('worker_cpu'))
    assert compile_source(SOURCE, config_dir=config).diagnostics[0].code == 'E_CONFIG'
