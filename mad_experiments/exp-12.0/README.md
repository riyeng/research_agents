# exp-12: specialist peer review

Select three specialists → independent PRE reviews → synchronous debate → independent
POST reassessment → qualitative editor synthesis. The architecture preserves raw
judgments and issue history without computing paper-quality scores or reviewer weights.

## Layout

```text
exp-12.py                         Prompts, examples, ledger, workflow and CLI
config.py                        Defaults and optional JSON overrides
schemas.py                       Review and saved-record contracts
token_tracker.py                 Usage and optional cost accounting
retrieval.py                     Deterministic manuscript evidence retrieval
pyproject.toml                   Installation and dependencies
tests/                           Offline contract, retrieval and workflow tests
analysis/                        Offline feature construction and evaluation
    feature_selection.py         PRE/POST summaries, persistence and source provenance
    evaluation_analysis.py       Evaluation orchestration, exports and CLI
    evaluation_core.py           Validation, rating registry, metrics and verdicts
    evaluation_models.py         Fixed/ridge adapters, holdout search and bootstrap
    evaluation_spec.example.json Explicit illustrative analysis/scoring settings
```

Feature construction and evaluation are implemented and tested offline with synthetic
review outputs. Actual benchmark findings require exp-12 runs. Splitting/resampling
uses papers as independent units, with outcomes and PRE/POST/mechanism measures kept
distinguishable. These analysis modules use only the Python standard library.

Create a CSV manifest with `doc_id,result_path` columns. Each path identifies one
exp-12 `result.json` and is resolved relative to the manifest. Run from this folder:

```sh
python analysis/feature_selection.py manifest.csv --output features.json
```

This standard-library-only command exports a versioned feature dictionary, paper
features, original reviewer judgments, issue snapshots, and source hashes. PRE and
POST have separate sums, means, medians, extrema, and category proportions. Repair
survival is unweighted: among paired assessed reviewers with PRE scope > 0, the
fraction still requiring repair POST. `concern_*` and `insight_weakness_*` provide
separate PRE/POST counts and severity/confidence summaries for `substantive_flaw`
and `insight_weakness`. Each family's survival follows its original PRE issue IDs,
with count, initial-severity, initial-confidence, and severity-times-confidence
weights. Weights stay fixed at PRE values. Missing and
uncertain endpoints are excluded from observed rates with counts and coverage;
complete-cohort rates are null when any endpoint is unresolved. No eligible items
or zero total weight gives a null rate. Primary POST summaries include maintained
or narrowed concerns regardless of creation/revision timing, plus newly asserted
POST observations. New POST observations retain their stated severity/confidence
and use the `NEW_POST` endpoint. Late timing remains an overlapping diagnostic;
late counts must not be added to primary POST counts. Uncertain/unknown concerns
remain separate. Proposed duplicate links never establish equivalence.
Newly created debate and POST IDs are tracked under `issue_debate_new_*` and
`issue_post_new_*`: counts by creation type, creation severity/confidence summaries,
and source-call coverage counts. Source-call denominators count distinct called
assessments: reviewer/round for debate and reviewer for POST. Failed calls count;
skipped records and failures before a call do not. Retries count once. The legacy
`n_source_calls_expected` column now carries this actual-called denominator.
Corresponding `concern_*_new_*` and
`insight_weakness_*_new_*` columns describe those discovery types separately.
Debate discovery cohorts also have final-endpoint survival, weighted at creation;
they never enter PRE-cohort denominators. Revisions retain their original ID and
are not discoveries. Issue rows retain creation round, original type/weights, and
related IDs, but related links do not identify parent-child forks. Feature exports
now use `exp12-features-v3`; regenerate older exports before evaluation.
Verdicts are preserved for disagreement analysis in `evaluation_analysis.py`.
The final editor verdict is retained separately in `editor_outcome`, with
availability and source references, and never enters the candidate predictors.
The evaluator exports `editor_verdicts.csv` and analyzes features against the editor
verdict using the ordinal ordering REJECT < RESUBMIT < ACCEPT for rank metrics.
Missing editor verdicts are excluded with coverage; they are not encoded as rejection.
Numeric ordinal encodings are exploratory, not quality scores.

