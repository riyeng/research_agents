"""Feature construction checks using raw ledger dictionaries, without model calls."""
from copy import deepcopy
import csv
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "analysis" / "feature_selection.py"
SPEC = importlib.util.spec_from_file_location("exp12_candidate_features", MODULE_PATH)
features = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = features
SPEC.loader.exec_module(features)

PANEL = ["Econometrician", "Theorist", "Historian"]
DOMAINS = ["methodological", "empirical", "theoretical", "policy"]
DOC_ID = "feds-2019-016"


def judgment(level):
    if level == "ABSTAIN":
        return {"level": level, "reason": "Outside expertise."}
    return {"level": level, "rationale": "Manuscript evidence.", "evidence": [
        {"kind": "MANUSCRIPT", "locator": "Section 2", "support": "Stated result."}
    ]}


def assessment(persona, level="MEDIUM", repair=2, status="OK"):
    payload = {
        "novelty_by_domain": {domain: judgment(level) for domain in DOMAINS},
        "insight": judgment(level),
        "repair_scope": ({"status": "ABSTAIN", "reason": "Cannot assess."}
                         if repair == "ABSTAIN" else
                         {"status": "ASSESSED", "level": repair, "rationale": "Repair."}),
        "verdict": "RESUBMIT", "revision_path": "Clarify assumptions.",
        "assessment_rationale": "Review.", "originator_updates": [], "late_observations": [],
    }
    return {"persona": persona, "outcome": {
        "status": status, "payload": payload if status == "OK" else None,
        "reason": None if status == "OK" else "Unavailable.",
        "call_ids": [f"call-{persona}"],
    }}


def record():
    return {
        "run_id": "test-run", "rounds_requested": 3,
        "selection": {"status": "OK", "payload": {"selected_personas": PANEL[:]}},
        "pre_assessments": [assessment(p) for p in PANEL],
        "post_assessments": [assessment(p) for p in PANEL],
        "issues": [], "issue_revisions": [], "issue_updates": [],
        "issue_links": [], "arguments": [], "rounds": [],
    }


def issue(number, severity=6, confidence=8, phase="PRE", issue_type="substantive_flaw"):
    return {
        "issue_id": f"I{number:06d}", "originator": PANEL[0],
        "phase_created": phase, "round_created": 0 if phase == "PRE" else 4,
        "event_sequence": number, "source_call_id": f"issue-{number}",
        "issue_type": issue_type, "severity": severity, "initial_confidence": confidence,
        "technical_statement": "Central assumption unsupported.",
        "plain_language_statement": "The result requires an unverified assumption.",
        "severity_rationale": "Threatens the main result.", "novelty_domains": [],
        "evidence": [{"kind": "MANUSCRIPT", "locator": "Section 2", "support": "Assumption."}],
        "related_issue_ids": [],
    }


def stance(number, value="MAINTAIN", confidence=8, sequence=20,
           phase="POST", version=1):
    return {
        "issue_id": f"I{number:06d}", "persona": PANEL[0], "phase": phase,
        "round_number": 4 if phase == "POST" else 2,
        "stance": value, "confidence": None if value == "RETRACT" else confidence,
        "issue_version": version, "event_sequence": sequence,
        "source_call_id": f"update-{sequence}", "rationale": "Reconsidered concern.",
    }


def revision(original, *, severity=1, confidence=8, phase="DEBATE", sequence=10,
             issue_type=None):
    result = {k: deepcopy(v) for k, v in original.items()
              if k not in ("originator", "phase_created", "round_created")}
    result.update({
        "persona": original["originator"], "version": 2,
        "phase": phase, "round_number": 4 if phase == "POST" else 1,
        "severity": severity, "initial_confidence": confidence,
        "event_sequence": sequence, "source_call_id": "revision-call",
    })
    if issue_type is not None:
        result["issue_type"] = issue_type
    return result


def construct(raw):
    return features.build_paper_features(raw, DOC_ID)


