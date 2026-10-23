"""Offline evaluation of frozen exp-12 features and separately specified scores.

No review outputs or external outcomes are synthesized by this module. Tests use
synthetic fixtures; real benchmark evaluation requires an exported feature JSON
from feature_selection.py plus a paper-linked outcome CSV such as rankings.csv.
Verdict disagreement is categorical and belongs here. Source feature formulas,
raw review records and ratings are never changed. Individual feature associations
are descriptive; trained score comparisons require explicit paper-level splits.

CLI:
    python analysis/evaluation_analysis.py --features features.json \
        --outcomes rankings.csv --spec evaluation_spec.json --output-dir evaluation

The JSON spec selects features, targets, metrics, and optional aggregation,
ablation, bootstrap, or score-audit settings. There is no default final score or
model search. Use --synthetic for fixture outputs. All operations are offline.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import csv
import hashlib
import json
import math
from pathlib import Path
from statistics import mean

try:
    from .evaluation_core import (
        load_features_and_outcomes, register_external_outcomes, metric_result,
        construct_verdict_diagnostics, describe_feature_relationships,
        analyze_persona_and_domain_patterns, derive_change_features,
    )
    from .evaluation_models import (
        LinearScoreAdapter, make_paper_splits, evaluate_aggregation_rules,
        evaluate_feature_families, run_robustness_checks, make_holdout_partition, evaluate_holdout_rules,
    )
except ImportError:  # Direct execution from the source-tree analysis directory.
    from evaluation_core import (
        load_features_and_outcomes, register_external_outcomes, metric_result,
        construct_verdict_diagnostics, describe_feature_relationships,
        analyze_persona_and_domain_patterns, derive_change_features,
    )
    from evaluation_models import (
        LinearScoreAdapter, make_paper_splits, evaluate_aggregation_rules,
        evaluate_feature_families, run_robustness_checks, make_holdout_partition, evaluate_holdout_rules,
    )


EVALUATION_VERSION = "exp12-evaluation-v3"
METRICS = ("spearman", "kendall_tau_b", "concordance", "mae", "rmse", "auc")
ERROR_METRICS = ("mae", "rmse")
SPEC_KEYS = {
    "features", "targets", "metrics", "feature_directions", "synthetic",
    "aggregation", "ablation", "bootstrap", "score_audit", "persona_analysis",
    "domain_records", "linkage", "verdict", "diagnostics", "pre_post_pairs",
    "internal_outcomes", "internal_spec", "generalist", "mechanisms",
    "derived_features", "holdout", "internal_aggregation", "development_spec_sha256",
    "development_data_sha256",
}
DEFAULT_FEATURES = [
    f"{construct}_{phase}_median"
    for construct in ("novelty_methodological", "novelty_empirical", "novelty_theoretical",
                      "novelty_policy", "insight", "repair_scope")
    for phase in ("pre", "post")
] + ["concern_pre_severity_max", "concern_post_severity_max",
     "repair_scope_survival_rate", "concern_survival_rate",
     "concern_survival_severity_weighted_rate", "concern_survival_confidence_weighted_rate",
     "concern_survival_severity_confidence_weighted_rate",
     "insight_weakness_pre_severity_max", "insight_weakness_post_severity_max",
     "insight_weakness_survival_rate", "concern_debate_new_count", "insight_weakness_debate_new_count",
     "repair_scope_matched_improvement_mean", "repair_scope_matched_max_level_improvement",
     "insight_matched_change_mean", "novelty_empirical_matched_change_mean",
     "concern_matched_severity_improvement_mean", "insight_weakness_matched_severity_improvement_mean"]


def _papers(source):
    return source["papers"] if isinstance(source, dict) else source


def _registry(outcomes, target_spec=None):
    if isinstance(outcomes, dict) and all(isinstance(value, dict) and "values" in value
                                         for value in outcomes.values()):
        return outcomes
    return register_external_outcomes(outcomes, target_spec)


def _numeric(value, label):
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be finite numeric or null")
    return value


def _feature_names(papers, spec):
    available = set(papers[0]["features"]) if papers else set()
    requested = spec.get("features")
    names = list(requested) if requested is not None else [name for name in DEFAULT_FEATURES if name in available]
    if isinstance(requested, str) or len(names) != len(set(names)):
        raise ValueError("features must be a list of distinct column names")
    unknown = set(names) - available
    if unknown:
        raise ValueError("Unknown feature columns: " + ", ".join(sorted(unknown)))
    if not names:
        raise ValueError("Select at least one documented numeric feature")
    return names


def _metrics(spec):
    result = spec.get("metrics", ["spearman", "kendall_tau_b", "concordance"])
    if not isinstance(result, list) or not result or len(set(result)) != len(result) or set(result) - set(METRICS):
        raise ValueError("metrics must be a nonempty distinct list of supported metric names")
    return result


def _direction(feature, spec):
    direction = spec.get("feature_directions", {}).get(feature, "higher")
    if direction not in ("higher", "lower"):
        raise ValueError(f"Feature direction must be higher or lower: {feature}")
    return 1 if direction == "higher" else -1


def _cohorts(papers, paper_splits):
    ids = {paper["doc_id"] for paper in papers}
    if paper_splits is None:
        return [("all_available", ids)]
    if not paper_splits:
        raise ValueError("An explicit paper-split list cannot be empty")
    result = []
    names = set()
    for index, fold in enumerate(paper_splits):
        name = fold.get("name", f"fold-{index}")
        train, test = fold["train"], fold["test"]
        if name in names or not train or not test or len(set(train)) != len(train) or len(set(test)) != len(test):
            raise ValueError("Fold names/IDs must be distinct and train/test sets nonempty")
        if set(train) & set(test) or (set(train) | set(test)) - ids:
            raise ValueError("Invalid paper split: overlap or unknown IDs")
        names.add(name)
        result.append((name, set(test)))
    return result


def pairwise_concordance(scores, outcomes):
    return metric_result(scores, outcomes, "concordance")


def spearman_correlation(scores, outcomes):
    return metric_result(scores, outcomes, "spearman")


def kendall_tau_b(scores, outcomes):
    return metric_result(scores, outcomes, "kendall_tau_b")


def analyze_external_validity(features, outcomes, paper_splits=None, analysis_spec=None):
    """Unfitted feature/outcome associations; each row discloses its actual cohort.

    Feature direction is declared before analysis (default higher), never chosen
    from the observed sign. Error metrics on raw features require an explicit
    request; a raw feature is not a calibrated citation-count prediction.
    """
    spec = analysis_spec or {}
    papers = _papers(features)
    names, metrics = _feature_names(papers, spec), _metrics(spec)
    registry = _registry(outcomes, spec.get("targets"))
    results = []
    for fold, cohort in _cohorts(papers, paper_splits):
        cohort_papers = [paper for paper in papers if paper["doc_id"] in cohort]
        for target, entry in sorted(registry.items()):
            for feature in names:
                eligible = [paper for paper in cohort_papers if
                            entry["values"].get(paper["doc_id"]) is not None and
                            paper["features"].get(feature) is not None]
                scores = [_direction(feature, spec) * _numeric(p["features"][feature], feature)
                          if p["features"][feature] is not None else None for p in cohort_papers]
                y = [entry["values"].get(p["doc_id"]) for p in cohort_papers]
                for metric in metrics:
                    if metric == "auc" and entry["spec"]["type"] != "binary":
                        continue
                    results.append({"target": target, "feature": feature, "metric": metric,
                                    "fold": fold, "analysis": "unfitted_feature_association",
                                    "n_cohort_papers": len(cohort_papers),
                                    "feature_direction": "higher" if _direction(feature, spec) == 1 else "lower",
                                    "paper_ids": [p["doc_id"] for p in eligible],
                                    **metric_result(scores, y, metric)})
    return results


def analyze_pre_post_change(pre_features, post_features, outcomes, paper_splits=None, analysis_spec=None):
    """Compare matched features on the same papers; never subtract unmatched metrics."""
    spec = analysis_spec or {}
    pre, post = _papers(pre_features), _papers(post_features)
    before, after = ({p["doc_id"]: p for p in papers} for papers in (pre, post))
    names = _feature_names(pre, spec)
    available_post = set(post[0]["features"]) if post else set()
    pairs = spec.get("pre_post_pairs")
    if pairs is None:
        pairs = [{"name": name.replace("_pre_", "_"), "pre": name,
                  "post": name.replace("_pre_", "_post_")}
                 for name in names if "_pre_" in name and name.replace("_pre_", "_post_") in available_post]
    results = []
    for pair in pairs:
        if pair["pre"] not in pre[0]["features"] or pair["post"] not in available_post:
            raise ValueError("PRE/POST pair references an unknown feature")
    registry = _registry(outcomes, spec.get("targets"))
    for fold, cohort in _cohorts(pre, paper_splits):
        for target, entry in sorted(registry.items()):
            for pair in pairs:
                ids = sorted(doc_id for doc_id in set(before) & set(after) & cohort
                             if entry["values"].get(doc_id) is not None and
                             before[doc_id]["features"].get(pair["pre"]) is not None and
                             after[doc_id]["features"].get(pair["post"]) is not None)
                x_pre = [_direction(pair["pre"], spec) * before[i]["features"][pair["pre"]] for i in ids]
                x_post = [_direction(pair["post"], spec) * after[i]["features"][pair["post"]] for i in ids]
                y = [entry["values"][i] for i in ids]
                for metric in _metrics(spec):
                    if metric == "auc" and entry["spec"]["type"] != "binary":
                        continue
                    first, last = metric_result(x_pre, y, metric), metric_result(x_post, y, metric)
                    delta = last["estimate"] - first["estimate"] if first["estimate"] is not None and last["estimate"] is not None else None
                    results.append({"target": target, "comparison": pair.get("name", pair["pre"]),
                                    "pre_feature": pair["pre"], "post_feature": pair["post"],
                                    "fold": fold, "metric": metric, "pre_estimate": first["estimate"],
                                    "post_estimate": last["estimate"], "post_minus_pre": delta,
                                    "improvement": -delta if delta is not None and metric in ERROR_METRICS else delta,
                                    "n_papers": len(ids), "paper_ids": ids,
                                    "n_excluded_from_cohort": len(cohort) - len(ids),
                                    "pre_diagnostics": first, "post_diagnostics": last})
    return results


def analyze_disagreement_moderation(pre_disagreement, performance_changes, analysis_spec=None):
    """Describe relation of PRE verdict disagreement to supplied per-paper gains.

    Gain must already mean improvement in held-out per-paper loss or a separately
    defined paper outcome. A dataset-wide rank correlation is not a per-paper gain.
    """
    if pre_disagreement is None or performance_changes is None:
        return {"status": "skipped", "reason": "Requires PRE verdict disagreement and per-paper gains"}
    def mapping(source, field):
        if isinstance(source, dict):
            return source
        return {row["doc_id"]: row[field] for row in source}
    disagreement = mapping(pre_disagreement, "pairwise_disagreement")
    gains = mapping(performance_changes, "gain")
    ids = sorted(i for i in set(disagreement) & set(gains) if disagreement[i] is not None and gains[i] is not None)
    groups = []
    for value in sorted({disagreement[i] for i in ids}):
        values = [gains[i] for i in ids if disagreement[i] == value]
        groups.append({"pre_verdict_disagreement": value, "n_papers": len(values), "mean_gain": mean(values)})
    return {"status": "ok", "association": metric_result([disagreement[i] for i in ids], [gains[i] for i in ids], "spearman"),
            "groups": groups, "paper_ids": ids, "interpretation": "predictive association; no isolated causal debate effect"}


def analyze_internal_outcomes(features, internal_outcomes=None, aggregation_spec=None, analysis_spec=None):
    """Audit fixed rules and internal outcomes, including exported editor verdicts.

    Editor verdicts use REJECT < RESUBMIT < ACCEPT for rank statistics only.
    This ordinal encoding is not a numeric quality score or a candidate feature.
    """
    papers = _papers(features)
    rules = (aggregation_spec or {}).get("rules", [])
    encoding = {"REJECT": 0, "RESUBMIT": 1, "ACCEPT": 2}
    editor_rows = [{"doc_id": paper["doc_id"], **paper["editor_outcome"]}
                   for paper in papers if "editor_outcome" in paper]
    editor_values = {row["doc_id"]: encoding[row["verdict"]]
                     if row["availability"] == "ASSESSED" else None for row in editor_rows}
    has_editor = any(value is not None for value in editor_values.values())
    result = {"status": "ok" if rules or internal_outcomes or has_editor else "skipped", "scores": [],
              "component_correlations": [], "outcome_associations": [],
              "editor_verdicts": editor_rows, "outcome_metadata": {},
              "interpretation": "Score contributions/correlations are formula-driven, not external validation."}
    if result["status"] == "skipped":
        result["reason"] = "No internal outcomes or score-audit rules supplied"
        return result
    for rule in rules:
        if rule.get("kind", "fixed") != "fixed":
            raise ValueError("Score audits require an explicit fixed rule, not a newly fit outcome model")
        adapter = LinearScoreAdapter(rule).fit(papers)
        rows = adapter.contributions(papers)
        for row in rows:
            result["scores"].append({"configuration": rule["name"], **row})
        component_names = sorted({key for row in rows for key in row["components"]})
        for component in component_names:
            result["component_correlations"].append({"configuration": rule["name"], "component": component,
                **metric_result([r["components"][component] for r in rows], [r["score"] for r in rows], "spearman")})
    if internal_outcomes:
        spec = analysis_spec or {}
        for name, values in sorted(internal_outcomes.items()):
            registry = {name: {"spec": {"type": "numeric", "direction": "higher"}, "values": values}}
            for row in analyze_external_validity(papers, registry, analysis_spec=spec):
                result["outcome_associations"].append({**row, "analysis": "internal_association_may_share_source"})
    if has_editor:
        if internal_outcomes and "editor_verdict" in internal_outcomes:
            raise ValueError("editor_verdict is reserved for the exported categorical editor outcome")
        spec = dict(analysis_spec or {})
        spec["metrics"] = [metric for metric in _metrics(spec)
                           if metric in {"spearman", "kendall_tau_b", "concordance"}]
        result["outcome_metadata"]["editor_verdict"] = {
            "type": "ordinal", "encoding": encoding, "direction": "higher",
            "source": "editor_outcome.verdict", "n_assessed": sum(v is not None for v in editor_values.values()),
            "association_status": "computed" if spec["metrics"] else "skipped_no_rank_metrics"}
        if spec["metrics"]:
            registry = {"editor_verdict": {"spec": {"type": "ordinal", "direction": "higher"},
                                           "values": editor_values}}
            for row in analyze_external_validity(papers, registry, analysis_spec=spec):
                result["outcome_associations"].append({**row, "analysis": "internal_association_may_share_source"})
    return result


def compare_mad_to_generalist(mad_outputs, generalist_outputs, outcomes, paper_splits=None, comparison_spec=None):
    """Compare supplied scores on matched papers; no new reviewer calls or fitting."""
    if mad_outputs is None or generalist_outputs is None:
        return {"status": "skipped", "reason": "Both MAD and generalist scores are required"}
    spec = comparison_spec or {}
    comparisons = []
    for target, entry in sorted(_registry(outcomes, spec.get("targets")).items()):
        ids = sorted(i for i in set(mad_outputs) & set(generalist_outputs)
                     if mad_outputs[i] is not None and generalist_outputs[i] is not None and entry["values"].get(i) is not None)
        pseudo_papers = [{"doc_id": i} for i in sorted(set(mad_outputs) & set(generalist_outputs))]
        for fold, cohort in _cohorts(pseudo_papers, paper_splits):
            matched = [i for i in ids if i in cohort]
            for metric in _metrics(spec):
                if metric == "auc" and entry["spec"]["type"] != "binary":
                    continue
                y = [entry["values"][i] for i in matched]
                mad = metric_result([mad_outputs[i] for i in matched], y, metric)
                generalist = metric_result([generalist_outputs[i] for i in matched], y, metric)
                delta = mad["estimate"] - generalist["estimate"] if mad["estimate"] is not None and generalist["estimate"] is not None else None
                comparisons.append({"target": target, "fold": fold, "metric": metric, "n_papers": len(matched),
                                    "paper_ids": matched, "n_excluded_from_cohort": len(cohort) - len(matched),
                                    "mad": mad, "generalist": generalist, "mad_minus_generalist": delta})
    return {"status": "ok", "comparisons": comparisons}


def analyze_debate_mechanisms(mechanism_features, changes=None, outcomes=None, analysis_spec=None):
    """Associate explicitly selected mechanism features with supplied paper changes."""
    if changes is None:
        return {"status": "skipped", "reason": "No paper-level change measure supplied"}
    papers, spec = _papers(mechanism_features), analysis_spec or {}
    names = _feature_names(papers, spec)
    results = []
    for name in names:
        ids = [p["doc_id"] for p in papers if p["features"].get(name) is not None and changes.get(p["doc_id"]) is not None]
        values = {p["doc_id"]: p["features"][name] for p in papers}
        results.append({"feature": name, "paper_ids": ids,
                        **metric_result([values[i] for i in ids], [changes[i] for i in ids], "spearman")})
    return {"status": "ok", "associations": results, "interpretation": "Association only; persistence is not correctness"}


def _split_for_target(papers, target, settings):
    if settings.get("paper_splits") is not None:
        return settings["paper_splits"]
    ids = [p["doc_id"] for p in papers if target["values"].get(p["doc_id"]) is not None]
    if len(ids) < 4:
        return None
    return make_paper_splits(ids, n_splits=min(settings.get("n_splits", 2), len(ids) // 2),
                             inner_splits=settings.get("inner_splits", 2), seed=settings.get("seed", 0))


def _restrict_targets(registry, ids):
    ids = set(ids)
    result = deepcopy(registry)
    for target in result.values():
        for key in ("values", "raw_values", "excluded", "snapshots"):
            if key in target:
                target[key] = {doc_id: value for doc_id, value in target[key].items() if doc_id in ids}
        target["eligible_ids"] = [doc_id for doc_id in target.get("eligible_ids", []) if doc_id in ids]
    return result


def _editor_values(papers):
    encoding = {"REJECT": 0, "RESUBMIT": 1, "ACCEPT": 2}
    return {p["doc_id"]: encoding[p["editor_outcome"]["verdict"]]
            if p["editor_outcome"]["availability"] == "ASSESSED" else None for p in papers}


def analyze_editor_external_validity(papers, registry, analysis_spec=None, partition="development"):
    """Evaluate the actual editor verdict as an ordered external-validity baseline."""
    values = _editor_values(papers)
    result = []
    for target, entry in sorted(registry.items()):
        ids = [p["doc_id"] for p in papers]
        for metric in _metrics(analysis_spec or {}):
            if metric in ERROR_METRICS or (metric == "auc" and entry["spec"]["type"] != "binary"):
                continue
            result.append({"target": target, "partition": partition, "score": "editor_verdict",
                "paper_ids": [i for i in ids if values[i] is not None and entry["values"].get(i) is not None],
                **metric_result([values[i] for i in ids], [entry["values"].get(i) for i in ids], metric)})
    return result


def run_evaluation(feature_source, outcome_source, spec=None):
    """Run only explicitly configured offline analyses; no implicit score search."""
    if spec is not None and not isinstance(spec, dict):
        raise ValueError("Evaluation spec must be a JSON object")
    settings = deepcopy(spec or {})
    unknown = set(settings) - SPEC_KEYS
    if unknown:
        raise ValueError("Unknown evaluation spec keys: " + ", ".join(sorted(unknown)))
    if not isinstance(settings.get("synthetic", False), bool):
        raise ValueError("synthetic must be boolean")
    dataset = load_features_and_outcomes(feature_source, outcome_source, settings.get("linkage"))
    papers = dataset["papers"]
    if not isinstance(settings.get("derived_features", True), bool):
        raise ValueError("derived_features must be boolean")
    derived = (derive_change_features(papers) if settings.get("derived_features", True)
               else {"papers": papers, "columns": {}, "rows": [], "version": None, "policies": {}})
    papers = derived["papers"]
    dictionary = deepcopy(dataset["bundle"]["feature_dictionary"])
    dictionary["columns"].update(derived["columns"])
    dictionary["derived_features"] = {"version": derived["version"], "policies": derived["policies"]}
    names = _feature_names(papers, settings)
    _metrics(settings)
    directions = settings.get("feature_directions", {})
    if set(directions) - set(dictionary["columns"]):
        raise ValueError("feature_directions references an unknown feature")
    for name in directions:
        _direction(name, settings)
    registry = register_external_outcomes(dataset["outcomes"], settings.get("targets"))
    if not registry:
        raise ValueError("No supported external targets; supply explicit target definitions")
    all_papers, all_registry = papers, registry
    partition, frozen_spec, development_hash = None, None, None
    if settings.get("holdout") is not False:
        holdout_spec = settings.get("holdout", {})
        if not isinstance(holdout_spec, dict):
            raise ValueError("holdout must be a settings mapping or false for exploratory full-sample analysis")
        partition = make_holdout_partition([row["doc_id"] for row in dataset["outcomes"]], holdout_spec)
        resolved = {key: partition[key] for key in ("development_ids", "holdout_ids", "seed", "stage")}
        development_ids = set(partition["development_ids"])
        development_data = {
            "feature_dictionary": dictionary,
            "papers": sorted((p for p in papers if p["doc_id"] in development_ids),
                             key=lambda p: p["doc_id"]),
            "targets": _restrict_targets(registry, development_ids),
        }
        data_hash = hashlib.sha256(json.dumps(development_data, sort_keys=True,
            allow_nan=False).encode()).hexdigest()
        if partition["stage"] == "final" and settings.get("development_data_sha256") != data_hash:
            raise ValueError("Final holdout evaluation requires unchanged development data and feature definitions; rerun development first")
        frozen_spec = deepcopy(settings)
        frozen_spec.pop("development_spec_sha256", None)
        frozen_spec.update(features=names, derived_features=settings.get("derived_features", True),
                           holdout={**resolved, "stage": "development"}, development_data_sha256=data_hash)
        development_hash = hashlib.sha256(json.dumps({"version": EVALUATION_VERSION, "spec": frozen_spec},
            sort_keys=True, allow_nan=False).encode()).hexdigest()
        if partition["stage"] == "final" and settings.get("development_spec_sha256") != development_hash:
            raise ValueError("Final holdout evaluation requires the unchanged frozen_evaluation_spec.json from a development run")
        frozen_spec["holdout"]["stage"] = "final"
        frozen_spec["development_spec_sha256"] = development_hash
        papers = [paper for paper in papers if paper["doc_id"] in set(partition["development_ids"])]
        if not papers:
            raise ValueError("No feature outputs are available for development papers yet")
        registry = _restrict_targets(registry, [p["doc_id"] for p in papers])
    scope = "development" if partition else "exploratory_all_available"
    reviewer_rows = [row for paper in papers for row in paper["reviewer_features"]]
    verdicts = construct_verdict_diagnostics(reviewer_rows, settings.get("verdict"))
    diagnostic_spec = {**settings.get("diagnostics", {}), "features": names}
    encoded_settings = json.dumps(settings, sort_keys=True, allow_nan=False).encode()
    results = {
        "metadata": {"evaluation_version": EVALUATION_VERSION,
                     "feature_version": dataset["bundle"]["feature_dictionary"]["version"],
                     "synthetic": settings.get("synthetic", False), "config": settings,
                     "config_sha256": hashlib.sha256(encoded_settings).hexdigest(),
                     "source_provenance": dataset["provenance"],
                     "n_papers": len(papers), "n_input_papers": len(all_papers), "selected_features": names,
                     "analysis_partition": scope, "development_spec_sha256": development_hash,
                     "association_cohorts": "Available pairs per feature/target; PRE/POST comparisons use common papers.",
                     "ranking_folds": "Trained rankings are computed within outer test folds, never pooled across fitted scales."},
        "feature_dictionary": dictionary, "coverage": dataset["coverage"],
        "holdout": partition, "frozen_evaluation_spec": frozen_spec,
        "derived_features": [r for r in derived["rows"] if r["doc_id"] in {p["doc_id"] for p in papers}],
        "targets": registry, "verdict_diagnostics": verdicts,
        "editor_external_validity": analyze_editor_external_validity(papers, registry, settings, scope),
        "descriptive": describe_feature_relationships(papers, reviewer_rows, diagnostic_spec),
        "external_validity": analyze_external_validity(papers, registry, analysis_spec={**settings, "features": names}),
        "pre_post": analyze_pre_post_change(papers, papers, registry, analysis_spec={**settings, "features": names}),
        "aggregation": {}, "internal_aggregation": {}, "ablation": {}, "robustness": [],
    }
    for table in ("external_validity", "pre_post"):
        for row in results[table]:
            row["partition"] = scope
    for target, entry in sorted(registry.items()):
        if settings.get("aggregation"):
            aggregation = settings["aggregation"]
            if partition:
                results["aggregation"][target] = evaluate_holdout_rules(
                    all_papers, all_registry[target]["values"], partition,
                    {"report_metrics": _metrics(settings), **aggregation, "target_type": entry["spec"]["type"]})
            else:
                splits = _split_for_target(papers, entry, aggregation)
                results["aggregation"][target] = (evaluate_aggregation_rules(papers, entry["values"], splits, aggregation)
                    if splits else {"status": "unavailable", "reason": "insufficient_papers_for_splits"})
        if settings.get("ablation"):
            ablation = settings["ablation"]
            splits = _split_for_target(papers, entry, ablation)
            results["ablation"][target] = (evaluate_feature_families(papers, entry["values"], splits, ablation)
                if splits else {"status": "unavailable", "reason": "insufficient_papers_for_splits"})
        if settings.get("bootstrap"):
            bootstrap = settings["bootstrap"]
            selected = bootstrap.get("features", names)
            if set(selected) - set(names):
                raise ValueError("Bootstrap features must be included in selected analysis features")
            for feature in selected:
                scores = {p["doc_id"]: _direction(feature, settings) * p["features"][feature]
                          for p in papers if p["features"][feature] is not None}
                if not any(entry["values"].get(doc_id) is not None for doc_id in scores):
                    results["robustness"].append({"target": target, "feature": feature,
                        "estimate": None, "lower": None, "upper": None, "n_papers": 0,
                        "status": "undefined", "undefined_reason": "no_complete_papers"})
                    continue
                interval = run_robustness_checks(papers, entry["values"], robustness_spec={**bootstrap, "scores": scores})
                results["robustness"].append({"target": target, "feature": feature, **interval,
                    "lower": interval["interval"]["lower"], "upper": interval["interval"]["upper"]})
    results["internal"] = analyze_internal_outcomes(papers, settings.get("internal_outcomes"),
        settings.get("score_audit"), {**settings, **settings.get("internal_spec", {}), "features": names})
    for row in results["internal"]["outcome_associations"]:
        row["partition"] = scope
    internal_model = settings.get("internal_aggregation", settings.get("aggregation"))
    if internal_model and partition:
        editor_values = _editor_values(all_papers)
        results["internal_aggregation"]["editor_verdict"] = evaluate_holdout_rules(
            all_papers, editor_values, partition,
            {"report_metrics": [m for m in _metrics(settings) if m in {"spearman", "kendall_tau_b", "concordance"}]
             or ["spearman", "kendall_tau_b", "concordance"], **internal_model, "target_type": "ordinal"})
    if partition and partition["stage"] == "final":
        heldout_papers = [p for p in all_papers if p["doc_id"] in set(partition["holdout_ids"])]
        results["editor_external_validity"].extend(
            analyze_editor_external_validity(heldout_papers, all_registry, settings, "holdout"))
    persona_options = settings.get("persona_analysis", False)
    if not isinstance(persona_options, (bool, dict)):
        raise ValueError("persona_analysis must be boolean or an options mapping")
    domain_records = settings.get("domain_records")
    if domain_records is not None and partition:
        domain_records = [r for r in domain_records if r["doc_id"] in set(partition["development_ids"])]
    results["persona"] = (analyze_persona_and_domain_patterns(reviewer_rows, papers, domain_records,
        persona_options if isinstance(persona_options, dict) else {})
        if persona_options is not False else {"status": "skipped", "reason": "Persona/domain analysis not requested"})
    generalist = settings.get("generalist", {})
    if partition:
        generalist = {key: {i: value for i, value in scores.items() if i in set(partition["development_ids"])}
                      for key, scores in generalist.items()}
    results["generalist"] = compare_mad_to_generalist(generalist.get("mad_scores"), generalist.get("generalist_scores"),
                                                       registry, comparison_spec=settings)
    mechanisms = settings.get("mechanisms", {})
    results["mechanisms"] = analyze_debate_mechanisms(papers, mechanisms.get("changes"), registry,
                                                       {**settings, **mechanisms, "features": mechanisms.get("features", names)})
    results["disagreement_moderation"] = analyze_disagreement_moderation(
        [row for row in verdicts["phase_summaries"] if row["phase"] == "PRE"], mechanisms.get("gains"))
    return results


TABLE_FIELDS = {
    "external_validity": ["target", "feature", "metric", "estimate", "n_papers", "undefined_reason"],
    "verdict_diagnostics": ["doc_id", "phase", "n_valid", "any_disagreement", "pairwise_disagreement", "polarized"],
    "verdict_transitions": ["doc_id", "persona", "pre", "post", "direction"],
    "verdict_matched_changes": ["doc_id", "n_matched", "pre_disagreement", "post_disagreement", "convergence"],
    "feature_summaries": ["feature", "n", "n_missing", "mean", "min", "max"],
    "feature_correlations": ["feature_a", "feature_b", "metric", "estimate", "n_papers"],
    "reviewer_changes": ["doc_id", "persona", "construct", "pre", "post", "delta", "absolute_delta"],
    "reviewer_availability": ["phase", "construct", "availability", "n"],
    "pre_post_comparisons": ["target", "comparison", "metric", "pre_estimate", "post_estimate", "improvement", "n_papers"],
    "paper_scores": ["target", "doc_id", "outer_fold", "configuration", "continuous_score", "discrete_score", "outcome"],
    "aggregation_results": ["target", "outer_fold", "configuration", "metric", "estimate", "n_papers"],
    "selection_records": ["target", "outer_fold", "configuration", "metric", "estimate"],
    "selected_configurations": ["target", "outer_fold", "configuration"],
    "ablation_results": ["target", "outer_fold", "configuration", "metric", "estimate", "n_papers"],
    "score_audit": ["configuration", "doc_id", "score", "intercept", "components"],
    "score_component_correlations": ["configuration", "component", "estimate", "n_papers"],
    "robustness": ["target", "feature", "estimate", "lower", "upper"],
    "persona_correlations": ["phase", "construct", "persona_a", "persona_b", "estimate", "n_shared"],
    "persona_contrasts": ["doc_id", "phase", "construct", "persona", "centered_difference"],
    "domain_summaries": ["domain", "phase", "construct", "n_papers", "mean_paper_assessment"],
    "internal_associations": ["target", "feature", "metric", "estimate", "n_papers"],
    "editor_verdicts": ["doc_id", "availability", "verdict", "source_call_ids", "source_path"],
    "editor_external_validity": ["target", "partition", "metric", "estimate", "n_papers"],
    "derived_features": ["doc_id"],
    "candidate_comparisons": ["outcome_scope", "target", "configuration", "metric", "estimate", "n_valid_folds"],
    "holdout_results": ["outcome_scope", "target", "configuration", "metric", "estimate", "n_papers"],
    "target_status": ["outcome_scope", "target", "status", "reason"],
    "generalist_comparisons": ["target", "fold", "metric", "n_papers", "mad_minus_generalist"],
    "mechanism_associations": ["feature", "estimate", "n_papers"],
}


def _result_tables(results):
    tables = {
        "external_validity": results["external_validity"],
        "verdict_diagnostics": results["verdict_diagnostics"]["phase_summaries"],
        "verdict_transitions": results["verdict_diagnostics"]["transitions"],
        "verdict_matched_changes": results["verdict_diagnostics"]["matched_changes"],
        "feature_summaries": results["descriptive"].get("summaries", []),
        "feature_correlations": results["descriptive"].get("correlations", []),
        "reviewer_changes": results["descriptive"].get("reviewer_changes", []),
        "reviewer_availability": results["descriptive"].get("availability", []),
        "pre_post_comparisons": results["pre_post"],
        "score_audit": results["internal"].get("scores", []),
        "score_component_correlations": results["internal"].get("component_correlations", []),
        "robustness": results["robustness"],
        "persona_correlations": results["persona"].get("persona_correlations", []),
        "persona_contrasts": results["persona"].get("within_paper_contrasts", []),
        "domain_summaries": results["persona"].get("domain_summaries", []),
        "internal_associations": results["internal"].get("outcome_associations", []),
        "editor_verdicts": results["internal"].get("editor_verdicts", []),
        "editor_external_validity": results.get("editor_external_validity", []),
        "derived_features": results.get("derived_features", []),
        "generalist_comparisons": results["generalist"].get("comparisons", []),
        "mechanism_associations": results["mechanisms"].get("associations", []),
    }
    for name in ("paper_scores", "aggregation_results", "selection_records", "selected_configurations", "ablation_results",
                 "candidate_comparisons", "holdout_results", "target_status"):
        tables[name] = []
    for scope, outputs in (("external", results["aggregation"]), ("internal", results.get("internal_aggregation", {}))):
        for target, output in sorted(outputs.items()):
            tables["target_status"].append({"outcome_scope": scope, "target": target,
                "status": output.get("status", "ok"), "reason": output.get("reason")})
            for table, key in (("paper_scores", "paper_scores"), ("aggregation_results", "fold_results"),
                              ("selection_records", "selection_records"), ("selected_configurations", "selected_configurations"),
                              ("candidate_comparisons", "candidate_summary"), ("holdout_results", "holdout_results")):
                tables[table].extend({"outcome_scope": scope, "target": target, **row} for row in output.get(key, []))
    for target, output in sorted(results["ablation"].items()):
        tables["ablation_results"].extend({"target": target, **row} for row in output.get("fold_results", []))
    return tables


def export_analysis_tables(results, destination, export_spec=None):
    """Write reproducible JSON/CSV into a new or empty directory, preserving inputs."""
    directory = Path(destination)
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Output directory must be new or empty; existing results are not overwritten")
    # Validate serialization before creating any output files.
    text = json.dumps(results, indent=2, sort_keys=True, allow_nan=False) + "\n"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "results.json").write_text(text, encoding="utf-8")
    (directory / "feature_dictionary.json").write_text(json.dumps(results["feature_dictionary"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
    files = ["results.json", "feature_dictionary.json"]
    for key, filename in (("holdout", "holdout_split.json"), ("frozen_evaluation_spec", "frozen_evaluation_spec.json")):
        if results.get(key) is not None:
            (directory / filename).write_text(json.dumps(results[key], indent=2, sort_keys=True, allow_nan=False) + "\n",
                                              encoding="utf-8")
            files.append(filename)
    for name, rows in _result_tables(results).items():
        base = ["synthetic"] + TABLE_FIELDS[name]
        fields = base + sorted({key for row in rows for key in row} - set(base))
        with (directory / (name + ".csv")).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                encoded = {key: json.dumps(value, sort_keys=True, allow_nan=False)
                           if isinstance(value, (dict, list)) else value for key, value in row.items()}
                writer.writerow({**encoded, "synthetic": results["metadata"]["synthetic"]})
        files.append(name + ".csv")
    return {"destination": str(directory), "files": files}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, required=True, help="Exported feature JSON, not a raw-result manifest")
    parser.add_argument("--outcomes", type=Path, required=True, help="CSV linked by id or doc_id")
    parser.add_argument("--spec", type=Path, help="Evaluation settings JSON; no score search by default")
    parser.add_argument("--output-dir", type=Path, required=True, help="New or empty output directory")
    parser.add_argument("--synthetic", action="store_true", help="Label all exported fixture results as synthetic")
    args = parser.parse_args(argv)
    try:
        spec = json.loads(args.spec.read_text(encoding="utf-8")) if args.spec else {}
        if args.synthetic:
            spec["synthetic"] = True
        results = run_evaluation(args.features, args.outcomes, spec)
        exported = export_analysis_tables(results, args.output_dir)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(2, f"Evaluation input/configuration error: {exc}\n")
    label = "synthetic fixtures" if results["metadata"]["synthetic"] else "supplied data"
    print(f"Evaluated {results['metadata']['n_papers']} papers ({label}); outputs: {exported['destination']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
