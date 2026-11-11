# Proposed feature subset (140 paper-level columns)

Source: `/Users/rithikaiyengar/Downloads/co_econ/benchmark-11-5/exp-12/analysis/feature_selection_subset.py`

Subset version: `exp12-analysis-subset-v3`; base version: `exp12-features-v7`.

Confidence cutoff: recorded confidence strictly greater than 6.0; change with `--confidence-threshold`. The exact numeric cutoff is saved in `feature_dictionary.parameters.confidence_threshold`.

All original inventory features 604–637 are retained by name. Original # references below refer to the full exp12-features-v7 inventory. Existing values and parent column metadata are copied unchanged. Additional explanations/formulas below are exported in `feature_dictionary.subset.column_notes`; the remaining definitions come directly from the parent dictionary.

**Rating formulas.** For assessed ratings x_1,...,x_n within one phase, sum = sum(x_i), mean = sum(x_i)/n, and category count = number of assessed ratings in that category. NONE=0, LOW=1, MEDIUM=2, HIGH=3; repair scope uses 0 through 4. LOW excludes NONE. Category counts are zero even when n_valid=0; sums/means are null without assessed ratings. Read zero counts alongside n_valid. Ordinal means assume category spacing.

**Novelty pooling.** Pool assessed reviewer-domain ratings over methodological, empirical, theoretical and policy novelty, separately PRE/POST (up to 12 ratings per phase). Sum and mean use all available encoded ratings; category counts count domain ratings, not distinct reviewers. Abstentions/failures are excluded. Unequal coverage gives domains unequal weight in the pooled mean; n_valid reports coverage. No cross-phase averaging.

**Issue cohorts.** concern means substantive_flaw; insight_weakness is a separate issue type. C_family,phase denotes that family's primary cohort for the phase. PRE cohorts use original PRE issue IDs, types, severity and confidence. Primary POST cohorts use current issue type and severity and endpoint MAINTAIN/NARROW or NEW_POST, including IDs created in PRE, DEBATE or POST. POST confidence comes from the final POST stance, or creation for NEW_POST; earlier confidence is not backfilled. These are phase-specific cohorts, not matched ID sets. Reclassification, new IDs and changed source coverage can change a comparison. UNCERTAIN/UNKNOWN endpoints are not inferred to be resolved. Unit is issue_id; proposed duplicate links do not merge IDs.

**Issue weights.** For each issue in the specified primary cohort, w_i = severity_i * confidence_i / 100, with severity in [1,10] and confidence in [0,10]. Weighted count = sum(w_i); weighted mean = sum(w_i)/n, where n is that cohort's raw issue count, including zero-confidence issues. This mean is not sum(severity_i*confidence_i)/sum(confidence_i). No confidence cutoff applies to weighted summaries. Weights are descriptive scores, not calibrated probabilities. An observed empty cohort has raw/weighted count 0 and mean null; without a successful source assessment in that phase these summaries are null. Partial coverage summarizes only available records.

**Discovery cohorts.** New-ID cohorts are selected by phase_created and creation_issue_type; they retain original creation severity/confidence even after revision, reclassification or retraction. Their means are unweighted arithmetic means over the discovery cohort. Revisions do not create new IDs. Within each phase, concern and insight_weakness discovery types are disjoint subsets of issue (all types). Their two counts can be added to count those two types, but adding either to the all-issue count double-counts IDs. POST new observations are assertions, not proof of survival after debate.

**Late coverage.** Existing *_post_unknown_count includes only nonlate unknown endpoints. *_post_late_count covers nonretracted issues created or revised in the final debate round or POST, including unknown endpoints. Late is an overlapping timing diagnostic: it can also include active or uncertain issues and must not be added to those counts.

**Legacy debate fields.** Original #628–632 use arguments as recorded whenever rounds_requested>0 and arguments is present: zero can therefore occur when all attempted debate calls failed. They are null when zero rounds were requested or the arguments field is absent/null. Original #633–636 count each ID's latest available owner stance across DEBATE and POST; a latest DEBATE MAINTAIN can count even without a final POST assessment. These and original #637 return integer zero for empty event arrays without a call-success gate. #637 counts every revision event in DEBATE and POST, not distinct revised IDs. Use source-call coverage and the DEBATE-phase-only counts to interpret these legacy fields.

**DEBATE-only activity.** New debate_phase_* counts restrict canonical revision/update events to phase=DEBATE. Event counts can count the same issue more than once; *_issue_count counts distinct issue IDs. Counts are null without any successful DEBATE assessment, recorded DEBATE event or argument; observed empty counts are zero. Partial source coverage describes recorded activity only.

**Dependencies between features.** With assessed ratings present, a pooled sum equals its mean times n_valid; at fixed coverage these two features are redundant. Likewise weighted issue count equals weighted mean times raw count when the cohort is nonempty and observed. Source-call coverage values are shared across the three discovery families within each phase; they do not count family-specific calls.

**Analysis roles.** Primary/secondary groups follow the proposed analysis outline. Coverage columns diagnose availability. These labels do not automatically configure primary hypotheses in evaluation_analysis.py.

**Weighted example.** Two issues with (severity, confidence) = (8,9) and (6,5) have weights 0.72 and 0.30: raw count 2, weighted count 1.02, weighted mean 0.51. Both enter the weighted summaries. At the selected cutoff >6.0, their confident count is 1.

**Repair survival.** E = reviewers assessed PRE>0; O = E with assessed POST (including POST=0); S = O with POST>0. rate = |S|/|O|; coverage_rate = |O|/|E|; complete_rate = |S|/|E| only if |O|=|E|. When E is nonempty and every eligible reviewer abstains POST, coverage is 0, while rate and complete_rate are null. Empty E gives null rates. Counts are still integers, including zero.

