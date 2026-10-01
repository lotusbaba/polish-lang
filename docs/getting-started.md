# Your first Polish runs

Run these commands from the repository root (the directory containing `pyproject.toml`
and `polish/`). Python 3.11 or newer is required.

## Install and verify the CLI

```sh
python3 -m venv .venv
.venv/bin/python -m pip install '.[dev,jev]'
.venv/bin/polish --help
```

`dev` installs pytest; `jev` installs the optional HTTP client. For offline use only,
install `.` instead. The help output should include `--candidates`, `--objective`,
and `--decision-provider`. Prefer explicit `.venv/bin/` paths so a globally installed
`polish` does not accidentally run a different version.

For source development, `.venv/bin/python -m polish` from the repository root runs
the current source. A regular installed CLI uses a packaged copy: rerun the install
after changes. Editable installation (`pip install -e '.[dev,jev]'`) is another option
if it works in your environment; use a regular installation if its import hook fails.

## 1. Run a simulation without any model

```sh
.venv/bin/polish simulate examples/shop.polishd
```

This checks modeled requests against declared expectations. It does not send requests
to a real shop, provision resources, or call an AI provider. `PASS` means the declared
expectation matched, including scenarios deliberately expecting an error or denial.

## 2. Compare candidate fixes offline

```sh
.venv/bin/polish plan \
  examples/recommendations/connection-pools/before.polishd \
  --proposed examples/recommendations/connection-pools/proposed.polishd \
  --candidates examples/recommendations/connection-pools/candidates.json \
  --objective "Preserve eight application instances and the existing database connection limit."
```

The baseline uses 2 × 50 = 100 database connections. The proposal uses 8 × 50 = 400,
exceeding the declared limit of 300. Two supplied fixes pass: 8 × 30 = 240 and
4 × 50 = 200. The unchanged failing candidate is rejected. `awaiting_model` means
Polish validated the alternatives but did not choose one. The objective does not
influence offline checks. **Exit code 1 is expected:** the original proposal fails.

## 3. Let Jev choose

Set the key without placing its literal value in shell history. In macOS's default
**zsh**:

```zsh
read -s "JEV_AI_API_KEY?Paste your Jev key: "
echo
export JEV_AI_API_KEY
```

In **bash**:

```bash
read -r -s -p "Paste your Jev key: " JEV_AI_API_KEY
echo
export JEV_AI_API_KEY
```

The CLI only reads environment variables (`JEV_AI_API_KEY`, then legacy `JEV_API_KEY`).
It does not load `.env` automatically or look in RadioWorkx or any other project.
You can use your existing secret manager to populate the environment instead. Do not
commit keys or put them in candidate descriptions. To remove the exported key from
this shell afterward, use `unset JEV_AI_API_KEY` (and `unset JEV_API_KEY` if used).

```sh
.venv/bin/polish plan \
  examples/recommendations/connection-pools/before.polishd \
  --proposed examples/recommendations/connection-pools/proposed.polishd \
  --candidates examples/recommendations/connection-pools/candidates.json \
  --objective "Preserve eight application instances and the existing database connection limit." \
  --decision-provider jev --json > /tmp/polish-jev-report.json
```

Enabling Jev sends the objective, candidate descriptions, graph changes, and simulation
evidence to its hosted API. This example makes one call when multiple candidates pass.
No application code or deployment is changed. Exit 1 still describes the original
failing proposal, even if recommendation selection succeeds.

## 4. Inspect the result

Open `/tmp/polish-jev-report.json` in your editor, or print just the recommendation:

```sh
.venv/bin/python -c 'import json; r=json.load(open("/tmp/polish-jev-report.json")); a=r["recommendations"]; print(json.dumps({k:a.get(k) for k in ("status", "selected", "selection", "model_calls", "probabilities", "error")}, indent=2))'
```

Look for `status: selected`, `selection: model`, and `model_calls: 1`. The objective
favors `smaller-pools`, though model selection is not guaranteed. These are model
choice probabilities, **not statistical confidence intervals or guarantees**. Historical
accuracy intervals require separate independent evaluation data; see the
[recommendation guide](../examples/recommendations/README.md#probabilities-versus-confidence-intervals).

To verify that fix directly, use its specification as the new proposal:

```sh
.venv/bin/polish plan \
  examples/recommendations/connection-pools/before.polishd \
  --proposed examples/recommendations/connection-pools/smaller-pools.polishd
```

This comparison should exit 0: the supplied fix satisfies the declared requirements.
It does not establish production capacity or apply the fix anywhere.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| `unrecognized arguments: --candidates` | Run `.venv/bin/polish --help`. Reinstall the current source with `.venv/bin/python -m pip install '.[dev,jev]'`; `command -v polish` shows which bare command your shell resolves. |
| `ModuleNotFoundError: polish` after editable installation | Use a regular install as above, or run `.venv/bin/python -m polish` from the repository root. |
| `model_error` asking for a key | Export `JEV_AI_API_KEY` in the same shell; placing it in `.env` alone is insufficient. |
| Missing `httpx` | Install the `jev` extra in the same virtual environment. |
| HTTP 401/403 | Check the key and provider access. Do not paste credentials into issues or logs. |
| Transport error or other HTTP error | Inspect the safe error message and provider availability; the client does not retry automatically. |
| `awaiting_model` | No provider was enabled and multiple candidates passed; add `--decision-provider jev` when ready. |
| `no_valid_candidates` | Inspect candidate validation findings and revise the alternatives without weakening requirements. |
| Exit 1 despite a recommendation | Expected when the original proposal fails; inspect the recommendation status separately. Exit codes do not encode provider success. |
| `confidence_interval: null` | A single prediction cannot supply a statistical confidence interval. No compatible independent evaluation data was provided. |

## Run the local tests

```sh
.venv/bin/python -m pytest -q
```

Install the `dev` and `jev` extras to include mocked HTTP adapter checks. Unit tests
make no hosted model requests and need no API keys. Live example reports are historical
integration evidence, not tests of a deployed application or a calibration benchmark.

## Use a local model instead of Jev

Use `--decision-provider laya` with an explicit local checkpoint and optional separate
Python runtime. Follow [the Laya walkthrough](../examples/recommendations/README.md#local-laya-provider).
Laya needs no key, blocks network access during inference, and reports a model error
if loading or inference exceeds the configured timeout. A valid local path and the
optional ML dependencies are required; selecting Laya does not download them.

The [browser/general checkpoint comparison](../examples/recommendations/README.md#browser-versus-general-english-checkpoint)
shows why reviewing the selected candidate matters: both local checkpoints selected
fewer instances even when asked to preserve eight. Simulator eligibility and objective
alignment are distinct; a high model probability does not replace that review.
