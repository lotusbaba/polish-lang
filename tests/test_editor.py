import json
from pathlib import Path
import threading
import urllib.error
import urllib.request

import pytest

from polish.editor import EditorServer, analyze

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT/'examples/cockroachdb.polishd').read_text()


@pytest.fixture
def server(tmp_path):
    static=tmp_path/'static';static.mkdir();(static/'index.html').write_text('<html>editor</html>')
    file=tmp_path/'system.polishd';file.write_text(SOURCE)
    with EditorServer(file,port=0,static_dir=static) as instance:
        thread=threading.Thread(target=instance.serve_forever,daemon=True);thread.start()
        yield instance
        instance.shutdown();thread.join()


def request(server, path, payload=None, **headers):
    data=json.dumps(payload).encode() if payload is not None else None
    headers={'Authorization':'Bearer '+server.token,'Content-Type':'application/json',**headers}
    req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}'+path,data=data,headers=headers)
    try:
        response=urllib.request.urlopen(req)
    except urllib.error.HTTPError as response:
        return response.code,json.loads(response.read())
    with response:
        return response.status,json.loads(response.read())


def test_graph_contains_every_declared_component_and_relation():
    result=analyze(SOURCE,run_scenarios=True)
    assert result['ok'] and len(result['results'])==5
    nodes={n['name']:n for n in result['graph']['nodes']}
    assert nodes['Store.Items']['parent']=='Store'
    assert nodes['Store.Items']['fields']['id']['primary_key']
    assert any(e['source']=='API.get' and e['target']=='Store.Items' and e['kind']=='reads' for e in result['graph']['edges'])


def test_invalid_source_does_not_produce_diagram():
    result=analyze('architecture Broken { service MissingHost }')
    assert not result['ok'] and result['graph'] is None and result['diagnostics']


def test_authenticated_check_and_simulation(server):
    status,session=request(server,'/api/session')
    assert status==200 and session['filename']=='system.polishd'
    assert request(server,'/api/check',{'source':SOURCE})[1]['graph']['name']=='CockroachStore'
    assert request(server,'/api/simulate',{'source':SOURCE})[1]['ok']


def test_only_attached_file_can_be_saved_and_disk_conflicts_are_rejected(server,tmp_path):
    session=request(server,'/api/session')[1]
    new=SOURCE+'\n// saved draft\n'
    status,saved=request(server,'/api/save',{'source':new,'revision':session['revision'],'path':str(tmp_path/'other.polishd')})
    assert status==200 and server.file.read_text()==new
    assert not (tmp_path/'other.polishd').exists()
    server.file.write_text(new+'// external change\n')
    assert request(server,'/api/save',{'source':SOURCE,'revision':saved['revision']})[0]==409
    assert 'external change' in server.file.read_text()


def test_cross_origin_authentication_and_dns_rebinding(server):
    assert request(server,'/api/session',Authorization='')[0]==401
    assert request(server,'/api/check',{'source':SOURCE},Origin='https://example.com')[0]==403
    assert request(server,'/api/session',Host='attacker.example')[0]==403
    assert request(server,'/api/session',Origin='null')[0]==403


def test_static_server_cannot_read_repo_files(server):
    assert request(server,'/../system.polishd')[0]==404
    assert request(server,'/%2e%2e/system.polishd')[0]==404
    assert request(server,'/api/unknown')[0]==404


def test_bad_request_is_reported(server):
    assert request(server,'/api/check',{'source':42})[0]==400
    assert request(server,'/api/check',{'source':SOURCE},**{'Content-Type':'text/plain'})[0]==415


def test_cli_editor_uses_optional_file_and_config(monkeypatch):
    from polish.cli import main
    calls=[]
    monkeypatch.setattr('polish.editor.serve',lambda *args,**kwargs:calls.append((args,kwargs)) or 0)
    assert main(['editor','--no-browser','--port','0'])==0
    assert calls[0][0]==(None,)
    assert calls[0][1]['port']==0 and not calls[0][1]['open_browser']
    with pytest.raises(SystemExit):main(['check'])
    with pytest.raises(SystemExit):main(['editor','--json'])


def test_canvas_api_updates_source_and_rejects_invalid_architecture(server):
    from test_editor_sync import model
    scene = model(SOURCE)
    next(n for n in scene['nodes'] if n['name'] == 'API')['name'] = 'Catalog'
    status, result = request(server, '/api/canvas', {'source': SOURCE, **scene})
    assert status == 200 and result['ok']
    assert 'service Catalog' in result['source'] and 'entry = Catalog' in result['source']
    assert server.file.read_text() == SOURCE  # Explicit Save file writes the draft.
    scene['edges'] = []
    status, result = request(server, '/api/canvas', {'source': SOURCE, **scene})
    assert status == 200 and not result['ok'] and result['source'] == SOURCE
    assert result['diagnostics']