**Spread and differences.** Range = max(x)-min(x). Population SD = sqrt(sum((x-mean(x))^2)/n), using assessed reviewers in that construct/phase; both are null for n<2. Every added difference is POST minus PRE, null if either operand is null. A negative spread difference means less observed dispersion. The assessed reviewer sets may change; these differences do not restrict to matched personas. The confident-concern difference compares C_concern,post and C_concern,pre as defined under Issue cohorts, not matched issue IDs; a decrease alone does not show resolution.

**Discovery availability.** Discovery cohorts are observed if there is a successful source assessment in that phase or at least one ID of any type created in that phase; empty observed counts are 0 and means are null. Without either source of evidence, counts/means are null, except that zero requested debate rounds give DEBATE-new count 0. `n_source_calls_expected` means assessments actually called (including failed calls), not all scheduled opportunities; retries count once. These coverage counts are shared across discovery families.

**Zero versus missing.** Reviewer category counts can be 0 with n_valid=0; this is not evidence that all reviewers rated another category. Issue raw/weighted counts can be 0 for an observed empty cohort, while unavailable phases are null. Legacy debate fields and DEBATE-only fields have different availability rules, specified above and per entry. Missingness is not replaced with substantive zero. Reviewer rows, issue snapshots, editor outcome and provenance remain separate outputs.

## Primary review summaries (20)

1. `novelty_pooled_pre_sum` — Sum of assessed encoded novelty ratings across all four domains and all reviewers (up to 12 ratings). Added by subset script.
    Availability: Sum/mean null without assessed ratings; category counts zero with n_valid=0. Abstentions and unavailable ratings are excluded, not assigned zero.
    Source: reviewer_features: all four novelty domains, phase=PRE; assessed reviewer-domain ratings

2. `novelty_pooled_pre_mean` — Pooled novelty sum / n_valid across assessed reviewer-domain ratings (NONE=0, LOW=1, MEDIUM=2, HIGH=3). Added by subset script.
    Availability: Sum/mean null without assessed ratings; category counts zero with n_valid=0. Abstentions and unavailable ratings are excluded, not assigned zero.
    Source: reviewer_features: all four novelty domains, phase=PRE; assessed reviewer-domain ratings

3. `novelty_pooled_pre_count_low` — Number of assessed LOW novelty domain ratings across all reviewers; a reviewer may contribute up to four. Added by subset script.
    Availability: Sum/mean null without assessed ratings; category counts zero with n_valid=0. Abstentions and unavailable ratings are excluded, not assigned zero.
    Source: reviewer_features: all four novelty domains, phase=PRE; assessed reviewer-domain ratings

4. `novelty_pooled_pre_count_high` — Number of assessed HIGH novelty domain ratings across all reviewers; a reviewer may contribute up to four. Added by subset script.
    Availability: Sum/mean null without assessed ratings; category counts zero with n_valid=0. Abstentions and unavailable ratings are excluded, not assigned zero.
    Source: reviewer_features: all four novelty domains, phase=PRE; assessed reviewer-domain ratings

5. `novelty_pooled_post_sum` — Sum of assessed encoded novelty ratings across all four domains and all reviewers (up to 12 ratings). Added by subset script.
    Availability: Sum/mean null without assessed ratings; category counts zero with n_valid=0. Abstentions and unavailable ratings are excluded, not assigned zero.
    Source: reviewer_features: all four novelty domains, phase=POST; assessed reviewer-domain ratings

6. `novelty_pooled_post_mean` — Pooled novelty sum / n_valid across assessed reviewer-domain ratings (NONE=0, LOW=1, MEDIUM=2, HIGH=3). Added by subset script.
    Availability: Sum/mean null without assessed ratings; category counts zero with n_valid=0. Abstentions and unavailable ratings are excluded, not assigned zero.
    Source: reviewer_features: all four novelty domains, phase=POST; assessed reviewer-domain ratings

7. `novelty_pooled_post_count_low` — Number of assessed LOW novelty domain ratings across all reviewers; a reviewer may contribute up to four. Added by subset script.
    Availability: Sum/mean null without assessed ratings; category counts zero with n_valid=0. Abstentions and unavailable ratings are excluded, not assigned zero.
    Source: reviewer_features: all four novelty domains, phase=POST; assessed reviewer-domain ratings

8. `novelty_pooled_post_count_high` — Number of assessed HIGH novelty domain ratings across all reviewers; a reviewer may contribute up to four. Added by subset script.
    Availability: Sum/mean null without assessed ratings; category counts zero with n_valid=0. Abstentions and unavailable ratings are excluded, not assigned zero.
    Source: reviewer_features: all four novelty domains, phase=POST; assessed reviewer-domain ratings

9. `insight_pre_mean` — mean of available insight_pre values; no averaging across PRE/POST. Original #154.
    Source: reviewer_features: construct=insight, phase=PRE

10. `insight_pre_count_low` — Count of assessed category LOW. Original #166.
    Source: reviewer_features: construct=insight, phase=PRE

11. `insight_pre_count_high` — Count of assessed category HIGH. Original #170.
    Source: reviewer_features: construct=insight, phase=PRE

12. `insight_post_mean` — mean of available insight_post values; no averaging across PRE/POST. Original #173.
    Source: reviewer_features: construct=insight, phase=POST

13. `insight_post_count_low` — Count of assessed category LOW. Original #185.
    Source: reviewer_features: construct=insight, phase=POST

14. `insight_post_count_high` — Count of assessed category HIGH. Original #189.
    Source: reviewer_features: construct=insight, phase=POST

15. `repair_scope_pre_mean` — mean of available repair_scope_pre values; no averaging across PRE/POST. Original #192.
    Source: reviewer_features: construct=repair_scope, phase=PRE

16. `repair_scope_pre_count_0` — Count of assessed category 0. Original #202.
    Source: reviewer_features: construct=repair_scope, phase=PRE

17. `repair_scope_pre_count_4` — Count of assessed category 4. Original #210.
    Source: reviewer_features: construct=repair_scope, phase=PRE

