"""Intervals describe labeled evaluation accuracy, never a single model probability."""
import json
import math
from pathlib import Path


def wilson_interval(correct, total):
    """Two-sided 95% Wilson binomial interval (NIST e-Handbook 7.2.4.1)."""
    if type(correct) is not int or type(total) is not int or not 0 <= correct <= total:
        raise ValueError('Invalid evaluation counts')
    if not total:
        return None
    z = 1.959963984540054
    p = correct / total
    denominator = 1 + z*z/total
    center = (p + z*z/(2*total)) / denominator
    half = z * math.sqrt(p*(1-p)/total + z*z/(4*total*total)) / denominator
    return dict(method='Wilson', level=0.95, lower=0.0 if correct == 0 else max(0, center-half), upper=1.0 if correct == total else min(1, center+half))


def evaluate_choices(evidence, *, model, choices):
    """Use independently labeled held-out choices with a matching model and choice meanings.

    Sampling provenance is a caller assertion, not something this calculator can verify.
    Synthetic demonstrations expose counts but intentionally suppress inferential intervals.
    """
    if not isinstance(evidence, dict) or set(evidence) != {'model','population','sampling','choices','records'}:
        raise ValueError('Invalid choice evaluation schema')
    if evidence['model'] != model or evidence['choices'] != choices:
        raise ValueError('Evaluation model or choice definitions do not match this recommendation')
    if not isinstance(evidence['population'],str) or not evidence['population'].strip():
        raise ValueError('Evaluation requires a target population description')
    if evidence['sampling'] not in ('independent_held_out','illustrative'):
        raise ValueError('Unknown evaluation sampling method')
    if not isinstance(evidence['records'], list):
        raise ValueError('Evaluation records must be a list')
    counts={c:[0,0] for c in choices}
    seen=set()
    for row in evidence['records']:
        if not isinstance(row,dict) or set(row) != {'id','selected','acceptable'}:
            raise ValueError('Invalid evaluation record')
        if not isinstance(row['id'],str) or not row['id'] or row['id'] in seen:
            raise ValueError('Evaluation IDs must be nonempty and unique')
        if not isinstance(row['selected'],str) or row['selected'] not in choices:
            raise ValueError('Unknown evaluated choice')
        if not isinstance(row['acceptable'],list) or not row['acceptable'] or any(not isinstance(c,str) or c not in choices for c in row['acceptable']):
            raise ValueError('Evaluation requires independent acceptable-choice labels')
        seen.add(row['id'])
        count=counts[row['selected']]
        count[0]+=int(row['selected'] in row['acceptable'])
        count[1]+=1
    inferential=evidence['sampling'] == 'independent_held_out'
    return dict(model=model,population=evidence['population'],sampling=evidence['sampling'],
        interpretation='Historical accuracy conditional on selecting each choice, not a confidence interval for this individual recommendation. '
                       'Intervals assume representative independent held-out trials and trustworthy labels; provenance is supplied by the caller. '
                       'Intervals are pointwise, not simultaneous across choices.',
        choices={c:dict(correct=k,total=n,accuracy=k/n if n else None,
                        confidence_interval=wilson_interval(k,n) if inferential else None)
                 for c,(k,n) in counts.items()})


def load_evaluation(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, UnicodeError, ValueError):
        raise ValueError('Cannot read choice evaluation JSON') from None