class AssessmentFeatureTests(unittest.TestCase):
    def test_phase_summaries_preserve_ordinal_none_and_domain_separation(self):
        raw = record()
        raw["pre_assessments"] = [
            assessment(PANEL[0], "NONE", 0), assessment(PANEL[1], "MEDIUM", 2),
            assessment(PANEL[2], "HIGH", 4),
        ]
        raw["pre_assessments"][0]["outcome"]["payload"]["novelty_by_domain"]["policy"] = judgment("HIGH")
        values = construct(raw)["features"]
        self.assertAlmostEqual(values["novelty_empirical_pre_mean"], 5 / 3)
        self.assertEqual(values["novelty_empirical_pre_sum"], 5)
        self.assertEqual(values["novelty_empirical_pre_median"], 2)
        self.assertEqual(values["novelty_empirical_pre_min"], 0)
        self.assertEqual(values["novelty_empirical_pre_max"], 3)
        self.assertEqual(values["novelty_empirical_pre_count_none"], 1)
        self.assertAlmostEqual(values["novelty_empirical_pre_share_none"], 1 / 3)
        self.assertEqual(values["novelty_policy_pre_min"], 2)
        self.assertEqual(values["insight_pre_sum"], 5)
        self.assertEqual(values["repair_scope_pre_mean"], 2)
        self.assertEqual(values["repair_scope_post_min"], 2)
        self.assertEqual(values["novelty_empirical_post_mean"], 2)

    def test_abstention_failure_skip_and_missing_are_not_zero(self):
        raw = record()
        raw["pre_assessments"] = [
            assessment(PANEL[0], "ABSTAIN", "ABSTAIN"),
            assessment(PANEL[1], status="FAILED"),
        ]
        raw["post_assessments"] = [assessment(p, status="SKIPPED") for p in PANEL]
        values = construct(raw)["features"]
        self.assertIsNone(values["novelty_empirical_pre_mean"])
        self.assertIsNone(values["novelty_empirical_pre_sum"])
        self.assertIsNone(values["novelty_empirical_pre_share_none"])
        self.assertEqual(values["novelty_empirical_pre_n_valid"], 0)
        self.assertEqual(values["novelty_empirical_pre_n_abstain"], 1)
        self.assertEqual(values["novelty_empirical_pre_n_failed"], 1)
        self.assertEqual(values["novelty_empirical_pre_n_missing"], 1)
        self.assertEqual(values["novelty_empirical_post_n_skipped"], 3)
        self.assertEqual(values["repair_scope_pre_n_abstain"], 1)

    def test_repair_survival_is_matched_unweighted_and_excludes_unknown_post(self):
        raw = record()
        raw["pre_assessments"] = [assessment(p, repair=r) for p, r in zip(PANEL, [4, 1, 0])]
        raw["post_assessments"] = [assessment(p, repair=r) for p, r in zip(PANEL, [1, 0, 4])]
        values = construct(raw)["features"]
        self.assertEqual(values["repair_scope_survival_n_eligible"], 2)
        self.assertEqual(values["repair_scope_survival_n_observed"], 2)
        self.assertEqual(values["repair_scope_survival_n_survived"], 1)
        self.assertEqual(values["repair_scope_survival_rate"], 0.5)
        raw["post_assessments"][1] = assessment(PANEL[1], status="FAILED")
        values = construct(raw)["features"]
        self.assertEqual(values["repair_scope_survival_n_unknown"], 1)
        self.assertEqual(values["repair_scope_survival_n_observed"], 1)
        self.assertEqual(values["repair_scope_survival_rate"], 1)
        self.assertIsNone(values["repair_scope_survival_complete_rate"])

    def test_empty_repair_baseline_has_no_survival_rate(self):
        raw = record()
        raw["pre_assessments"] = [assessment(p, repair=0) for p in PANEL]
        values = construct(raw)["features"]
        self.assertEqual(values["repair_scope_survival_n_eligible"], 0)
        self.assertIsNone(values["repair_scope_survival_rate"])

    def test_feature_bundle_documents_features_and_does_not_mutate_ledger(self):
        raw = record()
        raw["issues"] = [issue(1)]
        raw["issue_updates"] = [stance(1)]
        before = deepcopy(raw)
        result = construct(raw)
        self.assertEqual(raw, before)
        self.assertEqual(result["doc_id"], DOC_ID)
        self.assertEqual(result["run_id"], "test-run")
        self.assertTrue(result["reviewer_features"])
        definitions = result["feature_dictionary"]["columns"]
        self.assertIn("novelty_empirical_pre_mean", definitions)
        self.assertFalse(set(result["features"]) - set(definitions), "Undocumented feature columns")
        self.assertFalse(set(definitions) - set(result["features"]), "Definitions without feature columns")
        novelty_row = next(row for row in result["reviewer_features"]
                           if row["persona"] == PANEL[0] and row["phase"] == "PRE"
                           and row["construct"] == "novelty_empirical")
        self.assertEqual(novelty_row["source_path"],
                         f"pre_assessments[persona={PANEL[0]}].outcome.payload.novelty_by_domain.empirical")
        self.assertTrue(result["provenance"])