18. `repair_scope_post_mean` — mean of available repair_scope_post values; no averaging across PRE/POST. Original #213.
    Source: reviewer_features: construct=repair_scope, phase=POST

19. `repair_scope_post_count_0` — Count of assessed category 0. Original #223.
    Source: reviewer_features: construct=repair_scope, phase=POST

20. `repair_scope_post_count_4` — Count of assessed category 4. Original #231.
    Source: reviewer_features: construct=repair_scope, phase=POST


## Primary repair survival (7)

21. `repair_scope_survival_n_eligible` — Reviewers with an assessed PRE repair scope greater than zero. Original #233.
    Formula: `|E|`. Availability: Integer count, including zero.
    Source: Matched reviewer_features repair_scope, same persona; PRE>0 cohort.

22. `repair_scope_survival_n_observed` — Eligible reviewers with an assessed POST repair scope, including POST=0. Original #234.
    Formula: `|O|`. Availability: Integer count, including zero.
    Source: Matched reviewer_features repair_scope, same persona; PRE>0 cohort.

23. `repair_scope_survival_n_survived` — Eligible reviewers whose assessed POST repair scope remains greater than zero. Original #235.
    Formula: `|S|`. Availability: Integer count, including zero.
    Source: Matched reviewer_features repair_scope, same persona; PRE>0 cohort.

24. `repair_scope_survival_n_unknown` — Eligible reviewers without an assessed POST repair scope (including abstention/failure). Original #236.
    Formula: `|E| - |O|`. Availability: Integer count, including zero.
    Source: Matched reviewer_features repair_scope, same persona; PRE>0 cohort.

25. `repair_scope_survival_coverage_rate` — Fraction of eligible reviewers with assessed POST endpoints. Original #237.
    Formula: `|O| / |E|`. Availability: Null if |E|=0; zero when E is nonempty but no POST endpoint is assessed.
    Source: Matched reviewer_features repair_scope, same persona; PRE>0 cohort.

26. `repair_scope_survival_rate` — Fraction still requiring repair among eligible reviewers with assessed POST endpoints. Original #238.
    Formula: `|S| / |O|`. Availability: Null if |O|=0.
    Source: Matched reviewer_features repair_scope, same persona; PRE>0 cohort.

27. `repair_scope_survival_complete_rate` — Fraction still requiring repair in the entire eligible cohort, available only with complete POST coverage. Original #239.
    Formula: `|S| / |E|, only if |O| = |E|`. Availability: Null if |E|=0 or any eligible POST endpoint is unassessed.
    Source: Matched reviewer_features repair_scope, same persona; PRE>0 cohort.


## Primary concerns (9)

28. `concern_pre_count` — Number of original PRE substantive_flaw IDs. Original #240.
    Formula: `|C_concern,pre|`. Availability: Null without any successful PRE source assessment; zero for an observed empty cohort.
    Source: issue_features: concern_pre; units are issue IDs, not adjudicated unique concerns.

29. `concern_pre_severity_confidence_weighted_count` — Sum of per-issue severity*confidence/100 weights in the primary substantive-flaw cohort; original PRE or current POST judgments. Original #253.
    Availability: Null without successful source reviewer calls; observed empty count is zero and mean is null. Uncertain/unknown POST endpoints are excluded; see separate counts and source coverage.
    Source: issue_features: concern_pre; units are issue IDs, not adjudicated unique concerns.

30. `concern_pre_severity_confidence_weighted_mean` — Arithmetic mean of per-issue severity*confidence/100 weights in the primary substantive-flaw cohort; original PRE or current POST judgments. Original #254.
    Availability: Null without successful source reviewer calls; observed empty count is zero and mean is null. Uncertain/unknown POST endpoints are excluded; see separate counts and source coverage.
    Source: issue_features: concern_pre; units are issue IDs, not adjudicated unique concerns.

31. `concern_pre_n_confidence_above_threshold` — Number of original PRE substantive_flaw IDs with recorded confidence strictly greater than 6.0; no severity filter. Original #252.
    Formula: `sum(1[confidence_i > 6.0] for i in C_concern,pre)`. Availability: Null without any successful PRE source assessment; zero for an observed empty cohort. Missing confidence is not imputed.
    Source: issue_features: concern_pre; units are issue IDs, not adjudicated unique concerns.

32. `concern_post_count` — Number of current substantive_flaw IDs with MAINTAIN/NARROW/NEW_POST endpoints, from any creation phase. Original #255.
    Formula: `|C_concern,post|`. Availability: Null without any successful POST source assessment; zero for an observed empty cohort.
    Source: issue_features: concern_post; units are issue IDs, not adjudicated unique concerns.

33. `concern_post_severity_confidence_weighted_count` — Sum of per-issue severity*confidence/100 weights in the primary substantive-flaw cohort; original PRE or current POST judgments. Original #268.
    Availability: Null without successful source reviewer calls; observed empty count is zero and mean is null. Uncertain/unknown POST endpoints are excluded; see separate counts and source coverage.
    Source: issue_features: concern_post; units are issue IDs, not adjudicated unique concerns.

34. `concern_post_severity_confidence_weighted_mean` — Arithmetic mean of per-issue severity*confidence/100 weights in the primary substantive-flaw cohort; original PRE or current POST judgments. Original #269.
    Availability: Null without successful source reviewer calls; observed empty count is zero and mean is null. Uncertain/unknown POST endpoints are excluded; see separate counts and source coverage.
    Source: issue_features: concern_post; units are issue IDs, not adjudicated unique concerns.

35. `concern_post_n_confidence_above_threshold` — Number of current substantive_flaw IDs with MAINTAIN/NARROW/NEW_POST endpoints, from any creation phase with recorded confidence strictly greater than 6.0; no severity filter. Original #267.
    Formula: `sum(1[confidence_i > 6.0] for i in C_concern,post)`. Availability: Null without any successful POST source assessment; zero for an observed empty cohort. Missing confidence is not imputed.
    Source: issue_features: concern_post; units are issue IDs, not adjudicated unique concerns.

