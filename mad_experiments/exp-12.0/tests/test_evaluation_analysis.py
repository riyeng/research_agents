"""Offline evaluation contracts, checked on generated review ledgers and outcomes.

Every outcome below is synthetic. No benchmark model run, API, or numerical
dependency is needed to test the evaluator's statistical and data boundaries.
"""

from copy import deepcopy
from collections import Counter
import csv
import hashlib
import importlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


TESTS = Path(__file__).resolve().parent
ANALYSIS = TESTS.parent / "analysis"
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(ANALYSIS))
from test_feature_selection import PANEL, assessment, features, issue, record, stance, revision

evaluation = importlib.import_module("evaluation_analysis")


def synthetic_bundle(n=9):
    """Use the production feature builder; never hand-invent its export schema."""
    papers = []
    for index in range(n):
        raw = record()
        raw["run_id"] = f"synthetic-run-{index}"
        pre = ("NONE", "LOW", "MEDIUM", "HIGH")[index % 4]
        post = ("NONE", "LOW", "MEDIUM", "HIGH")[min(index % 4 + 1, 3)]
        raw["pre_assessments"] = [assessment(p, pre, index % 5) for p in PANEL]
        raw["post_assessments"] = [assessment(p, post, (index + 1) % 5) for p in PANEL]
        for phase in ("pre", "post"):
            for reviewer, row in enumerate(raw[phase + "_assessments"]):
                row["outcome"]["payload"]["verdict"] = (
                    "ACCEPT", "RESUBMIT", "REJECT"
                )[(index + reviewer + (phase == "post")) % 3]
        raw["issues"] = [issue(1, severity=1 + index % 10)]
        raw["issue_updates"] = [stance(1, "RETRACT" if index % 2 else "MAINTAIN")]
        papers.append(features.build_paper_features(raw, f"synthetic-{index}"))
    dictionary = papers[0]["feature_dictionary"]
    for paper in papers:
        if paper.pop("feature_dictionary") != dictionary:
            raise AssertionError("Synthetic feature definitions must be identical")
    return {"feature_dictionary": dictionary, "papers": papers}


def synthetic_outcomes(n=9):
    return [{
        "doc_id": f"synthetic-{index}",
        "crossref_wp_cites": str(index * index),
        "crossref_wp_status": "PULLED_CROSSREF_CITED_BY",
        "crossref_wp_retrieved_at_utc": "2026-10-09T00:00:00Z",
        "sjr_global_rank": str(index + 1),
        "pjip_status": "MATCHED_SCIMAGO_2025_ARCHIVED_EXPORT",
        "tier_used_in_experiments": ("Tier1", "Tier2", "Tier3")[index % 3],
        "stern_finance_top_tier": str(index % 2),
        "stern_status": "VERIFIED_LISTED_2020_FINANCE_LIST" if index % 2 else "VERIFIED_NOT_LISTED_2020_FINANCE_LIST",
        "ranking_snapshot_date": "2026-10-09",
    } for index in range(n)]


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def reviewer_rows(pre, post, *, missing_post=()):
    raw = record()
    for phase, values in (("pre", pre), ("post", post)):
        raw[phase + "_assessments"] = []
        for persona, verdict in zip(PANEL, values):
            entry = assessment(persona, status="FAILED" if phase == "post" and persona in missing_post else "OK")
            if entry["outcome"]["payload"] is not None:
                entry["outcome"]["payload"]["verdict"] = verdict
            raw[phase + "_assessments"].append(entry)
    return features.build_paper_features(raw, "paper")["reviewer_features"]


