"""Offline score adapters and paper-level evaluation orchestration.

No aggregation rule is chosen by default. Callers supply fixed weights or a
small ridge model and an outcome. The primary workflow reserves a shared holdout,
selects rules on development folds, then tests the selected rule in a final run.
Explicit nested paper splits remain available for exploratory analyses. Imputation,
scaling, coefficients and empirical bin thresholds use training papers only.
Fold rankings are evaluated separately, never pooled.
"""

from __future__ import annotations

import bisect
import math
import random
import statistics
from collections.abc import Mapping


_METRICS = {"concordance", "spearman", "kendall_tau_b", "mae", "rmse", "auc"}


def _number(value, label, allow_none=False):
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("{} must be a finite number{}".format(
            label, " or null" if allow_none else ""))
    try:
        value = float(value)
    except OverflowError as error:
        raise ValueError("{} must be finite".format(label)) from error
    if not math.isfinite(value):
        raise ValueError("{} must be finite".format(label))
    return value


def _quantile(values, probability):
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _papers_by_id(papers):
    result = {}
    for paper in papers:
        if not isinstance(paper, Mapping) or not isinstance(paper.get("features"), Mapping):
            raise ValueError("Each paper must contain doc_id and a features mapping")
        doc_id = paper.get("doc_id")
        if not isinstance(doc_id, str) or not doc_id.strip() or doc_id in result:
            raise ValueError("Paper doc_id must be a unique, nonempty string: {!r}".format(doc_id))
        result[doc_id] = paper
    return result