36. `concern_n_confidence_above_threshold_post_minus_pre` — Change in confident substantive-flaw counts across the two primary phase-specific cohorts; new/reclassified IDs, confidence changes and coverage can affect it. It is not a matched-issue survival rate. Added by subset script.
    Formula: `concern_post_n_confidence_above_threshold - concern_pre_n_confidence_above_threshold`. Availability: Null if either operand is null; positive means more confident concerns in the observed POST cohort.
    Source: features: concern_post_n_confidence_above_threshold, concern_pre_n_confidence_above_threshold


## Secondary insight weaknesses (6)

37. `insight_weakness_pre_count` — Number of original PRE insight_weakness IDs. Original #340.
    Formula: `|C_insight_weakness,pre|`. Availability: Null without any successful PRE source assessment; zero for an observed empty cohort.
    Source: issue_features: insight_weakness_pre; units are issue IDs, not adjudicated unique concerns.

38. `insight_weakness_pre_severity_confidence_weighted_count` — Sum of per-issue severity*confidence/100 weights in the primary insight-weakness cohort; original PRE or current POST judgments. Original #352.
    Availability: Null without successful source reviewer calls; observed empty count is zero and mean is null. Uncertain/unknown POST endpoints are excluded; see separate counts and source coverage.
    Source: issue_features: insight_weakness_pre; units are issue IDs, not adjudicated unique concerns.

39. `insight_weakness_pre_severity_confidence_weighted_mean` — Arithmetic mean of per-issue severity*confidence/100 weights in the primary insight-weakness cohort; original PRE or current POST judgments. Original #353.
    Availability: Null without successful source reviewer calls; observed empty count is zero and mean is null. Uncertain/unknown POST endpoints are excluded; see separate counts and source coverage.
    Source: issue_features: insight_weakness_pre; units are issue IDs, not adjudicated unique concerns.

40. `insight_weakness_post_count` — Number of current insight_weakness IDs with MAINTAIN/NARROW/NEW_POST endpoints, from any creation phase. Original #354.
    Formula: `|C_insight_weakness,post|`. Availability: Null without any successful POST source assessment; zero for an observed empty cohort.
    Source: issue_features: insight_weakness_post; units are issue IDs, not adjudicated unique concerns.

41. `insight_weakness_post_severity_confidence_weighted_count` — Sum of per-issue severity*confidence/100 weights in the primary insight-weakness cohort; original PRE or current POST judgments. Original #366.
    Availability: Null without successful source reviewer calls; observed empty count is zero and mean is null. Uncertain/unknown POST endpoints are excluded; see separate counts and source coverage.
    Source: issue_features: insight_weakness_post; units are issue IDs, not adjudicated unique concerns.

42. `insight_weakness_post_severity_confidence_weighted_mean` — Arithmetic mean of per-issue severity*confidence/100 weights in the primary insight-weakness cohort; original PRE or current POST judgments. Original #367.
    Availability: Null without successful source reviewer calls; observed empty count is zero and mean is null. Uncertain/unknown POST endpoints are excluded; see separate counts and source coverage.
    Source: issue_features: insight_weakness_post; units are issue IDs, not adjudicated unique concerns.


## Secondary discovery (18)

43. `issue_debate_new_count` — Newly issued IDs, excluding revisions; partial source coverage does not establish absence. Original #434.
    Source: issue_features: phase_created=DEBATE, creation_issue_type=all; original creation judgments.

44. `issue_debate_new_severity_mean` — mean of available issue_debate_new_severity values; no averaging across PRE/POST. Original #438.
    Source: issue_features: phase_created=DEBATE, creation_issue_type=all; original creation judgments.

45. `issue_debate_new_confidence_mean` — mean of available issue_debate_new_confidence values; no averaging across PRE/POST. Original #443.
    Source: issue_features: phase_created=DEBATE, creation_issue_type=all; original creation judgments.

46. `concern_debate_new_count` — Newly issued IDs, excluding revisions; partial source coverage does not establish absence. Original #480.
    Source: issue_features: phase_created=DEBATE, creation_issue_type=substantive_flaw; original creation judgments.

47. `concern_debate_new_severity_mean` — mean of available concern_debate_new_severity values; no averaging across PRE/POST. Original #484.
    Source: issue_features: phase_created=DEBATE, creation_issue_type=substantive_flaw; original creation judgments.

48. `concern_debate_new_confidence_mean` — mean of available concern_debate_new_confidence values; no averaging across PRE/POST. Original #489.
    Source: issue_features: phase_created=DEBATE, creation_issue_type=substantive_flaw; original creation judgments.

49. `insight_weakness_debate_new_count` — Newly issued IDs, excluding revisions; partial source coverage does not establish absence. Original #519.
    Source: issue_features: phase_created=DEBATE, creation_issue_type=insight_weakness; original creation judgments.

50. `insight_weakness_debate_new_severity_mean` — mean of available insight_weakness_debate_new_severity values; no averaging across PRE/POST. Original #523.
    Source: issue_features: phase_created=DEBATE, creation_issue_type=insight_weakness; original creation judgments.

51. `insight_weakness_debate_new_confidence_mean` — mean of available insight_weakness_debate_new_confidence values; no averaging across PRE/POST. Original #528.
    Source: issue_features: phase_created=DEBATE, creation_issue_type=insight_weakness; original creation judgments.

52. `issue_post_new_count` — Newly issued IDs, excluding revisions; partial source coverage does not establish absence. Original #558.
    Source: issue_features: phase_created=POST, creation_issue_type=all; original creation judgments.

53. `issue_post_new_severity_mean` — mean of available issue_post_new_severity values; no averaging across PRE/POST. Original #562.
    Source: issue_features: phase_created=POST, creation_issue_type=all; original creation judgments.

