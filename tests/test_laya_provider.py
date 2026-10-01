import json
import subprocess
from types import SimpleNamespace
import pytest
from polish.laya_provider import LayaDecisionModel
from polish.recommendations import RecommendationError
from polish.cli import main

STATE={'objective':'Keep eight instances','findings':[{'code':'CONNECTION_BUDGET_EXCEEDED'}]}
CHOICES={'small':'Smaller pools','few':'Fewer instances'}


def test_local_worker_contract_and_complete_choices(tmp_path,monkeypatch):
    monkeypatch.setenv('JEV_AI_API_KEY','should-not-be-inherited')
    def invoke(cmd,**kwargs):
        assert cmd[0]=='/custom/venv/bin/python'
        assert kwargs['env']['HF_HUB_OFFLINE']=='1'
        assert 'JEV_AI_API_KEY' not in kwargs['env']
        assert kwargs['timeout']==15
        request=json.loads(kwargs['input'])
        assert request['state']['objective']==STATE['objective']
        assert request['choices']==CHOICES
        return SimpleNamespace(returncode=0,stdout=json.dumps(dict(selected='small',probabilities={'small':.7,'few':.3},model='local-test',device='cpu',checkpoint_sha256='abc',runtime_version='0.3.21')))
    monkeypatch.setattr(subprocess,'run',invoke)
    model=LayaDecisionModel(tmp_path,python='/custom/venv/bin/python',timeout=15)
    assert model.choose(STATE,CHOICES)['selected']=='small'
    assert model.details['input_state']['objective']==STATE['objective']


def test_timeout_becomes_safe_model_error(tmp_path,monkeypatch):
    def timeout(*args,**kwargs): raise subprocess.TimeoutExpired('worker',1)
    monkeypatch.setattr(subprocess,'run',timeout)
    with pytest.raises(RecommendationError,match='worker terminated'):
        LayaDecisionModel(tmp_path,timeout=1).choose(STATE,CHOICES)


@pytest.mark.parametrize('code,message',[('dependencies','Install'),('context','context budget'),('options','collapsed'),('checkpoint','checkpoint'),('device','unavailable'),('inference','failed')])
def test_worker_errors(tmp_path,monkeypatch,code,message):
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1,stdout=json.dumps({'error':code}),stderr='private runtime output'))
    with pytest.raises(RecommendationError,match=message): LayaDecisionModel(tmp_path).choose(STATE,CHOICES)


def test_invalid_selection_is_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout=json.dumps(dict(selected='other',probabilities={'small':.7,'few':.3},model='test'))))
    with pytest.raises(RecommendationError): LayaDecisionModel(tmp_path).choose(STATE,CHOICES)


@pytest.mark.parametrize('timeout',[0,-1,float('nan'),float('inf')])
def test_invalid_timeout(tmp_path,timeout):
    with pytest.raises(RecommendationError): LayaDecisionModel(tmp_path,timeout=timeout)


def test_missing_checkpoint_does_not_launch(tmp_path,monkeypatch):
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:pytest.fail('must not launch'))
    with pytest.raises(RecommendationError,match='existing local directory'):
        LayaDecisionModel(tmp_path/'missing').choose(STATE,CHOICES)


def test_cli_requires_checkpoint():
    with pytest.raises(SystemExit): main(['plan','missing','--proposed','missing','--decision-provider','laya'])


def test_cli_preserves_python_venv_symlink_and_deterministic_result(tmp_path,monkeypatch,capsys):
    from pathlib import Path
    import polish.laya_provider as provider
    root=Path(__file__).resolve().parents[1]/'examples/recommendations/connection-pools'
    executable=tmp_path/'venv-python';executable.symlink_to('/usr/bin/python3')
    def choose(self,state,choices):
        assert str(self.python)==str(executable)
        return dict(selected='smaller-pools',probabilities={'smaller-pools':1,'fewer-instances':0},model='fake-laya')
    monkeypatch.setattr(provider.LayaDecisionModel,'choose',choose)
    code=main(['plan',str(root/'before.polishd'),'--proposed',str(root/'proposed.polishd'),
               '--candidates',str(root/'candidates.json'),'--objective','Keep eight instances',
               '--decision-provider','laya','--laya-checkpoint',str(tmp_path),'--laya-python',str(executable),'--json'])
    report=json.loads(capsys.readouterr().out)
    assert code==1 and report['recommendations']['selection']=='model'
    assert report['recommendations']['model']=='fake-laya'


def test_worker_offline_contract_and_checkpoint_identity(tmp_path,monkeypatch):
    import sys
    import polish.laya_worker as worker
    for name in worker.FILES:
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(json.dumps({'max_len':1024,'head_max_len':768}) if name=='rl_agent_config.json' else 'fixture')
    connections=SimpleNamespace(socket=SimpleNamespace())
    monkeypatch.setattr(worker,'socket',connections)
    monkeypatch.setattr(worker.importlib.metadata,'version',lambda name:'0.3.21')
    monkeypatch.setitem(sys.modules,'torch',SimpleNamespace(set_num_threads=lambda n:None))
    called=[]
    class Agent:
        def __init__(self,path,device):
            self.device=device
            self.tok=SimpleNamespace(encode=lambda text,**kw:text.split())
        def predict(self,state,questions,**kwargs):
            called.append((state,questions))
            return {'answers':{'recommendation':{'choice':'small','probabilities':{'small':.6,'few':.4}}}}
    monkeypatch.setitem(sys.modules,'laya',SimpleNamespace(Agent=Agent))
    request=dict(checkpoint=str(tmp_path),device='cpu',state=STATE,choices=CHOICES)
    answer=worker.run(request)
    assert json.loads(called[0][0])==STATE
    assert called[0][1]['recommendation']['criteria']==CHOICES
    assert answer['model'].startswith('laya@0.3.21:sha256:')
    with pytest.raises(OSError): connections.create_connection(('example.com',443))
    (tmp_path/'model.safetensors').write_text('changed fixture')
    assert worker.run(request)['model'] != answer['model']
    request['state']={'objective':'word '*2000}
    with pytest.raises(worker.WorkerError,match='context'): worker.run(request)
    assert len(called)==2  # oversized context never reached inference