## Offline evaluation

The evaluator reads the exported feature JSON, not the raw-result manifest. From
this folder, once benchmark feature outputs exist:

```sh
python analysis/evaluation_analysis.py --features features.json --outcomes ../../../rankings.csv --spec analysis/evaluation_spec.example.json --output-dir evaluation-development
```

Use the actual location of `rankings.csv`; it is outside the `research_agents`
repository in this workspace. `--output-dir` must be new or empty. Inputs are
read-only. `--synthetic` labels fixture results in JSON and every exported CSV.
No manuscript review, model API call, network access, or benchmark generation is
performed by the evaluator.

The current `rankings.csv` is accepted directly: `id` is joined to feature `doc_id`;
`wp_cites`, `cites`, `sjr_score`, rank columns, GSM measures, quartile, tier and Stern
membership have registered definitions. Historical citation/SJR names still work
as explicit aliases. Status checks apply when supplied; the compact CSV supplies no
per-cell status/date fields, and exports record that absence rather than inventing them.

By default, a seeded 30% holdout is reserved using benchmark IDs alone, shared across
all outcomes. For 50 papers this gives 35 development papers and 15 holdout papers.
Development reports expose associations, editor-verdict comparisons and model results
only for development papers. They export the split so it can be reused. Without an
aggregation spec, the evaluator reports descriptive/univariate associations and matched
PRE/POST comparisons without starting a model search.

The example JSON enables six illustrative fixed/ridge rules, including continuous
and binned representations and level-only versus level-plus-change predictors.
Its fixed weights are examples rather than established quality weights. Every rule
is compared on the same two large development validation folds for each target;
concordance selects the winner, while Spearman, Kendall tau-b and concordance are
all reported. Imputation, scaling, ridge coefficients and quantile bins are fitted
on each fold's training papers. Fold rankings are evaluated separately. With 35
eligible development papers, each validation fold contains 17 or 18 papers. Fold
counts adapt to target coverage; insufficient or fully tied targets report explicit
unavailable/undefined reasons without stopping other targets.

The same candidate search also predicts the internal editor verdict, encoded as
REJECT < RESUBMIT < ACCEPT. Separate tables describe individual features versus
that internal outcome, features versus external outcomes, and the actual editor
verdict versus every external target. This distinguishes explaining the editor's
decision from predicting external rankings. Editor verdict is never a predictor.

After choosing the analysis settings using development results, run the exported
frozen specification to expose final holdout results:

```sh
python analysis/evaluation_analysis.py --features features.json --outcomes ../../../rankings.csv --spec evaluation-development/frozen_evaluation_spec.json --output-dir evaluation-final
```

The frozen specification fixes settings, split membership, feature definitions and
development data. The final run evaluates only each target's development-selected
rule on holdout papers, plus the editor-verdict baseline. Changing development data
or settings requires a new development run. Treat holdout results as final: do not
use them to revise candidates, weights, feature selection or the split. Development
cross-validation comparisons guide selection; final holdout estimates assess it.

Change predictors are constructed in evaluation, leaving source feature exports
unchanged. For each numeric reviewer construct, PRE and POST are matched by persona.
`*_matched_change_{sum,mean,median,min,max}` summarizes POST minus PRE, and
`*_matched_improvement_{sum,mean,median,min,max}` summarizes PRE minus POST. The
minimum/maximum of individual changes differs from `*_matched_min_level_*` and
`*_matched_max_level_*`, which subtract cohort extrema across phases. These use the
same matched cohort and report coverage; missing endpoints never become zero.
"Improvement" names the requested reduction convention: positive reductions are
natural for repair scope and severity, whereas rising novelty/insight has positive
change. Both signs contain the same information; use one sign per quantity in a model.