54. `issue_post_new_confidence_mean` — mean of available issue_post_new_confidence values; no averaging across PRE/POST. Original #567.
    Source: issue_features: phase_created=POST, creation_issue_type=all; original creation judgments.

55. `concern_post_new_count` — Newly issued IDs, excluding revisions; partial source coverage does not establish absence. Original #578.
    Source: issue_features: phase_created=POST, creation_issue_type=substantive_flaw; original creation judgments.

56. `concern_post_new_severity_mean` — mean of available concern_post_new_severity values; no averaging across PRE/POST. Original #582.
    Source: issue_features: phase_created=POST, creation_issue_type=substantive_flaw; original creation judgments.

57. `concern_post_new_confidence_mean` — mean of available concern_post_new_confidence values; no averaging across PRE/POST. Original #587.
    Source: issue_features: phase_created=POST, creation_issue_type=substantive_flaw; original creation judgments.

58. `insight_weakness_post_new_count` — Newly issued IDs, excluding revisions; partial source coverage does not establish absence. Original #591.
    Source: issue_features: phase_created=POST, creation_issue_type=insight_weakness; original creation judgments.

59. `insight_weakness_post_new_severity_mean` — mean of available insight_weakness_post_new_severity values; no averaging across PRE/POST. Original #595.
    Source: issue_features: phase_created=POST, creation_issue_type=insight_weakness; original creation judgments.

60. `insight_weakness_post_new_confidence_mean` — mean of available insight_weakness_post_new_confidence values; no averaging across PRE/POST. Original #600.
    Source: issue_features: phase_created=POST, creation_issue_type=insight_weakness; original creation judgments.


## Secondary retractions (2)

61. `concern_post_retracted_count` — Current substantive_flaw IDs whose latest available owner update is RETRACT, whether in DEBATE or POST; not a count of every historical retraction event. Original #311.
    Formula: `count(current type = substantive_flaw and final endpoint = RETRACTED)`. Availability: Null without a successful PRE or POST assessment; otherwise an integer count including zero.
    Source: POST issue_features current version, separately from proof of correctness.

62. `insight_weakness_post_retracted_count` — Current insight_weakness IDs whose latest available owner update is RETRACT, whether in DEBATE or POST; not a count of every historical retraction event. Original #406.
    Formula: `count(current type = insight_weakness and final endpoint = RETRACTED)`. Availability: Null without a successful PRE or POST assessment; otherwise an integer count including zero.
    Source: POST issue_features current version, separately from proof of correctness.


## Secondary reviewer spread (36)

63. `novelty_methodological_pre_range` — Max minus min across assessed reviewers; null below two assessed. Original #604.
    Source: reviewer_features: construct=novelty_methodological, phase=PRE; assessed judgments only

64. `novelty_methodological_pre_sd` — Population SD across assessed reviewers; null below two assessed. Original #605.
    Source: reviewer_features: construct=novelty_methodological, phase=PRE; assessed judgments only

65. `novelty_methodological_post_range` — Max minus min across assessed reviewers; null below two assessed. Original #606.
    Source: reviewer_features: construct=novelty_methodological, phase=POST; assessed judgments only

66. `novelty_methodological_post_sd` — Population SD across assessed reviewers; null below two assessed. Original #607.
    Source: reviewer_features: construct=novelty_methodological, phase=POST; assessed judgments only

67. `novelty_empirical_pre_range` — Max minus min across assessed reviewers; null below two assessed. Original #608.
    Source: reviewer_features: construct=novelty_empirical, phase=PRE; assessed judgments only

68. `novelty_empirical_pre_sd` — Population SD across assessed reviewers; null below two assessed. Original #609.
    Source: reviewer_features: construct=novelty_empirical, phase=PRE; assessed judgments only

69. `novelty_empirical_post_range` — Max minus min across assessed reviewers; null below two assessed. Original #610.
    Source: reviewer_features: construct=novelty_empirical, phase=POST; assessed judgments only

70. `novelty_empirical_post_sd` — Population SD across assessed reviewers; null below two assessed. Original #611.
    Source: reviewer_features: construct=novelty_empirical, phase=POST; assessed judgments only

71. `novelty_theoretical_pre_range` — Max minus min across assessed reviewers; null below two assessed. Original #612.
    Source: reviewer_features: construct=novelty_theoretical, phase=PRE; assessed judgments only

72. `novelty_theoretical_pre_sd` — Population SD across assessed reviewers; null below two assessed. Original #613.
    Source: reviewer_features: construct=novelty_theoretical, phase=PRE; assessed judgments only

73. `novelty_theoretical_post_range` — Max minus min across assessed reviewers; null below two assessed. Original #614.
    Source: reviewer_features: construct=novelty_theoretical, phase=POST; assessed judgments only

74. `novelty_theoretical_post_sd` — Population SD across assessed reviewers; null below two assessed. Original #615.
    Source: reviewer_features: construct=novelty_theoretical, phase=POST; assessed judgments only

75. `novelty_policy_pre_range` — Max minus min across assessed reviewers; null below two assessed. Original #616.
    Source: reviewer_features: construct=novelty_policy, phase=PRE; assessed judgments only

76. `novelty_policy_pre_sd` — Population SD across assessed reviewers; null below two assessed. Original #617.
    Source: reviewer_features: construct=novelty_policy, phase=PRE; assessed judgments only

77. `novelty_policy_post_range` — Max minus min across assessed reviewers; null below two assessed. Original #618.
    Source: reviewer_features: construct=novelty_policy, phase=POST; assessed judgments only

78. `novelty_policy_post_sd` — Population SD across assessed reviewers; null below two assessed. Original #619.
    Source: reviewer_features: construct=novelty_policy, phase=POST; assessed judgments only

79. `insight_pre_range` — Max minus min across assessed reviewers; null below two assessed. Original #620.
    Source: reviewer_features: construct=insight, phase=PRE; assessed judgments only

