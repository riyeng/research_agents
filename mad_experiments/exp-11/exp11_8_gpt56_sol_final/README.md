# MAD exp-11-8 — GPT-5.6 Sol results

This package contains the canonical outputs for 50 papers in the
exp-11-8 manifest. It was assembled from the main `gpt56_sol_full` run with the
validated repair substitutions listed below.

## Experiment configuration

- Model: `gpt-5.6-sol`
- Papers: 50
- Debate rounds per paper: 2
- Expected model calls per paper: 11
- Canonical successful calls: 550
- Reasoning: `medium` for independent audits and both debate rounds
- No reasoning: panel selection and final editorial synthesis
- Selection/editor temperature: 0.0
- Audit/debate temperature when reasoning is active: forced to 1.0 by the engine
- Canonical measured model cost represented here: $111.0736

The cost above describes the selected canonical outputs. It does not include
discarded partial attempts, smoke tests, or unrelated runs.

## Repair substitutions

- `feds-2020-006` from repair run `repair_feds-2020-006`
- `ifdp-2019-1247` from repair run `repair_ifdp-2019-1247`

## Package contents

- `batch_summary_clean.csv`: one manifest-ordered row per paper
- `integrity_report.csv`: per-paper validation and source-run provenance
- `paper-manifest.csv`: the experiment manifest (source manuscripts excluded)
- `papers/<doc_id>/review.md`: human-readable review
- `papers/<doc_id>/structured_results.json`: machine-readable detailed result
- `papers/<doc_id>/metadata.json`: score, timing, token, and cost metadata
- `papers/<doc_id>/token_usage.json`: normalized token-usage record
- `reproducibility/`: runner, engine, configuration, and dependency specification
- `checksums.sha256`: SHA-256 checksums for package files

## Validation contract

Every included paper must have all required files, exactly 11 recorded model
calls, three valid independent audits, six usable debate responses, a usable
editor synthesis, and a non-system-error decision. `integrity_report.csv`
records these checks.

Local absolute paths were replaced with `PROJECT_ROOT/`. API credentials and
source manuscript files are not included.