For `concern` and `insight_weakness`, matched severity/confidence changes compare an
issue's creation value with its definitive final value, including debate-created
issues. Creation type defines the cohort even after reclassification. Retractions,
uncertain endpoints and missing endpoints are counted separately, not assigned zero
severity. New POST issues have zero elapsed change. Survival features capture the
separate question of which concerns were retained. Ordinal differences are descriptive
encodings rather than calibrated interval-scale changes.

Contracts and settings:

- Targets default to available citation, SJR, GSM, quartile, tier, and Stern columns;
  historical HWY columns are also supported when present.
  They retain eligibility/status/date metadata and higher-is-better orientation.
  Missing values remain null; citation zero is valid. Custom `targets` mappings can
  define other columns using `column`, `type`, `direction`, and optional status rules.
- Verdict disagreement uses only `ACCEPT`/`RESUBMIT`/`REJECT`: any split, fraction
  of differing pairs, and polarization. Matched transitions prevent a missing
  dissenting reviewer from creating apparent convergence.
- `metrics` supports `spearman`, `kendall_tau_b`, `concordance`, `auc`, `mae`, and
  `rmse`. Concordance excludes external ties and gives score ties half credit.
  AUC applies to binary targets; magnitude errors require an explicitly meaningful
  common score/outcome scale. No asymptotic p-values are fabricated.
- `feature_directions` maps selected feature names to `higher` or `lower`, declared
  before inspecting results. The default is `higher`; directions are not optimized.
  Single-feature tables report available-case paper IDs; PRE/POST comparisons use
  identical complete-paper cohorts. These raw associations are not held-out models.
- `holdout` accepts `fraction`, `seed`, and `stage`, or explicit `development_ids`
  and `holdout_ids` forming an exact partition of benchmark IDs. Its default stage
  is `development`; the exported frozen spec sets `final`. Set `holdout` to false
  only for exploratory full-sample analysis; that route supports legacy nested folds.
- `derived_features` defaults to true. The augmented dictionary documents all new
  matched changes, reductions and coverage counts. `features` selects analysis columns;
  aggregation candidates supply their own explicit predictor lists.
- `aggregation` supplies at most 64 `candidates`, a selection `metric`,
  `validation_folds` (default 2), `min_validation_papers` and `min_train_papers`
  (default 5 each), and `seed`. Fixed rules use `weights`; ridge rules use
  `features` and positive `alpha`. Imputation, scaling, coefficients, and empirical
  `bins.quantiles` are fitted on training papers only. `bins.thresholds` supplies fixed
  cuts. `representation` selects `continuous` or `binned` for a candidate.
- `internal_aggregation` defaults to the external `aggregation` settings, fitted
  separately against editor verdict. Set it to false to disable or supply a
  separate candidate configuration. Set `aggregation` to null to disable external
  model search. Feature associations and the editor baseline remain available.
- `ablation` supplies a fixed ridge `base` and `families` mapping names to feature
  lists. Full and reduced models are refitted on the same outer folds; no family
  winner is selected. `score_audit.rules` supplies explicit fixed formulas to inspect
  contributions separately from external validity.
- `bootstrap` accepts `features`, `metric`, `n_resamples`, `confidence`, and `seed`.
  Its paper bootstrap describes fixed feature/outcome associations; it does not
  refit models or quantify uncertainty of model selection. Empty cohorts and
  undefined resamples remain explicit.
- `persona_analysis` is true or an options object (`constructs`, `metric`). Optional
  `domain_records` are explicit `{doc_id, domain}` tags. Persona comparisons remain
  descriptive because the review panel is selected adaptively. Generalist, internal,
  and mechanism inputs are optional and report skipped status when absent.