80. `insight_pre_sd` — Population SD across assessed reviewers; null below two assessed. Original #621.
    Source: reviewer_features: construct=insight, phase=PRE; assessed judgments only

81. `insight_post_range` — Max minus min across assessed reviewers; null below two assessed. Original #622.
    Source: reviewer_features: construct=insight, phase=POST; assessed judgments only

82. `insight_post_sd` — Population SD across assessed reviewers; null below two assessed. Original #623.
    Source: reviewer_features: construct=insight, phase=POST; assessed judgments only

83. `repair_scope_pre_range` — Max minus min across assessed reviewers; null below two assessed. Original #624.
    Source: reviewer_features: construct=repair_scope, phase=PRE; assessed judgments only

84. `repair_scope_pre_sd` — Population SD across assessed reviewers; null below two assessed. Original #625.
    Source: reviewer_features: construct=repair_scope, phase=PRE; assessed judgments only

85. `repair_scope_post_range` — Max minus min across assessed reviewers; null below two assessed. Original #626.
    Source: reviewer_features: construct=repair_scope, phase=POST; assessed judgments only

86. `repair_scope_post_sd` — Population SD across assessed reviewers; null below two assessed. Original #627.
    Source: reviewer_features: construct=repair_scope, phase=POST; assessed judgments only

87. `novelty_methodological_range_post_minus_pre` — novelty_methodological_post_range minus novelty_methodological_pre_range; positive values indicate an increase. Added by subset script.
    Availability: Null if either PRE or POST value is null; no imputation.
    Source: features: novelty_methodological_post_range, novelty_methodological_pre_range

88. `novelty_methodological_sd_post_minus_pre` — novelty_methodological_post_sd minus novelty_methodological_pre_sd; positive values indicate an increase. Added by subset script.
    Availability: Null if either PRE or POST value is null; no imputation.
    Source: features: novelty_methodological_post_sd, novelty_methodological_pre_sd

89. `novelty_empirical_range_post_minus_pre` — novelty_empirical_post_range minus novelty_empirical_pre_range; positive values indicate an increase. Added by subset script.
    Availability: Null if either PRE or POST value is null; no imputation.
    Source: features: novelty_empirical_post_range, novelty_empirical_pre_range

90. `novelty_empirical_sd_post_minus_pre` — novelty_empirical_post_sd minus novelty_empirical_pre_sd; positive values indicate an increase. Added by subset script.
    Availability: Null if either PRE or POST value is null; no imputation.
    Source: features: novelty_empirical_post_sd, novelty_empirical_pre_sd

91. `novelty_theoretical_range_post_minus_pre` — novelty_theoretical_post_range minus novelty_theoretical_pre_range; positive values indicate an increase. Added by subset script.
    Availability: Null if either PRE or POST value is null; no imputation.
    Source: features: novelty_theoretical_post_range, novelty_theoretical_pre_range

92. `novelty_theoretical_sd_post_minus_pre` — novelty_theoretical_post_sd minus novelty_theoretical_pre_sd; positive values indicate an increase. Added by subset script.
    Availability: Null if either PRE or POST value is null; no imputation.
    Source: features: novelty_theoretical_post_sd, novelty_theoretical_pre_sd

93. `novelty_policy_range_post_minus_pre` — novelty_policy_post_range minus novelty_policy_pre_range; positive values indicate an increase. Added by subset script.
    Availability: Null if either PRE or POST value is null; no imputation.
    Source: features: novelty_policy_post_range, novelty_policy_pre_range

94. `novelty_policy_sd_post_minus_pre` — novelty_policy_post_sd minus novelty_policy_pre_sd; positive values indicate an increase. Added by subset script.
    Availability: Null if either PRE or POST value is null; no imputation.
    Source: features: novelty_policy_post_sd, novelty_policy_pre_sd

95. `insight_range_post_minus_pre` — insight_post_range minus insight_pre_range; positive values indicate an increase. Added by subset script.
    Availability: Null if either PRE or POST value is null; no imputation.
    Source: features: insight_post_range, insight_pre_range

96. `insight_sd_post_minus_pre` — insight_post_sd minus insight_pre_sd; positive values indicate an increase. Added by subset script.
    Availability: Null if either PRE or POST value is null; no imputation.
    Source: features: insight_post_sd, insight_pre_sd

97. `repair_scope_range_post_minus_pre` — repair_scope_post_range minus repair_scope_pre_range; positive values indicate an increase. Added by subset script.
    Availability: Null if either PRE or POST value is null; no imputation.
    Source: features: repair_scope_post_range, repair_scope_pre_range

98. `repair_scope_sd_post_minus_pre` — repair_scope_post_sd minus repair_scope_pre_sd; positive values indicate an increase. Added by subset script.
    Availability: Null if either PRE or POST value is null; no imputation.
    Source: features: repair_scope_post_sd, repair_scope_pre_sd


## Secondary debate activity (14)

99. `debate_challenge_count` — Number of recorded CHALLENGE arguments across debate rounds. Original #628.
    Formula: `count(argument.action = CHALLENGE)`. Availability: Null if rounds_requested=0 or arguments is absent/null; otherwise zero for an empty arguments array, including when debate calls failed.
    Source: arguments: action counts across all debate rounds

100. `debate_defense_count` — Number of recorded DEFENSE arguments across debate rounds. Original #629.
    Formula: `count(argument.action = DEFENSE)`. Availability: Null if rounds_requested=0 or arguments is absent/null; otherwise zero for an empty arguments array, including when debate calls failed.
    Source: arguments: action counts across all debate rounds

101. `debate_concession_count` — Number of recorded CONCESSION arguments across debate rounds. Original #630.
    Formula: `count(argument.action = CONCESSION)`. Availability: Null if rounds_requested=0 or arguments is absent/null; otherwise zero for an empty arguments array, including when debate calls failed.
    Source: arguments: action counts across all debate rounds