def _solve(matrix, response):
    """Solve the small positive-regularized normal equations with pivoting."""
    size = len(response)
    augmented = [list(row) + [value] for row, value in zip(matrix, response)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if augmented[pivot][column] == 0:
            raise ValueError("Singular ridge system; use a larger positive alpha")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row != column:
                multiplier = augmented[row][column]
                augmented[row] = [left - multiplier * right for left, right in
                                  zip(augmented[row], augmented[column])]
    return [_number(row[-1], "fitted coefficient") for row in augmented]


class LinearScoreAdapter:
    """A supplied fixed formula or an L2-regularized linear predictor.

    ``spec`` has kind='fixed', weights={feature: weight}, or kind='ridge',
    features=[...], alpha>0. Optional missing='median'|'error', standardize,
    intercept (fixed only), and bins={'thresholds': [...]} or
    bins={'quantiles': [...]} are explicit. Numeric ordinal encoding belongs to
    the feature dictionary. No encoding or outcome transformation happens here.
    """

    def __init__(self, spec=None):
        if spec is not None and not isinstance(spec, Mapping):
            raise ValueError("Score specification must be a mapping")
        self.spec = dict(spec or {})
        self.kind = self.spec.get("kind", "fixed")
        if self.kind not in {"fixed", "ridge"}:
            raise ValueError("Score kind must be fixed or ridge")
        weights = self.spec.get("weights", {})
        if self.kind == "fixed" and not isinstance(weights, Mapping):
            raise ValueError("Fixed weights must be a mapping")
        feature_names = list(weights) if self.kind == "fixed" else self.spec.get("features", [])
        if not isinstance(feature_names, (list, tuple)):
            raise ValueError("features must be an explicit list of names")
        self.features = list(feature_names)
        if (not self.features or any(not isinstance(name, str) or not name for name in self.features)
                or len(set(self.features)) != len(self.features)):
            raise ValueError("Specify distinct, nonempty feature names")
        self.missing = self.spec.get("missing", "median")
        if self.missing not in {"median", "error"}:
            raise ValueError("missing must be median or error")
        self.standardize = self.spec.get("standardize", self.kind == "ridge")
        if not isinstance(self.standardize, bool):
            raise ValueError("standardize must be boolean")
        self.alpha = _number(self.spec.get("alpha", 1.0), "alpha")
        if self.kind == "ridge" and self.alpha <= 0:
            raise ValueError("Ridge alpha must be positive")
        self._fixed_weights = {name: _number(weights[name], "weight " + name)
                               for name in self.features} if self.kind == "fixed" else {}
        self.fit_ids = []
        self.bin_thresholds = None
        self.diagnostics = {}
        self._fitted = False
        self._validate_bins()

    def _validate_bins(self):
        bins = self.spec.get("bins")
        if bins is None:
            return
        if not isinstance(bins, Mapping) or len(set(bins) & {"thresholds", "quantiles"}) != 1:
            raise ValueError("bins requires either thresholds or quantiles")
        key = "thresholds" if "thresholds" in bins else "quantiles"
        if not isinstance(bins[key], (list, tuple)):
            raise ValueError("Bin {} must be a list".format(key))
        values = [_number(value, "bin " + key) for value in bins[key]]
        if any(left >= right for left, right in zip(values, values[1:])):
            raise ValueError("Bin {} must be strictly increasing".format(key))
        if key == "quantiles" and any(not 0 < value < 1 for value in values):
            raise ValueError("Bin quantiles must be inside (0, 1)")

    def _matrix(self, papers):
        rows = []
        for paper in papers:
            row = []
            for name in self.features:
                value = _number(paper["features"].get(name), name, allow_none=True)
                if value is None:
                    if self.missing == "error":
                        raise ValueError("Missing feature {} for {}".format(name, paper["doc_id"]))
                    value = self.medians[name]
                row.append((value - self.centers[name]) / self.scales[name])
            rows.append(row)
        return rows

    def fit(self, papers, outcomes=None):
        papers = list(papers)
        indexed = _papers_by_id(papers)
        if not indexed:
            raise ValueError("Cannot fit a score on zero papers")
        self._fitted = False
        self.fit_ids = list(indexed)
        self.medians, self.centers, self.scales = {}, {}, {}
        for name in self.features:
            values = [_number(paper["features"].get(name), name, allow_none=True) for paper in papers]
            observed = [value for value in values if value is not None]
            if not observed:
                raise ValueError("All training values are missing for feature {}".format(name))
            if self.missing == "error" and len(observed) != len(values):
                raise ValueError("Missing training feature {}".format(name))
            self.medians[name] = statistics.median(observed)
            filled = [self.medians[name] if value is None else value for value in values]
            self.centers[name] = statistics.mean(filled) if self.standardize else 0.0
            scale = statistics.pstdev(filled) if self.standardize else 1.0
            self.scales[name] = scale if scale > 0 else 1.0
        matrix = self._matrix(papers)
        if self.kind == "fixed":
            self.coefficients = dict(self._fixed_weights)
            self.intercept = _number(self.spec.get("intercept", 0.0), "intercept")
        else:
            if outcomes is None:
                raise ValueError("Ridge fitting requires training outcomes")
            response = ([outcomes.get(paper["doc_id"]) for paper in papers]
                        if isinstance(outcomes, Mapping) else list(outcomes))
            if len(response) != len(papers):
                raise ValueError("Training outcomes must align with papers")
            response = [_number(value, "training outcome") for value in response]
            x_means = [statistics.mean(row[column] for row in matrix)
                       for column in range(len(self.features))]
            y_mean = statistics.mean(response)
            centered = [[value - mean for value, mean in zip(row, x_means)] for row in matrix]
            normal = [[sum(row[a] * row[b] for row in centered) + (self.alpha if a == b else 0)
                       for b in range(len(self.features))] for a in range(len(self.features))]
            rhs = [sum(row[a] * (y - y_mean) for row, y in zip(centered, response))
                   for a in range(len(self.features))]
            coefficients = _solve(normal, rhs)
            self.coefficients = dict(zip(self.features, coefficients))
            self.intercept = _number(y_mean - sum(a * b for a, b in zip(coefficients, x_means)),
                                     "fitted intercept")
        self._fitted = True
        bins = self.spec.get("bins")
        self.bin_thresholds = None
        self.diagnostics = {"n_fit_papers": len(papers), "collapsed_bin_thresholds": 0}
        if bins is not None:
            thresholds = (list(bins["thresholds"]) if "thresholds" in bins else
                          [_quantile(self.predict(papers), q) for q in bins["quantiles"]])
            self.bin_thresholds = sorted(set(thresholds))
            self.diagnostics["collapsed_bin_thresholds"] = len(thresholds) - len(self.bin_thresholds)
        return self

    def predict(self, papers):
        if not self._fitted:
            raise ValueError("Fit the score adapter before predicting")
        papers = list(papers)
        _papers_by_id(papers)
        return [_number(self.intercept + sum(self.coefficients[name] * value for name, value in
                                            zip(self.features, row)), "predicted score")
                for row in self._matrix(papers)]

    def predict_bins(self, papers):
        if not self._fitted or self.bin_thresholds is None:
            raise ValueError("Fit an adapter with an explicit bins specification first")
        return [bisect.bisect_right(self.bin_thresholds, score) for score in self.predict(papers)]

    def contributions(self, papers):
        if not self._fitted:
            raise ValueError("Fit the score adapter before inspecting contributions")
        papers = list(papers)
        _papers_by_id(papers)
        result = []
        for paper, row in zip(papers, self._matrix(papers)):
            components = {name: self.coefficients[name] * value for name, value in zip(self.features, row)}
            result.append({"doc_id": paper["doc_id"], "intercept": self.intercept,
                           "components": components, "score": self.intercept + sum(components.values())})
        return result

    def export_metadata(self):
        if not self._fitted:
            raise ValueError("Adapter has not been fitted")
        return {"kind": self.kind, "features": list(self.features), "fit_ids": list(self.fit_ids),
                "medians": dict(self.medians), "centers": dict(self.centers), "scales": dict(self.scales),
                "coefficients": dict(self.coefficients), "intercept": self.intercept,
                "bin_thresholds": self.bin_thresholds, "diagnostics": dict(self.diagnostics)}


def _metric(scores, targets, metric):
    if metric not in _METRICS:
        raise ValueError("Unknown evaluation metric: {}".format(metric))
    try:
        from .evaluation_core import metric_result
    except ImportError:
        from evaluation_core import metric_result
    return metric_result(scores, targets, metric)


def _ids(values, label, known):
    if not isinstance(values, (list, tuple)):
        raise ValueError("{} must be an explicit list of paper IDs".format(label))
    values = list(values)
    if (not values or any(not isinstance(value, str) for value in values)
            or len(set(values)) != len(values) or not set(values) <= known):
        raise ValueError("{} must be nonempty, unique, known paper IDs".format(label))
    return values


def _validate_splits(paper_splits, known):
    normalized, names = [], set()
    for index, fold in enumerate(paper_splits):
        if not isinstance(fold, Mapping):
            raise ValueError("Paper splits must be mappings")
        name = fold.get("name", "outer_{}".format(index + 1))
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("Outer fold names must be unique nonempty strings")
        names.add(name)
        train = _ids(fold.get("train", []), name + " train", known)
        test = _ids(fold.get("test", []), name + " test", known)
        if set(train) & set(test):
            raise ValueError("Outer training and test papers overlap: " + name)
        inner, inner_names = [], set()
        for inner_index, item in enumerate(fold.get("inner", [])):
            if not isinstance(item, Mapping):
                raise ValueError("Inner paper splits must be mappings")
            inner_name = item.get("name", "inner_{}".format(inner_index + 1))
            if not isinstance(inner_name, str) or not inner_name or inner_name in inner_names:
                raise ValueError("Inner fold names must be unique nonempty strings")
            inner_names.add(inner_name)
            inner_train = _ids(item.get("train", []), name + " inner train", set(train))
            inner_test = _ids(item.get("test", []), name + " inner test", set(train))
            if set(inner_train) & set(inner_test):
                raise ValueError("Inner training and test papers overlap: " + name)
            inner.append({"name": inner_name, "train": inner_train, "test": inner_test})
        normalized.append({"name": name, "train": train, "test": test, "inner": inner})
    if not normalized:
        raise ValueError("At least one explicit paper split is required")
    return normalized


def make_paper_splits(ids, n_splits=3, inner_splits=2, seed=0):
    """Return reproducible, shuffled outer and inner paper folds (no outcomes)."""
    ids = list(ids)
    if (not ids or any(not isinstance(value, str) or not value for value in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError("Split paper IDs must be unique nonempty strings")
    if isinstance(n_splits, bool) or not isinstance(n_splits, int) or not 2 <= n_splits <= len(ids):
        raise ValueError("n_splits must be between 2 and the number of papers")
    if (isinstance(inner_splits, bool) or not isinstance(inner_splits, int)
            or (inner_splits != 0 and inner_splits < 2)):
        raise ValueError("inner_splits must be 0 or at least 2")
    shuffled = list(ids)
    rng = random.Random(seed)
    rng.shuffle(shuffled)
    result = []
    for index in range(n_splits):
        test = shuffled[index::n_splits]
        train = [value for value in shuffled if value not in set(test)]
        inner = []
        if inner_splits:
            if inner_splits > len(train):
                raise ValueError("Too few outer-training papers for the requested inner folds")
            inner_ids = list(train)
            rng.shuffle(inner_ids)
            for inner_index in range(inner_splits):
                inner_test = inner_ids[inner_index::inner_splits]
                inner.append({"name": "inner_{}".format(inner_index + 1),
                              "train": [value for value in inner_ids if value not in set(inner_test)],
                              "test": inner_test})
        result.append({"name": "outer_{}".format(index + 1), "train": train, "test": test, "inner": inner})
    return result


def make_holdout_partition(ids, spec=None):
    """Reserve paper IDs without reading features, verdicts or external values."""
    spec = dict(spec or {})
    allowed = {"fraction", "seed", "stage", "development_ids", "holdout_ids"}
    if set(spec) - allowed:
        raise ValueError("Unknown holdout settings: " + ", ".join(sorted(set(spec) - allowed)))
    ids = sorted(ids)
    if len(ids) != len(set(ids)) or len(ids) < 2:
        raise ValueError("Holdout partition needs at least two distinct paper IDs")
    stage = spec.get("stage", "development")
    if stage not in {"development", "final"}:
        raise ValueError("Holdout stage must be development or final")
    if "development_ids" in spec or "holdout_ids" in spec:
        development = list(spec.get("development_ids", []))
        holdout = list(spec.get("holdout_ids", []))
        if (not development or not holdout or len(set(development)) != len(development) or
                len(set(holdout)) != len(holdout) or set(development) & set(holdout) or
                set(development) | set(holdout) != set(ids)):
            raise ValueError("Explicit holdout IDs must partition the benchmark exactly, without overlap")
    else:
        fraction = _number(spec.get("fraction", 0.3), "holdout fraction")
        if not 0 < fraction < 1:
            raise ValueError("Holdout fraction must be between zero and one")
        shuffled = ids[:]
        random.Random(spec.get("seed", 12)).shuffle(shuffled)
        count = max(1, min(len(ids) - 1, math.ceil(len(ids) * fraction)))
        holdout, development = shuffled[:count], shuffled[count:]
    return {"development_ids": sorted(development), "holdout_ids": sorted(holdout),
            "seed": spec.get("seed", 12), "stage": stage,
            "membership": "Fixed from benchmark IDs only; common across all outcomes."}


def evaluate_holdout_rules(features, outcomes, partition, aggregation_spec):
    """Compare all candidates on development folds, then test only the winner.

    Development is the default stage: holdout values are not inspected. Final
    stage fits the selected rule on development papers and evaluates once on the
    fixed holdout. Missing/sparse targets return an explicit unavailable status.
    """
    indexed = _papers_by_id(features)
    spec = dict(aggregation_spec)
    selection_metric = spec.get("metric", "concordance")
    metrics = spec.get("report_metrics", ["spearman", "kendall_tau_b", "concordance"])
    if (selection_metric not in _METRICS or not isinstance(metrics, list) or not metrics or
            len(set(metrics)) != len(metrics) or set(metrics) - _METRICS):
        raise ValueError("Supply supported selection and reporting metrics")
    target_type = spec.get("target_type", "numeric")
    if selection_metric == "auc" and target_type != "binary":
        raise ValueError("AUC selection requires a binary target")
    metrics = [m for m in metrics if m != "auc" or target_type == "binary"]
    candidates = [dict(c) for c in spec.get("candidates", [])]
    if not 1 <= len(candidates) <= 64:
        raise ValueError("Supply between 1 and 64 explicit aggregation candidates")
    available = set(next(iter(indexed.values()))["features"]) if indexed else set()
    names = set()
    for index, candidate in enumerate(candidates):
        name = candidate.setdefault("name", f"candidate_{index + 1}")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("Candidate names must be unique nonempty strings")
        names.add(name)
        adapter = LinearScoreAdapter(candidate)
        if set(adapter.features) - available:
            raise ValueError("Candidate references unknown features: " + ", ".join(sorted(set(adapter.features) - available)))
        representation = candidate.get("representation", spec.get("representation", "continuous"))
        if representation not in {"continuous", "binned"} or (representation == "binned" and not candidate.get("bins")):
            raise ValueError("Binned candidates require bins; representation must be continuous or binned")
    development = list(partition["development_ids"])
    holdout = list(partition["holdout_ids"])
    if set(development) & set(holdout):
        raise ValueError("Development and holdout IDs overlap")
    y = {key: _number(outcomes.get(key), "development outcome", allow_none=True)
         for key in development if key in indexed}
    ids = [key for key in development if key in y and y[key] is not None]
    requested_folds = spec.get("validation_folds", 2)
    minimum = spec.get("min_validation_papers", 5)
    min_train = spec.get("min_train_papers", 5)
    for name, value, lower in (("validation_folds", requested_folds, 2),
                               ("min_validation_papers", minimum, 2), ("min_train_papers", min_train, 2)):
        if isinstance(value, bool) or not isinstance(value, int) or value < lower:
            raise ValueError(f"{name} must be an integer >= {lower}")
    output = {"status": "ok", "fold_results": [], "paper_scores": [], "selection_records": [],
              "selected_configurations": [], "candidate_summary": [], "holdout_results": [],
              "metadata": {"stage": partition["stage"], "selection_metric": selection_metric,
                           "n_development_eligible": len(ids), "n_development_excluded": len(development) - len(ids),
                           "holdout_accessed": False, "requested_validation_folds": requested_folds,
                           "selection_ties": "candidate order", "report_metrics": metrics}}
    folds = min(requested_folds, len(ids) // minimum)
    if folds < 2 or len(ids) - math.ceil(len(ids) / folds) < min_train:
        output.update(status="unavailable", reason="insufficient_development_papers_for_validation")
        return output
    # Spread the DEVELOPMENT outcome distribution over generous validation folds.
    # This may use development labels; holdout labels never influence membership.
    rng = random.Random(spec.get("seed", 12))
    ordered = sorted(ids, key=lambda key: (y[key], rng.random()))
    bins = [ordered[index::folds] for index in range(folds)]
    splits = [{"name": f"validation-{index + 1}", "test": sorted(test),
               "train": sorted(set(ids) - set(test))} for index, test in enumerate(bins)]
    output["metadata"].update(validation_folds=folds, validation_splits=splits)
    scored = []

    def predictions(adapter, candidate, keys):
        papers = [indexed[key] for key in keys]
        continuous = adapter.predict(papers)
        discrete = adapter.predict_bins(papers) if candidate.get("bins") else [None] * len(keys)
        representation = candidate.get("representation", spec.get("representation", "continuous"))
        return continuous, discrete, discrete if representation == "binned" else continuous

    for candidate in candidates:
        estimates = []
        per_metric = {m: [] for m in metrics}
        for fold in splits:
            train, test = fold["train"], fold["test"]
            try:
                adapter = LinearScoreAdapter(candidate).fit([indexed[k] for k in train], {k: y[k] for k in train})
                continuous, discrete, evaluated = predictions(adapter, candidate, test)
                target = [y[k] for k in test]
                selection = _metric(evaluated, target, selection_metric)
                if selection["estimate"] is not None:
                    estimates.append(selection["estimate"])
                output["selection_records"].append({"configuration": candidate["name"], "fold": fold["name"],
                    "metric": selection_metric, "partition": "development_validation", **selection})
                for metric in metrics:
                    result = _metric(evaluated, target, metric)
                    if result["estimate"] is not None:
                        per_metric[metric].append(result["estimate"])
                    output["fold_results"].append({"configuration": candidate["name"], "outer_fold": fold["name"],
                        "partition": "development_validation", "n_train": len(train), **result})
                for key, score, discrete_score, value in zip(test, continuous, discrete, evaluated):
                    output["paper_scores"].append({"doc_id": key, "outer_fold": fold["name"],
                        "configuration": candidate["name"], "partition": "development_validation",
                        "continuous_score": score, "discrete_score": discrete_score, "evaluated_score": value,
                        "outcome": y[key]})
            except ValueError as exc:
                output["selection_records"].append({"configuration": candidate["name"], "fold": fold["name"],
                    "metric": selection_metric, "partition": "development_validation", "estimate": None,
                    "status": "invalid", "reason": str(exc)})
        for metric, values in per_metric.items():
            output["candidate_summary"].append({"configuration": candidate["name"], "metric": metric,
                "estimate": statistics.mean(values) if len(values) == folds else None,
                "n_valid_folds": len(values), "n_folds": folds, "partition": "development_validation"})
        if len(estimates) == folds:
            scored.append((candidate, statistics.mean(estimates)))
    if not scored:
        output.update(status="unavailable", reason="no_candidate_has_defined_selection_metrics_on_all_folds")
        return output
    selected, estimate = min(scored, key=lambda item: item[1] if selection_metric in {"mae", "rmse"} else -item[1])
    adapter = LinearScoreAdapter(selected).fit([indexed[k] for k in ids], {k: y[k] for k in ids})
    output["selected_configurations"].append({"configuration": selected["name"], "metric": selection_metric,
        "inner_estimate": estimate, "status": "ok", "selection": "development cross-validation",
        "fit_metadata": adapter.export_metadata()})
    if partition["stage"] == "final":
        test_y = {k: _number(outcomes.get(k), "holdout outcome", allow_none=True) for k in holdout if k in indexed}
        test = [k for k in holdout if k in test_y and test_y[k] is not None]
        output["metadata"].update(holdout_accessed=True, n_holdout_eligible=len(test),
                                  n_holdout_excluded=len(holdout) - len(test))
        continuous, discrete, evaluated = predictions(adapter, selected, test)
        for metric in metrics:
            output["holdout_results"].append({"configuration": selected["name"], "partition": "holdout",
                **_metric(evaluated, [test_y[k] for k in test], metric)})
        for key, score, discrete_score, value in zip(test, continuous, discrete, evaluated):
            output["paper_scores"].append({"doc_id": key, "outer_fold": "holdout", "partition": "holdout",
                "configuration": selected["name"], "continuous_score": score, "discrete_score": discrete_score,
                "evaluated_score": value, "outcome": test_y[key]})
    return output


def evaluate_aggregation_rules(features, outcomes, paper_splits, aggregation_spec):
    """Select candidates using inner folds; assess only on outer-test papers.

    ``outcomes`` is doc_id -> oriented numeric value or null. All candidates use
    the same outcome-eligible cohort in a fold; feature imputation never changes
    that cohort. A selection candidate must yield a defined metric in EVERY
    specified inner fold. Equal inner-fold weighting and original candidate
    order break ties. Candidate count is bounded at 64. A single supplied rule
    needs no inner selection; multiple candidates require explicit inner folds.
    """
    indexed = _papers_by_id(features)
    if not isinstance(outcomes, Mapping):
        raise ValueError("outcomes must map paper IDs to oriented values")
    target = {key: _number(outcomes.get(key), "outcome " + key, allow_none=True) for key in indexed}
    splits = _validate_splits(paper_splits, set(indexed))
    spec = dict(aggregation_spec or {})
    metric = spec.get("metric", "concordance")
    if metric not in _METRICS:
        raise ValueError("Unknown evaluation metric: " + str(metric))
    candidates = [dict(candidate) for candidate in spec.get("candidates", [])]
    if not 1 <= len(candidates) <= 64:
        raise ValueError("Supply between 1 and 64 explicit aggregation candidates")
    names = []
    feature_columns = {column for paper in indexed.values() for column in paper["features"]}
    for index, candidate in enumerate(candidates):
        name = candidate.get("name", "candidate_{}".format(index + 1))
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("Candidate names must be unique nonempty strings")
        candidate["name"] = name
        names.append(name)
        referenced = (candidate.get("weights", {}) if candidate.get("kind", "fixed") == "fixed"
                      else candidate.get("features", []))
        if not isinstance(referenced, (Mapping, list, tuple)) or any(
                not isinstance(feature, str) or not feature for feature in referenced):
            raise ValueError("Candidate feature names must be explicit nonempty strings")
        unknown = set(referenced) - feature_columns
        if unknown:
            raise ValueError("Unknown candidate features: " + ", ".join(sorted(unknown)))
        representation = candidate.get("representation", spec.get("representation", "continuous"))
        if representation not in {"continuous", "binned"}:
            raise ValueError("Score representation must be continuous or binned")
        if representation == "binned" and candidate.get("bins") is None:
            raise ValueError("A binned candidate requires explicit bin thresholds or quantiles")
    if len(candidates) > 1 and any(not fold["inner"] for fold in splits):
        raise ValueError("Multiple candidates require explicit inner folds in every outer fold")
    factory = spec.get("adapter_factory", LinearScoreAdapter)
    if not callable(factory):
        raise ValueError("adapter_factory must be callable")
    test_occurrences = {key: sum(key in fold["test"] for fold in splits) for key in indexed}
    output = {"fold_results": [], "paper_scores": [], "selection_records": [], "selected_configurations": [],
              "metadata": {"selection_metric": metric, "selection_ties": "candidate order",
                           "selection_eligibility": "all specified inner folds have defined metrics",
                           "inner_aggregation": "unweighted mean across folds",
                           "missing_outcomes": "exclude with counts; same cohort for every candidate",
                           "rank_comparisons": "within each held-out fold only",
                           "outer_test_occurrences": test_occurrences,
                           "split_design": "explicit folds; outer-test papers may repeat",
                           "error_interpretation": "Errors use evaluated scores; substantive prediction error "
                               "requires scores calibrated to the outcome scale. Ranking rules need not be calibrated."}}

    def cohort(ids):
        return [value for value in ids if target[value] is not None]

    def coverage(train_ids, test_ids):
        n_train, n_test = len(cohort(train_ids)), len(cohort(test_ids))
        return {"n_train": n_train, "n_test": n_test,
                "n_train_excluded_outcome": len(train_ids) - n_train,
                "n_test_excluded_outcome": len(test_ids) - n_test}

    def fit_and_score(candidate, train_ids, test_ids):
        train, test = cohort(train_ids), cohort(test_ids)
        if not train or not test:
            raise ValueError("No outcome-eligible training or test papers")
        adapter = factory(dict(candidate))
        adapter.fit([indexed[key] for key in train], {key: target[key] for key in train})
        heldout = [indexed[key] for key in test]
        continuous = [_number(value, "held-out score") for value in adapter.predict(heldout)]
        if len(continuous) != len(test):
            raise ValueError("Adapter prediction length does not match test papers")
        binned = adapter.predict_bins(heldout) if candidate.get("bins") is not None else [None] * len(test)
        if len(binned) != len(test):
            raise ValueError("Adapter bin prediction length does not match test papers")
        representation = candidate.get("representation", spec.get("representation", "continuous"))
        evaluated = binned if representation == "binned" else continuous
        result = _metric(evaluated, [target[key] for key in test], metric)
        result.update(coverage(train_ids, test_ids))
        result["representation"] = representation
        return adapter, test, continuous, binned, result

    for outer in splits:
        eligible_candidates = []
        for candidate_index, candidate in enumerate(candidates):
            inner_estimates = []
            for inner in outer["inner"]:
                record = {"outer_fold": outer["name"], "inner_fold": inner["name"],
                          "configuration": candidate["name"], "metric": metric}
                record.update(coverage(inner["train"], inner["test"]))
                try:
                    _, _, _, _, result = fit_and_score(candidate, inner["train"], inner["test"])
                    record.update(result)
                    estimate = result.get("estimate")
                    record["status"] = "ok" if estimate is not None else "undefined"
                    if estimate is not None:
                        inner_estimates.append(estimate)
                except ValueError as error:
                    record.update({"status": "invalid", "estimate": None, "reason": str(error)})
                output["selection_records"].append(record)
            complete = len(inner_estimates) == len(outer["inner"])
            if (complete and inner_estimates) or len(candidates) == 1:
                score = statistics.mean(inner_estimates) if inner_estimates else None
                eligible_candidates.append((candidate_index, candidate, score))
        if not eligible_candidates:
            output["selected_configurations"].append({"outer_fold": outer["name"], "configuration": None,
                "status": "undefined", "reason": "No candidate has defined metrics on every inner fold"})
            continue
        if len(candidates) == 1:
            _, selected, selection_estimate = eligible_candidates[0]
        else:
            minimizing = metric in {"mae", "rmse"}
            _, selected, selection_estimate = min(eligible_candidates,
                key=lambda item: ((item[2] if minimizing else -item[2]), item[0]))
        selection = {"outer_fold": outer["name"], "configuration": selected["name"],
                     "inner_estimate": selection_estimate, "metric": metric,
                     "selection": "single supplied candidate" if len(candidates) == 1 else "inner cross-validation"}
        try:
            adapter, test, continuous, binned, result = fit_and_score(selected, outer["train"], outer["test"])
            selection["status"] = "ok"
            if hasattr(adapter, "export_metadata"):
                selection["fit_metadata"] = adapter.export_metadata()
            row = {"outer_fold": outer["name"], "configuration": selected["name"], "metric": metric,
                   "status": "ok" if result.get("estimate") is not None else "undefined"}
            row.update(result)
            output["fold_results"].append(row)
            for key, score, discrete in zip(test, continuous, binned):
                evaluated_score = discrete if result["representation"] == "binned" else score
                residual = evaluated_score - target[key]
                output["paper_scores"].append({"doc_id": key, "outer_fold": outer["name"],
                    "configuration": selected["name"], "continuous_score": score, "discrete_score": discrete,
                    "evaluated_score": evaluated_score, "outcome": target[key],
                    "residual": residual, "continuous_residual": score - target[key], "absolute_error": abs(residual),
                    "squared_error": residual * residual})
        except ValueError as error:
            selection.update({"status": "invalid", "reason": str(error)})
            output["fold_results"].append({"outer_fold": outer["name"], "configuration": selected["name"],
                "metric": metric, "estimate": None, "status": "invalid", "reason": str(error),
                **coverage(outer["train"], outer["test"])})
        output["selected_configurations"].append(selection)
    return output


def evaluate_feature_families(features, outcomes, paper_splits, analysis_spec):
    """Compare a full model and refitted family ablations on identical outer folds.

    ``base`` is a frozen ridge specification; ``families`` maps family names to
    features removed together. No best family is selected. Zero-feature reduced
    models are explicitly skipped. Alpha is supplied, not tuned on outer tests.
    """
    spec = dict(analysis_spec or {})
    families = spec.get("families", {})
    if not isinstance(families, Mapping) or not families:
        raise ValueError("Supply feature families as name -> feature-name list")
    if any(not isinstance(name, str) or not name or not isinstance(members, (list, tuple))
           or any(not isinstance(member, str) or not member for member in members)
           for name, members in families.items()):
        raise ValueError("Each family needs a nonempty name and a list of feature names")
    if len(families) > 63:
        raise ValueError("At most 63 feature families are supported")
    base = dict(spec.get("base", {"kind": "ridge", "alpha": 1.0}))
    if base.get("kind") != "ridge":
        raise ValueError("Family refitting requires an explicit learned ridge model")
    all_features = list(base.get("features", dict.fromkeys(
        feature for members in families.values() for feature in members)))
    configurations = [dict(base, name="full", features=all_features)]
    skipped = []
    for name, members in families.items():
        if not isinstance(name, str) or not name or not isinstance(members, (list, tuple)):
            raise ValueError("Each family needs a nonempty name and a list of features")
        if not set(members) <= set(all_features):
            raise ValueError("Family contains features absent from the full model: " + name)
        remaining = [feature for feature in all_features if feature not in set(members)]
        if remaining:
            configurations.append(dict(base, name="without_" + name, features=remaining))
        else:
            skipped.append({"configuration": "without_" + name, "reason": "No features remain"})
    result = {key: [] for key in ("fold_results", "paper_scores", "selection_records", "selected_configurations")}
    result.update({"skipped": skipped, "comparison_scope": "held-out refitted ablations; no family selection"})
    features = list(features)
    splits = [dict(fold, inner=[]) for fold in paper_splits]
    for model in configurations:
        tables = evaluate_aggregation_rules(features, outcomes, splits,
            {"candidates": [model], "metric": spec.get("metric", "concordance"),
             "representation": spec.get("representation", "continuous"),
             "adapter_factory": spec.get("adapter_factory", LinearScoreAdapter)})
        for key in ("fold_results", "paper_scores", "selection_records", "selected_configurations"):
            result[key].extend(tables[key])
    return result


def run_robustness_checks(features, outcomes, paper_splits=None, robustness_spec=None):
    """Percentile paper bootstrap of a FIXED score/outcome association.

    This does not refit models or repeat rule selection and therefore does not
    estimate uncertainty of a model-selection procedure. An iid paper bootstrap
    treats all reviewer/issue records within a sampled paper as a single unit.
    """
    indexed = _papers_by_id(features)
    spec = dict(robustness_spec or {})
    scores = spec.get("scores")
    if not isinstance(scores, Mapping) or not isinstance(outcomes, Mapping):
        raise ValueError("Supply fixed scores and outcomes as doc_id mappings")
    metric = spec.get("metric", "concordance")
    if metric not in _METRICS:
        raise ValueError("Unknown evaluation metric: " + str(metric))
    count = spec.get("n_resamples", 200)
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 10000:
        raise ValueError("n_resamples must be an integer from 1 through 10000")
    confidence = _number(spec.get("confidence", 0.95), "confidence")
    if not 0 < confidence < 1:
        raise ValueError("confidence must lie inside (0, 1)")
    pairs = []
    for key in indexed:
        score = _number(scores.get(key), "score " + key, allow_none=True)
        target = _number(outcomes.get(key), "outcome " + key, allow_none=True)
        if score is not None and target is not None:
            pairs.append((score, target))
    if not pairs:
        raise ValueError("No papers have both a fixed score and an outcome")
    point = _metric([row[0] for row in pairs], [row[1] for row in pairs], metric)
    rng = random.Random(spec.get("seed", 0))
    estimates = []
    for _ in range(count):
        sample = [pairs[rng.randrange(len(pairs))] for _ in pairs]
        value = _metric([row[0] for row in sample], [row[1] for row in sample], metric).get("estimate")
        if value is not None:
            estimates.append(value)
    tail = (1.0 - confidence) / 2.0
    return {"metric": metric, "estimate": point.get("estimate"), "point_result": point,
            "interval": {"lower": _quantile(estimates, tail) if estimates else None,
                         "upper": _quantile(estimates, 1 - tail) if estimates else None},
            "confidence": confidence, "n_papers": len(pairs), "n_excluded": len(indexed) - len(pairs),
            "n_resamples": count, "n_valid_resamples": len(estimates), "n_valid": len(estimates),
            "n_undefined_resamples": count - len(estimates), "seed": spec.get("seed", 0),
            "unit": "paper", "method": "percentile bootstrap",
            "scope": "fixed-score association; no model refitting or configuration reselection"}
