# Decision-model recommendations

The deterministic compiler, simulator, and planner remain authoritative. An optional
Jev decision model chooses among explicitly supplied architecture alternatives that
pass **all** requirements from both the baseline and proposed specifications.
A model recommendation cannot turn a failing proposal into a passing plan. No edits
are applied, and scenario expectations are never changed by this feature.

The current implementation ranks authored candidate changes; it does not generate
new patches, infer deployment facts, measure costs, or replace configured rule hooks.
Existing `decisions.json` suggestions remain available without model calls. One
eligible candidate is selected deterministically; zero eligible candidates stop
recommendation selection. Two or more eligible candidates require one model call.

## Examples to try

| Example | Failure | Eligible alternatives | Example objective |
| --- | --- | --- | --- |
| `connection-pools` | Eight pools of 50 exceed a database connection limit of 300 | Reduce pools to 30; reduce app instances to four | Preserve eight application instances |
| `cpu-capacity` | One two-vCPU instance cannot handle the declared workload | Two two-vCPU instances; one four-vCPU instance | Preserve the existing host size |
| `stream-retention` | 160 messages at eight/sec cannot cover 21 seconds of lag | Retain 240 messages; retain 480 | Choose the smaller passing retention allocation |

Each directory contains a baseline, failing proposal, two passing alternatives,
an unchanged failing control, and `candidates.json`. All capacity values are synthetic.
These are intentionally clear preference examples, not a difficult model benchmark.
Pooling wait time, instance prices, and stream memory bytes are not modeled in these
examples. They remain follow-up measurements before a production decision.

The [live example report](results/report.html) links to raw JSON for three Jev calls.
All three choices matched their stated objectives. Each returned probability 1 for
its selected choice; those values are **not** calibrated guarantees or confidence
intervals. The original plan stays `ok: false` with an `introduced` failure.

## Run

For environment setup and troubleshooting, see [the new-user guide](../../docs/getting-started.md).
The CLI reads `JEV_AI_API_KEY` (or legacy `JEV_API_KEY`) from the process environment;
it does **not** automatically load `.env` or search another project's credentials.
Without `--decision-provider jev`, no key is needed and no hosted request is made.


From the Polish repository root:

```sh
.venv/bin/python -m pip install '.[jev]'
# Supply JEV_AI_API_KEY through your existing local secret/environment mechanism.
# Never put the key in the manifest, command arguments, source, or report.
.venv/bin/python -m polish plan \
  examples/recommendations/connection-pools/before.polishd \
  --proposed examples/recommendations/connection-pools/proposed.polishd \
  --candidates examples/recommendations/connection-pools/candidates.json \
  --objective 'Preserve eight application instances and the existing database connection limit.' \
  --decision-provider jev --json
```

This command exits **1** because the original proposal fails, even though passing
remedies are recommended. Omit `--decision-provider jev` for offline candidate checks;
with multiple eligible candidates the status is `awaiting_model`, with no invented
probabilities. `--scenario` filters the main plan, but candidate validation always
runs every declared scenario to avoid hiding a regression elsewhere.

Use the corresponding paths for the other examples, with these objectives:

- CPU: “Retain the existing two-vCPU host size; prefer adding an instance.”
- Retention: “Cover the 21-second lag with the smallest offered passing retention allocation.”

The Jev request contains the objective, model findings, graph changes, candidate
validation evidence, and descriptions. Enabling the provider sends that architectural
information to Jev. Credentials are used only in the Authorization header. The client
uses a 60-second timeout, no retries or redirects, and does not print response bodies
on errors. Reports distinguish `model_error` from a successful selection. No hosted
calls are made by the ordinary unit tests.

## Probabilities versus confidence intervals

`recommendations.probabilities` contains the provider's distribution over the eligible
choices. It describes model preference in this request. It does not estimate the
chance that a deployment will work. `confidence_interval` for a single recommendation
is `null`, with an explicit explanation.

`--choice-evaluation evaluation.json` optionally attaches historical per-choice
accuracy with a **95% Wilson interval**. This requires independently labeled held-out
trials, the exact returned model identifier, and matching choice IDs and descriptions.
The interval estimates historical correctness conditional on selecting a choice,
not the correctness probability of this particular architecture change. It assumes
representative independent trials with trustworthy labels; Polish checks the file
structure but cannot verify the caller's sampling provenance. Intervals are pointwise,
not simultaneous across choices. See the [NIST Wilson interval reference](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm).