102. `debate_question_count` — Number of recorded QUESTION arguments across debate rounds. Original #631.
    Formula: `count(argument.action = QUESTION)`. Availability: Null if rounds_requested=0 or arguments is absent/null; otherwise zero for an empty arguments array, including when debate calls failed.
    Source: arguments: action counts across all debate rounds

103. `debate_argument_count` — Total recorded debate arguments across all action types. Original #632.
    Formula: `len(arguments)`. Availability: Null if rounds_requested=0 or arguments is absent/null; otherwise zero for an empty arguments array, including when debate calls failed.
    Source: arguments: action counts across all debate rounds

104. `debate_final_retract_count` — Issue IDs whose latest recorded owner update (by event_sequence, across DEBATE/POST) is RETRACT; no final POST update is required. Original #633.
    Formula: `count(latest_update_per_issue.stance = RETRACT)`. Availability: Zero if no updates have this latest stance; no call-success gate. IDs with no update do not enter any stance count.
    Source: issue_updates: latest owner stance per issue ID

105. `debate_final_narrow_count` — Issue IDs whose latest recorded owner update (by event_sequence, across DEBATE/POST) is NARROW; no final POST update is required. Original #634.
    Formula: `count(latest_update_per_issue.stance = NARROW)`. Availability: Zero if no updates have this latest stance; no call-success gate. IDs with no update do not enter any stance count.
    Source: issue_updates: latest owner stance per issue ID

106. `debate_final_maintain_count` — Issue IDs whose latest recorded owner update (by event_sequence, across DEBATE/POST) is MAINTAIN; no final POST update is required. Original #635.
    Formula: `count(latest_update_per_issue.stance = MAINTAIN)`. Availability: Zero if no updates have this latest stance; no call-success gate. IDs with no update do not enter any stance count.
    Source: issue_updates: latest owner stance per issue ID

107. `debate_final_uncertain_count` — Issue IDs whose latest recorded owner update (by event_sequence, across DEBATE/POST) is UNCERTAIN; no final POST update is required. Original #636.
    Formula: `count(latest_update_per_issue.stance = UNCERTAIN)`. Availability: Zero if no updates have this latest stance; no call-success gate. IDs with no update do not enter any stance count.
    Source: issue_updates: latest owner stance per issue ID

108. `debate_revision_count` — All recorded revision events in DEBATE and POST, counting repeated revisions of an ID separately. Original #637.
    Formula: `len(issue_revisions)`. Availability: Zero for an empty revision array; no call-success gate.
    Source: issue_revisions

109. `debate_phase_revision_count` — Number of revision events recorded in DEBATE; excludes POST events and can count an issue more than once. Added by subset script.
    Availability: Null without successful DEBATE assessments or recorded DEBATE events/arguments; zero for observed debate without these events. Partial coverage describes recorded activity only.
    Source: issue_revisions: phase=DEBATE

110. `debate_phase_revised_issue_count` — Number of distinct issue IDs revised during DEBATE; excludes POST events and counts each ID once. Added by subset script.
    Availability: Null without successful DEBATE assessments or recorded DEBATE events/arguments; zero for observed debate without these events. Partial coverage describes recorded activity only.
    Source: issue_revisions: phase=DEBATE

111. `debate_phase_retraction_count` — Number of retraction events recorded in DEBATE; excludes POST events and can count an issue more than once. Added by subset script.
    Availability: Null without successful DEBATE assessments or recorded DEBATE events/arguments; zero for observed debate without these events. Partial coverage describes recorded activity only.
    Source: issue_updates: phase=DEBATE, stance=RETRACT

112. `debate_phase_retracted_issue_count` — Number of distinct issue IDs retracted during DEBATE; excludes POST events and counts each ID once. Added by subset script.
    Availability: Null without successful DEBATE assessments or recorded DEBATE events/arguments; zero for observed debate without these events. Partial coverage describes recorded activity only.
    Source: issue_updates: phase=DEBATE, stance=RETRACT


## Coverage (28)

113. `novelty_pooled_pre_n_valid` — Number of assessed novelty reviewer-domain ratings, from 0 through 12. Added by subset script.
    Availability: Sum/mean null without assessed ratings; category counts zero with n_valid=0. Abstentions and unavailable ratings are excluded, not assigned zero.
    Source: reviewer_features: all four novelty domains, phase=PRE; assessed reviewer-domain ratings

114. `novelty_pooled_post_n_valid` — Number of assessed novelty reviewer-domain ratings, from 0 through 12. Added by subset script.
    Availability: Sum/mean null without assessed ratings; category counts zero with n_valid=0. Abstentions and unavailable ratings are excluded, not assigned zero.
    Source: reviewer_features: all four novelty domains, phase=POST; assessed reviewer-domain ratings

115. `insight_pre_n_valid` — Number of ASSESSED judgments, separately from substantive zero. Original #159.
    Source: reviewer_features: construct=insight, phase=PRE

116. `insight_post_n_valid` — Number of ASSESSED judgments, separately from substantive zero. Original #178.
    Source: reviewer_features: construct=insight, phase=POST

117. `repair_scope_pre_n_valid` — Number of ASSESSED judgments, separately from substantive zero. Original #197.
    Source: reviewer_features: construct=repair_scope, phase=PRE

118. `repair_scope_post_n_valid` — Number of ASSESSED judgments, separately from substantive zero. Original #218.
    Source: reviewer_features: construct=repair_scope, phase=POST

119. `concern_pre_n_reviewers_valid` — Successful source reviewer calls; partial summaries describe only observed records. Original #309.
    Source: pre_assessments

120. `concern_post_n_reviewers_valid` — Successful source reviewer calls; partial summaries describe only observed records. Original #310.
    Source: post_assessments

121. `insight_weakness_pre_n_reviewers_valid` — Successful source reviewer calls; partial summaries describe only observed records. Original #404.
    Source: pre_assessments

