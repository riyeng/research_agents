"""Export the proposed primary, secondary and coverage subset of exp-12 features.

Reuse feature_selection.py for source validation and all existing definitions.
Add pooled novelty summaries, the confident-concern POST-minus-PRE difference,
POST-minus-PRE changes in reviewer range/SD, and DEBATE-phase revision/retraction
counts. Retain every original inventory feature numbered 604--637 in
exp12-features-v7.

CLI (from exp-12):
  python analysis/feature_selection_subset.py manifest.csv --output subset.json
  python analysis/feature_selection_subset.py --inventory-only --inventory subset.md

The JSON bundle retains the base version/policies for evaluation compatibility;
feature_dictionary.subset records this selection's version, groups and additions.
The proposed groups do not automatically set an evaluator's primary hypothesis.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
from statistics import mean

try:
    from . import feature_selection as base
except ImportError:
    import feature_selection as base


SUBSET_VERSION = "exp12-analysis-subset-v3"
BASE_FEATURE_VERSION = "exp12-features-v7"
DEFAULT_CONFIDENCE_THRESHOLD = base.DEFAULT_CONFIDENCE_THRESHOLD
PHASES = ("pre", "post")

# Use names, rather than inventory positions, so additions cannot silently shift
# which columns are selected. Order below also controls the numbered inventory.
FEATURE_GROUPS = {
    "primary_review_summaries": tuple(
        f"novelty_pooled_{phase}_{stat}"
        for phase in PHASES for stat in ("sum", "mean", "count_low", "count_high")
    ) + tuple(
        f"{construct}_{phase}_{stat}"
        for construct, stats in (("insight", ("mean", "count_low", "count_high")),
                                 ("repair_scope", ("mean", "count_0", "count_4")))
        for phase in PHASES for stat in stats
    ),
    "primary_repair_survival": tuple(
        "repair_scope_survival_" + stat
        for stat in ("n_eligible", "n_observed", "n_survived", "n_unknown",
                     "coverage_rate", "rate", "complete_rate")
    ),
    "primary_concerns": tuple(
        f"concern_{phase}_{stat}"
        for phase in PHASES
        for stat in ("count", "severity_confidence_weighted_count",
                     "severity_confidence_weighted_mean", "n_confidence_above_threshold")
    ) + ("concern_n_confidence_above_threshold_post_minus_pre",),
    "secondary_insight_weaknesses": tuple(
        f"insight_weakness_{phase}_{stat}"
        for phase in PHASES
        for stat in ("count", "severity_confidence_weighted_count",
                     "severity_confidence_weighted_mean")
    ),
    "secondary_discovery": tuple(
        f"{family}_{phase}_new_{stat}"
        for phase in ("debate", "post")
        for family in ("issue", "concern", "insight_weakness")
        for stat in ("count", "severity_mean", "confidence_mean")
    ),
    "secondary_retractions": ("concern_post_retracted_count",
                              "insight_weakness_post_retracted_count"),
    "secondary_reviewer_spread": tuple(
        f"{construct}_{phase}_{stat}"
        for construct in base.CONSTRUCTS for phase in PHASES for stat in ("range", "sd")
    ) + tuple(
        f"{construct}_{stat}_post_minus_pre"
        for construct in base.CONSTRUCTS for stat in ("range", "sd")
    ),
    "secondary_debate_activity": (
        "debate_challenge_count", "debate_defense_count", "debate_concession_count",
        "debate_question_count", "debate_argument_count", "debate_final_retract_count",
        "debate_final_narrow_count", "debate_final_maintain_count",
        "debate_final_uncertain_count", "debate_revision_count",
        "debate_phase_revision_count", "debate_phase_revised_issue_count",
        "debate_phase_retraction_count", "debate_phase_retracted_issue_count",
    ),
    "coverage": tuple(f"novelty_pooled_{phase}_n_valid" for phase in PHASES) + tuple(
        f"{construct}_{phase}_n_valid"
        for construct in ("insight", "repair_scope") for phase in PHASES
    ) + tuple(
        f"{family}_{phase}_n_reviewers_valid"
        for family in ("concern", "insight_weakness") for phase in PHASES
    ) + tuple(
        f"{family}_post_{endpoint}_count"
        for family in ("concern", "insight_weakness") for endpoint in ("uncertain", "unknown", "late")
    ) + tuple(
        f"{family}_{phase}_new_n_source_calls_{status}"
        for phase in ("debate", "post")
        for family in ("issue", "concern", "insight_weakness")
        for status in ("valid", "expected")
    ),
}
SELECTED_FEATURES = tuple(name for names in FEATURE_GROUPS.values() for name in names)
SUBSET_POLICIES = {
    "scope": "The base policies describe reused fields. This subset additionally pools novelty, computes the explicitly documented POST-minus-PRE differences and counts DEBATE-phase revision/retraction events and issue IDs; it fits no model and uses no external outcomes.",
    "novelty_pooling": "Pool assessed reviewer-domain ratings over methodological, empirical, theoretical and policy novelty, separately PRE/POST (up to 12 ratings per phase). Sum and mean use all available encoded ratings; category counts count domain ratings, not distinct reviewers. Abstentions/failures are excluded. Unequal coverage gives domains unequal weight in the pooled mean; n_valid reports coverage. No cross-phase averaging.",
    "differences": "Every difference is POST minus PRE, null if either operand is null. Spread differences require at least two assessed reviewers in each phase; the assessed reviewer sets may differ.",
    "roles": "Primary/secondary groups follow the proposed analysis outline. Coverage columns diagnose availability. These labels do not automatically configure primary hypotheses in evaluation_analysis.py.",
    "debate_timing": "Existing debate_final_* fields use the latest owner stance across review, including POST. debate_revision_count counts revisions across review, including POST, not only DEBATE-phase events. Discovery counts distinguish DEBATE and POST exactly.",
    "debate_phase_activity": "New debate_phase_* counts restrict canonical revision/update events to phase=DEBATE. Event counts can count the same issue more than once; *_issue_count counts distinct issue IDs. Counts are null without any successful DEBATE assessment, recorded DEBATE event or argument; observed empty counts are zero. Partial source coverage describes recorded activity only.",
    "late_coverage": "Existing *_post_unknown_count includes only nonlate unknown endpoints. *_post_late_count covers nonretracted issues created or revised in the final debate round or POST, including unknown endpoints. Late is an overlapping timing diagnostic: it can also include active or uncertain issues and must not be added to those counts.",
    "rating_statistics": "For assessed ratings x_1,...,x_n within one phase, sum = sum(x_i), mean = sum(x_i)/n, and category count = number of assessed ratings in that category. NONE=0, LOW=1, MEDIUM=2, HIGH=3; repair scope uses 0 through 4. LOW excludes NONE. Category counts are zero even when n_valid=0; sums/means are null without assessed ratings. Read zero counts alongside n_valid. Ordinal means assume category spacing.",
    "issue_cohorts": "concern means substantive_flaw; insight_weakness is a separate issue type. C_family,phase denotes that family's primary cohort for the phase. PRE cohorts use original PRE issue IDs, types, severity and confidence. Primary POST cohorts use current issue type and severity and endpoint MAINTAIN/NARROW or NEW_POST, including IDs created in PRE, DEBATE or POST. POST confidence comes from the final POST stance, or creation for NEW_POST; earlier confidence is not backfilled. These are phase-specific cohorts, not matched ID sets. Reclassification, new IDs and changed source coverage can change a comparison. UNCERTAIN/UNKNOWN endpoints are not inferred to be resolved. Unit is issue_id; proposed duplicate links do not merge IDs.",
    "issue_weights": "For each issue in the specified primary cohort, w_i = severity_i * confidence_i / 100, with severity in [1,10] and confidence in [0,10]. Weighted count = sum(w_i); weighted mean = sum(w_i)/n, where n is that cohort's raw issue count, including zero-confidence issues. This mean is not sum(severity_i*confidence_i)/sum(confidence_i). No confidence cutoff applies to weighted summaries. Weights are descriptive scores, not calibrated probabilities. An observed empty cohort has raw/weighted count 0 and mean null; without a successful source assessment in that phase these summaries are null. Partial coverage summarizes only available records.",
    "discovery_cohorts": "New-ID cohorts are selected by phase_created and creation_issue_type; they retain original creation severity/confidence even after revision, reclassification or retraction. Their means are unweighted arithmetic means over the discovery cohort. Revisions do not create new IDs. Within each phase, concern and insight_weakness discovery types are disjoint subsets of issue (all types). Their two counts can be added to count those two types, but adding either to the all-issue count double-counts IDs. POST new observations are assertions, not proof of survival after debate.",
    "legacy_activity": "Original #628–632 use arguments as recorded whenever rounds_requested>0 and arguments is present: zero can therefore occur when all attempted debate calls failed. They are null when zero rounds were requested or the arguments field is absent/null. Original #633–636 count each ID's latest available owner stance across DEBATE and POST; a latest DEBATE MAINTAIN can count even without a final POST assessment. These and original #637 return integer zero for empty event arrays without a call-success gate. #637 counts every revision event in DEBATE and POST, not distinct revised IDs. Use source-call coverage and the DEBATE-phase-only counts to interpret these legacy fields.",
    "feature_dependencies": "With assessed ratings present, a pooled sum equals its mean times n_valid; at fixed coverage these two features are redundant. Likewise weighted issue count equals weighted mean times raw count when the cohort is nonempty and observed. Source-call coverage values are shared across the three discovery families within each phase; they do not count family-specific calls.",
}


def _column_notes(confidence_threshold):
    """Clarify generic parent definitions without modifying the copied columns."""
    notes = {}

    def add(name, description, formula, availability):
        notes[name] = {"description": description, "formula": formula,
                       "availability": availability}

    for suffix, description, formula in (
            ("n_eligible", "Reviewers with an assessed PRE repair scope greater than zero.", "|E|"),
            ("n_observed", "Eligible reviewers with an assessed POST repair scope, including POST=0.", "|O|"),
            ("n_survived", "Eligible reviewers whose assessed POST repair scope remains greater than zero.", "|S|"),
            ("n_unknown", "Eligible reviewers without an assessed POST repair scope (including abstention/failure).", "|E| - |O|"),
            ("coverage_rate", "Fraction of eligible reviewers with assessed POST endpoints.", "|O| / |E|"),
            ("rate", "Fraction still requiring repair among eligible reviewers with assessed POST endpoints.", "|S| / |O|"),
            ("complete_rate", "Fraction still requiring repair in the entire eligible cohort, available only with complete POST coverage.", "|S| / |E|, only if |O| = |E|")):
        availability = ("Integer count, including zero." if suffix.startswith("n_") else
                        "Null if |O|=0." if suffix == "rate" else
                        "Null if |E|=0 or any eligible POST endpoint is unassessed." if suffix == "complete_rate" else
                        "Null if |E|=0; zero when E is nonempty but no POST endpoint is assessed.")
        add("repair_scope_survival_" + suffix, description, formula, availability)

    for family, issue_type in base.ISSUE_FAMILIES:
        for phase in PHASES:
            cohort = (f"original PRE {issue_type} IDs" if phase == "pre" else
                      f"current {issue_type} IDs with MAINTAIN/NARROW/NEW_POST endpoints, from any creation phase")
            availability = f"Null without any successful {phase.upper()} source assessment; zero for an observed empty cohort."
            add(f"{family}_{phase}_count", f"Number of {cohort}.",
                f"|C_{family},{phase}|", availability)
            if family == "concern":
                add(f"concern_{phase}_n_confidence_above_threshold",
                    f"Number of {cohort} with recorded confidence strictly greater than {confidence_threshold}; no severity filter.",
                    f"sum(1[confidence_i > {confidence_threshold}] for i in C_concern,{phase})",
                    availability + " Missing confidence is not imputed.")
        for endpoint, condition, availability in (
                ("uncertain", "endpoint = UNCERTAIN (including late issues)", "a successful POST assessment"),
                ("unknown", "endpoint = UNKNOWN and late = false", "a successful PRE assessment"),
                ("late", "late = true and endpoint != RETRACTED", "a successful PRE or POST assessment")):
            add(f"{family}_post_{endpoint}_count",
                f"Current {issue_type} IDs satisfying {condition}. Late cohorts overlap active/uncertain cohorts.",
                f"count(current type = {issue_type} and {condition})",
                f"Null without {availability}; zero when available with no qualifying IDs.")
        add(f"{family}_post_retracted_count",
            f"Current {issue_type} IDs whose latest available owner update is RETRACT, whether in DEBATE or POST; not a count of every historical retraction event.",
            f"count(current type = {issue_type} and final endpoint = RETRACTED)",
            "Null without a successful PRE or POST assessment; otherwise an integer count including zero.")
    add("concern_n_confidence_above_threshold_post_minus_pre",
        "Change in confident substantive-flaw counts across the two primary phase-specific cohorts; new/reclassified IDs, confidence changes and coverage can affect it. It is not a matched-issue survival rate.",
        "concern_post_n_confidence_above_threshold - concern_pre_n_confidence_above_threshold",
        "Null if either operand is null; positive means more confident concerns in the observed POST cohort.")

    argument_availability = "Null if rounds_requested=0 or arguments is absent/null; otherwise zero for an empty arguments array, including when debate calls failed."
    for action in ("CHALLENGE", "DEFENSE", "CONCESSION", "QUESTION"):
        add(f"debate_{action.lower()}_count", f"Number of recorded {action} arguments across debate rounds.",
            f"count(argument.action = {action})", argument_availability)
    add("debate_argument_count", "Total recorded debate arguments across all action types.",
        "len(arguments)", argument_availability)
    for stance in ("RETRACT", "NARROW", "MAINTAIN", "UNCERTAIN"):
        add(f"debate_final_{stance.lower()}_count",
            f"Issue IDs whose latest recorded owner update (by event_sequence, across DEBATE/POST) is {stance}; no final POST update is required.",
            f"count(latest_update_per_issue.stance = {stance})",
            "Zero if no updates have this latest stance; no call-success gate. IDs with no update do not enter any stance count.")
    add("debate_revision_count", "All recorded revision events in DEBATE and POST, counting repeated revisions of an ID separately.",
        "len(issue_revisions)", "Zero for an empty revision array; no call-success gate.")
    return notes


def _derived_features(paper, record):
    features = base._Features()
    rows = paper["reviewer_features"]
    for phase in PHASES:
        selected = [row for row in rows if row["phase"] == phase.upper()
                    and row["construct"].startswith("novelty_")
                    and row["availability"] == "ASSESSED"]
        values = [row["value"] for row in selected]
        prefix = f"novelty_pooled_{phase}"
        source = f"reviewer_features: all four novelty domains, phase={phase.upper()}; assessed reviewer-domain ratings"
        missingness = "Sum/mean null without assessed ratings; category counts zero with n_valid=0. Abstentions and unavailable ratings are excluded, not assigned zero."
        for stat, value, definition in (
            ("sum", sum(values) if values else None,
             "Sum of assessed encoded novelty ratings across all four domains and all reviewers (up to 12 ratings)."),
            ("mean", mean(values) if values else None,
             "Pooled novelty sum / n_valid across assessed reviewer-domain ratings (NONE=0, LOW=1, MEDIUM=2, HIGH=3)."),
            ("count_low", sum(row["raw_value"] == "LOW" for row in selected),
             "Number of assessed LOW novelty domain ratings across all reviewers; a reviewer may contribute up to four."),
            ("count_high", sum(row["raw_value"] == "HIGH" for row in selected),
             "Number of assessed HIGH novelty domain ratings across all reviewers; a reviewer may contribute up to four."),
            ("n_valid", len(values), "Number of assessed novelty reviewer-domain ratings, from 0 through 12."),
        ):
            features.add(prefix + "_" + stat, value, definition, source, missingness=missingness)

    values = paper["features"]
    differences = [("concern_n_confidence_above_threshold_post_minus_pre",
                    "concern_pre_n_confidence_above_threshold",
                    "concern_post_n_confidence_above_threshold")]
    differences.extend((f"{construct}_{stat}_post_minus_pre",
                        f"{construct}_pre_{stat}", f"{construct}_post_{stat}")
                       for construct in base.CONSTRUCTS for stat in ("range", "sd"))
    for name, pre, post in differences:
        before, after = values[pre], values[post]
        value = after - before if before is not None and after is not None else None
        features.add(name, value, f"{post} minus {pre}; positive values indicate an increase.",
                     f"features: {post}, {pre}", missingness="Null if either PRE or POST value is null; no imputation.")

    revisions = [event for event in record["issue_revisions"] if event["phase"] == "DEBATE"]
    updates = [event for event in record["issue_updates"] if event["phase"] == "DEBATE"]
    retractions = [event for event in updates if event["stance"] == "RETRACT"]
    valid, _ = base._source_call_counts(record, {}, "DEBATE")
    observed = bool(valid or revisions or updates or record.get("arguments"))
    for label, events, source in (
            ("revision", revisions, "issue_revisions: phase=DEBATE"),
            ("retraction", retractions, "issue_updates: phase=DEBATE, stance=RETRACT")):
        issue_label = "revised" if label == "revision" else "retracted"
        missingness = "Null without successful DEBATE assessments or recorded DEBATE events/arguments; zero for observed debate without these events. Partial coverage describes recorded activity only."
        features.add(f"debate_phase_{label}_count", len(events) if observed else None,
                     f"Number of {label} events recorded in DEBATE; excludes POST events and can count an issue more than once.",
                     source, missingness=missingness)
        features.add(f"debate_phase_{issue_label}_issue_count",
                     len({event["issue_id"] for event in events}) if observed else None,
                     f"Number of distinct issue IDs {issue_label} during DEBATE; excludes POST events and counts each ID once.",
                     source, missingness=missingness)
    return features


def _select_paper(paper, dictionary, record):
    if dictionary.get("version") != BASE_FEATURE_VERSION:
        raise ValueError(f"Subset requires base feature version {BASE_FEATURE_VERSION}")
    if "subset" in dictionary:
        raise ValueError("Expected the full base feature bundle, not an already selected subset")
    derived = _derived_features(paper, record)
    values = {**paper["features"], **derived.values}
    columns = {**dictionary["columns"], **derived.definitions}
    required = set(SELECTED_FEATURES)
    missing = (required - values.keys()) | (required - columns.keys())
    if missing:
        raise ValueError(f"Source lacks selected features: {sorted(missing)}")
    numbers = {name: i for i, name in enumerate(dictionary["columns"], 1)}
    selected = deepcopy(paper)
    selected["features"] = {name: values[name] for name in SELECTED_FEATURES}
    result_dictionary = deepcopy(dictionary)
    result_dictionary["subset"] = {
        "version": SUBSET_VERSION,
        "base_feature_version": BASE_FEATURE_VERSION,
        "groups": {group: list(names) for group, names in FEATURE_GROUPS.items()},
        "policies": deepcopy(SUBSET_POLICIES),
        "column_notes": _column_notes(dictionary["parameters"]["confidence_threshold"]),
        "base_inventory_numbers": {name: numbers[name] for name in SELECTED_FEATURES if name in numbers},
        "derived_features": [name for name in SELECTED_FEATURES if name in derived.values],
    }
    result_dictionary["columns"] = {name: deepcopy(columns[name]) for name in SELECTED_FEATURES}
    selected.pop("feature_dictionary", None)
    return selected, result_dictionary


def build_paper_features(record, doc_id, *, source_path=None, source_sha256=None,
                         confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD):
    """Construct the subset for one canonical record without changing its inputs."""
    full = base.build_paper_features(record, doc_id, source_path=source_path,
                                     source_sha256=source_sha256,
                                     confidence_threshold=confidence_threshold)
    paper, dictionary = _select_paper(full, full["feature_dictionary"], record)
    paper["feature_dictionary"] = dictionary
    return paper


def load_candidate_features(source, feature_spec=None, *,
                            confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD):
    """Read the same doc_id,result_path CSV as the full constructor and subset it.

    A supplied frozen subset dictionary must match, including confidence cutoff.
    Reviewer rows, issue snapshots, editor outcome and provenance are retained.
    """
    papers = []
    dictionary = None
    for entry in base.load_review_records(source):
        paper = build_paper_features(entry["record"], entry["doc_id"],
                                     source_path=entry["source_path"], source_sha256=entry["source_sha256"],
                                     confidence_threshold=confidence_threshold)
        current = paper.pop("feature_dictionary")
        if dictionary is not None and dictionary != current:
            raise ValueError("Subset definitions differ across source records")
        dictionary = current
        papers.append(paper)
    if feature_spec is not None and dictionary != feature_spec:
        raise ValueError("Requested frozen subset feature dictionary does not match this version/cutoff")
    return {"feature_dictionary": dictionary, "papers": papers}


def inventory_dictionary(*, confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD):
    """Obtain data-independent definitions without requiring benchmark results."""
    record = {"selection": {"payload": {"selected_personas": ["R1", "R2", "R3"]}},
              "pre_assessments": [], "post_assessments": [], "issues": [],
              "issue_revisions": [], "issue_updates": [], "issue_links": [],
              "rounds_requested": 0, "rounds": [], "arguments": []}
    return build_paper_features(record, "inventory", confidence_threshold=confidence_threshold)["feature_dictionary"]


def render_inventory(dictionary):
    """Render every selected column in the same order as the subset export."""
    subset = dictionary["subset"]
    columns = dictionary["columns"]
    cutoff = dictionary["parameters"]["confidence_threshold"]
    example_confident = sum(confidence > cutoff for confidence in (9, 5))
    lines = [f"# Proposed feature subset ({len(columns)} paper-level columns)", "",
             f"Source: `{Path(__file__).resolve()}`", "",
             f"Subset version: `{subset['version']}`; base version: `{dictionary['version']}`.", "",
             f"Confidence cutoff: recorded confidence strictly greater than {cutoff}; change with `--confidence-threshold`. The exact numeric cutoff is saved in `feature_dictionary.parameters.confidence_threshold`.", "",
             "All original inventory features 604–637 are retained by name. Original # references below refer to the full exp12-features-v7 inventory. Existing values and parent column metadata are copied unchanged. Additional explanations/formulas below are exported in `feature_dictionary.subset.column_notes`; the remaining definitions come directly from the parent dictionary.", ""]
    for title, key in (
            ("Rating formulas", "rating_statistics"), ("Novelty pooling", "novelty_pooling"),
            ("Issue cohorts", "issue_cohorts"), ("Issue weights", "issue_weights"),
            ("Discovery cohorts", "discovery_cohorts"), ("Late coverage", "late_coverage"),
            ("Legacy debate fields", "legacy_activity"), ("DEBATE-only activity", "debate_phase_activity"),
            ("Dependencies between features", "feature_dependencies"), ("Analysis roles", "roles")):
        lines.extend([f"**{title}.** {subset['policies'][key]}", ""])
    lines.extend([
        f"**Weighted example.** Two issues with (severity, confidence) = (8,9) and (6,5) have weights 0.72 and 0.30: raw count 2, weighted count 1.02, weighted mean 0.51. Both enter the weighted summaries. At the selected cutoff >{cutoff}, their confident count is {example_confident}.", "",
        "**Repair survival.** E = reviewers assessed PRE>0; O = E with assessed POST (including POST=0); S = O with POST>0. rate = |S|/|O|; coverage_rate = |O|/|E|; complete_rate = |S|/|E| only if |O|=|E|. When E is nonempty and every eligible reviewer abstains POST, coverage is 0, while rate and complete_rate are null. Empty E gives null rates. Counts are still integers, including zero.", "",
        "**Spread and differences.** Range = max(x)-min(x). Population SD = sqrt(sum((x-mean(x))^2)/n), using assessed reviewers in that construct/phase; both are null for n<2. Every added difference is POST minus PRE, null if either operand is null. A negative spread difference means less observed dispersion. The assessed reviewer sets may change; these differences do not restrict to matched personas. The confident-concern difference compares C_concern,post and C_concern,pre as defined under Issue cohorts, not matched issue IDs; a decrease alone does not show resolution.", "",
        "**Discovery availability.** Discovery cohorts are observed if there is a successful source assessment in that phase or at least one ID of any type created in that phase; empty observed counts are 0 and means are null. Without either source of evidence, counts/means are null, except that zero requested debate rounds give DEBATE-new count 0. `n_source_calls_expected` means assessments actually called (including failed calls), not all scheduled opportunities; retries count once. These coverage counts are shared across discovery families.", "",
        "**Zero versus missing.** Reviewer category counts can be 0 with n_valid=0; this is not evidence that all reviewers rated another category. Issue raw/weighted counts can be 0 for an observed empty cohort, while unavailable phases are null. Legacy debate fields and DEBATE-only fields have different availability rules, specified above and per entry. Missingness is not replaced with substantive zero. Reviewer rows, issue snapshots, editor outcome and provenance remain separate outputs.", ""])
    number = 0
    for group, names in subset["groups"].items():
        lines.extend([f"## {group.replace('_', ' ').capitalize()} ({len(names)})", ""])
        for name in names:
            number += 1
            entry = columns[name]
            note = subset["column_notes"].get(name)
            original = subset["base_inventory_numbers"].get(name)
            origin = f"Original #{original}." if original is not None else "Added by subset script."
            definition = note["description"] if note else entry["definition"]
            lines.append(f"{number}. `{name}` — {definition} {origin}")
            if note:
                lines.append(f"    Formula: `{note['formula']}`. Availability: {note['availability']}")
            elif entry["missingness"] != "See feature policies.":
                lines.append(f"    Availability: {entry['missingness']}")
            lines.extend([f"    Source: {entry['source']}", ""])
        lines.append("")
    return "\n".join(lines)


def _check_output_paths(inputs, outputs):
    """Reject path aliases, including hard links, before writing any output."""
    def same_file(left, right):
        if left.resolve() == right.resolve():
            return True
        try:
            return left.samefile(right)
        except FileNotFoundError:
            return False

    inputs = [path for path in inputs if path is not None]
    outputs = [path for path in outputs if path is not None]
    for index, output in enumerate(outputs):
        if any(same_file(output, source) for source in inputs):
            raise ValueError(f"Output path would overwrite an input file: {output}")
        if any(same_file(output, previous) for previous in outputs[:index]):
            raise ValueError("JSON output and inventory paths must refer to distinct files")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", nargs="?", type=Path, help="CSV containing doc_id,result_path")
    parser.add_argument("--output", type=Path, help="Subset feature bundle JSON; default stdout")
    parser.add_argument("--inventory", type=Path, help="Write the numbered Markdown feature inventory")
    parser.add_argument("--inventory-only", action="store_true", help="Write definitions without a manifest; requires --inventory")
    parser.add_argument("--confidence-threshold", type=float, default=DEFAULT_CONFIDENCE_THRESHOLD,
                        help="Strict confidence cutoff from 0 to 10 (default: %(default)s)")
    args = parser.parse_args(argv)
    if args.inventory_only and (args.manifest or args.output or not args.inventory):
        parser.error("--inventory-only requires --inventory and cannot be combined with a manifest or --output")
    if not args.inventory_only and args.manifest is None:
        parser.error("Provide a manifest, or use --inventory-only --inventory PATH")
    try:
        _check_output_paths([args.manifest], [args.output, args.inventory])
        if args.inventory_only:
            dictionary = inventory_dictionary(confidence_threshold=args.confidence_threshold)
        else:
            bundle = load_candidate_features(args.manifest, confidence_threshold=args.confidence_threshold)
            source_paths = [Path(paper["provenance"]["source_path"]) for paper in bundle["papers"]]
            _check_output_paths(source_paths, [args.output, args.inventory])
            dictionary = bundle["feature_dictionary"]
            serialized = json.dumps(bundle, indent=2, allow_nan=False) + "\n"
            if args.output:
                args.output.write_text(serialized, encoding="utf-8")
            else:
                print(serialized, end="")
        if args.inventory:
            args.inventory.write_text(render_inventory(dictionary), encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"Subset feature construction error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