Evaluation JSON structure (the record below is illustrative, not real calibration):

```json
{
  "model": "exact-returned-model-id",
  "population": "Describe the held-out architecture tasks and labeling process",
  "sampling": "illustrative",
  "choices": {"a": "Exact choice description A", "b": "Exact choice description B"},
  "records": [
    {"id": "case-001", "selected": "a", "acceptable": ["a"]}
  ]
}
```

`illustrative` evidence reports counts but suppresses confidence intervals. Only
use `independent_held_out` for data actually collected that way. A choice with no
observations also receives no interval. Model/choice mismatches are reported as
`evaluation_error`, without silently borrowing incompatible evidence. Three hand-picked
examples or repeated requests on one prompt are not independent accuracy trials.

As a **hypothetical arithmetic example**, 80 correct selections out of 100 independent
held-out trials gives a 95% Wilson interval of approximately **71.1%–86.7%**. No such
100-trial evaluation was run here. Unit tests check this calculation and reject
malformed, duplicated, or mismatched evidence.

## Read the output

Top-level `ok` and the exit code describe the **original proposal**, not the chosen
remedy. In these examples, `ok: false`, `status: introduced`, and exit 1 are expected.
Inspect `recommendations` separately:

| Field | Meaning |
| --- | --- |
| `status: awaiting_model` | Offline validation found multiple eligible choices; none selected |
| `status: selected`, `selection: model` | The provider returned a validated candidate ID |
| `selection: deterministic_sole_candidate` | Only one candidate passed; no model call needed |
| `status: no_valid_candidates` | No candidate passed all original requirements |
| `status: model_error` | Provider setup, transport, or response failed; inspect `error` |
| `candidates[].eligible` | Candidate passed both baseline and proposal requirement sets |
| `candidates[].validation` | Full deterministic comparison evidence for that candidate |
| `selected` | Candidate ID, linked to an authored `.polishd` file by the manifest |
| `model_calls` | Model-call attempts in this recommendation step, not scenario count or guaranteed billable requests |
| `probabilities` | Provider preference over eligible choices; absent for offline or deterministic selection |

The objective is sent to the decision model. It does not change simulation constraints
or filter candidates by itself. A candidate can pass the simulator but be a poor match
for your objective; the model chooses among the passing candidates. Verify the selected
ID yourself when an objective is a hard requirement.

## Supply your own alternatives

Create one complete `.polishd` specification for each candidate and a JSON manifest:

```json
[
  {"id": "smaller-pools", "description": "Preserve eight instances; use pools of 30.", "file": "smaller-pools.polishd"},
  {"id": "fewer-instances", "description": "Use four instances with pools of 50.", "file": "fewer-instances.polishd"}
]
```

Paths are relative to the manifest. Provide 1–10 unique IDs and a nonempty objective.
Describe trade-offs accurately; the model cannot verify unmodeled claims. Keep the
original requirements: candidate validation reruns requirements from both input
specifications, so deleting or weakening a candidate's expectations cannot hide an
existing regression. Add scenarios for important properties currently unmodeled.

After reviewing a selected candidate, run `plan` again with that file as `--proposed`.
This is a separate verification step; choosing a candidate never edits the proposal
or deploys anything.

## Local Laya provider

Laya uses the same candidate validation and output checks as Jev. Enable it with
`--decision-provider laya --laya-checkpoint /path/to/checkpoint`. No API key is needed,
and inference never downloads a checkpoint. The child process uses Hugging Face
offline settings and blocks outbound socket connections. A separate process also
keeps model logs out of JSON output and allows a stalled load/inference to be killed.

Install the optional runtime in the main virtual environment:

```sh
.venv/bin/python -m pip install '.[laya]'
```

Alternatively, keep the larger ML dependencies separate:

```sh
python3 -m venv .venv-laya
.venv-laya/bin/python -m pip install 'laya==0.3.21'
```