122. `insight_weakness_post_n_reviewers_valid` — Successful source reviewer calls; partial summaries describe only observed records. Original #405.
    Source: post_assessments

123. `concern_post_uncertain_count` — Current substantive_flaw IDs satisfying endpoint = UNCERTAIN (including late issues). Late cohorts overlap active/uncertain cohorts. Original #283.
    Formula: `count(current type = substantive_flaw and endpoint = UNCERTAIN (including late issues))`. Availability: Null without a successful POST assessment; zero when available with no qualifying IDs.
    Source: issue_features: concern_post_uncertain; units are issue IDs, not adjudicated unique concerns.

124. `concern_post_unknown_count` — Current substantive_flaw IDs satisfying endpoint = UNKNOWN and late = false. Late cohorts overlap active/uncertain cohorts. Original #296.
    Formula: `count(current type = substantive_flaw and endpoint = UNKNOWN and late = false)`. Availability: Null without a successful PRE assessment; zero when available with no qualifying IDs.
    Source: issue_features: concern_post_unknown; units are issue IDs, not adjudicated unique concerns.

125. `concern_post_late_count` — Current substantive_flaw IDs satisfying late = true and endpoint != RETRACTED. Late cohorts overlap active/uncertain cohorts. Original #270.
    Formula: `count(current type = substantive_flaw and late = true and endpoint != RETRACTED)`. Availability: Null without a successful PRE or POST assessment; zero when available with no qualifying IDs.
    Source: issue_features: concern_post_late; units are issue IDs, not adjudicated unique concerns.

126. `insight_weakness_post_uncertain_count` — Current insight_weakness IDs satisfying endpoint = UNCERTAIN (including late issues). Late cohorts overlap active/uncertain cohorts. Original #380.
    Formula: `count(current type = insight_weakness and endpoint = UNCERTAIN (including late issues))`. Availability: Null without a successful POST assessment; zero when available with no qualifying IDs.
    Source: issue_features: insight_weakness_post_uncertain; units are issue IDs, not adjudicated unique concerns.

127. `insight_weakness_post_unknown_count` — Current insight_weakness IDs satisfying endpoint = UNKNOWN and late = false. Late cohorts overlap active/uncertain cohorts. Original #392.
    Formula: `count(current type = insight_weakness and endpoint = UNKNOWN and late = false)`. Availability: Null without a successful PRE assessment; zero when available with no qualifying IDs.
    Source: issue_features: insight_weakness_post_unknown; units are issue IDs, not adjudicated unique concerns.

128. `insight_weakness_post_late_count` — Current insight_weakness IDs satisfying late = true and endpoint != RETRACTED. Late cohorts overlap active/uncertain cohorts. Original #368.
    Formula: `count(current type = insight_weakness and late = true and endpoint != RETRACTED)`. Availability: Null without a successful PRE or POST assessment; zero when available with no qualifying IDs.
    Source: issue_features: insight_weakness_post_late; units are issue IDs, not adjudicated unique concerns.

129. `issue_debate_new_n_source_calls_valid` — Successful source calls in this creation phase. Original #435.
    Source: issue_features: phase_created=DEBATE, creation_issue_type=all; original creation judgments.

130. `issue_debate_new_n_source_calls_expected` — Denominator: distinct reviewer assessments actually called in this phase, including failed calls; excludes skipped/uncalled records and counts retries once. Original #436.
    Source: issue_features: phase_created=DEBATE, creation_issue_type=all; original creation judgments.

131. `concern_debate_new_n_source_calls_valid` — Successful source calls in this creation phase. Original #481.
    Source: issue_features: phase_created=DEBATE, creation_issue_type=substantive_flaw; original creation judgments.

132. `concern_debate_new_n_source_calls_expected` — Denominator: distinct reviewer assessments actually called in this phase, including failed calls; excludes skipped/uncalled records and counts retries once. Original #482.
    Source: issue_features: phase_created=DEBATE, creation_issue_type=substantive_flaw; original creation judgments.

133. `insight_weakness_debate_new_n_source_calls_valid` — Successful source calls in this creation phase. Original #520.
    Source: issue_features: phase_created=DEBATE, creation_issue_type=insight_weakness; original creation judgments.

134. `insight_weakness_debate_new_n_source_calls_expected` — Denominator: distinct reviewer assessments actually called in this phase, including failed calls; excludes skipped/uncalled records and counts retries once. Original #521.
    Source: issue_features: phase_created=DEBATE, creation_issue_type=insight_weakness; original creation judgments.

135. `issue_post_new_n_source_calls_valid` — Successful source calls in this creation phase. Original #559.
    Source: issue_features: phase_created=POST, creation_issue_type=all; original creation judgments.

136. `issue_post_new_n_source_calls_expected` — Denominator: distinct reviewer assessments actually called in this phase, including failed calls; excludes skipped/uncalled records and counts retries once. Original #560.
    Source: issue_features: phase_created=POST, creation_issue_type=all; original creation judgments.

137. `concern_post_new_n_source_calls_valid` — Successful source calls in this creation phase. Original #579.
    Source: issue_features: phase_created=POST, creation_issue_type=substantive_flaw; original creation judgments.

138. `concern_post_new_n_source_calls_expected` — Denominator: distinct reviewer assessments actually called in this phase, including failed calls; excludes skipped/uncalled records and counts retries once. Original #580.
    Source: issue_features: phase_created=POST, creation_issue_type=substantive_flaw; original creation judgments.

139. `insight_weakness_post_new_n_source_calls_valid` — Successful source calls in this creation phase. Original #592.
    Source: issue_features: phase_created=POST, creation_issue_type=insight_weakness; original creation judgments.

140. `insight_weakness_post_new_n_source_calls_expected` — Denominator: distinct reviewer assessments actually called in this phase, including failed calls; excludes skipped/uncalled records and counts retries once. Original #593.
    Source: issue_features: phase_created=POST, creation_issue_type=insight_weakness; original creation judgments.

