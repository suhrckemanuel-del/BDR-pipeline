# Pipeline Eval Report — tenant `demo`, mode `sample`

**3/3 accounts passed all blocking gates.**

## Globex Industries — PASS — 18/22 checks passed, 4 warning(s)

| Check | Result | Detail |
| --- | --- | --- |
| run_completed | pass |  |
| enrichment_present | pass |  |
| icp_classified | pass |  |
| strategy_present | pass |  |
| angle_valid | pass | got 'angle1' |
| drafts_all_angles | warn | 1/3 angle drafts |
| draft_angle1_nonempty | pass |  |
| sequence_present | pass |  |
| sequence_touch_count | warn | 2/5 touches |
| sequence_days_ordered | pass | days=[0, 1] |
| sequence_channels_valid | pass | invalid=[] |
| email_subjects_present | pass | touches missing subject: [] |
| body_length_in_range | pass | too long: [] |
| no_placeholder_leaks | pass |  |
| personalized_to_company | pass | company name appears in 3/4 pieces |
| anti_ai_filter_idempotent | pass | residual AI patterns in:  |
| critic_present | pass |  |
| critic_score_min | pass | 3.4 < 3.0 |
| critic_score_target | warn | 3.4 < 3.5 |
| gate_not_blocked | pass | verdict=needs_edit |
| no_high_severity_risks | pass |  |
| no_unsupported_claims | warn | 1 unsupported |

## Initech — PASS — 20/22 checks passed, 2 warning(s)

| Check | Result | Detail |
| --- | --- | --- |
| run_completed | pass |  |
| enrichment_present | pass |  |
| icp_classified | pass |  |
| strategy_present | pass |  |
| angle_valid | pass | got 'angle1' |
| drafts_all_angles | warn | 1/3 angle drafts |
| draft_angle1_nonempty | pass |  |
| sequence_present | pass |  |
| sequence_touch_count | warn | 2/5 touches |
| sequence_days_ordered | pass | days=[0, 1] |
| sequence_channels_valid | pass | invalid=[] |
| email_subjects_present | pass | touches missing subject: [] |
| body_length_in_range | pass | too long: [] |
| no_placeholder_leaks | pass |  |
| personalized_to_company | pass | company name appears in 3/4 pieces |
| anti_ai_filter_idempotent | pass | residual AI patterns in:  |
| critic_present | pass |  |
| critic_score_min | pass | 3.8 < 3.0 |
| critic_score_target | pass | 3.8 < 3.5 |
| gate_not_blocked | pass | verdict=approved |
| no_high_severity_risks | pass |  |
| no_unsupported_claims | pass | 0 unsupported |

## Hooli — PASS — 18/22 checks passed, 4 warning(s)

| Check | Result | Detail |
| --- | --- | --- |
| run_completed | pass |  |
| enrichment_present | pass |  |
| icp_classified | pass |  |
| strategy_present | pass |  |
| angle_valid | pass | got 'angle1' |
| drafts_all_angles | warn | 1/3 angle drafts |
| draft_angle1_nonempty | pass |  |
| sequence_present | pass |  |
| sequence_touch_count | warn | 2/5 touches |
| sequence_days_ordered | pass | days=[0, 1] |
| sequence_channels_valid | pass | invalid=[] |
| email_subjects_present | pass | touches missing subject: [] |
| body_length_in_range | pass | too long: [] |
| no_placeholder_leaks | pass |  |
| personalized_to_company | pass | company name appears in 3/4 pieces |
| anti_ai_filter_idempotent | pass | residual AI patterns in:  |
| critic_present | pass |  |
| critic_score_min | pass | 3.4 < 3.0 |
| critic_score_target | warn | 3.4 < 3.5 |
| gate_not_blocked | pass | verdict=needs_edit |
| no_high_severity_risks | pass |  |
| no_unsupported_claims | warn | 1 unsupported |
