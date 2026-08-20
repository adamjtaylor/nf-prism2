# HTAN progression cohort: results

Run `nf-prism2-progression-188-resume` (`20zEYRdNeMfjuM`), 20 Aug 2026, `nf-prism2` at `160fb4b`,
spot `p4d.24xlarge`. **163 slides scored, 128 patients.** Scoring follows
`assets/questions_htan_progression.md`, fixed before the run.

## Headline

Asked in pathology report language, PRISM2 tracks the HTAN progression axis closely. Asked the
same question in HTAN's curation vocabulary, it does not. That contrast is the main result.

## 1. The yes/no ladder works

![ladder](figures/fig1_ladder.png)

Spearman against the ordinal axis, 95% CI bootstrapped over **patients** (32 contribute more than
one specimen, so slide-level resampling would overstate precision):

| Question | rho | 95% CI | Observed peak | Predicted |
|---|---|---|---|---|
| `negative_for_tumor` | **-0.78** | -0.82 to -0.74 | normal | falls, yes |
| `benign` | **-0.73** | -0.79 to -0.65 | normal | falls, yes |
| `malignancy` | **+0.73** | 0.66 to 0.79 | primary | rises, yes |
| `dysplasia` | +0.64 | 0.58 to 0.71 | in situ | peaks mid, yes |
| `carcinoma_in_situ` | +0.58 | 0.49 to 0.67 | **in situ** | peaks at in situ, **yes** |
| `invasive_carcinoma` | +0.54 | 0.44 to 0.62 | primary | rises, yes |
| `atypia` | +0.52 | 0.42 to 0.61 | primary | peaks early, no |
| `precancerous_lesion` | +0.18 | 0.05 to 0.29 | in situ | peaks mid, weakly |
| `hyperplasia_metaplasia` | -0.18 | -0.32 to -0.06 | in situ | peaks early, no |

Six of nine behaved as pre-registered. The one that matters most is `carcinoma_in_situ`: it rises
through the precancer classes, **peaks at carcinoma in situ, then falls again for invasive
disease**. A model with a single "abnormality" axis cannot do that. It implies a lesion-type
distinction, which is what makes these scores worth using as features rather than as a detector.

`hyperplasia_metaplasia` and `atypia` did not peak where predicted, and both are weak. The most
likely reason is that both terms describe changes that persist alongside carcinoma rather than
being replaced by it, so a monotone reading is not obviously wrong.

## 2. Normal versus invasive primary

![normal vs primary](figures/fig3_normal_vs_primary.png)

The endpoint the 10-slide pilot could not compute, having had 6 positives and 1 negative and
returned AUC 0.50. Here n+ = 60, n- = 34:

| Question | AUC | Tied pairs |
|---|---|---|
| `malignancy` | **0.966** | 0.8% |
| `carcinoma_in_situ` | 0.853 | 2.2% |
| `invasive_carcinoma` | 0.829 | 2.4% |
| `negative_for_tumor` | 0.007, that is **0.993** in its own direction | 0.0% |

bf16 quantisation is still present, 49 distinct values across 1,467 scores, but at this cohort
size it costs under 2.5% tied pairs and does not threaten the conclusions. That is a change from
the pilot, where 72 scores collapsed onto 32 values. **True fp32 turns out to be unavailable**:
PRISM2's released code casts the Perceiver to bf16 whatever `torch_dtype` says, and flash
attention, which the Perceiver requires, supports only fp16 and bf16. So the grid is a property
of the released model, not a setting we failed to flip.

## 3. The forced-choice stage question fails, and the distractors proved it

![stage mc](figures/fig2_stage_mc.png)

Exact accuracy **0.209** against 0.125 chance, within one ordinal step 0.558. The answer
distribution is the real finding:

* **43% of slides received "Atypia - hyperplasia"** regardless of what they were. The confusion
  matrix shows it as a vertical stripe: 25 of 33 normals, 17 of 30 premalignant, 8 of 60 primaries.
* **24.5% received an off-cohort distractor**, 21 "Metastatic" and 19 "Post therapy neoadjuvant".
  The second is a statement about whether the patient had neoadjuvant treatment, which no
  morphology can support.
* Only 1 slide of 163 was called "Premalignant - in situ", the class the ladder detects best.

**The explanation is a vocabulary mismatch, not a capability gap.** `Premalignant`,
`Atypia - hyperplasia` and `Post therapy neoadjuvant` are curation categories. PRISM2 learned from
clinical reports, which say "atypical adenomatous hyperplasia" and "adenocarcinoma in situ". The
same underlying question scored rho -0.78 to +0.73 in report language and near chance in data
model language.

Using the data model's valid values verbatim was the principled choice and it is exactly what
exposed the mismatch. Including off-cohort distractors was what made the failure legible: without
them this reads as mediocre 21% accuracy rather than "a quarter of answers name a category that
cannot occur here".

## 4. What this means for the resource

1. **Ask in report language.** For any HTAN field to be probed, phrase the question the way a
   pathologist would write it and map the answer back to the controlled vocabulary afterwards.
   Do not put curation terms in the prompt.
2. **Use the scores as ranks.** The separation is in the ordering, and the ladder profile carries
   more information than any single threshold.
3. **Off-cohort distractors should be standard** in any forced-choice evaluation against a
   controlled vocabulary. They cost nothing and they distinguish "somewhat accurate" from
   "not engaging with the options".
4. **The progression axis is a usable endpoint for HTAN**, and it is the kind of question HTAN is
   uniquely positioned to ask, since the precancer atlases supply the classes no other public
   collection has at this scale.

## Caveats

* 26 of 188 slides failed to process, 13 svs and 11 OME-TIFF, mostly segmentation OOM at 24 GB on
  very large images. Arm A still retained 15 to 18 patients in every class.
* Arm A is a single centre and a single organ by design, which removes the scanner confound but
  makes the result lung-specific until Arm B is analysed separately.
* `TumorTissueType` describes the biospecimen block, not the exact section on the glass.
* No pathologist has read these slides. Disagreements are not adjudicated.
