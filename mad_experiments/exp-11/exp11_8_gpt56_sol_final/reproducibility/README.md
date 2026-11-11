# GPT-5.6 Sol paper batch

This directory is the self-contained exp-11-8 benchmark:

- `paper-manifest.csv` — canonical 50-paper manifest
- `papers/` — local paper inputs
- `config/` — model, temperature, and credential configuration
- `exp-11-8.py` — peer-review engine
- `batch_runner_exp11_8.py` — manifest runner
- `scripts/` — start, status, and stop commands
- `../results/batch_experiments/exp11_8/<run-id>/` — isolated run outputs and logs

The active baseline uses `gpt-5.6-sol`, explicitly sends
`reasoning_effort: "none"` on every Chat Completions request, and preserves the
configured temperatures: 0.0 for selection/audits/editor and 0.35 for debate.

## Setup and validation

```sh
cd PROJECT_ROOT
python3 -m venv .venv
.venv/bin/python -m pip install -r benchmark/requirements.txt
.venv/bin/python benchmark/batch_runner_exp11_8.py --validate-only
```

The runner resolves each paper by `doc_id`, so the manifest remains portable
even if an older `source_path` value is stale.

## Start the full run

First run one complete paper through the same two-round pipeline:

```sh
benchmark/scripts/run_smoke_test.sh
```

The default smoke paper is `feds-2020-021`, the smallest local input. To choose
another paper or output ID:

```sh
benchmark/scripts/run_smoke_test.sh feds-2019-008 my_smoke_run
```

After reviewing the smoke-test report, start the full run:

```sh
PYTHON_BIN=.venv/bin/python benchmark/scripts/start_batch.sh gpt56_sol_full
```

The script prompts for the API key with hidden input and does not write it to
disk. It launches with `nohup` and `caffeinate`, writes a PID and log under the
run directory, and resumes by skipping any paper with a completed
`structured_results.json`.

```sh
benchmark/scripts/status_batch.sh gpt56_sol_full
benchmark/scripts/stop_batch.sh gpt56_sol_full
```

`caffeinate` prevents idle sleep while the batch is active. A MacBook still
suspends local processes when its lid is closed or the machine loses power. For
that requirement, run the same directory on an always-on server or cloud VM.
