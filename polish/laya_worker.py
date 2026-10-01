"""Standalone worker: stdout is JSON only; optional ML imports stay out of CLI startup."""
import contextlib
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import sys

FILES=('rl_agent_config.json','encoder/config.json','model.safetensors',
       'tokenizer/tokenizer.json','tokenizer/tokenizer_config.json')


class WorkerError(Exception):
    pass


def run(request):
    # Defense in depth beyond HF offline flags: inference must not open sockets.
    def blocked(*args,**kwargs):
        raise OSError('Network access disabled for local inference')
    socket.socket.connect=blocked
    socket.socket.connect_ex=blocked
    socket.create_connection=blocked
    root=Path(request['checkpoint'])
    try:
        fingerprint=hashlib.sha256()
        for name in FILES:
            with (root/name).open('rb') as source:
                digest=hashlib.file_digest(source,'sha256').digest()
            fingerprint.update(name.encode()+b'\0'+digest)
        config=json.loads((root/'rl_agent_config.json').read_text())
    except (OSError,ValueError):
        raise WorkerError('checkpoint') from None
    try:
        import torch
        from laya import Agent
    except ImportError:
        raise WorkerError('dependencies') from None
    device=request['device']
    if device=='mps' and not torch.backends.mps.is_available() or device=='cuda' and not torch.cuda.is_available():
        raise WorkerError('device')
    torch.set_num_threads(4)
    agent=Agent(str(root),device=device)
    instruction='Choose the architecture change that best satisfies the objective. All offered candidates pass the declared scenarios. Do not invent unmodeled benefits.'
    # Conservative complete-input check prevents silent state/option truncation.
    state=json.dumps(request['state'],ensure_ascii=False)
    choices=request['choices']
    head=instruction+' '+json.dumps(choices,ensure_ascii=False)
    state_tokens=len(agent.tok.encode(state,add_special_tokens=False))
    head_tokens=len(agent.tok.encode(head,add_special_tokens=False))+64
    max_len=config.get('max_len',512)
    head_limit=config.get('head_max_len',192)
    if head_tokens>head_limit or state_tokens+head_tokens+64>max_len:
        raise WorkerError('context')
    result=agent.predict(state,{'recommendation':{'type':'choice','instructions':instruction,'criteria':choices}},
                         max_len=max_len,head_max_len=head_tokens)
    if result.get('usage',{}).get('options'):
        raise WorkerError('options')
    answer=result['answers']['recommendation']
    version=importlib.metadata.version('laya')
    checksum=fingerprint.hexdigest()
    return dict(selected=answer['choice'],probabilities=answer['probabilities'],
                model=f'laya@{version}:sha256:{checksum}',checkpoint_sha256=checksum,
                device=str(agent.device),runtime_version=version)


def main():
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',TOKENIZERS_PARALLELISM='false')
    try:
        with contextlib.redirect_stdout(sys.stderr):
            response=run(json.load(sys.stdin))
    except WorkerError as exc:
        print(json.dumps({'error':str(exc)}));return 1
    except Exception:
        print(json.dumps({'error':'inference'}));return 1
    print(json.dumps(response));return 0


if __name__=='__main__':
    raise SystemExit(main())