class ConcernFeatureTests(unittest.TestCase):
    def test_weighted_survival_uses_original_weights_and_current_burden_uses_revision(self):
        raw = record()
        first, second = issue(1, severity=10, confidence=2), issue(2, severity=2, confidence=10)
        raw["issues"] = [first, second]
        raw["issue_revisions"] = [revision(first)]
        raw["issue_updates"] = [
            stance(1, "MAINTAIN", version=2, confidence=8),
            stance(2, "RETRACT", sequence=21),
        ]
        values = construct(raw)["features"]
        self.assertEqual(values["concern_pre_count"], 2)
        self.assertEqual(values["concern_pre_severity_max"], 10)
        self.assertEqual(values["concern_post_count"], 1)
        self.assertEqual(values["concern_post_severity_max"], 1)
        self.assertEqual(values["concern_survival_n_eligible"], 2)
        self.assertEqual(values["concern_survival_n_observed"], 2)
        self.assertEqual(values["concern_survival_n_survived"], 1)
        self.assertEqual(values["concern_survival_n_revised"], 1)
        self.assertEqual(values["concern_survival_n_narrowed"], 0)
        self.assertEqual(values["concern_survival_rate"], 0.5)
        self.assertAlmostEqual(values["concern_survival_severity_weighted_rate"], 10 / 12)
        self.assertAlmostEqual(values["concern_survival_confidence_weighted_rate"], 2 / 12)
        self.assertEqual(values["concern_survival_severity_confidence_weighted_rate"], 0.5)

    def test_new_post_concern_enters_final_burden_but_not_pre_survival_cohort(self):
        raw = record()
        raw["issues"] = [issue(1), issue(2, phase="POST", severity=10)]
        raw["issue_updates"] = [stance(1)]
        values = construct(raw)["features"]
        self.assertEqual(values["concern_pre_count"], 1)
        self.assertEqual(values["concern_post_count"], 2)
        self.assertEqual(values["concern_post_severity_max"], 10)
        self.assertEqual(values["concern_post_late_count"], 1)
        self.assertEqual(values["concern_survival_n_eligible"], 1)
        self.assertEqual(values["concern_survival_rate"], 1)

    def test_post_narrowing_survives_and_enters_final_burden_with_late_flag(self):
        raw = record()
        original = issue(1, severity=10)
        raw["issues"] = [original]
        raw["issue_revisions"] = [revision(original, phase="POST")]
        raw["issue_updates"] = [stance(1, "NARROW", version=2)]
        values = construct(raw)["features"]
        self.assertEqual(values["concern_survival_rate"], 1)
        self.assertEqual(values["concern_survival_n_narrowed"], 1)
        self.assertEqual(values["concern_survival_n_revised"], 1)
        self.assertEqual(values["concern_post_count"], 1)
        self.assertEqual(values["concern_post_severity_max"], 1)
        self.assertEqual(values["concern_post_late_count"], 1)

    def test_uncertain_missing_and_debate_maintain_are_not_resolutions(self):
        raw = record()
        raw["issues"] = [issue(i) for i in range(1, 5)]
        raw["post_assessments"][0] = assessment(PANEL[0], status="FAILED")
        raw["issue_updates"] = [
            stance(1, "RETRACT", phase="DEBATE"),
            stance(2, "UNCERTAIN", sequence=21),
            stance(3, "MAINTAIN", phase="DEBATE", sequence=22),
        ]
        values = construct(raw)["features"]
        self.assertEqual(values["concern_survival_n_eligible"], 4)
        self.assertEqual(values["concern_survival_n_observed"], 1)
        self.assertEqual(values["concern_survival_n_uncertain"], 1)
        self.assertEqual(values["concern_survival_n_unknown"], 2)
        self.assertEqual(values["concern_survival_rate"], 0)
        self.assertIsNone(values["concern_survival_complete_rate"])

    def test_failed_pre_sources_are_distinct_from_successfully_empty_issue_sets(self):
        raw = record()
        self.assertEqual(construct(raw)["features"]["concern_pre_count"], 0)
        raw["pre_assessments"] = [assessment(p, status="FAILED") for p in PANEL]
        raw["post_assessments"] = [assessment(p, status="SKIPPED") for p in PANEL]
        self.assertIsNone(construct(raw)["features"]["concern_pre_count"])

    def test_zero_confidence_is_a_valid_weight_but_zero_total_weight_is_undefined(self):
        raw = record()
        raw["issues"] = [issue(1, confidence=0)]
        raw["issue_updates"] = [stance(1, confidence=0)]
        values = construct(raw)["features"]
        self.assertEqual(values["concern_survival_rate"], 1)
        self.assertEqual(values["concern_survival_severity_weighted_rate"], 1)
        self.assertIsNone(values["concern_survival_confidence_weighted_rate"])
        self.assertIsNone(values["concern_survival_severity_confidence_weighted_rate"])

    def test_non_substantive_issues_are_not_counted_as_baseline_concerns(self):
        raw = record()
        raw["issues"] = [issue(1, issue_type="future_work")]
        raw["issue_updates"] = [stance(1)]
        values = construct(raw)["features"]
        self.assertEqual(values["concern_pre_count"], 0)
        self.assertEqual(values["concern_post_count"], 0)
        self.assertEqual(values["concern_survival_n_eligible"], 0)
        self.assertIsNone(values["concern_survival_rate"])

    def test_pre_cohort_survival_is_preserved_when_a_concern_is_reclassified(self):
        raw = record()
        original = issue(1)
        raw["issues"] = [original]
        raw["issue_revisions"] = [revision(original, issue_type="future_work")]
        raw["issue_updates"] = [stance(1, version=2)]
        values = construct(raw)["features"]
        self.assertEqual(values["concern_survival_rate"], 1)
        self.assertEqual(values["concern_post_count"], 0)

    def test_possible_duplicate_proposals_do_not_merge_independent_issue_ids(self):
        raw = record()
        raw["issues"] = [issue(1), issue(2)]
        raw["issue_updates"] = [stance(1), stance(2, sequence=21)]
        raw["issue_links"] = [{
            "issue_ids": ["I000001", "I000002"], "relationship": "POSSIBLE_DUPLICATE",
            "rationale": "May concern the same assumption.", "proposer": PANEL[1],
            "round_created": 1, "event_sequence": 10, "source_call_id": "link-call",
        }]
        values = construct(raw)["features"]
        self.assertEqual(values["concern_pre_count"], 2)
        self.assertEqual(values["concern_post_count"], 2)
        self.assertEqual(values["concern_survival_n_eligible"], 2)

    def test_tested_concerns_require_valid_opportunity_on_current_version(self):
        raw = record()
        original = issue(1)
        raw["issues"] = [original]
        raw["issue_revisions"] = [revision(original)]
        raw["issue_updates"] = [stance(1, version=2)]
        old_opportunity = {
            "issue_id": "I000001", "issue_version": 1, "persona": PANEL[1],
            "round_number": 1, "call_id": "old-version-call",
            "outcome": "VALID_RESPONSE", "response_argument_ids": [],
        }
        raw["rounds"] = [{"round_number": 1, "opportunities": [old_opportunity]}]
        self.assertEqual(construct(raw)["features"]["concern_post_tested_count"], 0)
        current_opportunity = {
            **old_opportunity, "issue_version": 2, "call_id": "current-version-call",
            "round_number": 2, "outcome": "FAILED_CALL",
        }
        raw["rounds"].append({"round_number": 2, "opportunities": [current_opportunity]})
        self.assertEqual(construct(raw)["features"]["concern_post_tested_count"], 0)
        current_opportunity["outcome"] = "VALID_RESPONSE"
        self.assertEqual(construct(raw)["features"]["concern_post_tested_count"], 1)


