"""Offline input contracts, targets, and descriptive statistics for exp-12.

All public helpers return ordinary JSON-serializable mappings and never mutate
source records. External target ``values`` are oriented so higher is better;
lower-is-better binary labels use 1-native_value and other lower targets use
negation. ``raw_values`` retain the native numerical scale. No imputation, model fitting,
paper scoring rule, or claims about benchmark performance are defined here.

Ranking statistics use complete paper pairs, average ranks for Spearman, and
Kendall's tau-b tie correction. Pair counts describe data coverage, not independent
sample size. Uncertainty must resample papers in the orchestration layer.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import csv
import hashlib
from itertools import combinations
import json
import math
from pathlib import Path
import re
from statistics import mean, median

try:
    from .feature_selection import FEATURE_VERSION, POLICIES as FEATURE_POLICIES
except ImportError:
    from feature_selection import FEATURE_VERSION, POLICIES as FEATURE_POLICIES


VERDICTS = ("REJECT", "RESUBMIT", "ACCEPT")
PHASES = ("PRE", "POST")
CONSTRUCTS = tuple("novelty_" + domain for domain in
                   ("methodological", "empirical", "theoretical", "policy")) + (
                       "insight", "repair_scope", "verdict")
AVAILABILITY = {"ASSESSED", "ABSTAIN", "FAILED", "SKIPPED", "MISSING"}
METRICS = {"spearman", "kendall_tau_b", "concordance", "mae", "rmse", "auc"}


def _nonempty_string(value, label):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{label} must be a nonempty string without surrounding whitespace")
    return value


def _finite(value, label, *, nullable=False, allow_bool=False):
    if value is None and nullable:
        return None
    if ((isinstance(value, bool) and not allow_bool) or
            not isinstance(value, (int, float)) or not math.isfinite(value)):
        raise ValueError(f"{label} must be a finite number" + (" or null" if nullable else ""))
    return value


def _read_json(source):
    if isinstance(source, dict):
        return deepcopy(source), {"kind": "in_memory"}
    path = Path(source).resolve()
    raw = path.read_bytes()
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f"Invalid feature JSON: {path}: {exc}") from exc
    return result, {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}


def _read_rows(source):
    if isinstance(source, (list, tuple)):
        rows, provenance = deepcopy(list(source)), {"kind": "in_memory"}
    else:
        path = Path(source).resolve()
        raw = path.read_bytes()
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            names = reader.fieldnames or []
            if not {"id", "doc_id"}.intersection(names) or len(names) != len(set(names)):
                raise ValueError("Outcome CSV needs unique column names including id or doc_id")
            rows = list(reader)
        provenance = {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError("Outcome CSV has inconsistent row lengths")
    for row in rows:
        if "id" in row:
            if "doc_id" in row and row["doc_id"] != row["id"]:
                raise ValueError("Outcome id and doc_id disagree")
            row["doc_id"] = row["id"]
    return rows, provenance


def _validate_rows(rows, label):
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"{label} rows must be mappings")
        doc_id = _nonempty_string(row.get("doc_id"), f"{label} doc_id")
        if doc_id in seen:
            raise ValueError(f"Duplicate {label} doc_id: {doc_id}")
        seen.add(doc_id)
    return seen


def _validate_reviewer_rows(rows, *, doc_id=None, require_complete_columns=False):
    if not isinstance(rows, list):
        raise ValueError("reviewer_features must be a list")
    seen = set()
    panels = defaultdict(lambda: defaultdict(set))
    expected_keys = {"doc_id", "persona", "phase", "construct", "raw_value", "value",
                     "availability", "source_call_ids", "source_path"}
    for row in rows:
        if not isinstance(row, dict) or not expected_keys <= row.keys():
            raise ValueError("Reviewer rows require identity, raw/value, availability and provenance fields")
        identity = _nonempty_string(row["doc_id"], "Reviewer doc_id")
        if doc_id is not None and identity != doc_id:
            raise ValueError("Reviewer doc_id disagrees with its paper")
        persona = _nonempty_string(row["persona"], "Reviewer persona")
        phase, construct = row["phase"], row["construct"]
        if phase not in PHASES or construct not in CONSTRUCTS:
            raise ValueError(f"Unknown reviewer phase/construct: {phase!r}/{construct!r}")
        key = (identity, persona, phase, construct)
        if key in seen:
            raise ValueError(f"Duplicate reviewer identity/phase/construct: {key}")
        seen.add(key)
        panels[identity][persona].add((phase, construct))
        status = row["availability"]
        if status not in AVAILABILITY:
            raise ValueError(f"Unknown reviewer availability: {status!r}")
        raw, value = row["raw_value"], row["value"]
        _finite(value, "Reviewer encoded value", nullable=True)
        if not isinstance(row["source_call_ids"], list) or not isinstance(row["source_path"], str):
            raise ValueError("Reviewer provenance needs source_call_ids list and source_path string")
        if construct == "verdict":
            if value is not None:
                raise ValueError("Verdict value must remain null; use raw categorical verdict")
            if status == "ASSESSED" and raw not in VERDICTS:
                raise ValueError(f"Unknown assessed verdict: {raw!r}")
            if status != "ASSESSED" and (raw is not None or status == "ABSTAIN"):
                raise ValueError("Unavailable verdict must have null raw_value; verdict has no ABSTAIN state")
        elif status == "ASSESSED":
            if construct == "repair_scope":
                if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw <= 4 or value != raw:
                    raise ValueError("Assessed repair scope must be an integer from 0 through 4")
            else:
                levels = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
                if not isinstance(raw, str) or raw not in levels or value != levels[raw]:
                    raise ValueError("Assessed ordinal label disagrees with its encoded value")
        elif value is not None:
            raise ValueError("Unavailable reviewer judgment must have null encoded value")
        elif status == "ABSTAIN":
            if construct != "repair_scope" and raw != "ABSTAIN":
                raise ValueError("Ordinal abstention must retain ABSTAIN raw_value")
        elif raw is not None:
            raise ValueError("Unavailable reviewer judgment must have null raw_value")
    if require_complete_columns:
        expected = {(phase, construct) for phase in PHASES for construct in CONSTRUCTS}
        for identity, panel in panels.items():
            if len(panel) != 3 or any(columns != expected for columns in panel.values()):
                raise ValueError(f"Paper {identity} must retain all three selected personas and missingness rows")


def _validate_bundle(bundle):
    if not isinstance(bundle, dict) or not {"feature_dictionary", "papers"} <= bundle.keys():
        raise ValueError("Feature input must be an exported feature bundle, not a run manifest")
    dictionary = bundle["feature_dictionary"]
    if not isinstance(dictionary, dict) or dictionary.get("version") != FEATURE_VERSION:
        raise ValueError(f"Feature dictionary version must be {FEATURE_VERSION}")
    policies, columns = dictionary.get("policies"), dictionary.get("columns")
    if not isinstance(policies, dict) or not policies or not isinstance(columns, dict) or not columns:
        raise ValueError("Feature dictionary requires nonempty policies and columns mappings")
    if policies != FEATURE_POLICIES:
        raise ValueError(f"Feature dictionary policies are incompatible with {FEATURE_VERSION}")
    for name, entry in columns.items():
        _nonempty_string(name, "Feature column")
        if not isinstance(entry, dict) or not {"definition", "source", "unit", "missingness"} <= entry.keys():
            raise ValueError(f"Feature column {name} lacks documented definition/source/unit/missingness")
        if any(not isinstance(entry[key], str) or not entry[key] for key in
               ("definition", "source", "unit", "missingness")):
            raise ValueError(f"Feature column {name} has malformed metadata")
    papers = bundle["papers"]
    if not isinstance(papers, list) or not papers:
        raise ValueError("Feature bundle must contain at least one paper")
    _validate_rows(papers, "feature")
    required = {"doc_id", "run_id", "features", "reviewer_features", "issue_features", "editor_outcome", "provenance"}
    for paper in papers:
        if not required <= paper.keys():
            raise ValueError(f"Paper {paper['doc_id']} lacks required exported fields")
        if paper["run_id"] is not None and not isinstance(paper["run_id"], str):
            raise ValueError("run_id must be a string or null")
        if not isinstance(paper["features"], dict) or set(paper["features"]) != set(columns):
            raise ValueError(f"Paper {paper['doc_id']} feature columns differ from frozen dictionary")
        for name, value in paper["features"].items():
            _finite(value, f"{paper['doc_id']} feature {name}", nullable=True, allow_bool=True)
        if "feature_dictionary" in paper and paper["feature_dictionary"] != dictionary:
            raise ValueError("Per-paper feature dictionary differs from the bundle dictionary")
        if not isinstance(paper["issue_features"], list) or not isinstance(paper["provenance"], dict):
            raise ValueError("Paper requires issue_features list and provenance mapping")
        editor = paper["editor_outcome"]
        if (not isinstance(editor, dict) or
                not {"availability", "verdict", "source_call_ids", "source_path"} <= editor.keys()):
            raise ValueError("Paper requires editor_outcome with verdict, availability and provenance")
        if editor["availability"] not in {"ASSESSED", "FAILED", "SKIPPED", "MISSING"}:
            raise ValueError("Unknown editor availability")
        if ((editor["availability"] == "ASSESSED" and editor["verdict"] not in VERDICTS) or
                (editor["availability"] != "ASSESSED" and editor["verdict"] is not None)):
            raise ValueError("Editor verdict disagrees with availability")
        if not isinstance(editor["source_call_ids"], list) or not isinstance(editor["source_path"], str):
            raise ValueError("Editor outcome requires source_call_ids list and source_path string")
        _validate_reviewer_rows(paper["reviewer_features"], doc_id=paper["doc_id"],
                                require_complete_columns=True)
        if not paper["reviewer_features"]:
            raise ValueError("Paper reviewer_features cannot be empty")


def load_features_and_outcomes(feature_source, outcome_source, linkage_spec=None):
    """Validate exported JSON and CSV/list outcomes; retain unmatched coverage.

    ``linkage_spec`` accepts ``strict`` (both ID sets equal),
    ``require_all_features`` (each feature paper needs an outcome row), and
    ``require_all_outcomes``. ``expected_dictionary_sha256`` optionally requires
    an exact frozen feature-definition match. Its digest uses UTF-8 JSON with
    sorted keys, compact separators, and allow_nan=False. This is a definition
    consistency check, not cryptographic authentication of a source.
    Missing target cells are independent of row linkage. Input mappings/rows are
    deep-copied; source hashes and the feature-dictionary digest are retained.
    """
    spec = {} if linkage_spec is None else dict(linkage_spec)
    boolean_keys = {"strict", "require_all_features", "require_all_outcomes"}
    unknown = set(spec) - boolean_keys - {"expected_dictionary_sha256"}
    if unknown or any(not isinstance(spec[key], bool) for key in boolean_keys if key in spec):
        raise ValueError(f"Invalid linkage options: {sorted(unknown) or spec}")
    expected_hash = spec.get("expected_dictionary_sha256")
    if "expected_dictionary_sha256" in spec and (
            not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash)):
        raise ValueError("expected_dictionary_sha256 must contain exactly 64 hexadecimal characters")
    bundle, feature_provenance = _read_json(feature_source)
    _validate_bundle(bundle)
    dictionary_serialized = json.dumps(bundle["feature_dictionary"], sort_keys=True,
                                       separators=(",", ":"), allow_nan=False)
    dictionary_hash = hashlib.sha256(dictionary_serialized.encode("utf-8")).hexdigest()
    if expected_hash is not None and dictionary_hash != expected_hash.lower():
        raise ValueError("Feature dictionary sha256 does not match expected_dictionary_sha256")
    rows, outcome_provenance = _read_rows(outcome_source)
    outcome_ids = _validate_rows(rows, "outcome")
    feature_ids = {paper["doc_id"] for paper in bundle["papers"]}
    coverage = {"feature_only_ids": sorted(feature_ids - outcome_ids),
                "outcome_only_ids": sorted(outcome_ids - feature_ids),
                "matched_ids": sorted(feature_ids & outcome_ids)}
    if ((spec.get("strict") or spec.get("require_all_features")) and coverage["feature_only_ids"] or
            (spec.get("strict") or spec.get("require_all_outcomes")) and coverage["outcome_only_ids"]):
        raise ValueError(f"Paper linkage requirements failed: {coverage}")
    bundle["papers"] = sorted(bundle["papers"], key=lambda row: row["doc_id"])
    return {"bundle": bundle, "papers": bundle["papers"],
            "outcomes": sorted(rows, key=lambda row: row["doc_id"]), "coverage": coverage,
            "provenance": {"feature_source": feature_provenance,
                           "outcome_source": outcome_provenance,
                           "feature_version": FEATURE_VERSION,
                           "feature_dictionary_sha256": dictionary_hash}}


def _target(column, kind="numeric", direction="higher", status_column=None,
            accepted_statuses=None, date_columns=None, **extra):
    return {"column": column, "type": kind, "direction": direction,
            "status_column": status_column, "accepted_statuses": accepted_statuses,
            "date_columns": date_columns or ["ranking_snapshot_date"], **extra}


_SJR_STATUS = ["MATCHED_SCIMAGO_2025_ARCHIVED_EXPORT"]
_GSM_STATUS = ["MATCHED_GOOGLE_SCHOLAR_METRICS"]
_HWY_STATUS = ["VERIFIED_2021_TABLE1_BASELINE_2015_2019"]
DEFAULT_TARGETS = {
    "wp_cites": _target("wp_cites", "count"),
    "cites": _target("cites", "count"),
    "sjr_score": _target("sjr_score", minimum=0),
    "sjr_best_quartile": _target("sjr_best_quartile", "ordinal",
        encoding={"Q1": 4, "Q2": 3, "Q3": 2, "Q4": 1}),
    "crossref_wp_cites": _target("crossref_wp_cites", "count", status_column="crossref_wp_status",
        accepted_statuses=["PULLED_CROSSREF_CITED_BY"],
        date_columns=["crossref_wp_retrieved_at_utc", "ranking_snapshot_date"]),
    "crossref_pub_cites": _target("crossref_pub_cites", "count", status_column="crossref_pub_status",
        accepted_statuses=["PULLED_CROSSREF_CITED_BY"],
        date_columns=["crossref_pub_retrieved_at_utc", "ranking_snapshot_date"]),
    "pjip_sjr_score": _target("pjip_sjr_score", status_column="pjip_status",
        accepted_statuses=_SJR_STATUS, date_columns=["pjip_snapshot", "sjr_year", "ranking_snapshot_date"], minimum=0),
    "pjip_sjr_rank": _target("pjip_sjr_rank", "rank", "lower", "pjip_status", _SJR_STATUS,
        ["pjip_snapshot", "sjr_year", "ranking_snapshot_date"]),
    "sjr_global_rank": _target("sjr_global_rank", "rank", "lower", "pjip_status", _SJR_STATUS,
        ["sjr_year", "ranking_snapshot_date"]),
    "sjr_economics_finance_rank": _target("sjr_economics_finance_rank", "rank", "lower", "pjip_status", _SJR_STATUS,
        ["sjr_year", "ranking_snapshot_date"]),
    "gsm_h5_index": _target("gsm_h5_index", "count", status_column="gsm_status",
        accepted_statuses=_GSM_STATUS, date_columns=["gsm_release", "gsm_citation_window", "ranking_snapshot_date"]),
    "gsm_h5_median": _target("gsm_h5_median", "count", status_column="gsm_status",
        accepted_statuses=_GSM_STATUS, date_columns=["gsm_release", "gsm_citation_window", "ranking_snapshot_date"]),
    "gsm_business_econ_mgmt_rank": _target("gsm_business_econ_mgmt_rank", "rank", "lower",
        "gsm_business_econ_mgmt_rank_status", ["VERIFIED_TOP20_CATEGORY_RANK"], ["gsm_release", "ranking_snapshot_date"]),
    "gsm_economics_rank": _target("gsm_economics_rank", "rank", "lower", "gsm_status", _GSM_STATUS,
        ["gsm_release", "ranking_snapshot_date"]),
    "gsm_finance_rank": _target("gsm_finance_rank", "rank", "lower", "gsm_status", _GSM_STATUS,
        ["gsm_release", "ranking_snapshot_date"]),
    "hwy_invariant_geom_rank_2015_2019": _target("hwy_invariant_geom_rank_2015_2019", "rank", "lower",
        "hwy_status", _HWY_STATUS),
    "hwy_top5_geom_rank_2015_2019": _target("hwy_top5_geom_rank_2015_2019", "rank", "lower",
        "hwy_status", _HWY_STATUS),
    "tier_used_in_experiments": _target("tier_used_in_experiments", "ordinal",
        encoding={"Tier1": 3, "Tier2": 2, "Tier3": 1}),
    "stern_finance_top_tier": _target("stern_finance_top_tier", "binary", status_column="stern_status",
        accepted_statuses=["VERIFIED_LISTED_2020_FINANCE_LIST", "VERIFIED_NOT_LISTED_2020_FINANCE_LIST",
                           "NOT_LISTED_NON_JOURNAL"]),
}


def _parse_target(value, spec, label):
    if value is None or isinstance(value, str) and not value.strip():
        return None
    kind = spec["type"]
    if kind == "ordinal":
        if not isinstance(value, (str, int, float)) or value not in spec["encoding"]:
            raise ValueError(f"{label}: unknown ordinal category {value!r}")
        numeric = spec["encoding"][value]
    else:
        if isinstance(value, bool):
            raise ValueError(f"{label}: booleans are not numerical target values")
        try:
            numeric = float(value)
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValueError(f"{label}: malformed numerical target {value!r}") from exc
    _finite(numeric, label)
    if kind in ("count", "rank", "binary") and int(numeric) != numeric:
        raise ValueError(f"{label}: {kind} requires an integer")
    if kind == "count" and numeric < 0 or kind == "rank" and numeric < 1:
        raise ValueError(f"{label}: invalid negative count or nonpositive rank")
    if kind == "binary" and numeric not in (0, 1):
        raise ValueError(f"{label}: binary outcome must be 0 or 1")
    if "minimum" in spec and numeric < spec["minimum"]:
        raise ValueError(f"{label}: target is below minimum {spec['minimum']}")
    if "maximum" in spec and numeric > spec["maximum"]:
        raise ValueError(f"{label}: target is above maximum {spec['maximum']}")
    return numeric


def register_external_outcomes(outcomes, target_spec=None):
    """Register native and higher-is-better targets without imputation.

    ``target_spec=None`` selects recognized columns present in the input. A list
    selects named defaults; a mapping selects names with default overrides or
    explicit custom definitions. Allowed definition keys are those in
    ``DEFAULT_TARGETS``, plus minimum/maximum and description. An explicit custom
    target needs column/type/direction. Missing or unacceptable source status
    excludes a cell; malformed nonempty values raise even when status is rejected.
    All IDs and snapshot cells remain auditable in the returned mappings.
    Lower-is-better binary labels are complemented (1-native_value), preserving
    AUC's 0/1 label contract; other lower-is-better outcomes are negated.
    """
    rows, _ = _read_rows(outcomes)
    _validate_rows(rows, "outcome")
    available = {key for row in rows for key in row}
    defaults = deepcopy(DEFAULT_TARGETS)
    compact_csv = bool({"wp_cites", "cites", "sjr_score", "sjr_best_quartile"} & available)
    if compact_csv:
        for definition in defaults.values():
            if definition["status_column"] not in available:
                definition["status_column"] = None
                definition["accepted_statuses"] = None
            definition["date_columns"] = [c for c in definition["date_columns"] if c in available]
    aliases = {"crossref_wp_cites": "wp_cites", "crossref_pub_cites": "cites", "pjip_sjr_score": "sjr_score"}
    for old, new in aliases.items():
        if old not in available and new in available:
            defaults[old] = deepcopy(defaults[new])
    if target_spec is None:
        definitions = {name: deepcopy(spec) for name, spec in defaults.items()
                       if name in available}
    elif isinstance(target_spec, (list, tuple)):
        if len(set(target_spec)) != len(target_spec) or any(name not in DEFAULT_TARGETS for name in target_spec):
            raise ValueError("Target list must contain unique recognized default names")
        definitions = {name: deepcopy(defaults[name]) for name in target_spec}
    elif isinstance(target_spec, dict):
        definitions = {}
        allowed = {"column", "type", "direction", "status_column", "accepted_statuses",
                   "date_columns", "encoding", "minimum", "maximum", "description"}
        for name, override in target_spec.items():
            _nonempty_string(name, "Target name")
            if not isinstance(override, dict) or set(override) - allowed:
                raise ValueError(f"Unknown or malformed target options for {name}")
            base = deepcopy(defaults.get(name, {}))
            base.update(deepcopy(override))
            if not {"column", "type", "direction"} <= base.keys():
                raise ValueError(f"Custom target {name} requires column, type, direction")
            base.setdefault("status_column", None)
            base.setdefault("accepted_statuses", None)
            base.setdefault("date_columns", [])
            definitions[name] = base
    else:
        raise ValueError("target_spec must be a list, mapping, or null")
    registered = {}
    for name, spec in definitions.items():
        _nonempty_string(spec["column"], f"Target {name} column")
        if spec["type"] not in {"numeric", "count", "rank", "ordinal", "binary"}:
            raise ValueError(f"Unknown target type for {name}: {spec['type']}")
        if spec["direction"] not in {"higher", "lower"}:
            raise ValueError(f"Target {name} direction must be higher or lower")
        if spec["status_column"] is not None:
            _nonempty_string(spec["status_column"], f"Target {name} status_column")
        if spec["column"] not in available and rows:
            raise ValueError(f"Requested target column absent: {spec['column']}")
        if (not isinstance(spec["date_columns"], list) or
                any(not isinstance(column, str) for column in spec["date_columns"])):
            raise ValueError(f"Target {name} date_columns must be a list of column names")
        statuses = spec["accepted_statuses"]
        if statuses is not None and (not isinstance(statuses, list) or not statuses or
                                     any(not isinstance(status, str) for status in statuses)):
            raise ValueError(f"Target {name} accepted_statuses must be a nonempty list or null")
        if statuses is not None and not spec["status_column"]:
            raise ValueError(f"Target {name} accepted statuses require a status_column")
        if spec["type"] == "ordinal":
            if not isinstance(spec.get("encoding"), dict) or not spec["encoding"]:
                raise ValueError(f"Target {name} ordinal type requires an encoding mapping")
            for value in spec["encoding"].values():
                _finite(value, f"Target {name} encoding")
        for bound in ("minimum", "maximum"):
            if bound in spec:
                _finite(spec[bound], f"Target {name} {bound}")
        if "minimum" in spec and "maximum" in spec and spec["minimum"] > spec["maximum"]:
            raise ValueError(f"Target {name} has inconsistent bounds")
        raw_values, values, excluded, snapshots = {}, {}, {}, {}
        for row in sorted(rows, key=lambda item: item["doc_id"]):
            doc_id = row["doc_id"]
            numeric = _parse_target(row.get(spec["column"]), spec, f"{doc_id}:{name}")
            raw_values[doc_id] = numeric
            snapshots[doc_id] = {column: row.get(column) for column in spec["date_columns"]}
            reason = None
            if numeric is None:
                reason = "missing_value"
            elif spec["status_column"] and statuses is not None:
                status = row.get(spec["status_column"])
                if status not in statuses:
                    reason = "missing_status" if not status else "status_not_accepted:" + str(status)
            if reason:
                excluded[doc_id] = reason
                values[doc_id] = None
            else:
                values[doc_id] = (numeric if spec["direction"] == "higher" else
                                  1 - numeric if spec["type"] == "binary" else -numeric)
        registered[name] = {"spec": spec, "values": values, "raw_values": raw_values,
                            "eligible_ids": sorted(doc_id for doc_id, value in values.items() if value is not None),
                            "excluded": excluded, "snapshots": snapshots,
                            "source_status_validation": "enforced" if spec["status_column"] else "not_supplied"}
    # Identical rank columns are useful source aliases, not independent outcomes.
    # Retain both requested names, but make the duplication visible to callers.
    prior_ranks = []
    for name, target in registered.items():
        if target["spec"]["type"] == "rank":
            target["duplicate_of"] = next((prior for prior in prior_ranks
                if target["values"] == registered[prior]["values"] and target["eligible_ids"]), None)
            prior_ranks.append(name)
    return registered


def _average_ranks(values):
    result = [None] * len(values)
    order = sorted(range(len(values)), key=values.__getitem__)
    start = 0
    while start < len(order):
        stop = start + 1
        while stop < len(order) and values[order[stop]] == values[order[start]]:
            stop += 1
        average = (start + 1 + stop) / 2
        for index in order[start:stop]:
            result[index] = average
        start = stop
    return result


def _pearson(x, y):
    if len(x) < 2:
        return None
    mx, my = mean(x), mean(y)
    dx, dy = [value - mx for value in x], [value - my for value in y]
    xx, yy = sum(value * value for value in dx), sum(value * value for value in dy)
    if not xx or not yy:
        return None
    return max(-1.0, min(1.0, sum(a * b for a, b in zip(dx, dy)) / math.sqrt(xx * yy)))


def metric_result(scores, targets, metric="spearman"):
    """Return a tie-aware statistic, complete-case counts, and undefined reason.

    None on either side excludes a paper; other nonfinite/malformed values raise.
    Concordance excludes external ties and gives score ties half credit. AUC
    requires native binary targets 0/1 (rather than an oriented negative target).
    MAE/RMSE require predictions and outcomes on the same explicitly chosen scale.
    """
    if metric not in METRICS:
        raise ValueError(f"Unknown metric: {metric}")
    scores, targets = list(scores), list(targets)
    if len(scores) != len(targets):
        raise ValueError("Score and target vectors must have equal lengths")
    for label, vector in (("Score", scores), ("Target", targets)):
        for value in vector:
            _finite(value, label, nullable=True, allow_bool=True)
    paired = [(score, target) for score, target in zip(scores, targets)
              if score is not None and target is not None]
    n = len(paired)
    x, y = [row[0] for row in paired], [row[1] for row in paired]
    concordant = discordant = score_ties = outcome_ties = both_ties = comparable_score_ties = 0
    for a, b in combinations(range(n), 2):
        sx = (x[a] > x[b]) - (x[a] < x[b])
        sy = (y[a] > y[b]) - (y[a] < y[b])
        if not sx:
            score_ties += 1
        if not sy:
            outcome_ties += 1
        if not sx and not sy:
            both_ties += 1
        if sy and not sx:
            comparable_score_ties += 1
        if sx * sy > 0:
            concordant += 1
        elif sx * sy < 0:
            discordant += 1
    total = n * (n - 1) // 2
    comparable = total - outcome_ties
    result = {"metric": metric, "estimate": None, "n_papers": n, "n_pairs": total,
              "n_comparable_pairs": comparable, "n_outcome_ties": outcome_ties,
              "n_score_ties": score_ties, "n_both_ties": both_ties,
              "n_comparable_score_ties": comparable_score_ties,
              "n_excluded": len(scores) - n, "undefined_reason": None}
    if not n:
        result["undefined_reason"] = "no_complete_papers"
        return result
    if metric in ("mae", "rmse"):
        errors = [abs(a - b) for a, b in paired]
        result["estimate"] = mean(errors) if metric == "mae" else math.sqrt(mean(e * e for e in errors))
    elif metric == "spearman":
        result["estimate"] = _pearson(_average_ranks(x), _average_ranks(y))
        if result["estimate"] is None:
            result["undefined_reason"] = "fewer_than_two_papers" if n < 2 else "constant_scores_or_outcomes"
    elif metric == "kendall_tau_b":
        denominator = math.sqrt((total - score_ties) * (total - outcome_ties))
        if denominator:
            result["estimate"] = (concordant - discordant) / denominator
        else:
            result["undefined_reason"] = "fewer_than_two_papers" if n < 2 else "constant_scores_or_outcomes"
    elif metric == "concordance":
        if comparable:
            result["estimate"] = (concordant + 0.5 * comparable_score_ties) / comparable
        else:
            result["undefined_reason"] = "no_comparable_outcome_pairs"
    elif metric == "auc":
        if any(value not in (0, 1) for value in y):
            raise ValueError("AUC requires native binary outcome values 0 and 1")
        positives = [score for score, target in paired if target == 1]
        negatives = [score for score, target in paired if target == 0]
        if not positives or not negatives:
            result["undefined_reason"] = "single_outcome_class"
        else:
            result["estimate"] = sum((a > b) + 0.5 * (a == b)
                                     for a in positives for b in negatives) / (len(positives) * len(negatives))
    return result


def pairwise_concordance(scores, targets):
    return metric_result(scores, targets, "concordance")


def spearman_correlation(scores, targets):
    return metric_result(scores, targets, "spearman")


def kendall_tau_b(scores, targets):
    return metric_result(scores, targets, "kendall_tau_b")


def _verdict_summary(values, n_expected):
    n = len(values)
    counts = Counter(values)
    pairs = n * (n - 1) // 2
    differing = sum(a != b for a, b in combinations(values, 2))
    return {"n_expected": n_expected, "n_valid": n, "complete": n == n_expected,
            "counts": {label: counts[label] for label in VERDICTS},
            "n_accept": counts["ACCEPT"], "n_resubmit": counts["RESUBMIT"], "n_reject": counts["REJECT"],
            "any_disagreement": int(len(counts) > 1) if pairs else None,
            "pairwise_disagreement": differing / pairs if pairs else None,
            "polarized": int(bool(counts["ACCEPT"] and counts["REJECT"])) if pairs else None}


def construct_verdict_diagnostics(reviewer_records, verdict_spec=None):
    """Describe verdicts only; compare PRE/POST on the same assessed personas.

    The expected panel is the union of personas in all supplied construct rows
    for a paper; frozen exports retain placeholders for missing assessments.
    Optional ``expected_panel_size`` (default 3) also prevents absent rows from
    silently declaring a reduced panel complete. All matched-set diagnostics
    explicitly report coverage; fewer than two matched reviewers yields null.
    """
    spec = {} if verdict_spec is None else dict(verdict_spec)
    if set(spec) - {"expected_panel_size"}:
        raise ValueError("Unknown verdict diagnostic options")
    expected_size = spec.get("expected_panel_size", 3)
    if isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size < 1:
        raise ValueError("expected_panel_size must be a positive integer")
    rows = deepcopy(list(reviewer_records))
    _validate_reviewer_rows(rows)
    panels = defaultdict(set)
    values = defaultdict(lambda: {phase: {} for phase in PHASES})
    for row in rows:
        panels[row["doc_id"]].add(row["persona"])
        if row["construct"] == "verdict" and row["availability"] == "ASSESSED":
            values[row["doc_id"]][row["phase"]][row["persona"]] = row["raw_value"]
    phases, transitions, changes = [], [], []
    order = {verdict: rank for rank, verdict in enumerate(VERDICTS)}
    for doc_id in sorted(panels):
        n_expected = max(expected_size, len(panels[doc_id]))
        for phase in PHASES:
            summary = _verdict_summary(list(values[doc_id][phase].values()), n_expected)
            phases.append({"doc_id": doc_id, "phase": phase, **summary})
        pre, post = values[doc_id]["PRE"], values[doc_id]["POST"]
        matched = sorted(set(pre) & set(post))
        for persona in matched:
            movement = order[post[persona]] - order[pre[persona]]
            transitions.append({"doc_id": doc_id, "persona": persona,
                                "pre": pre[persona], "post": post[persona],
                                "direction": "up" if movement > 0 else "down" if movement < 0 else "unchanged"})
        pre_match = _verdict_summary([pre[persona] for persona in matched], n_expected)
        post_match = _verdict_summary([post[persona] for persona in matched], n_expected)
        a, b = pre_match["pairwise_disagreement"], post_match["pairwise_disagreement"]
        changes.append({"doc_id": doc_id, "n_matched": len(matched), "n_expected": n_expected,
                        "complete": len(matched) == n_expected,
                        "matched_personas": matched,
                        "pre_disagreement": a, "post_disagreement": b,
                        "convergence": a - b if a is not None and b is not None else None,
                        "pre_any_disagreement": pre_match["any_disagreement"],
                        "post_any_disagreement": post_match["any_disagreement"],
                        "n_up": sum(order[post[p]] > order[pre[p]] for p in matched),
                        "n_down": sum(order[post[p]] < order[pre[p]] for p in matched),
                        "n_unchanged": sum(post[p] == pre[p] for p in matched)})
    return {"phase_summaries": phases, "transitions": transitions, "matched_changes": changes}


def _selected_features(papers, spec):
    available = sorted({name for paper in papers for name in paper["features"]})
    selected = spec.get("features", available)
    if not isinstance(selected, list) or len(set(selected)) != len(selected) or set(selected) - set(available):
        raise ValueError("features must be unique existing feature names")
    return selected


def derive_change_features(papers):
    """Return detached papers with matched change (POST-PRE) and reduction (PRE-POST).

    Names use 'improvement' for the requested PRE-POST convention. Positive values
    mean a reduction, not universally better quality (novelty/insight rise instead).
    No outcomes or fitted statistics enter these within-paper transformations.
    """
    papers = deepcopy(list(papers))
    definitions, exported = {}, []
    for paper in papers:
        values = {}

        def add(name, value, definition, source):
            if name in paper["features"]:
                raise ValueError(f"Derived feature collides with existing feature: {name}")
            values[name] = value
            definitions[name] = {"definition": definition, "source": source, "unit": "paper",
                                 "missingness": "Matched observed endpoints only; empty summaries are null. Coverage counts retained."}

        def paired(prefix, pairs, source):
            for direction, sign, formula in (("change", 1, "final minus initial"),
                                             ("improvement", -1, "initial minus final")):
                deltas = [sign * (after - before) for before, after in pairs]
                stats = {"sum": sum(deltas), "mean": mean(deltas) if deltas else None,
                         "median": median(deltas) if deltas else None,
                         "min": min(deltas) if deltas else None, "max": max(deltas) if deltas else None}
                for stat, value in stats.items():
                    add(prefix + "_" + direction + "_" + stat, value if deltas else None,
                        f"{stat} of matched {formula}; improvement is a signed reduction, not a universal quality label.", source)
                for label, function in (("min", min), ("max", max)):
                    level = sign * (function(b for a, b in pairs) - function(a for a, b in pairs)) if pairs else None
                    add(prefix + "_" + label + "_level_" + direction, level,
                        f"{formula} of the {label} level over the SAME matched cohort; differs from {label} individual change.", source)

        for construct in CONSTRUCTS[:-1]:
            rows = [r for r in paper["reviewer_features"] if r["construct"] == construct]
            phases = {phase: {r["persona"]: r["value"] for r in rows
                              if r["phase"] == phase and r["availability"] == "ASSESSED"}
                      for phase in PHASES}
            ids = sorted(set(phases["PRE"]) & set(phases["POST"]))
            prefix, source = construct + "_matched", f"reviewer_features: {construct}, same persona at PRE/POST"
            paired(prefix, [(phases["PRE"][i], phases["POST"][i]) for i in ids], source)
            for suffix, value in (("n_pre", len(phases["PRE"])), ("n_post", len(phases["POST"])),
                                  ("n_pairs", len(ids)),
                                  ("coverage_rate", len(ids) / len(phases["PRE"]) if phases["PRE"] else None)):
                add(prefix + "_" + suffix, value, "Matched reviewer coverage: " + suffix, source)

        for family, issue_type in (("concern", "substantive_flaw"), ("insight_weakness", "insight_weakness")):
            baseline = [r for r in paper["issue_features"] if r["phase"] == "POST"
                        and r["creation_issue_type"] == issue_type]
            matched = [r for r in baseline if r["endpoint"] in {"MAINTAIN", "NARROW", "NEW_POST"}]
            source = f"issue_features: initial {issue_type} IDs matched to definitive final assertions, any creation phase"
            for field in ("severity", "confidence"):
                pairs = []
                for row in matched:
                    before, after = row["creation_" + field], row[field]
                    _finite(before, "Issue creation " + field)
                    _finite(after, "Issue final " + field)
                    pairs.append((before, after))
                paired(family + "_matched_" + field, pairs, source)
            diagnostics = {"n_eligible": len(baseline), "n_pairs": len(matched),
                           "n_retracted": sum(r["endpoint"] == "RETRACTED" for r in baseline),
                           "n_uncertain": sum(r["endpoint"] == "UNCERTAIN" for r in baseline),
                           "n_unknown": sum(r["endpoint"] == "UNKNOWN" for r in baseline),
                           "n_new_post": sum(r["endpoint"] == "NEW_POST" for r in matched),
                           "n_revised": sum(r["version"] > 1 for r in matched),
                           "n_reclassified": sum(r["issue_type"] != issue_type for r in matched),
                           "coverage_rate": len(matched) / len(baseline) if baseline else None}
            for suffix, value in diagnostics.items():
                add(family + "_matched_" + suffix, value,
                    "Matched issue coverage: " + suffix + "; retractions are not severity zero; NEW_POST has zero elapsed change.", source)
        paper["features"].update(values)
        exported.append({"doc_id": paper["doc_id"], **values})
    return {"papers": papers, "columns": definitions, "rows": exported,
            "version": "exp12-derived-v1",
            "policies": {"change": "final-initial", "improvement": "initial-final (reduction)",
                         "redundancy": "Change and improvement are sign reversals; avoid including both in the same model.",
                         "issue_cohort": "Creation type retained after reclassification; RETRACT/UNCERTAIN/UNKNOWN excluded; NEW_POST retained with zero change."}}


def describe_feature_relationships(features, reviewer_records=None, diagnostic_spec=None):
    """Describe paper features; full correlations require an explicit small list.

    Options: ``features``, ``metric`` (spearman/kendall_tau_b),
    ``correlations`` (default true only for <=40 selected features). Correlations
    use pair-specific complete cases and report their coverage. The matched
    reviewer change table retains encoded ordinal deltas as descriptive quantities.
    """
    papers = list(features)
    _validate_rows(papers, "feature")
    spec = {} if diagnostic_spec is None else dict(diagnostic_spec)
    if set(spec) - {"features", "metric", "correlations"}:
        raise ValueError("Unknown descriptive options")
    selected = _selected_features(papers, spec)
    metric = spec.get("metric", "spearman")
    if metric not in {"spearman", "kendall_tau_b"}:
        raise ValueError("Descriptive correlation metric must be spearman or kendall_tau_b")
    summaries, correlations = [], []
    for name in selected:
        values = [paper["features"].get(name) for paper in papers]
        for value in values:
            _finite(value, name, nullable=True, allow_bool=True)
        observed = [value for value in values if value is not None]
        summaries.append({"feature": name, "n": len(observed), "n_missing": len(values) - len(observed),
                          "mean": mean(observed) if observed else None,
                          "median": median(observed) if observed else None,
                          "min": min(observed) if observed else None,
                          "max": max(observed) if observed else None,
                          "n_unique": len(set(observed))})
    compute = spec.get("correlations", len(selected) <= 40)
    if not isinstance(compute, bool):
        raise ValueError("correlations must be a boolean")
    if compute:
        for a, b in combinations(selected, 2):
            statistic = metric_result([paper["features"].get(a) for paper in papers],
                                      [paper["features"].get(b) for paper in papers], metric)
            correlations.append({"feature_a": a, "feature_b": b, **statistic})
    result = {"summaries": summaries, "correlations": correlations,
              "correlation_status": "computed" if compute else "skipped_large_feature_set",
              "reviewer_changes": [], "availability": []}
    if reviewer_records is not None:
        rows = list(reviewer_records)
        _validate_reviewer_rows(rows)
        groups = defaultdict(dict)
        availability = Counter()
        for row in rows:
            availability[row["phase"], row["construct"], row["availability"]] += 1
            if row["construct"] != "verdict" and row["availability"] == "ASSESSED":
                groups[row["doc_id"], row["persona"], row["construct"]][row["phase"]] = row["value"]
        for (doc_id, persona, construct), phases in sorted(groups.items()):
            if set(phases) == set(PHASES):
                delta = phases["POST"] - phases["PRE"]
                result["reviewer_changes"].append({"doc_id": doc_id, "persona": persona,
                    "construct": construct, "pre": phases["PRE"], "post": phases["POST"],
                    "delta": delta, "absolute_delta": abs(delta),
                    "direction": "up" if delta > 0 else "down" if delta < 0 else "unchanged"})
        result["availability"] = [{"phase": phase, "construct": construct,
                                   "availability": status, "n": count}
                                  for (phase, construct, status), count in sorted(availability.items())]
    return result


def analyze_persona_and_domain_patterns(reviewer_records, features=None, domain_records=None, analysis_spec=None):
    """Across-paper correlations and within-paper contrasts for encoded judgments.

    Verdicts remain categorical and are handled by construct_verdict_diagnostics.
    Persona correlations use jointly assessed papers, separately by phase and
    construct. Centered contrasts subtract the other assessed reviewers' mean on
    that paper, retaining the number of comparators. Adaptive persona assignment
    makes these descriptive comparisons, not causal persona effects.

    Optional domain_records is a list of {doc_id, domain}; each paper may have
    multiple distinct auditable tags. It yields descriptive construct summaries
    by tag, without inferring an intellectual tradition from reviewer names.
    """
    spec = {} if analysis_spec is None else dict(analysis_spec)
    if set(spec) - {"constructs", "metric"}:
        raise ValueError("Unknown persona/domain analysis options")
    constructs = spec.get("constructs", list(CONSTRUCTS[:-1]))
    if not isinstance(constructs, list) or set(constructs) - set(CONSTRUCTS[:-1]):
        raise ValueError("Persona constructs must be encoded non-verdict judgments")
    metric = spec.get("metric", "spearman")
    if metric not in {"spearman", "kendall_tau_b"}:
        raise ValueError("Persona metric must be spearman or kendall_tau_b")
    rows = list(reviewer_records)
    _validate_reviewer_rows(rows)
    observations = defaultdict(dict)
    by_paper = defaultdict(dict)
    for row in rows:
        if row["availability"] == "ASSESSED" and row["construct"] in constructs:
            observations[row["phase"], row["construct"], row["persona"]][row["doc_id"]] = row["value"]
            by_paper[row["doc_id"], row["phase"], row["construct"]][row["persona"]] = row["value"]
    correlations, contrasts = [], []
    for phase in PHASES:
        for construct in constructs:
            personas = sorted(persona for p, k, persona in observations if p == phase and k == construct)
            for a, b in combinations(personas, 2):
                va, vb = observations[phase, construct, a], observations[phase, construct, b]
                ids = sorted(set(va) & set(vb))
                statistic = metric_result([va[doc] for doc in ids], [vb[doc] for doc in ids], metric)
                correlations.append({"phase": phase, "construct": construct, "persona_a": a,
                    "persona_b": b, "n_shared": len(ids), **statistic,
                    "agreement_rate": mean(va[doc] == vb[doc] for doc in ids) if ids else None,
                    "mean_difference": mean(va[doc] - vb[doc] for doc in ids) if ids else None})
    for (doc_id, phase, construct), panel in sorted(by_paper.items()):
        for persona, value in sorted(panel.items()):
            others = [v for p, v in panel.items() if p != persona]
            contrasts.append({"doc_id": doc_id, "phase": phase, "construct": construct,
                              "persona": persona, "value": value, "n_other_reviewers": len(others),
                              "other_reviewers_mean": mean(others) if others else None,
                              "centered_difference": value - mean(others) if others else None})
    result = {"persona_correlations": correlations, "within_paper_contrasts": contrasts,
              "domain_summaries": [], "domain_status": "skipped_no_domain_records",
              "interpretation": "Descriptive associations under adaptive persona assignment; not causal effects."}
    if domain_records is not None:
        seen, tags = set(), defaultdict(set)
        known = {row["doc_id"] for row in rows}
        for row in domain_records:
            if not isinstance(row, dict):
                raise ValueError("Domain records must be mappings")
            doc_id = _nonempty_string(row.get("doc_id"), "Domain doc_id")
            domain = _nonempty_string(row.get("domain"), "Domain tag")
            if doc_id not in known or (doc_id, domain) in seen:
                raise ValueError("Domain tags contain an unknown paper or duplicate paper/domain")
            seen.add((doc_id, domain))
            tags[doc_id].add(domain)
        groups = defaultdict(dict)
        for (doc_id, phase, construct), panel in by_paper.items():
            for domain in tags[doc_id]:
                groups[domain, phase, construct][doc_id] = mean(panel.values())
        result["domain_summaries"] = [{"domain": domain, "phase": phase, "construct": construct,
                                      "n_papers": len(values), "mean_paper_assessment": mean(values.values())}
                                     for (domain, phase, construct), values in sorted(groups.items())]
        result["domain_status"] = "computed" if groups else "no_assessed_tagged_papers"
    return result