class InputContractTests(unittest.TestCase):
    def test_export_json_and_ratings_csv_load_without_manifest_or_source_mutation(self):
        bundle, outcomes = synthetic_bundle(), synthetic_outcomes()
        original = deepcopy((bundle, outcomes))
        direct = evaluation.load_features_and_outcomes(bundle, outcomes)
        self.assertEqual((bundle, outcomes), original)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feature_path, outcome_path = root / "features.json", root / "outcomes.csv"
            feature_path.write_text(json.dumps(bundle), encoding="utf-8")
            write_csv(outcome_path, outcomes)
            loaded = evaluation.load_features_and_outcomes(feature_path, outcome_path)
        self.assertEqual(loaded["papers"], direct["papers"])
        self.assertEqual(set(loaded["coverage"]["matched_ids"]), {p["doc_id"] for p in bundle["papers"]})
        self.assertEqual(loaded["coverage"]["feature_only_ids"], [])

    def test_join_coverage_preserves_unmatched_ids(self):
        outcomes = synthetic_outcomes(2)
        outcomes.append({**outcomes[0], "doc_id": "outcome-only"})
        result = evaluation.load_features_and_outcomes(synthetic_bundle(3), outcomes)
        self.assertEqual(result["coverage"]["feature_only_ids"], ["synthetic-2"])
        self.assertEqual(result["coverage"]["outcome_only_ids"], ["outcome-only"])

    def test_unknown_dictionary_version_rejected(self):
        bundle = synthetic_bundle(2)
        bundle["feature_dictionary"]["version"] = "incompatible-future-version"
        with self.assertRaises(ValueError):
            evaluation.load_features_and_outcomes(bundle, synthetic_outcomes(2))

    def test_duplicate_paper_and_outcome_ids_rejected(self):
        bundle, outcomes = synthetic_bundle(2), synthetic_outcomes(2)
        with self.assertRaises(ValueError):
            evaluation.load_features_and_outcomes({**bundle, "papers": bundle["papers"] * 2}, outcomes)
        with self.assertRaises(ValueError):
            evaluation.load_features_and_outcomes(bundle, outcomes * 2)

    def test_nonfinite_features_and_undocumented_columns_rejected(self):
        for value in (float("nan"), float("inf")):
            bundle = synthetic_bundle(2)
            bundle["papers"][0]["features"]["insight_pre_mean"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                evaluation.load_features_and_outcomes(bundle, synthetic_outcomes(2))
        bundle = synthetic_bundle(2)
        bundle["papers"][0]["features"]["undefined_feature"] = 3
        with self.assertRaises(ValueError):
            evaluation.load_features_and_outcomes(bundle, synthetic_outcomes(2))

    def test_editor_outcome_availability_and_verdict_are_validated(self):
        invalid = [None, {},
                   {"availability": "ASSESSED", "verdict": "MAYBE", "source_call_ids": [], "source_path": "editor"},
                   {"availability": "FAILED", "verdict": "REJECT", "source_call_ids": [], "source_path": "editor"}]
        for editor in invalid:
            bundle = synthetic_bundle(2)
            bundle["papers"][0]["editor_outcome"] = editor
            with self.subTest(editor=editor), self.assertRaises(ValueError):
                evaluation.load_features_and_outcomes(bundle, synthetic_outcomes(2))
    def test_expected_dictionary_hash_accepts_frozen_export_and_rejects_changed_definition(self):
        bundle, outcomes = synthetic_bundle(2), synthetic_outcomes(2)
        dictionary_hash = hashlib.sha256(json.dumps(bundle["feature_dictionary"], sort_keys=True,
                                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        result = evaluation.load_features_and_outcomes(bundle, outcomes, {
            "expected_dictionary_sha256": dictionary_hash})
        self.assertEqual(result["provenance"]["feature_dictionary_sha256"], dictionary_hash)
        bundle["feature_dictionary"]["columns"]["insight_pre_mean"]["definition"] += " Changed aggregation."
        with self.assertRaises(ValueError):
            evaluation.load_features_and_outcomes(bundle, outcomes, {
                "expected_dictionary_sha256": dictionary_hash})


class OutcomeRegistryTests(unittest.TestCase):
    def test_zero_citations_are_valid_and_missing_is_excluded(self):
        outcomes = synthetic_outcomes(3)
        outcomes[1]["crossref_wp_cites"] = ""
        target = evaluation.register_external_outcomes(outcomes, ["crossref_wp_cites"])["crossref_wp_cites"]
        self.assertEqual(target["values"]["synthetic-0"], 0)
        self.assertIn("synthetic-0", target["eligible_ids"])
        self.assertNotIn("synthetic-1", target["eligible_ids"])
        self.assertEqual(target["values"]["synthetic-2"], 4)

    def test_ranks_and_tiers_are_oriented_higher_is_better(self):
        targets = evaluation.register_external_outcomes(synthetic_outcomes(3), ["sjr_global_rank", "tier_used_in_experiments"])
        for target in targets.values():
            values = target["values"]
            self.assertGreater(values["synthetic-0"], values["synthetic-1"])
            self.assertGreater(values["synthetic-1"], values["synthetic-2"])

    def test_binary_membership_retains_zero_and_one(self):
        target = evaluation.register_external_outcomes(synthetic_outcomes(2), ["stern_finance_top_tier"])["stern_finance_top_tier"]
        self.assertEqual(target["values"], {"synthetic-0": 0, "synthetic-1": 1})

    def test_lower_is_better_binary_target_orients_desired_class_to_one_for_auc(self):
        outcomes = synthetic_outcomes(4)
        targets = evaluation.register_external_outcomes(outcomes, {
            "stern_finance_top_tier": {"direction": "lower"}})
        target = targets["stern_finance_top_tier"]
        self.assertEqual(target["raw_values"], {"synthetic-0": 0, "synthetic-1": 1,
                                                "synthetic-2": 0, "synthetic-3": 1})
        self.assertEqual(target["values"], {"synthetic-0": 1, "synthetic-1": 0,
                                            "synthetic-2": 1, "synthetic-3": 0})
        papers = synthetic_bundle(4)["papers"]
        for paper in papers:
            paper["features"]["insight_pre_mean"] = target["values"][paper["doc_id"]]
        result = evaluation.analyze_external_validity(papers, targets, analysis_spec={
            "features": ["insight_pre_mean"], "metrics": ["auc"]})
        self.assertEqual(result[0]["estimate"], 1)

    def test_unknown_target_requires_explicit_definition(self):
        with self.assertRaises(ValueError):
            evaluation.register_external_outcomes(synthetic_outcomes(), ["unrecognized_column"])

    def test_negative_citation_or_nonfinite_target_cannot_enter_metrics(self):
        for value in ("-1", "nan", "inf"):
            outcomes = synthetic_outcomes(2)
            outcomes[0]["crossref_wp_cites"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                evaluation.register_external_outcomes(outcomes, ["crossref_wp_cites"])

    def test_existing_rankings_snapshot_registers_without_evaluation_or_modification(self):
        path = TESTS.parents[3] / "rankings.csv"
        before = path.read_bytes()
        registry = evaluation.register_external_outcomes(path)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(registry), 10)
        self.assertEqual(len(registry["wp_cites"]["eligible_ids"]), 50)
        self.assertEqual(len(registry["cites"]["eligible_ids"]), 50)
        self.assertEqual(len(registry["stern_finance_top_tier"]["eligible_ids"]), 50)
        self.assertEqual(len(registry["sjr_best_quartile"]["eligible_ids"]), 44)
        self.assertTrue(any(value == 0 for value in registry["wp_cites"]["values"].values()))
        ranks = registry["sjr_global_rank"]
        for doc_id in ranks["eligible_ids"]:
            self.assertEqual(ranks["values"][doc_id], -ranks["raw_values"][doc_id])


class MetricTests(unittest.TestCase):
    def test_perfect_and_reversed_orderings(self):
        for metric in ("spearman", "kendall_tau_b", "concordance"):
            with self.subTest(metric=metric):
                self.assertAlmostEqual(evaluation.metric_result([1, 2, 3], [1, 2, 3], metric)["estimate"], 1)
                expected = 0 if metric == "concordance" else -1
                self.assertAlmostEqual(evaluation.metric_result([3, 2, 1], [1, 2, 3], metric)["estimate"], expected)

    def test_tied_scores_have_correct_fractional_concordance_and_rank_adjustments(self):
        x, y = [1, 1, 3], [1, 2, 3]
        self.assertAlmostEqual(evaluation.metric_result(x, y, "concordance")["estimate"], 5 / 6)
        self.assertAlmostEqual(evaluation.metric_result(x, y, "spearman")["estimate"], math.sqrt(3) / 2)
        self.assertAlmostEqual(evaluation.metric_result(x, y, "kendall_tau_b")["estimate"], 2 / math.sqrt(6))

    def test_external_ties_are_excluded_from_concordance(self):
        result = evaluation.metric_result([3, 1, 2], [1, 1, 2], "concordance")
        self.assertAlmostEqual(result["estimate"], 0.5)
        self.assertEqual(result["n_comparable_pairs"], 2)
        self.assertEqual(result["n_outcome_ties"], 1)

    def test_missing_pairs_are_removed_together_and_constant_ranks_undefined(self):
        result = evaluation.metric_result([1, None, 3, 4], [1, 2, None, 4], "spearman")
        self.assertAlmostEqual(result["estimate"], 1)
        for metric in ("spearman", "kendall_tau_b"):
            undefined = evaluation.metric_result([1, 1, 1], [1, 2, 3], metric)
            self.assertIsNone(undefined["estimate"])
            self.assertTrue(undefined["undefined_reason"])
        self.assertIsNone(evaluation.metric_result([1], [1], "concordance")["estimate"])

    def test_prediction_error_and_binary_auc(self):
        self.assertAlmostEqual(evaluation.metric_result([1, 3], [2, 1], "mae")["estimate"], 1.5)
        self.assertAlmostEqual(evaluation.metric_result([1, 3], [2, 1], "rmse")["estimate"], math.sqrt(2.5))
        self.assertAlmostEqual(evaluation.metric_result([0.1, 0.9, 0.5], [0, 1, 1], "auc")["estimate"], 1)
        self.assertIsNone(evaluation.metric_result([0.1, 0.9], [1, 1], "auc")["estimate"])

    def test_vector_length_and_nonfinite_errors_are_explicit(self):
        with self.assertRaises(ValueError):
            evaluation.metric_result([1, 2], [1], "spearman")
        with self.assertRaises(ValueError):
            evaluation.metric_result([1, float("inf")], [1, 2], "spearman")


class VerdictDiagnosticTests(unittest.TestCase):
    def test_unanimity_two_to_one_and_three_distinct_verdicts(self):
        cases = [
            (["ACCEPT"] * 3, 0, 0, 0),
            (["ACCEPT", "ACCEPT", "RESUBMIT"], 1, 2 / 3, 0),
            (["ACCEPT", "RESUBMIT", "REJECT"], 1, 1, 1),
            (["ACCEPT", "ACCEPT", "REJECT"], 1, 2 / 3, 1),
        ]
        for verdicts, any_disagreement, pairwise, polarized in cases:
            with self.subTest(verdicts=verdicts):
                result = evaluation.construct_verdict_diagnostics(reviewer_rows(verdicts, verdicts))
                self.assertEqual(len(result["phase_summaries"]), 2)
                for summary in result["phase_summaries"]:
                    self.assertEqual(summary["any_disagreement"], any_disagreement)
                    self.assertAlmostEqual(summary["pairwise_disagreement"], pairwise)
                    self.assertEqual(summary["polarized"], polarized)
                    self.assertTrue(summary["complete"])
                    self.assertEqual(summary["n_valid"], 3)

    def test_missing_dissenting_reviewer_does_not_create_matched_convergence(self):
        rows = reviewer_rows(["ACCEPT", "ACCEPT", "REJECT"], ["ACCEPT"] * 3,
                             missing_post=(PANEL[2],))
        result = evaluation.construct_verdict_diagnostics(rows)
        phases = {row["phase"]: row for row in result["phase_summaries"]}
        self.assertAlmostEqual(phases["PRE"]["pairwise_disagreement"], 2 / 3)
        self.assertEqual(phases["POST"]["pairwise_disagreement"], 0)
        self.assertFalse(phases["POST"]["complete"])
        matched = result["matched_changes"][0]
        self.assertEqual(matched["n_matched"], 2)
        self.assertEqual(matched["pre_disagreement"], 0)
        self.assertEqual(matched["post_disagreement"], 0)
        self.assertEqual(matched["convergence"], 0)
        self.assertEqual(len(result["transitions"]), 2)

    def test_fewer_than_two_valid_verdicts_is_unknown_disagreement(self):
        rows = reviewer_rows(["ACCEPT"] * 3, ["ACCEPT"] * 3,
                             missing_post=(PANEL[1], PANEL[2]))
        result = evaluation.construct_verdict_diagnostics(rows)
        post = next(row for row in result["phase_summaries"] if row["phase"] == "POST")
        self.assertEqual(post["n_valid"], 1)
        self.assertIsNone(post["any_disagreement"])
        self.assertIsNone(post["pairwise_disagreement"])
        self.assertIsNone(post["polarized"])
        self.assertIsNone(result["matched_changes"][0]["convergence"])

    def test_verdict_transitions_preserve_direction_and_ignore_other_constructs(self):
        rows = reviewer_rows(["REJECT", "RESUBMIT", "ACCEPT"],
                             ["ACCEPT", "REJECT", "ACCEPT"])
        original = deepcopy(rows)
        result = evaluation.construct_verdict_diagnostics(rows)
        directions = {row["persona"]: row["direction"] for row in result["transitions"]}
        self.assertEqual(directions, {PANEL[0]: "up", PANEL[1]: "down", PANEL[2]: "unchanged"})
        matched = result["matched_changes"][0]
        self.assertEqual((matched["n_up"], matched["n_down"], matched["n_unchanged"]), (1, 1, 1))
        self.assertEqual(rows, original)


class ScoringAndSplitTests(unittest.TestCase):
    @staticmethod
    def paper(doc_id, value):
        return {"doc_id": doc_id, "features": {"x": value}}

    def test_fixed_rule_learns_median_scale_and_quantiles_only_on_training_papers(self):
        papers = [self.paper("a", 0), self.paper("b", 2), self.paper("c", None)]
        before = deepcopy(papers)
        adapter = evaluation.LinearScoreAdapter({"name": "fixed", "kind": "fixed", "weights": {"x": 1},
                                                "missing": "median", "standardize": True,
                                                "bins": {"quantiles": [0.5]}})
        adapter.fit(papers, {"a": 0, "b": 1, "c": 2})
        snapshot = deepcopy(adapter.export_metadata())
        self.assertEqual(adapter.medians["x"], 1)
        self.assertEqual(set(adapter.fit_ids), {"a", "b", "c"})
        self.assertAlmostEqual(adapter.predict([self.paper("unknown", None)])[0], 0)
        self.assertEqual(adapter.bin_thresholds, [0])
        adapter.predict([self.paper("held-out", 100000)])
        self.assertEqual(adapter.export_metadata(), snapshot)
        self.assertEqual(papers, before)

    def test_fixed_contributions_sum_to_score_and_bins_are_ordered(self):
        papers = [self.paper("a", 1), self.paper("b", 2), self.paper("c", 3)]
        adapter = evaluation.LinearScoreAdapter({"name": "fixed", "kind": "fixed", "weights": {"x": 2},
                                                "bins": {"thresholds": [3, 5]}})
        adapter.fit(papers, {})
        self.assertEqual(adapter.predict(papers), [2, 4, 6])
        bins = adapter.predict_bins(papers)
        self.assertEqual(len(set(bins)), 3)
        self.assertEqual(sorted(bins), bins)
        contributions = adapter.contributions(papers)
        self.assertEqual(len(contributions), len(papers))
        for row, score in zip(contributions, adapter.predict(papers)):
            self.assertEqual(row["intercept"] + sum(row["components"].values()), score)

    def test_ridge_learns_simple_relationship_and_predicts_unseen_papers(self):
        papers = [self.paper(str(i), i) for i in range(6)]
        adapter = evaluation.LinearScoreAdapter({"name": "ridge", "kind": "ridge", "features": ["x"],
                                                "alpha": 0.001})
        adapter.fit(papers, {str(i): 3 * i + 2 for i in range(6)})
        self.assertAlmostEqual(adapter.predict([self.paper("test", 6)])[0], 20, places=2)
        self.assertGreater(adapter.coefficients["x"], 0)

    def test_missing_error_and_prediction_before_fit_are_explicit(self):
        adapter = evaluation.LinearScoreAdapter({"kind": "fixed", "weights": {"x": 1}, "missing": "error"})
        with self.assertRaises(ValueError):
            adapter.predict([self.paper("test", 1)])
        with self.assertRaises(ValueError):
            adapter.fit([self.paper("a", None)], {})

    def test_all_missing_training_feature_rejected_and_constant_quantile_bins_documented(self):
        adapter = evaluation.LinearScoreAdapter({"kind": "fixed", "weights": {"x": 1}, "missing": "median"})
        with self.assertRaises(ValueError):
            adapter.fit([self.paper("a", None), self.paper("b", None)], {})
        adapter = evaluation.LinearScoreAdapter({"kind": "fixed", "weights": {"x": 1},
                                                "bins": {"quantiles": [0.33, 0.67]}})
        papers = [self.paper("a", 3), self.paper("b", 3)]
        adapter.fit(papers, {})
        self.assertEqual(adapter.bin_thresholds, [3])
        self.assertEqual(adapter.diagnostics["collapsed_bin_thresholds"], 1)
        self.assertEqual(adapter.predict_bins(papers), [1, 1])

    def test_paper_splits_are_reproducible_disjoint_and_nested(self):
        ids = [f"paper-{i}" for i in range(12)]
        splits = evaluation.make_paper_splits(ids, n_splits=3, inner_splits=2, seed=19)
        self.assertEqual(splits, evaluation.make_paper_splits(ids, n_splits=3, inner_splits=2, seed=19))
        test_ids = []
        for fold in splits:
            train, test = set(fold["train"]), set(fold["test"])
            self.assertFalse(train & test)
            self.assertEqual(train | test, set(ids))
            test_ids.extend(test)
            for inner in fold["inner"]:
                self.assertFalse(set(inner["train"]) & set(inner["test"]))
                self.assertLessEqual(set(inner["train"]) | set(inner["test"]), train)
        self.assertCountEqual(test_ids, ids)

    def test_search_rejects_outer_overlap_and_multiple_candidates_without_inner_folds(self):
        papers = [self.paper(str(i), i) for i in range(6)]
        outcomes = {str(i): i for i in range(6)}
        candidate = {"name": "fixed", "kind": "fixed", "weights": {"x": 1}}
        invalid = [{"name": "bad", "train": ["0", "1", "2"], "test": ["2", "3"]}]
        with self.assertRaises(ValueError):
            evaluation.evaluate_aggregation_rules(papers, outcomes, invalid, {"candidates": [candidate], "metric": "concordance"})
        no_inner = [{"name": "valid", "train": ["0", "1", "2"], "test": ["3", "4", "5"]}]
        with self.assertRaises(ValueError):
            evaluation.evaluate_aggregation_rules(papers, outcomes, no_inner, {
                "candidates": [candidate, {**candidate, "name": "other"}], "metric": "concordance"})

    def test_nested_search_fits_exact_training_sets_and_scores_each_held_out_paper_once(self):
        papers = [self.paper(str(i), i) for i in range(12)]
        outcomes = {str(i): i for i in range(12)}
        splits = evaluation.make_paper_splits(list(outcomes), n_splits=3, inner_splits=2, seed=4)
        observed_fit_sets = []

        class SpyAdapter(evaluation.LinearScoreAdapter):
            def fit(self, training, y):
                observed_fit_sets.append(frozenset(p["doc_id"] for p in training))
                return super().fit(training, y)

        candidates = [{"name": "positive", "kind": "fixed", "weights": {"x": 1}},
                      {"name": "negative", "kind": "fixed", "weights": {"x": -1}}]
        result = evaluation.evaluate_aggregation_rules(papers, outcomes, splits, {
            "candidates": candidates, "metric": "concordance", "adapter_factory": SpyAdapter})
        expected_fit_sets = []
        for outer in splits:
            for inner in outer["inner"]:
                expected_fit_sets.extend([frozenset(inner["train"])] * len(candidates))
            expected_fit_sets.append(frozenset(outer["train"]))
        self.assertEqual(Counter(observed_fit_sets), Counter(expected_fit_sets))
        self.assertCountEqual([r["doc_id"] for r in result["paper_scores"]], list(outcomes))
        self.assertEqual(len(result["fold_results"]), 3)
        self.assertTrue(all(r["configuration"] == "positive" for r in result["selected_configurations"]))
        self.assertTrue(all(r["estimate"] == 1 for r in result["fold_results"]))

    def test_undefined_inner_metrics_do_not_select_a_best_configuration(self):
        papers = [self.paper(str(i), 1) for i in range(12)]
        outcomes = {str(i): i for i in range(12)}
        splits = evaluation.make_paper_splits(list(outcomes), n_splits=3, inner_splits=2, seed=4)
        result = evaluation.evaluate_aggregation_rules(papers, outcomes, splits, {
            "candidates": [{"name": "positive", "kind": "fixed", "weights": {"x": 1}},
                           {"name": "negative", "kind": "fixed", "weights": {"x": -1}}],
            "metric": "spearman"})
        self.assertEqual(result["paper_scores"], [])
        self.assertEqual(result["fold_results"], [])
        self.assertEqual(len(result["selected_configurations"]), 3)
        self.assertTrue(all(row["configuration"] is None and row["status"] == "undefined"
                            for row in result["selected_configurations"]))

    def test_family_ablation_refits_and_compares_identical_held_out_cohorts(self):
        papers = [{"doc_id": str(i), "features": {"x": i, "constant": 7}} for i in range(12)]
        outcomes = {str(i): 3 * i for i in range(12)}
        splits = evaluation.make_paper_splits(list(outcomes), n_splits=3, inner_splits=2, seed=8)
        result = evaluation.evaluate_feature_families(papers, outcomes, splits, {
            "base": {"kind": "ridge", "features": ["x", "constant"], "alpha": 0.1},
            "families": {"signal": ["x"], "constant": ["constant"]}, "metric": "concordance"})
        self.assertEqual(len(result["fold_results"]), 9)
        for row in result["fold_results"]:
            expected = 0.5 if row["configuration"] == "without_signal" else 1
            self.assertEqual(row["estimate"], expected)
        cohorts = {}
        for row in result["paper_scores"]:
            cohorts.setdefault(row["configuration"], set()).add((row["outer_fold"], row["doc_id"]))
        self.assertEqual(cohorts["full"], cohorts["without_signal"])
        self.assertEqual(cohorts["full"], cohorts["without_constant"])

    def test_binned_prediction_errors_use_evaluated_bins_and_preserve_continuous_residual(self):
        papers = [self.paper(str(i), i) for i in range(6)]
        outcomes = {str(i): i for i in range(6)}
        folds = [{"name": "heldout", "train": ["0", "1", "2"], "test": ["3", "4", "5"]}]
        result = evaluation.evaluate_aggregation_rules(papers, outcomes, folds, {
            "candidates": [{"name": "binned", "kind": "fixed", "weights": {"x": 1},
                            "bins": {"thresholds": [1, 2]}, "representation": "binned"}], "metric": "mae"})
        self.assertEqual(result["fold_results"][0]["estimate"], 2)
        for row in result["paper_scores"]:
            self.assertEqual(row["evaluated_score"], 2)
            self.assertEqual(row["residual"], 2 - row["outcome"])
            self.assertEqual(row["absolute_error"], abs(row["residual"]))
            self.assertEqual(row["squared_error"], row["residual"] ** 2)
            self.assertEqual(row["continuous_residual"], 0)


class EndToEndTests(unittest.TestCase):
    def test_editor_verdict_is_an_internal_ordinal_target_and_is_exported(self):
        bundle = synthetic_bundle(4)
        for paper, verdict in zip(bundle["papers"], ("REJECT", "RESUBMIT", "ACCEPT", None)):
            paper["editor_outcome"] = {
                "availability": "ASSESSED" if verdict else "FAILED", "verdict": verdict,
                "source_call_ids": ["editor-call"], "source_path": "editor.payload.verdict"}
        result = evaluation.run_evaluation(bundle, synthetic_outcomes(4), {
            "holdout": False,
            "features": ["insight_pre_mean"], "targets": ["crossref_wp_cites"],
            "metrics": ["spearman", "kendall_tau_b", "concordance", "rmse"], "synthetic": True})
        internal = result["internal"]
        self.assertEqual(internal["status"], "ok")
        self.assertEqual(len(internal["editor_verdicts"]), 4)
        self.assertIsNone(internal["editor_verdicts"][-1]["verdict"])
        self.assertEqual(internal["outcome_metadata"]["editor_verdict"]["n_assessed"], 3)
        self.assertEqual(internal["outcome_metadata"]["editor_verdict"]["encoding"],
                         {"REJECT": 0, "RESUBMIT": 1, "ACCEPT": 2})
        rows = internal["outcome_associations"]
        self.assertEqual({r["metric"] for r in rows}, {"spearman", "kendall_tau_b", "concordance"})
        self.assertTrue(all(r["target"] == "editor_verdict" and r["n_papers"] == 3 for r in rows))
        self.assertTrue(all(abs(r["estimate"] - 1) < 1e-12 for r in rows))
        self.assertFalse(any("editor" in name for name in result["metadata"]["selected_features"]))
        with tempfile.TemporaryDirectory() as directory:
            evaluation.export_analysis_tables(result, Path(directory) / "evaluation")
            with (Path(directory) / "evaluation" / "editor_verdicts.csv").open(newline="") as handle:
                exported = list(csv.DictReader(handle))
            self.assertEqual(len(exported), 4)
            self.assertEqual(exported[0]["verdict"], "REJECT")
            self.assertEqual(exported[-1]["availability"], "FAILED")

    def test_run_is_reproducible_synthetic_and_does_not_mutate_inputs(self):
        bundle, outcomes = synthetic_bundle(), synthetic_outcomes()
        spec = {"features": ["insight_pre_mean", "repair_scope_pre_mean"],
                "targets": ["crossref_wp_cites"],
                "metrics": ["spearman", "kendall_tau_b", "concordance"], "synthetic": True}
        before = deepcopy((bundle, outcomes, spec))
        first = evaluation.run_evaluation(bundle, outcomes, spec)
        second = evaluation.run_evaluation(bundle, outcomes, spec)
        self.assertEqual(first, second)
        self.assertEqual((bundle, outcomes, spec), before)
        self.assertTrue(first["metadata"]["synthetic"])
        self.assertTrue(first["external_validity"])
        self.assertTrue(first["verdict_diagnostics"])
        self.assertEqual(first["aggregation"], {})
        self.assertEqual(first["internal"]["status"], "skipped")
        self.assertEqual(first["internal"]["scores"], [])
        json.dumps(first, allow_nan=False)

    def test_empty_and_unknown_specifications_raise_useful_input_errors(self):
        bad_specs = [
            {"features": []}, {"metrics": []}, {"targets": []},
            {"features": ["unknown_feature"]}, {"unknown_setting": True},
            {"feature_directions": {"insight_pre_mean": "sideways"}},
            {"feature_directions": {"unknown_feature": "higher"}},
        ]
        bundle, outcomes = synthetic_bundle(), synthetic_outcomes()
        for spec in bad_specs:
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                evaluation.run_evaluation(bundle, outcomes, spec)

    def test_pre_post_metrics_use_common_papers_with_both_features_and_outcome(self):
        papers = synthetic_bundle()["papers"]
        papers[0]["features"]["insight_pre_mean"] = None
        papers[1]["features"]["insight_post_mean"] = None
        outcomes = synthetic_outcomes()
        outcomes[2]["crossref_wp_cites"] = ""
        result = evaluation.analyze_pre_post_change(papers, papers, outcomes, analysis_spec={
            "features": ["insight_pre_mean"], "targets": ["crossref_wp_cites"],
            "metrics": ["spearman", "concordance"]})
        expected = [f"synthetic-{index}" for index in range(3, 9)]
        self.assertEqual(len(result), 2)
        for row in result:
            self.assertEqual(row["paper_ids"], expected)
            self.assertEqual(row["n_papers"], 6)
            self.assertEqual(row["pre_diagnostics"]["n_papers"], 6)
            self.assertEqual(row["post_diagnostics"]["n_papers"], 6)

    def test_explicit_empty_paper_splits_do_not_silently_become_full_sample(self):
        with self.assertRaises(ValueError):
            evaluation.analyze_external_validity(synthetic_bundle()["papers"], synthetic_outcomes(), [], {
                "features": ["insight_pre_mean"], "targets": ["crossref_wp_cites"], "metrics": ["spearman"]})

    def test_all_missing_bootstrap_feature_reports_undefined_without_aborting_evaluation(self):
        bundle = synthetic_bundle()
        for paper in bundle["papers"]:
            paper["features"]["insight_pre_mean"] = None
        result = evaluation.run_evaluation(bundle, synthetic_outcomes(), {
            "synthetic": True, "features": ["insight_pre_mean"], "targets": ["crossref_wp_cites"],
            "metrics": ["spearman"], "bootstrap": {"features": ["insight_pre_mean"], "n_resamples": 20}})
        self.assertEqual(len(result["robustness"]), 1)
        undefined = result["robustness"][0]
        self.assertEqual(undefined["status"], "undefined")
        self.assertEqual(undefined["undefined_reason"], "no_complete_papers")
        self.assertEqual(undefined["n_papers"], 0)
        self.assertIsNone(undefined["estimate"])
        self.assertIsNone(undefined["lower"])
        self.assertIsNone(undefined["upper"])

    def test_optional_internal_generalist_and_mechanism_inputs_are_consumed_separately(self):
        bundle, outcomes = synthetic_bundle(6), synthetic_outcomes(6)
        values = {p["doc_id"]: p["features"]["insight_pre_mean"] for p in bundle["papers"]}
        mad_scores = {row["doc_id"]: float(row["crossref_wp_cites"]) for row in outcomes}
        result = evaluation.run_evaluation(bundle, outcomes, {
            "synthetic": True, "features": ["insight_pre_mean"], "targets": ["crossref_wp_cites"],
            "holdout": False,
            "metrics": ["concordance"], "internal_outcomes": {"editor_numeric": values},
            "internal_spec": {"metrics": ["spearman"]},
            "generalist": {"mad_scores": mad_scores, "generalist_scores": {k: -v for k, v in mad_scores.items()}},
            "mechanisms": {"features": ["insight_pre_mean"], "changes": values,
                           "gains": {k: float(index) for index, k in enumerate(values)}},
        })
        internal = result["internal"]["outcome_associations"]
        self.assertEqual(internal[0]["target"], "editor_numeric")
        self.assertEqual(internal[0]["metric"], "spearman")
        self.assertAlmostEqual(internal[0]["estimate"], 1)
        comparison = result["generalist"]["comparisons"][0]
        self.assertEqual(comparison["mad"]["estimate"], 1)
        self.assertEqual(comparison["generalist"]["estimate"], 0)
        self.assertEqual(comparison["mad_minus_generalist"], 1)
        self.assertAlmostEqual(result["mechanisms"]["associations"][0]["estimate"], 1)
        self.assertEqual(result["disagreement_moderation"]["status"], "ok")
        self.assertEqual(result["disagreement_moderation"]["groups"][0]["n_papers"], 6)

    def test_full_configured_pipeline_search_bins_ablation_bootstrap_and_internal_audit(self):
        bundle, outcomes = synthetic_bundle(12), synthetic_outcomes(12)
        selected = ["insight_pre_mean", "insight_post_mean", "repair_scope_pre_mean", "repair_scope_post_mean"]
        fixed = {"name": "fixed", "kind": "fixed", "weights": {"insight_pre_mean": 1},
                 "bins": {"thresholds": [1, 2]}}
        ridge = {"name": "ridge", "kind": "ridge", "features": ["insight_pre_mean", "repair_scope_pre_mean"],
                 "alpha": 1, "bins": {"quantiles": [0.33, 0.67]}}
        spec = {
            "synthetic": True, "features": selected, "targets": ["crossref_wp_cites"],
            "holdout": False, "derived_features": False,
            "metrics": ["spearman", "concordance"],
            "aggregation": {"candidates": [fixed, {**fixed, "name": "fixed_bins", "representation": "binned"}, ridge],
                            "metric": "concordance", "n_splits": 3, "inner_splits": 2, "seed": 7},
            "ablation": {"base": {"kind": "ridge", "features": ridge["features"], "alpha": 1},
                         "families": {"insight": ["insight_pre_mean"], "repair": ["repair_scope_pre_mean"]},
                         "metric": "concordance", "n_splits": 3, "inner_splits": 2, "seed": 7},
            "bootstrap": {"features": ["insight_pre_mean"], "metric": "spearman", "n_resamples": 30, "seed": 5},
            "score_audit": {"rules": [{"name": "audit", "kind": "fixed", "intercept": 0.5,
                                       "weights": {"insight_pre_mean": 1, "repair_scope_pre_mean": -0.5}}]},
            "persona_analysis": True,
            "domain_records": [{"doc_id": f"synthetic-{i}", "domain": "empirical" if i % 2 else "theoretical"}
                               for i in range(12)],
        }
        before = deepcopy((bundle, outcomes, spec))
        result = evaluation.run_evaluation(bundle, outcomes, spec)
        self.assertEqual((bundle, outcomes, spec), before)
        aggregation = result["aggregation"]["crossref_wp_cites"]
        self.assertEqual(len(aggregation["selection_records"]), 18)
        self.assertEqual(len(aggregation["fold_results"]), 3)
        self.assertTrue(all(row["status"] == "ok" for row in aggregation["selected_configurations"]))
        self.assertCountEqual([row["doc_id"] for row in aggregation["paper_scores"]],
                              [paper["doc_id"] for paper in bundle["papers"]])
        self.assertTrue(all(row["discrete_score"] is not None for row in aggregation["paper_scores"]))
        self.assertEqual(len(result["ablation"]["crossref_wp_cites"]["fold_results"]), 9)
        self.assertEqual(result["persona"]["domain_status"], "computed")
        self.assertTrue(result["persona"]["persona_correlations"])
        self.assertTrue(result["persona"]["within_paper_contrasts"])
        bootstrap = result["robustness"][0]
        self.assertEqual(bootstrap["unit"], "paper")
        self.assertEqual(bootstrap["n_papers"], 12)
        self.assertEqual(bootstrap["n_resamples"], 30)
        self.assertIn("fixed-score association", bootstrap["scope"])
        self.assertIn("no model refitting", bootstrap["scope"])
        audit = result["internal"]
        self.assertEqual(audit["status"], "ok")
        self.assertEqual(len(audit["scores"]), 12)
        self.assertIn("formula-driven", audit["interpretation"])
        expected_scores = {p["doc_id"]: 0.5 + p["features"]["insight_pre_mean"] -
                           0.5 * p["features"]["repair_scope_pre_mean"] for p in bundle["papers"]}
        for row in audit["scores"]:
            self.assertEqual(row["score"], expected_scores[row["doc_id"]])
        self.assertEqual(result, evaluation.run_evaluation(bundle, outcomes, spec))
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "evaluation"
            evaluation.export_analysis_tables(result, destination)
            self.assertEqual(json.loads((destination / "feature_dictionary.json").read_text())["columns"],
                             bundle["feature_dictionary"]["columns"])
            for path in destination.glob("*.csv"):
                with path.open(newline="", encoding="utf-8") as handle:
                    reader = csv.DictReader(handle)
                    self.assertIn("synthetic", reader.fieldnames)
                    self.assertTrue(all(row["synthetic"] == "True" for row in reader))
            saved = (destination / "results.json").read_bytes()
            with self.assertRaises(ValueError):
                evaluation.export_analysis_tables(result, destination)
            self.assertEqual((destination / "results.json").read_bytes(), saved)

    def test_cli_exports_json_and_csv_from_feature_export_without_raw_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feature_path, outcome_path, spec_path = root / "features.json", root / "outcomes.csv", root / "spec.json"
            output = root / "analysis"
            feature_path.write_text(json.dumps(synthetic_bundle()), encoding="utf-8")
            write_csv(outcome_path, synthetic_outcomes())
            spec_path.write_text(json.dumps({"features": ["insight_pre_mean"], "targets": ["crossref_wp_cites"],
                                            "metrics": ["spearman", "concordance"]}), encoding="utf-8")
            completed = subprocess.run([sys.executable, "-B", str(ANALYSIS / "evaluation_analysis.py"),
                                        "--features", str(feature_path), "--outcomes", str(outcome_path),
                                        "--spec", str(spec_path), "--output-dir", str(output), "--synthetic"],
                                       cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads((output / "results.json").read_text(encoding="utf-8"))
            self.assertTrue(result["metadata"]["synthetic"])
            self.assertTrue(list(output.glob("*.csv")))
            self.assertFalse("NaN" in (output / "results.json").read_text(encoding="utf-8"))


class DerivedChangeTests(unittest.TestCase):
    def test_matched_repair_change_and_maximum_level_reduction_use_same_reviewers(self):
        raw = record()
        raw["pre_assessments"] = [assessment(p, repair=r) for p, r in zip(PANEL, [4, 2, 0])]
        raw["post_assessments"] = [assessment(PANEL[0], repair=1), assessment(PANEL[1], repair=2),
                                   assessment(PANEL[2], status="FAILED")]
        paper = features.build_paper_features(raw, "paper")
        before = deepcopy(paper)
        result = evaluation.derive_change_features([paper])
        values = result["papers"][0]["features"]
        self.assertEqual(values["repair_scope_matched_change_mean"], -1.5)
        self.assertEqual(values["repair_scope_matched_improvement_mean"], 1.5)
        self.assertEqual(values["repair_scope_matched_improvement_max"], 3)
        self.assertEqual(values["repair_scope_matched_max_level_improvement"], 2)
        self.assertEqual(values["repair_scope_matched_min_level_improvement"], 1)
        self.assertEqual(values["repair_scope_matched_n_pairs"], 2)
        self.assertEqual(values["repair_scope_matched_coverage_rate"], 2 / 3)
        self.assertEqual(paper, before)
        self.assertEqual(set(result["rows"][0]) - {"doc_id"}, set(result["columns"]))

    def test_issue_change_excludes_retractions_and_unknowns_and_retains_creation_type(self):
        raw = record()
        original = issue(1, severity=9, phase="DEBATE")
        original["round_created"] = 1
        raw["issues"] = [original, issue(2, severity=10), issue(3, phase="POST", severity=5), issue(4)]
        raw["issue_revisions"] = [revision(original, severity=2, confidence=4, issue_type="rephrasal")]
        raw["issue_updates"] = [stance(1, "NARROW", version=2, confidence=4), stance(2, "RETRACT", sequence=21)]
        values = evaluation.derive_change_features([features.build_paper_features(raw, "paper")])["papers"][0]["features"]
        self.assertEqual(values["concern_matched_severity_change_mean"], -3.5)
        self.assertEqual(values["concern_matched_severity_improvement_sum"], 7)
        self.assertEqual(values["concern_matched_n_pairs"], 2)
        self.assertEqual(values["concern_matched_n_retracted"], 1)
        self.assertEqual(values["concern_matched_n_unknown"], 1)
        self.assertEqual(values["concern_matched_n_reclassified"], 1)
        self.assertEqual(values["concern_matched_n_new_post"], 1)
        self.assertIsNone(values["insight_weakness_matched_severity_change_mean"])

    def test_no_matched_reviewers_gives_null_change_not_zero(self):
        raw = record()
        raw["post_assessments"] = [assessment(p, status="FAILED") for p in PANEL]
        values = evaluation.derive_change_features([features.build_paper_features(raw, "paper")])["papers"][0]["features"]
        self.assertIsNone(values["insight_matched_change_sum"])
        self.assertEqual(values["insight_matched_n_pairs"], 0)


class CurrentRankingContractTests(unittest.TestCase):
    def test_current_names_id_alias_zeros_quartiles_and_legacy_target_aliases(self):
        rows = [{"id": "a", "wp_cites": "0", "cites": "7", "sjr_score": "1.5",
                 "sjr_global_rank": "9", "sjr_best_quartile": "Q1", "stern_finance_top_tier": "0"},
                {"id": "b", "wp_cites": "4", "cites": "", "sjr_score": "2",
                 "sjr_global_rank": "2", "sjr_best_quartile": "Q4", "stern_finance_top_tier": "1"}]
        before = deepcopy(rows)
        targets = evaluation.register_external_outcomes(rows)
        self.assertEqual(targets["wp_cites"]["values"], {"a": 0, "b": 4})
        self.assertEqual(targets["sjr_global_rank"]["values"], {"a": -9, "b": -2})
        self.assertEqual(targets["sjr_best_quartile"]["values"], {"a": 4, "b": 1})
        self.assertIsNone(targets["cites"]["values"]["b"])
        alias = evaluation.register_external_outcomes(rows, ["crossref_wp_cites"])
        self.assertEqual(alias["crossref_wp_cites"]["spec"]["column"], "wp_cites")
        self.assertEqual(rows, before)
        with self.assertRaises(ValueError):
            evaluation.register_external_outcomes([{**rows[0], "doc_id": "wrong"}])


class CleanHoldoutTests(unittest.TestCase):
    def inputs(self, n=30):
        bundle = synthetic_bundle(n)
        rows = []
        for index, paper in enumerate(bundle["papers"]):
            paper["editor_outcome"] = {"availability": "ASSESSED",
                "verdict": ("REJECT", "RESUBMIT", "ACCEPT")[index % 3],
                "source_call_ids": ["editor"], "source_path": "editor.payload.verdict"}
            rows.append({"id": paper["doc_id"], "wp_cites": str(10 * (index % 4) + index // 4),
                         "sjr_score": "1" if index == 0 else ""})
        spec = {"synthetic": True, "features": ["insight_pre_mean", "repair_scope_matched_improvement_mean"],
                "targets": ["wp_cites", "sjr_score"], "metrics": ["spearman", "kendall_tau_b", "concordance"],
                "aggregation": {"metric": "concordance", "candidates": [
                    {"name": "positive", "kind": "fixed", "weights": {"insight_pre_mean": 1}, "bins": {"quantiles": [0.33, 0.66]}},
                    {"name": "negative", "kind": "fixed", "weights": {"insight_pre_mean": -1}},
                    {"name": "binned", "kind": "fixed", "weights": {"insight_pre_mean": 1},
                     "bins": {"quantiles": [0.33, 0.66]}, "representation": "binned"}]}}
        return bundle, rows, spec

    def test_development_hides_holdout_and_compares_all_candidates_on_all_metrics(self):
        bundle, rows, spec = self.inputs()
        result = evaluation.run_evaluation(bundle, rows, spec)
        dev, test = (set(result["holdout"][key]) for key in ("development_ids", "holdout_ids"))
        self.assertEqual((len(dev), len(test)), (21, 9))
        self.assertFalse(dev & test)
        self.assertEqual(set(result["targets"]["wp_cites"]["values"]), dev)
        self.assertEqual({r["doc_id"] for r in result["derived_features"]}, dev)
        model = result["aggregation"]["wp_cites"]
        self.assertEqual(len(model["fold_results"]), 3 * 2 * 3)
        self.assertEqual(len(model["candidate_summary"]), 3 * 3)
        self.assertEqual(model["holdout_results"], [])
        self.assertFalse(model["metadata"]["holdout_accessed"])
        self.assertTrue(all(set(f["train"]) | set(f["test"]) <= dev for f in model["metadata"]["validation_splits"]))
        self.assertTrue(all(len(f["test"]) >= 10 for f in model["metadata"]["validation_splits"]))
        self.assertEqual(set(model["selected_configurations"][0]["fit_metadata"]["fit_ids"]), dev)
        self.assertEqual(result["aggregation"]["sjr_score"]["status"], "unavailable")
        self.assertTrue(result["internal"]["outcome_associations"])
        self.assertTrue(result["internal_aggregation"]["editor_verdict"]["candidate_summary"])
        self.assertTrue(all(r["partition"] == "development" for r in result["editor_external_validity"]))
        self.assertEqual({r["doc_id"] for r in model["paper_scores"]}, dev)

    def test_holdout_values_cannot_change_development_selection_or_diagnostics(self):
        bundle, rows, spec = self.inputs()
        first = evaluation.run_evaluation(bundle, rows, spec)
        holdout = set(first["holdout"]["holdout_ids"])
        for paper in bundle["papers"]:
            if paper["doc_id"] in holdout:
                paper["features"]["insight_pre_mean"] = 10000
                paper["editor_outcome"]["verdict"] = "REJECT"
        for row in rows:
            if row["id"] in holdout:
                row["wp_cites"] = "999999"
        second = evaluation.run_evaluation(bundle, rows, spec)
        for key in ("aggregation", "internal_aggregation", "external_validity", "editor_external_validity", "descriptive"):
            self.assertEqual(first[key], second[key], key)

    def test_final_requires_frozen_spec_and_evaluates_selected_rule_on_same_holdout(self):
        bundle, rows, spec = self.inputs()
        development = evaluation.run_evaluation(bundle, rows, spec)
        frozen = development["frozen_evaluation_spec"]
        final = evaluation.run_evaluation(bundle, rows, frozen)
        self.assertEqual(development["holdout"]["holdout_ids"], final["holdout"]["holdout_ids"])
        first, last = development["aggregation"]["wp_cites"], final["aggregation"]["wp_cites"]
        self.assertEqual(first["selected_configurations"], last["selected_configurations"])
        self.assertEqual(len(last["holdout_results"]), 3)
        self.assertTrue(all(r["n_papers"] == 9 for r in last["holdout_results"]))
        self.assertEqual({r["doc_id"] for r in last["paper_scores"] if r["partition"] == "holdout"},
                         set(final["holdout"]["holdout_ids"]))
        self.assertTrue(any(r["partition"] == "holdout" for r in final["editor_external_validity"]))
        altered = deepcopy(frozen)
        altered["aggregation"]["candidates"][0]["weights"]["insight_pre_mean"] = 7
        with self.assertRaises(ValueError):
            evaluation.run_evaluation(bundle, rows, altered)
        with self.assertRaises(ValueError):
            evaluation.run_evaluation(bundle, rows, {**spec, "holdout": {"stage": "final"}})
        changed = deepcopy(rows)
        development_id = development["holdout"]["development_ids"][0]
        next(r for r in changed if r["id"] == development_id)["wp_cites"] = "999999"
        with self.assertRaisesRegex(ValueError, "unchanged development data"):
            evaluation.run_evaluation(bundle, changed, frozen)
        changed = deepcopy(rows)
        holdout_id = development["holdout"]["holdout_ids"][0]
        next(r for r in changed if r["id"] == holdout_id)["wp_cites"] = "999999"
        updated_final = evaluation.run_evaluation(bundle, changed, frozen)
        self.assertEqual(first["selected_configurations"],
                         updated_final["aggregation"]["wp_cites"]["selected_configurations"])
        with tempfile.TemporaryDirectory() as directory:
            exported = evaluation.export_analysis_tables(development, directory)
            self.assertIn("frozen_evaluation_spec.json", exported["files"])
            self.assertIn("holdout_split.json", exported["files"])
            self.assertIn("candidate_comparisons.csv", exported["files"])
            self.assertIn("editor_external_validity.csv", exported["files"])
            self.assertEqual(json.loads((Path(directory) / "frozen_evaluation_spec.json").read_text()), frozen)

    def test_tied_target_and_missing_training_features_do_not_abort_other_candidates(self):
        bundle, rows, spec = self.inputs()
        spec["aggregation"]["validation_folds"] = 20
        spec["aggregation"]["candidates"].append({"name": "unobserved", "kind": "ridge",
            "features": ["insight_weakness_pre_severity_max"]})
        first = evaluation.run_evaluation(bundle, rows, spec)
        model = first["aggregation"]["wp_cites"]
        self.assertEqual(model["status"], "ok")
        self.assertEqual(model["metadata"]["validation_folds"], 4)
        self.assertTrue(any(r["configuration"] == "unobserved" and r["status"] == "invalid"
                            for r in model["selection_records"]))
        self.assertTrue(all(len(f["test"]) >= 5 for f in model["metadata"]["validation_splits"]))
        for row in rows:
            row["wp_cites"] = "1"
        tied = evaluation.run_evaluation(bundle, rows, spec)
        self.assertEqual(tied["aggregation"]["wp_cites"]["status"], "unavailable")
        self.assertEqual(tied["aggregation"]["wp_cites"]["reason"],
                         "no_candidate_has_defined_selection_metrics_on_all_folds")

    def test_example_spec_runs_current_csv_schema_through_development_and_final_cli(self):
        bundle, _, _ = self.inputs(50)
        rows = [{"id": p["doc_id"], "wp_cites": str(i * i), "cites": str(i * 3),
                 "sjr_score": str(i / 10), "sjr_global_rank": str(50 - i),
                 "sjr_economics_finance_rank": str(100 - i), "sjr_best_quartile": "Q" + str(1 + i % 4),
                 "gsm_h5_index": str(i), "gsm_h5_median": str(i * 2),
                 "tier_used_in_experiments": ("Tier1", "Tier2", "Tier3")[i % 3],
                 "stern_finance_top_tier": str(i % 2)} for i, p in enumerate(bundle["papers"])]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            features_path, outcomes_path = root / "features.json", root / "rankings.csv"
            features_path.write_text(json.dumps(bundle))
            write_csv(outcomes_path, rows)
            development_path, final_path = root / "development", root / "final"
            base = [sys.executable, "-B", str(ANALYSIS / "evaluation_analysis.py"),
                    "--features", str(features_path), "--outcomes", str(outcomes_path), "--synthetic"]
            for spec_path, output_path in ((ANALYSIS / "evaluation_spec.example.json", development_path),
                                           (development_path / "frozen_evaluation_spec.json", final_path)):
                process = subprocess.run(base + ["--spec", str(spec_path), "--output-dir", str(output_path)],
                                         capture_output=True, text=True)
                self.assertEqual(process.returncode, 0, process.stderr)
            development = json.loads((development_path / "results.json").read_text())
            final = json.loads((final_path / "results.json").read_text())
            self.assertEqual((len(development["holdout"]["development_ids"]),
                              len(development["holdout"]["holdout_ids"])), (35, 15))
            self.assertEqual(len(final["aggregation"]), 10)
            self.assertEqual(len(final["editor_external_validity"]), 60)
            for name, model in final["aggregation"].items():
                self.assertEqual(model["status"], "ok", name)
                self.assertEqual(len(model["holdout_results"]), 3, name)
                self.assertTrue(all(row["n_papers"] == 15 for row in model["holdout_results"]))


if __name__ == "__main__":
    unittest.main()