class IssueDiscoveryFeatureTests(unittest.TestCase):
    def test_insight_weakness_has_separate_burden_and_fixed_weight_survival(self):
        raw = record()
        first = issue(1, severity=9, confidence=2, issue_type="insight_weakness")
        second = issue(2, severity=3, confidence=8, issue_type="insight_weakness")
        raw["issues"] = [first, second, issue(3, severity=10)]
        raw["issue_revisions"] = [revision(first, severity=1, confidence=5)]
        raw["issue_updates"] = [stance(1, "NARROW", version=2, confidence=5),
                                stance(2, "RETRACT", sequence=21), stance(3, sequence=22)]
        values = construct(raw)["features"]
        self.assertEqual(values["insight_weakness_pre_count"], 2)
        self.assertEqual(values["insight_weakness_pre_severity_max"], 9)
        self.assertEqual(values["insight_weakness_post_count"], 1)
        self.assertEqual(values["insight_weakness_post_severity_max"], 1)
        self.assertEqual(values["insight_weakness_post_confidence_mean"], 5)
        self.assertEqual(values["insight_weakness_survival_rate"], 0.5)
        self.assertEqual(values["insight_weakness_survival_severity_weighted_rate"], 9 / 12)
        self.assertEqual(values["insight_weakness_survival_confidence_weighted_rate"], 2 / 10)
        self.assertEqual(values["insight_weakness_survival_severity_confidence_weighted_rate"], 18 / 42)
        self.assertEqual(values["concern_pre_count"], 1)
        self.assertEqual(values["concern_survival_rate"], 1)

    def test_reclassified_insight_survives_original_cohort_but_changes_post_family(self):
        raw = record()
        original = issue(1, issue_type="insight_weakness")
        raw["issues"] = [original]
        raw["issue_revisions"] = [revision(original, issue_type="substantive_flaw")]
        raw["issue_updates"] = [stance(1, version=2)]
        values = construct(raw)["features"]
        self.assertEqual(values["insight_weakness_survival_rate"], 1)
        self.assertEqual(values["insight_weakness_survival_n_reclassified"], 1)
        self.assertEqual(values["insight_weakness_post_count"], 0)
        self.assertEqual(values["concern_post_count"], 1)
        self.assertEqual(values["concern_survival_n_eligible"], 0)

    def test_debate_discovery_uses_creation_type_weights_and_distinct_ids(self):
        raw = record()
        pre = issue(1)
        first = issue(2, severity=9, confidence=2, phase="DEBATE", issue_type="insight_weakness")
        second = issue(3, severity=3, confidence=8, phase="DEBATE", issue_type="insight_weakness")
        other = issue(4, phase="DEBATE", issue_type="future_work")
        for new in (first, second, other):
            new["round_created"] = 1
        first["related_issue_ids"] = [pre["issue_id"]]
        raw["issues"] = [pre, first, second, other]
        raw["issue_revisions"] = [revision(first, severity=1, confidence=5, issue_type="substantive_flaw")]
        raw["issue_updates"] = [stance(1, "RETRACT"), stance(2, version=2, confidence=5, sequence=21),
                                stance(3, "RETRACT", sequence=22), stance(4, "UNCERTAIN", sequence=23)]
        result = construct(raw)
        values = result["features"]
        self.assertEqual(values["issue_debate_new_count"], 3)
        self.assertEqual(values["issue_debate_new_count_insight_weakness"], 2)
        self.assertEqual(values["issue_debate_new_count_future_work"], 1)
        self.assertEqual(values["insight_weakness_debate_new_count"], 2)
        self.assertEqual(values["insight_weakness_debate_new_severity_max"], 9)
        self.assertEqual(values["insight_weakness_debate_new_survival_rate"], 0.5)
        self.assertEqual(values["insight_weakness_debate_new_survival_severity_weighted_rate"], 9 / 12)
        self.assertEqual(values["insight_weakness_debate_new_survival_confidence_weighted_rate"], 2 / 10)
        self.assertEqual(values["insight_weakness_debate_new_survival_n_reclassified"], 1)
        self.assertEqual(values["concern_debate_new_count"], 0)
        self.assertEqual(values["concern_pre_count"], 1)
        self.assertEqual(values["concern_survival_n_eligible"], 1)
        self.assertEqual(values["concern_survival_rate"], 0)
        self.assertEqual(values["issue_debate_new_survival_n_uncertain"], 1)
        row = next(r for r in result["issue_features"] if r["issue_id"] == first["issue_id"])
        self.assertEqual(row["round_created"], 1)
        self.assertEqual(row["initial_related_issue_ids"], [pre["issue_id"]])
        self.assertEqual(row["creation_source_call_id"], first["source_call_id"])

    def test_final_burden_includes_late_and_new_post_but_excludes_unknown(self):
        raw = record()
        early = issue(1, phase="DEBATE", issue_type="insight_weakness")
        early["round_created"] = 1
        late = issue(2, phase="DEBATE", issue_type="insight_weakness")
        late["round_created"] = 3
        post = issue(3, phase="POST", issue_type="insight_weakness")
        raw["issues"] = [early, late, post]
        raw["issue_updates"] = [stance(1, phase="DEBATE"), stance(2, sequence=21)]
        values = construct(raw)["features"]
        self.assertEqual(values["insight_weakness_debate_new_survival_n_eligible"], 2)
        self.assertEqual(values["insight_weakness_debate_new_survival_n_unknown"], 1)
        self.assertEqual(values["insight_weakness_debate_new_survival_rate"], 1)
        self.assertEqual(values["insight_weakness_debate_new_survival_coverage_rate"], 0.5)
        self.assertIsNone(values["insight_weakness_debate_new_survival_complete_rate"])
        self.assertEqual(values["insight_weakness_post_count"], 2)
        self.assertEqual(values["insight_weakness_post_late_count"], 2)
        self.assertEqual(values["insight_weakness_post_new_count"], 1)
        self.assertEqual(values["issue_post_new_count_insight_weakness"], 1)
        self.assertEqual(values["insight_weakness_survival_n_eligible"], 0)

    def test_discovery_distinguishes_missing_sources_from_observed_empty(self):
        raw = record()
        values = construct(raw)["features"]
        self.assertIsNone(values["issue_debate_new_count"])
        self.assertEqual(values["issue_debate_new_n_source_calls_valid"], 0)
        self.assertEqual(values["issue_debate_new_n_source_calls_expected"], 0)
        raw["rounds"] = [{"round_number": 1, "opportunities": [], "responses": [
            {"persona": PANEL[0], "outcome": {"status": "FAILED", "call_ids": ["failed"]}},
            {"persona": PANEL[1], "outcome": {"status": "OK", "call_ids": ["ok"]}}]}]
        values = construct(raw)["features"]
        self.assertEqual(values["issue_debate_new_count"], 0)
        self.assertIsNone(values["issue_debate_new_severity_max"])
        self.assertEqual(values["issue_debate_new_n_source_calls_valid"], 1)
        self.assertIsNone(values["issue_debate_new_survival_rate"])
        self.assertEqual(values["insight_weakness_debate_new_count"], 0)
        raw["post_assessments"] = [assessment(p, status="FAILED") for p in PANEL]
        self.assertIsNone(construct(raw)["features"]["issue_post_new_count"])

    def test_post_observation_preserves_final_confidence_for_both_issue_families(self):
        for prefix, issue_type in features.ISSUE_FAMILIES:
            with self.subTest(issue_type=issue_type):
                raw = record()
                raw["issues"] = [issue(1, severity=9, confidence=8, phase="POST", issue_type=issue_type)]
                result = construct(raw)
                row = result["issue_features"][0]
                self.assertEqual(row["endpoint"], "NEW_POST")
                self.assertEqual(row["confidence"], 8)
                self.assertEqual(row["creation_confidence"], 8)
                self.assertTrue(row["late"])
                self.assertEqual(result["features"][prefix + "_post_count"], 1)
                self.assertEqual(result["features"][prefix + "_post_confidence_max"], 8)
                self.assertEqual(result["features"][prefix + "_post_late_confidence_max"], 8)
                self.assertEqual(result["features"][prefix + "_post_late_n_confidence_observed"], 1)
                self.assertEqual(result["features"][prefix + "_survival_n_eligible"], 0)

    def test_late_maintained_concerns_keep_current_severity_and_confidence(self):
        for prefix, issue_type in features.ISSUE_FAMILIES:
            raw = record()
            original = issue(1, severity=9, confidence=8, issue_type=issue_type)
            new = issue(2, severity=7, confidence=6, phase="DEBATE", issue_type=issue_type)
            new["round_created"] = raw["rounds_requested"]
            raw["issues"] = [original, new]
            raw["issue_revisions"] = [revision(original, phase="POST", severity=8, confidence=7)]
            raw["issue_updates"] = [stance(1, "NARROW", version=2, confidence=7),
                                    stance(2, "MAINTAIN", confidence=6, sequence=21)]
            result = construct(raw)
            self.assertEqual(result["features"][prefix + "_post_count"], 2)
            self.assertEqual(result["features"][prefix + "_post_severity_max"], 8)
            self.assertEqual(result["features"][prefix + "_post_confidence_sum"], 13)
            self.assertEqual(result["features"][prefix + "_post_late_count"], 2)
            self.assertEqual(result["features"][prefix + "_survival_n_eligible"], 1)
            self.assertEqual(result["features"][prefix + "_survival_rate"], 1)

    def test_source_call_denominators_follow_distinct_called_assessments(self):
        raw = record()
        raw["pre_assessments"][2] = assessment(PANEL[2], status="FAILED")
        raw["post_assessments"][2] = assessment(PANEL[2], status="SKIPPED")
        raw["rounds"] = [{"round_number": number, "opportunities": [], "responses": [
            {"persona": p, "outcome": {"status": "OK", "call_ids": [f"{number}-{p}-retry1", f"{number}-{p}-retry2"]}}
            for p in PANEL[:2]]} for number in range(1, 4)]
        values = construct(raw)["features"]
        self.assertEqual(values["issue_debate_new_n_source_calls_expected"], 6)
        self.assertEqual(values["issue_debate_new_n_source_calls_valid"], 6)
        self.assertEqual(values["issue_post_new_n_source_calls_expected"], 2)
        self.assertEqual(values["issue_post_new_n_source_calls_valid"], 2)
        raw["rounds"][0]["responses"][1]["outcome"]["status"] = "FAILED"
        # Retrieval failure never called the reviewer; skipped slots also do not count.
        raw["post_assessments"][1] = assessment(PANEL[1], status="FAILED")
        raw["post_assessments"][1]["outcome"]["call_ids"] = []
        values = construct(raw)["features"]
        self.assertEqual(values["issue_debate_new_n_source_calls_expected"], 6)
        self.assertEqual(values["issue_debate_new_n_source_calls_valid"], 5)
        self.assertEqual(values["issue_post_new_n_source_calls_expected"], 1)
        self.assertEqual(values["issue_post_new_n_source_calls_valid"], 1)