Outputs include `results.json`, the augmented feature dictionary,
`derived_features.csv`, `holdout_split.json`, `frozen_evaluation_spec.json`,
`internal_associations.csv`, `editor_external_validity.csv`, and external feature
associations. `candidate_comparisons.csv` reports every candidate and reporting
metric; `selected_configurations.csv` includes fitted weights, transforms and bins;
`paper_scores.csv` retains continuous and discrete scores. `holdout_results.csv`
contains only final selected-rule results and is empty during development.
`target_status.csv` identifies searches that could not be estimated. Verdict,
transition, correlation, matched PRE/POST, persona, and optional ablation/bootstrap/
score-audit tables are also exported. Configuration/input hashes, coverage, ties,
and undefined reasons are retained.
An optional `linkage.expected_dictionary_sha256` can require an exact frozen
dictionary in addition to the version/policy checks.

Run the independent analysis tests without installing runtime dependencies:

```sh
python3 -B -m unittest discover -s tests -p 'test_*analysis.py' -v
python3 -B -m unittest discover -s tests -p test_feature_selection.py -v
```

See [the original specification](exp_12_instructions.md) and
[the changelog](exp_12_proposal.md) for detailed decisions and validation history.

## Install and test

From this folder, using Python 3.9 or newer:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
python -m unittest discover -s tests -v
```

Dependencies are `pydantic` and `requests`; `requirements.txt` provides an equivalent
installation shortcut. Tests run offline without credentials. Installation includes
the review command and four helper modules; run `analysis/` directly from the source tree.

## Run

Supply a UTF-8 manuscript text file and a chat-completions-compatible endpoint/model:

```sh
export PEER_REVIEW_API_BASE="https://your-provider.example/v1"
export PEER_REVIEW_MODEL="your-model-id"
# Set PEER_REVIEW_API_KEY if your endpoint requires it.
python exp-12.py /path/to/manuscript.txt --output-dir /path/to/reviews
```

Keyless local endpoints are supported. `OPENAI_BASE_URL` and `OPENAI_API_KEY` are
accepted fallbacks. Choose a model supported by your endpoint.

Defaults: two debate rounds, 14 successful review calls, reasoning off, temperature
0 except debate at 0.35. Retries retain the requested temperature. Use `--rounds`,
`--model`, or `--config settings.json` to override [configuration defaults](config.py);
explicit stage models take precedence over `--model`.

## Context and issue history

- PRE is independent; each debate round shares one frozen ledger snapshot. POST
  sees the final debate state and its own PRE, with no peer POST responses.
- All personas can assess all four novelty domains. Domain labels belong to claims.
- Substantive revisions retain the issue ID and append statement, evidence, severity
  and confidence versions. Arguments retain the version they address. Silence does
  not resolve an issue; late revisions do not inherit earlier response opportunities.
- PRE and round 1 use bounded manuscript text; later rounds use the abstract and
  complete ledger. Oversized contexts fail explicitly without dropping ledger entries.

POST defaults to retrieved BM25 evidence: at most 12,000 serialized characters,
with raw passages capped at 25% of manuscript length. Exact text, offsets, queries
and rankings are saved. `post_manuscript_context` also supports `abstract_only` and
bounded `full_manuscript`. Embedding/hybrid retrieval requires an explicit backend
passed to `run_review`; no embedding model is downloaded automatically.

## Outputs and status

Each run creates a unique folder under `--output-dir` (default `results/exp-12/`):

- `result.json` and `report.md`: raw assessments/history and readable editor review.
- `calls.json` and `tokens.json`: attempts, errors, usage and optional priced costs.
- Exact requests/responses, settings, prompt configuration, snapshots, POST evidence
  traces and reproducibility hashes.

Failed assessments remain null; successful empty issue lists remain valid. Empty
retrieval is recorded; failed retrieval prevents that POST call. Runs report
`COMPLETE`, `PARTIAL` or `FAILED`; the CLI exits nonzero for incomplete runs.
Missing usage or prices remain unknown.

Recorded validation: 78 offline tests and an installed-package workflow passed.
Live manuscript acceptance is pending. Evaluator validation uses synthetic outputs;
no empirical exp-12 benchmark evaluation has been performed.
