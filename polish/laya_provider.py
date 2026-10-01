"""Local, process-isolated Laya inference with a bounded runtime."""
import json
import math
import os
from pathlib import Path
import subprocess
import sys

from .recommendations import RecommendationError, validate_answer


class LayaDecisionModel:
    def __init__(self, checkpoint, *, device='cpu', timeout=180, python=None):
        self.checkpoint = Path(checkpoint).expanduser().resolve()
        self.device, self.timeout = device, timeout
        self.python = python or sys.executable
        self.details = None
        if device not in ('cpu', 'mps', 'cuda'):
            raise RecommendationError('Unsupported Laya device')
        if not math.isfinite(timeout) or timeout <= 0:
            raise RecommendationError('Laya timeout must be a positive finite number')

    def choose(self, state, choices):
        if not self.checkpoint.is_dir():
            raise RecommendationError('Laya checkpoint must be an existing local directory')
        if not 2 <= len(choices) <= 10:
            raise RecommendationError('Laya requires 2–10 eligible choices')
        # Complete objective and choices, with concise deterministic failure context.
        # Full graph checks remain in the report, not in the limited model context.
        compact = dict(objective=state['objective'],
                       failure_codes=sorted({f['code'] for f in state['findings']}),
                       candidate_validation='All offered choices pass all declared baseline and proposal scenarios. '
                                            'Unmodeled trade-offs in descriptions remain unverified.')
        request = dict(checkpoint=str(self.checkpoint),device=self.device,state=compact,choices=choices)
        env = {k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','LANG','SYSTEMROOT','WINDIR')}
        env.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',
                   TOKENIZERS_PARALLELISM='false')
        try:
            result = subprocess.run([str(self.python), str(Path(__file__).with_name('laya_worker.py'))],
                                    input=json.dumps(request),text=True,capture_output=True,env=env,
                                    timeout=self.timeout)
        except subprocess.TimeoutExpired:
            raise RecommendationError(f'Laya exceeded {self.timeout:g} seconds; worker terminated') from None
        except OSError:
            raise RecommendationError('Cannot start Laya Python runtime; check --laya-python') from None
        try:
            response=json.loads(result.stdout)
            if result.returncode or 'error' in response:
                code=response.get('error')
                errors={'dependencies':'Install polish-lang[laya] in the selected Python runtime',
                        'checkpoint':'Invalid or incomplete local Laya checkpoint',
                        'context':'Laya context budget exceeded; shorten the objective or candidate descriptions',
                        'device':'Requested Laya device is unavailable',
                        'inference':'Local Laya inference failed',
                        'options':'Laya option representations collapsed; shorten candidate descriptions'}
                raise RecommendationError(errors.get(code,'Local Laya worker failed'))
            answer=validate_answer(response,choices)
            self.details=dict(input_state=compact,device=response['device'],
                              checkpoint_sha256=response['checkpoint_sha256'],runtime_version=response['runtime_version'])
            return answer
        except (ValueError,KeyError,TypeError) as exc:
            if isinstance(exc,RecommendationError): raise
            raise RecommendationError('Laya worker returned an invalid response') from None