class EditorOutcomeTests(unittest.TestCase):
    def test_editor_verdict_is_preserved_separately_with_source_provenance(self):
        raw = record()
        raw["editor"] = {"status": "OK", "payload": {"verdict": "REJECT"},
                         "call_ids": ["editor-retry", "editor-ok"]}
        before = deepcopy(raw)
        result = construct(raw)
        self.assertEqual(result["editor_outcome"], {
            "availability": "ASSESSED", "verdict": "REJECT",
            "source_call_ids": ["editor-retry", "editor-ok"], "source_path": "editor.payload.verdict"})
        self.assertFalse(any("editor" in name for name in result["features"]))
        self.assertEqual(raw, before)

    def test_unavailable_editor_verdicts_remain_null_and_keep_distinct_statuses(self):
        raw = record()
        self.assertEqual(construct(raw)["editor_outcome"]["availability"], "MISSING")
        for status in ("FAILED", "SKIPPED"):
            raw["editor"] = {"status": status, "payload": None, "call_ids": []}
            editor = construct(raw)["editor_outcome"]
            self.assertEqual(editor["availability"], status)
            self.assertIsNone(editor["verdict"])

    def test_invalid_or_inconsistent_editor_verdicts_are_rejected(self):
        raw = record()
        for editor in ({"status": "OK", "payload": None},
                       {"status": "FAILED", "payload": {"verdict": "REJECT"}},
                       {"status": "OK", "payload": {"verdict": None}},
                       {"status": "OK", "payload": {"verdict": "MAYBE"}}):
            raw["editor"] = editor
            with self.subTest(editor=editor), self.assertRaises(ValueError):
                construct(raw)


