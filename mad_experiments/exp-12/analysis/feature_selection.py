"""Construct documented paper-level features from exp-12 raw review records.

Implemented: feature dictionary, reviewer-level source judgments, separate PRE/POST
summaries, unweighted repair persistence, and substantive-flaw/insight-weakness
survival with fixed baseline severity/confidence weights. New debate and POST
issues are tracked separately from original PRE cohorts. No external outcomes,
fitted models or final quality score enter construction. The editor verdict is
exported separately as an internal outcome. Verdicts are preserved for evaluation_analysis.py, which
owns verdict disagreement and subsequent offline evaluation.

Python API: build_paper_features(result, doc_id). Batch input is a CSV manifest with
doc_id,result_path columns; paths are relative to the manifest, not the working
directory. Explicit paper IDs are necessary because RunResult has no doc_id.
CLI: python analysis/feature_selection.py manifest.csv --output features.json

Ordinal coding NONE=0, LOW=1, MEDIUM=2, HIGH=3 is an exploratory representation,
not an interval-scale claim. Original categories and category shares are retained.
ABSTAIN, failed calls, skipped calls, absent records and substantive zero differ.
Sums and means are redundant with fixed reviewer count. The independent unit for
later splitting/resampling is the paper; feature importance is not causal importance.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import csv
import hashlib
import json
import math
from pathlib import Path
from statistics import mean, median


FEATURE_VERSION = "exp12-features-v3"
LEVELS = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
DOMAINS = ("methodological", "empirical", "theoretical", "policy")
CONSTRUCTS = tuple("novelty_" + domain for domain in DOMAINS) + ("insight", "repair_scope")
ISSUE_TYPES = ("substantive_flaw", "substantial_rephrasal", "rephrasal", "extension",
               "literature_dispute", "insight_weakness", "future_work")
ISSUE_FAMILIES = (("concern", "substantive_flaw"), ("insight_weakness", "insight_weakness"))
POLICIES = {
    "unit": "One explicit doc_id per run; no inference from titles or run folder names.",
    "encoding": LEVELS,
    "ordinal_statistics": "Numeric means, sums and interpolated medians assume category spacing; retain labels/shares.",
    "missingness": "Missing/abstained/failed/skipped is not zero. Empty observed counts/sums are zero; empty extrema/means are null.",
    "issue_families": "concern_* describes substantive_flaw only; insight_weakness_* describes insight_weakness separately. Other issue types remain in source rows and new-issue counts by creation type.",
    "duplicate_policy": "Unit is issue_id, not verified distinct concern. POSSIBLE_DUPLICATE/RELATED are not equivalence and are never merged. Do not use later links to construct PRE features.",
    "post_concerns": "Primary POST summaries include final MAINTAIN/NARROW and newly asserted POST observations (NEW_POST), regardless of creation/revision timing. Late is an overlapping timing diagnostic, not an exclusion. UNCERTAIN and unknown endpoints remain separate.",
    "repair_survival": "Among matched assessed reviewers with PRE>0, fraction with POST>0; unweighted. Unknown endpoints are excluded and coverage is reported.",
    "concern_survival": "Separate original PRE cohorts by issue type: MAINTAIN/NARROW survives (including narrowed/reclassified/late versions); terminal RETRACT does not. UNCERTAIN and missing final judgments are excluded with coverage, never treated as resolved.",
    "new_issues": "New IDs created in DEBATE and POST are separate discovery cohorts, classified and weighted at creation. Debate cohorts also have final-endpoint survival. Revisions are not new IDs; RELATED/POSSIBLE_DUPLICATE links do not establish fork lineage or change survival. PRE cohorts never acquire later IDs.",
    "survival_weights": "Fixed severity, initial_confidence, or severity*initial_confidence at cohort entry (PRE or debate creation). These are descriptive weights, not calibrated probabilities.",
    "denominators": "Rates condition on observed definitive endpoints. Zero denominators are null. Complete rates are null unless every initial endpoint is definitive.",
    "source_call_coverage": "Source-call denominators count distinct called reviewer assessments (persona within round for debate, persona for POST), including failed calls and excluding skipped/uncalled records. Retries do not add assessments.",
    "editor_outcome": "Categorical editor verdict and availability are exported outside candidate features. No numerical verdict encoding or predictor is constructed here.",
    "scope": "No feature fitting, learned weights, numeric paper-quality score, external-outcome linkage, deltas or verdict disagreement are implemented here.",
}


class _Features:
    def __init__(self):
        self.values = {}
        self.definitions = {}

    def add(self, name, value, definition, source, unit="paper", missingness="See feature policies."):
        self.values[name] = value
        self.definitions[name] = {
            "definition": definition, "source": source, "unit": unit,
            "missingness": missingness,
        }

    def summary(self, prefix, values, source, *, observed=True):
        numbers = list(values)
        stats = {
            "sum": sum(numbers) if observed else None,
            "mean": mean(numbers) if numbers else None,
            "median": median(numbers) if numbers else None,
            "min": min(numbers) if numbers else None,
            "max": max(numbers) if numbers else None,
        }
        for statistic, value in stats.items():
            self.add(prefix + "_" + statistic, value,
                     f"{statistic} of available {prefix} values; no averaging across PRE/POST.", source)


def _number(value, lower, upper, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    if not lower <= value <= upper:
        raise ValueError(f"{label} must be in [{lower}, {upper}]")
    return value


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def _assessment_map(record, phase, panel):
    records = record[phase.lower() + "_assessments"]
    result = {}
    for entry in records:
        persona = entry["persona"]
        if persona not in panel or persona in result:
            raise ValueError(f"Unexpected or duplicate {phase} persona: {persona}")
        outcome = entry["outcome"]
        if outcome["status"] not in ("OK", "FAILED", "SKIPPED"):
            raise ValueError(f"Invalid {phase} assessment status: {outcome['status']}")
        if (outcome["status"] == "OK") != (outcome.get("payload") is not None):
            raise ValueError(f"{phase} payload disagrees with availability for {persona}")
        result[persona] = outcome
    return result


def _reviewer_rows(record, doc_id, panel, assessments):
    rows = []
    for phase in ("PRE", "POST"):
        for persona in panel:
            outcome = assessments[phase].get(persona)
            for construct in CONSTRUCTS + ("verdict",):
                availability = outcome["status"] if outcome else "MISSING"
                raw = None
                value = None
                judgment = None
                if availability == "OK":
                    payload = outcome["payload"]
                    if construct.startswith("novelty_"):
                        judgment = payload["novelty_by_domain"][construct[len("novelty_"):]]
                    elif construct != "verdict":
                        judgment = payload[construct]
                    if construct == "verdict":
                        raw = payload["verdict"]
                        if raw not in ("ACCEPT", "RESUBMIT", "REJECT"):
                            raise ValueError(f"Unknown verdict: {raw}")
                        availability = "ASSESSED"
                    elif construct == "repair_scope":
                        raw = judgment.get("level")
                        availability = judgment["status"]
                        if availability == "ASSESSED":
                            value = _number(raw, 0, 4, "repair_scope")
                            if not isinstance(raw, int):
                                raise ValueError("Repair scope must be an integer")
                        elif availability != "ABSTAIN":
                            raise ValueError(f"Unknown repair status: {availability}")
                    else:
                        raw = judgment["level"]
                        availability = "ABSTAIN" if raw == "ABSTAIN" else "ASSESSED"
                        if availability == "ASSESSED":
                            if raw not in LEVELS:
                                raise ValueError(f"Unknown judgment level: {raw}")
                            value = LEVELS[raw]
                source_field = ("novelty_by_domain." + construct[len("novelty_"):]
                                if construct.startswith("novelty_") else construct)
                rows.append({
                    "doc_id": doc_id, "persona": persona, "phase": phase,
                    "construct": construct, "raw_value": raw, "value": value,
                    "availability": availability, "judgment": deepcopy(judgment),
                    "source_call_ids": list(outcome.get("call_ids", [])) if outcome else [],
                    "source_path": f"{phase.lower()}_assessments[persona={persona}].outcome.payload.{source_field}",
                })
    return rows


def _assessment_features(features, rows):
    for construct in CONSTRUCTS:
        for phase in ("PRE", "POST"):
            selected = [row for row in rows if row["construct"] == construct and row["phase"] == phase]
            values = [row["value"] for row in selected if row["availability"] == "ASSESSED"]
            prefix = construct + "_" + phase.lower()
            source = f"reviewer_features: construct={construct}, phase={phase}"
            features.summary(prefix, values, source, observed=bool(values))
            features.add(prefix + "_n_expected", len(selected), "Selected panel size.", source)
            for name, status in (("valid", "ASSESSED"), ("abstain", "ABSTAIN"),
                                 ("failed", "FAILED"), ("skipped", "SKIPPED"), ("missing", "MISSING")):
                features.add(prefix + "_n_" + name,
                             sum(row["availability"] == status for row in selected),
                             f"Number of {status} judgments, separately from substantive zero.", source)
            categories = list(LEVELS) if construct != "repair_scope" else list(range(5))
            for category in categories:
                count = sum(row["availability"] == "ASSESSED" and row["raw_value"] == category for row in selected)
                suffix = str(category).lower()
                features.add(prefix + "_count_" + suffix, count, f"Count of assessed category {category}.", source)
                features.add(prefix + "_share_" + suffix, _ratio(count, len(values)),
                             f"Share of assessed judgments in category {category}; abstentions excluded.", source)


def _repair_survival(features, rows):
    repair = {(row["persona"], row["phase"]): row for row in rows if row["construct"] == "repair_scope"}
    baseline = [row for row in repair.values() if row["phase"] == "PRE" and
                row["availability"] == "ASSESSED" and row["value"] > 0]
    matched = [row for row in baseline if repair[row["persona"], "POST"]["availability"] == "ASSESSED"]
    survived = sum(repair[row["persona"], "POST"]["value"] > 0 for row in matched)
    source = "Matched reviewer_features repair_scope, same persona; PRE>0 cohort."
    metrics = {
        "n_eligible": len(baseline), "n_observed": len(matched), "n_survived": survived,
        "n_unknown": len(baseline) - len(matched),
        "coverage_rate": _ratio(len(matched), len(baseline)),
        "rate": _ratio(survived, len(matched)),
        "complete_rate": _ratio(survived, len(baseline)) if len(matched) == len(baseline) else None,
    }
    for name, value in metrics.items():
        features.add("repair_scope_survival_" + name, value,
                     "Unweighted PRE>0 to POST>0 persistence: " + name + ". Rate uses assessed POST endpoints.", source)


def _issue_rows(record, doc_id, panel):
    issues = record["issues"]
    by_id = {issue["issue_id"]: issue for issue in issues}
    if len(by_id) != len(issues):
        raise ValueError("Duplicate issue_id in canonical issues")
    for key in ("issue_revisions", "issue_updates"):
        if any(event["issue_id"] not in by_id for event in record[key]):
            raise ValueError(f"Unknown issue_id in {key}")
    opportunities = [opportunity for turn in record.get("rounds", []) for opportunity in turn["opportunities"]]
    rows = []
    for original in issues:
        issue_id = original["issue_id"]
        if original["originator"] not in panel:
            raise ValueError(f"Unknown issue originator for {issue_id}")
        revisions = sorted((r for r in record["issue_revisions"] if r["issue_id"] == issue_id),
                           key=lambda r: r["event_sequence"])
        updates = sorted((u for u in record["issue_updates"] if u["issue_id"] == issue_id),
                         key=lambda u: u["event_sequence"])
        latest = updates[-1] if updates else None
        current = {**original, **(revisions[-1] if revisions else {})}
        version = current.get("version", 1)
        if latest and latest["issue_version"] != version:
            raise ValueError(f"Latest stance does not address current version of {issue_id}")
        for phase in ("PRE", "POST"):
            if phase == "PRE" and original["phase_created"] != "PRE":
                continue
            issue = original if phase == "PRE" else current
            severity = _number(issue["severity"], 1, 10, "issue severity")
            confidence = _number(issue["initial_confidence"], 0, 10, "issue confidence")
            endpoint = "INITIAL"
            if phase == "POST":
                if latest and latest["stance"] == "RETRACT":
                    endpoint = "RETRACTED"
                    confidence = None
                elif latest and latest["phase"] == "POST":
                    endpoint = latest["stance"]
                    if endpoint not in ("MAINTAIN", "NARROW", "UNCERTAIN"):
                        raise ValueError(f"Unknown final stance: {endpoint}")
                    confidence = _number(latest["confidence"], 0, 10, "POST issue confidence")
                elif original["phase_created"] == "POST":
                    endpoint = "NEW_POST"
                    # Creation itself is a POST judgment; no second update is required.
                else:
                    endpoint = "UNKNOWN"
                    confidence = None  # A PRE or debate judgment is not a final POST judgment.
            proposition_phase = issue.get("phase", original["phase_created"])
            proposition_round = issue.get("round_number", original["round_created"])
            late = phase == "POST" and (proposition_phase == "POST" or
                    (proposition_phase == "DEBATE" and proposition_round == record["rounds_requested"]))
            valid_opportunities = sum(o["issue_id"] == issue_id and o["issue_version"] == version and
                                      o["outcome"] == "VALID_RESPONSE" for o in opportunities) if phase == "POST" else 0
            rows.append({
                "doc_id": doc_id, "phase": phase, "issue_id": issue_id,
                "originator": original["originator"], "phase_created": original["phase_created"],
                "round_created": original["round_created"],
                "creation_issue_type": original["issue_type"],
                "creation_severity": _number(original["severity"], 1, 10, "creation severity"),
                "creation_confidence": _number(original["initial_confidence"], 0, 10, "creation confidence"),
                "creation_source_call_id": original["source_call_id"],
                "initial_related_issue_ids": list(original.get("related_issue_ids", [])),
                "related_issue_ids": list(issue.get("related_issue_ids", [])),
                "issue_type": issue["issue_type"], "version": 1 if phase == "PRE" else version,
                "severity": severity, "confidence": confidence,
                "initial_confidence": issue["initial_confidence"], "endpoint": endpoint,
                "late": late, "response_opportunity_count": valid_opportunities,
                "was_narrowed": phase == "POST" and any(u["stance"] == "NARROW" for u in updates),
                "source_call_id": issue["source_call_id"],
                "stance_source_call_id": latest["source_call_id"] if phase == "POST" and latest else None,
                "source_path": "issues" if phase == "PRE" or not revisions else "issue_revisions",
            })
    return rows


def _concern_features(features, record, issue_rows, assessments, prefix="concern", issue_type="substantive_flaw"):
    pre = [row for row in issue_rows if row["phase"] == "PRE" and row["issue_type"] == issue_type]
    post = [row for row in issue_rows if row["phase"] == "POST" and row["issue_type"] == issue_type]
    post_available = any(outcome["status"] == "OK" for outcome in assessments["POST"].values())
    pre_available = any(outcome["status"] == "OK" for outcome in assessments["PRE"].values())
    groups = {
        prefix + "_pre": (pre, pre_available),
        prefix + "_post": ([r for r in post if r["endpoint"] in ("MAINTAIN", "NARROW", "NEW_POST")], post_available),
        prefix + "_post_late": ([r for r in post if r["late"] and r["endpoint"] != "RETRACTED"], pre_available or post_available),
        prefix + "_post_uncertain": ([r for r in post if r["endpoint"] == "UNCERTAIN"], post_available),
        prefix + "_post_unknown": ([r for r in post if r["endpoint"] == "UNKNOWN" and not r["late"]], pre_available),
    }
    for group, (selected, observed) in groups.items():
        source = "issue_features: " + group + "; units are issue IDs, not adjudicated unique concerns."
        features.add(group + "_count", len(selected) if observed else None,
                     "Number of issue IDs in this cohort; see POST/duplicate policies.", source)
        features.summary(group + "_severity", (r["severity"] for r in selected), source, observed=observed)
        confidences = [r["confidence"] for r in selected if r["confidence"] is not None]
        features.summary(group + "_confidence", confidences, source,
                         observed=observed and (bool(confidences) or not selected))
        features.add(group + "_n_confidence_observed", len(confidences) if observed else None,
                     "Number of source judgments with confidence; unknown POST confidence is not backfilled.", source)
    for phase in ("PRE", "POST"):
        valid = sum(outcome["status"] == "OK" for outcome in assessments[phase].values())
        features.add(prefix + "_" + phase.lower() + "_n_reviewers_valid", valid,
                     "Successful source reviewer calls; partial summaries describe only observed records.", phase.lower() + "_assessments")
    source = "POST issue_features current version, separately from proof of correctness."
    features.add(prefix + "_post_retracted_count", sum(r["endpoint"] == "RETRACTED" for r in post) if pre_available or post_available else None,
                 f"Current {issue_type} IDs explicitly withdrawn, including terminal debate retractions.", source)
    features.add(prefix + "_post_tested_count", sum(r["endpoint"] in ("MAINTAIN", "NARROW") and not r["late"] and
                 r["response_opportunity_count"] > 0 for r in post) if post_available else None,
                 f"Maintained nonlate {issue_type} IDs with a valid peer opportunity on the CURRENT version; opportunity is not validation.", source)
    duplicate_links = [link for link in record["issue_links"] if link["relationship"] == "POSSIBLE_DUPLICATE"]
    if prefix == "concern":
        features.add("concern_post_possible_duplicate_link_count", len(duplicate_links),
                     "Proposed duplicate links across all issue types; never a license to merge IDs or alter PRE values.", "issue_links")


def _concern_survival(features, issue_rows, prefix="concern", issue_type="substantive_flaw",
                      creation_phase="PRE"):
    if creation_phase == "PRE":
        baseline = [r for r in issue_rows if r["phase"] == "PRE" and
                    (issue_type is None or r["issue_type"] == issue_type)]
    else:
        baseline = [{**r, "issue_type": r["creation_issue_type"],
                     "severity": r["creation_severity"], "confidence": r["creation_confidence"]}
                    for r in issue_rows if r["phase"] == "POST" and r["phase_created"] == creation_phase
                    and (issue_type is None or r["creation_issue_type"] == issue_type)]
    final = {r["issue_id"]: r for r in issue_rows if r["phase"] == "POST"}
    observed = [r for r in baseline if final[r["issue_id"]]["endpoint"] in ("MAINTAIN", "NARROW", "RETRACTED")]
    survivors = [r for r in observed if final[r["issue_id"]]["endpoint"] in ("MAINTAIN", "NARROW")]
    source = f"{creation_phase} {issue_type or 'all-type'} issue_id cohort joined to final issue_features; fixed creation weights."
    metrics = {
        "n_eligible": len(baseline), "n_observed": len(observed), "n_survived": len(survivors),
        "n_uncertain": sum(final[r["issue_id"]]["endpoint"] == "UNCERTAIN" for r in baseline),
        "n_unknown": sum(final[r["issue_id"]]["endpoint"] == "UNKNOWN" for r in baseline),
        "n_narrowed": sum(final[r["issue_id"]]["was_narrowed"] for r in survivors),
        "n_revised": sum(final[r["issue_id"]]["version"] > 1 for r in survivors),
        "n_reclassified": sum(final[r["issue_id"]]["issue_type"] != r["issue_type"] for r in survivors),
        "coverage_rate": _ratio(len(observed), len(baseline)),
        "rate": _ratio(len(survivors), len(observed)),
        "complete_rate": _ratio(len(survivors), len(baseline)) if len(observed) == len(baseline) else None,
    }
    for name, value in metrics.items():
        features.add(prefix + "_survival_" + name, value,
                     f"Maintained/narrowed {creation_phase}-issue cohort survival: " + name + ". UNCERTAIN/missing excluded; see coverage.", source)
    weights = {
        "severity": lambda row: row["severity"],
        "confidence": lambda row: row["confidence"],
        "severity_confidence": lambda row: row["severity"] * row["confidence"],
    }
    for label, weight in weights.items():
        total = sum(weight(r) for r in baseline)
        denominator = sum(weight(r) for r in observed)
        numerator = sum(weight(r) for r in survivors)
        for suffix, value, definition in (
            ("weighted_rate", _ratio(numerator, denominator), "sum(initial weights of survivors) / sum(initial weights with definitive endpoints)"),
            ("eligible_weight", total, "sum(initial weights over the whole creation cohort)"),
            ("observed_weight", denominator, "sum(initial weights with definitive endpoints)"),
            ("survived_weight", numerator, "sum(initial weights of survivors)"),
            ("weight_coverage_rate", _ratio(denominator, total), "observed initial weight / all initial weight"),
        ):
            features.add(prefix + "_survival_" + label + "_" + suffix, value,
                         definition + "; weights=" + label + "; zero denominator gives null.", source)


def _source_call_counts(record, assessments, phase):
    """Count called assessments, once per reviewer/round rather than per retry."""
    entries = ([(turn["round_number"], response["persona"], response["outcome"])
                for turn in record.get("rounds", []) for response in turn.get("responses", [])]
               if phase == "DEBATE" else
               [(None, persona, outcome) for persona, outcome in assessments["POST"].items()])
    called, valid = set(), set()
    for number, persona, outcome in entries:
        if outcome["status"] == "SKIPPED":
            continue
        key = (number, persona)
        if outcome["status"] == "OK" or outcome.get("call_ids"):
            called.add(key)
        if outcome["status"] == "OK":
            valid.add(key)
    return len(valid), len(called)


def _new_issue_features(features, record, issue_rows, assessments):
    """Track discovery by creation phase/type without interpreting links as forks."""
    final = [r for r in issue_rows if r["phase"] == "POST"]
    for phase in ("DEBATE", "POST"):
        valid, called = _source_call_counts(record, assessments, phase)
        cohort = [r for r in final if r["phase_created"] == phase]
        observed = bool(valid or cohort) or (phase == "DEBATE" and record["rounds_requested"] == 0)
        for family, issue_type in (("issue", None),) + ISSUE_FAMILIES:
            selected = [r for r in cohort if issue_type is None or r["creation_issue_type"] == issue_type]
            prefix = family + "_" + phase.lower() + "_new"
            source = f"issue_features: phase_created={phase}, creation_issue_type={issue_type or 'all'}; original creation judgments."
            features.add(prefix + "_count", len(selected) if observed else None,
                         "Newly issued IDs, excluding revisions; partial source coverage does not establish absence.", source)
            features.add(prefix + "_n_source_calls_valid", valid, "Successful source calls in this creation phase.", source)
            features.add(prefix + "_n_source_calls_expected", called,
                         "Denominator: distinct reviewer assessments actually called in this phase, including failed calls; excludes skipped/uncalled records and counts retries once.", source)
            for field in ("severity", "confidence"):
                features.summary(prefix + "_" + field, (r["creation_" + field] for r in selected),
                                 source, observed=observed)
            if issue_type is None:
                for category in ISSUE_TYPES:
                    features.add(prefix + "_count_" + category,
                                 sum(r["creation_issue_type"] == category for r in selected) if observed else None,
                                 f"New IDs classified as {category} at creation, irrespective of later reclassification.", source)
            if phase == "DEBATE":
                _concern_survival(features, issue_rows, prefix, issue_type, creation_phase=phase)


def _editor_outcome(record):
    """Preserve the final verdict as an outcome, never as a candidate predictor."""
    outcome = record.get("editor")
    status = outcome["status"] if outcome is not None else "MISSING"
    if status not in ("OK", "FAILED", "SKIPPED", "MISSING"):
        raise ValueError(f"Invalid editor status: {status}")
    payload = outcome.get("payload") if outcome is not None else None
    if (status == "OK") != (payload is not None):
        raise ValueError("Editor payload disagrees with availability")
    verdict = payload["verdict"] if payload is not None else None
    if status == "OK" and verdict not in ("ACCEPT", "RESUBMIT", "REJECT"):
        raise ValueError(f"Unknown editor verdict: {verdict}")
    return {"availability": "ASSESSED" if status == "OK" else status,
            "verdict": verdict,
            "source_call_ids": list(outcome.get("call_ids", [])) if outcome is not None else [],
            "source_path": "editor.payload.verdict"}


def build_paper_features(record, doc_id, *, source_path=None, source_sha256=None):
    """Return a detached, JSON-serializable feature record with per-field definitions.

    Accept an exp-12 RunResult mapping, never mutate it, and never infer paper IDs.
    The canonical issue arrays are required; missing arrays are not empty evidence.
    """
    if not isinstance(doc_id, str) or not doc_id.strip():
        raise ValueError("A nonempty explicit doc_id is required")
    for key in ("selection", "pre_assessments", "post_assessments", "issues",
                "issue_revisions", "issue_updates", "issue_links", "rounds_requested"):
        if key not in record:
            raise ValueError(f"Missing canonical RunResult field: {key}")
    selection = record["selection"].get("payload")
    panel = selection["selected_personas"] if selection else []
    if len(panel) != 3 or len(set(panel)) != 3:
        raise ValueError("The source record must identify three distinct selected personas")
    assessments = {phase: _assessment_map(record, phase, panel) for phase in ("PRE", "POST")}
    reviewer_rows = _reviewer_rows(record, doc_id, panel, assessments)
    issue_rows = _issue_rows(record, doc_id, panel)
    features = _Features()
    _assessment_features(features, reviewer_rows)
    _repair_survival(features, reviewer_rows)
    for prefix, issue_type in ISSUE_FAMILIES:
        _concern_features(features, record, issue_rows, assessments, prefix, issue_type)
        _concern_survival(features, issue_rows, prefix, issue_type)
    _new_issue_features(features, record, issue_rows, assessments)
    return {
        "doc_id": doc_id, "run_id": record.get("run_id"), "features": features.values,
        "reviewer_features": reviewer_rows, "issue_features": issue_rows,
        "editor_outcome": _editor_outcome(record),
        "feature_dictionary": {"version": FEATURE_VERSION, "policies": deepcopy(POLICIES),
                               "columns": features.definitions},
        "provenance": {"source_path": str(source_path) if source_path is not None else None,
                       "source_sha256": source_sha256,
                       **{key: record.get(key) for key in ("architecture_version", "schema_version", "prompt_version",
                          "code_sha256", "prompt_sha256", "manuscript_sha256", "status")}},
    }


def load_review_records(source):
    """Load explicit paper/run linkage from CSV doc_id,result_path; reject duplicates."""
    manifest = Path(source).resolve()
    records = []
    seen = set()
    with manifest.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not {"doc_id", "result_path"} <= set(reader.fieldnames or []):
            raise ValueError("Manifest must have doc_id,result_path columns")
        for entry in reader:
            doc_id = (entry["doc_id"] or "").strip()
            result_path = (entry["result_path"] or "").strip()
            if not doc_id or doc_id in seen or not result_path:
                raise ValueError(f"Empty/duplicate paper ID or empty result path: {doc_id!r}")
            seen.add(doc_id)
            path = (manifest.parent / result_path).resolve()
            raw = path.read_bytes()
            records.append({"doc_id": doc_id, "record": json.loads(raw),
                            "source_path": str(path), "source_sha256": hashlib.sha256(raw).hexdigest()})
    if not records:
        raise ValueError("Manifest contains no review records")
    return records


def load_candidate_features(source, feature_spec=None):
    """Construct a versioned bundle from a manifest; no fitted feature selection.

    An optional feature_spec is a previously exported dictionary and must match
    exactly, preventing accidental changes to frozen definitions.
    """
    papers = [build_paper_features(entry["record"], entry["doc_id"],
                                  source_path=entry["source_path"], source_sha256=entry["source_sha256"])
              for entry in load_review_records(source)]
    dictionary = papers[0]["feature_dictionary"]
    if any(paper["feature_dictionary"] != dictionary for paper in papers):
        raise ValueError("Feature definitions differ across source records")
    if feature_spec is not None and feature_spec != dictionary:
        raise ValueError("Requested frozen feature dictionary does not match this version")
    for paper in papers:
        del paper["feature_dictionary"]
    return {"feature_dictionary": dictionary, "papers": papers}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="CSV containing doc_id,result_path")
    parser.add_argument("--output", type=Path, help="Feature/provenance/dictionary JSON; default stdout")
    args = parser.parse_args(argv)
    try:
        bundle = load_candidate_features(args.manifest)
        serialized = json.dumps(bundle, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.write_text(serialized, encoding="utf-8")
        else:
            print(serialized, end="")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"Feature construction error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