Pass `--laya-python .venv-laya/bin/python` when using that separate environment.
An existing compatible runtime can also be reused; no RadioWorkx import is required.
The integration is tested against the Laya 0.3.21 API. See the
[upstream project](https://github.com/NandhaKishorM/laya) for runtime installation.

Supply a trusted local checkpoint containing `rl_agent_config.json`,
`model.safetensors`, `encoder/config.json`, `tokenizer/tokenizer.json`, and
`tokenizer/tokenizer_config.json`. For a reproducible initial example, download the
same pinned browser checkpoint used in the local RadioWorkx experiments. This is
an explicit network step, separate from inference; a browser checkpoint is not an
architecture-recommendation quality benchmark:

```sh
.venv-laya/bin/python - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id="cklxx/laya-browser",
    revision="645cf366a2ae35f1086e8c20eff48f909bb49206",
    local_dir=".models/laya-browser",
    allow_patterns=["rl_agent_config.json", "model.safetensors", "encoder/config.json",
                    "tokenizer/tokenizer.json", "tokenizer/tokenizer_config.json"],
)
PY
```

Run the same candidate comparison locally:

```sh
.venv/bin/python -m polish plan \
  examples/recommendations/connection-pools/before.polishd \
  --proposed examples/recommendations/connection-pools/proposed.polishd \
  --candidates examples/recommendations/connection-pools/candidates.json \
  --objective "Preserve eight application instances and the existing database connection limit." \
  --decision-provider laya \
  --laya-checkpoint .models/laya-browser \
  --laya-python .venv-laya/bin/python \
  --laya-device cpu --laya-timeout 180 \
  --json > /tmp/polish-laya-report.json
```

Omit `--laya-python` if Laya is installed in the same environment as Polish.
`--laya-device` defaults to `cpu`; `mps` and `cuda` must be available in the runtime.
`--laya-timeout` defaults to 180 seconds and includes hashing, imports, loading, and
inference. A timeout produces `status: model_error` and terminates the worker. There
are no retries or implicit hosted fallbacks. One eligible candidate still bypasses
inference entirely. The original plan exit code remains independent of model status.

Laya receives a compact state containing the complete objective, failure codes, and
a statement of candidate validation, plus every eligible ID and description. Full
graph evidence remains in the report. `provider_details.input_state` shows this exact
state. Oversized inputs are rejected rather than knowingly truncating the objective
or dropping candidates. Shorten descriptions if a context-budget error is reported.

Successful reports include the runtime version, device, and a SHA-256 fingerprint
of the five checkpoint files. `model` incorporates the fingerprint and Laya version,
so historical choice evaluations cannot silently match a different checkpoint.
Probabilities remain uncalibrated preferences; the confidence-interval rules above
apply equally to Laya. Different provider inputs mean this is an integration comparison,
not a controlled model-quality benchmark. A fresh worker loads once per recommendation;
there is no persistent model server or shared queue in this CLI.

### Local smoke result

The [local CPU run](results/laya-connection-pools.json) completed using Laya 0.3.21
and the pinned browser checkpoint above, with socket connections blocked. The first
90-second attempt timed out during startup; the 180-second attempt completed.
It selected `fewer-instances` (0.8544) over `smaller-pools` (0.1456), despite the
objective to preserve eight instances. Both candidates satisfy the declared
simulation requirements, but this selection does **not** satisfy that preference.
This verifies integration, not recommendation quality. No provider fallback or
changes to expectations were used to obtain the result.

### Browser versus general English checkpoint

We also tested the cached general English checkpoint
`convaiinnovations/laya@55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851` on CPU with
Laya 0.3.21. Its local files were verified against their recorded SHA-256 manifest.
The objective, candidate descriptions, compact input state, and deterministic
validation results were identical to the browser-checkpoint run. Each checkpoint
used its own configured context limits; both accepted the complete test input.

| Checkpoint | Smaller pools | Fewer instances | Selected |
| --- | ---: | ---: | --- |
| Browser | 0.1456 | 0.8544 | Fewer instances |
| General English | 0.3941 | 0.6059 | Fewer instances |

Both selected an alternative inconsistent with the objective to preserve eight
instances. The general checkpoint assigned more probability to the preferred choice,
but did not improve the selected answer on this case. These are uncalibrated choice
probabilities, not confidence intervals. One synthetic case does not establish
relative accuracy across architecture tasks. This run does not change the checkpoint
you pass explicitly to the CLI.

Evidence: [general English report](results/laya-general-connection-pools.json),
[comparison and model fingerprints](results/laya-checkpoint-comparison.json), and
[HTML overview](results/report.html). The general checkpoint is an existing pinned
snapshot, not a claim about the latest upstream revision.

To reproduce, download that explicit revision into a separate directory using the
`snapshot_download` example above, changing `repo_id` to `convaiinnovations/laya`,
`revision` to `55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851`, and `local_dir` to
`.models/laya-general`. Keep the same five allowed files. Then repeat the identical
plan command, changing only `--laya-checkpoint .models/laya-general` and the output
file to `/tmp/polish-laya-general-report.json`.