class ManifestFeatureTests(unittest.TestCase):
    def write_manifest(self, root, rows):
        path = root / "manifest.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["doc_id", "result_path"])
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_relative_manifest_paths_and_candidate_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "result.json").write_text(json.dumps(record()), encoding="utf-8")
            path = self.write_manifest(root, [{"doc_id": DOC_ID, "result_path": "result.json"}])
            result = features.load_candidate_features(path)
            self.assertEqual(len(result["papers"]), 1)
            self.assertEqual(result["papers"][0]["doc_id"], DOC_ID)
            self.assertIn("repair_scope_pre_mean", result["feature_dictionary"]["columns"])

    def test_cli_exports_json_with_dictionary_and_source_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "result.json").write_text(json.dumps(record()), encoding="utf-8")
            manifest = self.write_manifest(root, [{"doc_id": DOC_ID, "result_path": "result.json"}])
            output = root / "features.json"
            command = [sys.executable, "-B", str(MODULE_PATH), str(manifest), "--output", str(output)]
            completed = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(output.read_text(encoding="utf-8"))
            paper = result["papers"][0]
            self.assertEqual(paper["doc_id"], DOC_ID)
            self.assertEqual(paper["features"]["repair_scope_pre_mean"], 2)
            self.assertEqual(Path(paper["provenance"]["source_path"]), (root / "result.json").resolve())
            self.assertEqual(len(paper["provenance"]["source_sha256"]), 64)
            definitions = result["feature_dictionary"]["columns"]
            self.assertFalse(set(paper["features"]) ^ set(definitions), "Feature/dictionary column mismatch")

    def test_duplicate_and_blank_doc_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "result.json").write_text(json.dumps(record()), encoding="utf-8")
            row = {"doc_id": DOC_ID, "result_path": "result.json"}
            for rows in ([row, row], [{"doc_id": " ", "result_path": "result.json"}]):
                with self.subTest(rows=rows):
                    path = self.write_manifest(root, rows)
                    with self.assertRaises(ValueError):
                        features.load_review_records(path)


if __name__ == "__main__":
    unittest.main()
