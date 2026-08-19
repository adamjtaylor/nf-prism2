# Question set and pre-registered scoring: HTAN progression cohort

Every scored question maps to an HTAN field that is actually populated for H&E slides. Coverage
comes from `htan_label_availability.json`, produced by `scripts/audit_htan_labels.py` over the
1,089 patients and 2,327 biospecimens behind H&E Level 2 slides.

Metrics are fixed here, before the run, so the analysis cannot drift towards whichever metric
flatters the result.

## Why these fields

| Field | Table | Coverage | Role |
|---|---|---|---|
| `TumorTissueType` | specimen | **92%** | **primary endpoint**, and an ordinal progression axis |
| `TissueorOrganofOrigin` | diagnosis | 95% | secondary |
| `PrimaryDiagnosis` | diagnosis | 85% | secondary |
| `TumorGrade` | diagnosis | 77% | secondary |
| `HistologicMorphologyCode` | specimen | 98% nominal | **not used as a label**, see below |
| `LymphaticInvasionPresent` | diagnosis | 11% | exploratory only |
| `PerineuralInvasionPresent` | diagnosis | 7% | exploratory only |
| `AJCCPathologicStage` | diagnosis | 10% | not asked |
| `TumorInfiltratingLymphocytes`, `PercentTumorCells`, `PercentNecrosis` | specimen | **0%** | not asked, the schema has them and the data does not |

`HistologicMorphologyCode` is nominally 98% populated but mixes at least four coding systems and
placeholders: ICD-10 diagnosis codes (`D05.1`, 875 slides), ICD-10 topography (`C50.91`), ICD-O-3
morphology (`8140/3`), SNOMED (`M82110`), free text ("Superficial spreading melanoma") and
placeholders (`Not Specified`, `99999`, `0`, `Unknown`, 1,082 slides between them). Real usable
coverage is under 40% across incompatible vocabularies. It is reported as a harmonisation finding,
not used to score anything.

## The progression axis

`TumorTissueType` is treated as ordinal. Metadata values collapse as follows, because the
distinctions dropped are not visible in a section:

| Ordinal rank | Scored as | Metadata values folded in |
|---|---|---|
| 0 | Normal | `Normal`, `Normal adjacent`, `Normal distant` |
| 1 | Atypia - hyperplasia | `Atypia - hyperplasia` |
| 2 | Premalignant | `Premalignant` |
| 3 | Premalignant - in situ | `Premalignant - in situ` |
| 4 | Primary | `Primary`, `Additional Primary`, `Recurrent`, `Local recurrence`, `Post therapy*` |
| excluded | - | `Not Otherwise Specified`, `Not analyzed`, `Metastatic` (too few slides) |

"Normal adjacent" versus "Normal" is a statement about where the block was taken, and
"Recurrent" versus "Primary" is clinical history. Neither is a morphological claim, so scoring
them separately would manufacture errors.

## Off-cohort distractors

Every multiple-choice list mixes values the cohort realises with real data model values that no
slide here carries. Without distractors the option set leaks the answer: a model could score well
by elimination from a list that happens to contain only the six organs present. Distractors are
picked to be morphologically confusable with something in the cohort rather than absurd.

| Question | In cohort | Off-cohort distractors | Chance |
|---|---|---|---|
| `progression_stage_mc` | Normal, Atypia - hyperplasia, Premalignant, Premalignant - in situ, Primary | Metastatic, Post therapy neoadjuvant, Local recurrence | 0.125 |
| `primary_site_mc` | Breast, Lung, Colon or rectum, Skin, Pancreas, Fallopian tube or ovary | Stomach NOS, Prostate gland, Kidney NOS, Esophagus NOS | 0.10 |
| `histologic_type_mc` | Adenocarcinoma NOS, Squamous cell carcinoma NOS, DCIS NOS, Lobular carcinoma NOS, Malignant melanoma NOS, High-grade serous carcinoma, Pancreatobiliary-type carcinoma | Basal cell carcinoma NOS, Hepatocellular carcinoma NOS, Combined small cell carcinoma, Urothelial carcinoma NOS | 0.083 |
| `tumor_grade_mc` | G1/Low, G2/Intermediate, G3-G4/High | GX, GB | 0.167 |

**Report the distractor rate separately from accuracy.** An off-cohort answer is a different kind
of error from an in-cohort confusion: picking "Hepatocellular carcinoma NOS" for a breast slide
says something quite different from picking "Lobular carcinoma NOS". Distractor selections are also
the cheapest available check that the model is reading morphology rather than exploiting the option
list, so a near-zero distractor rate is itself a result.

## Cohort structure

`assets/samplesheet_progression.csv`, built by `scripts/build_progression_cohort.py`.
188 slides, 146 patients, 70.7 GB.

| Arm | What | Slides | Purpose |
|---|---|---|---|
| A | HTAN BU, lung, 18 patients in each of the 6 classes | 108 (+8 pairs) | the primary endpoint, measured **within one centre and one organ**, so a normal-versus-primary difference cannot be BU versus Duke |
| B | HTAN Vanderbilt, colon, 4 classes | 27 | independent replication in a different organ and centre |
| C | Primary only, Duke breast, HMS, WUSTL pancreas | 45 | several centres per organ, needed by the site and type questions and by the tile-locking analysis |

BU is the only atlas carrying the whole axis including Primary, which is what makes the
within-centre design possible. Every non-primary specimen in HTAN comes from a precancer atlas, so
spreading the axis across centres would have confounded stage with scanner and stain.

**32 patients contribute specimens at more than one progression class.** Those support a
within-patient paired analysis, which removes patient-level confounding entirely, so report both
the all-specimens analysis with patient as a random effect and the paired subset.

`SiteofResectionorBiopsy` was considered as a third sampling axis and rejected: it is
near-collinear with organ of origin, and in this cohort the two fields disagree on **0 of 188
slides**, so the discrimination test it would have supported does not exist here. The
`specimen_site` question is retained as descriptive only.

## Primary endpoints

1. **Forced-choice stage accuracy.** `progression_stage_mc` against the collapsed axis. Report a
   confusion matrix, overall accuracy, and accuracy within one ordinal step (an adjacent-class
   error is a different kind of error from calling normal tissue invasive).
2. **Monotonicity of the yes/no ladder.** Each ladder question has a predicted profile across the
   axis. Report Spearman rho against ordinal rank, with 95% CI bootstrapped **over patients**,
   never over tiles or slides.

| Question | Predicted profile across normal to primary | Test |
|---|---|---|
| `negative_for_tumor`, `benign` | decreasing | rho < 0 |
| `hyperplasia_metaplasia` | peaks at rank 1 | peak location |
| `dysplasia`, `atypia`, `precancerous_lesion` | peak at ranks 1 to 2 | peak location |
| `carcinoma_in_situ` | peaks at rank 3 | peak location |
| `invasive_carcinoma`, `malignancy` | increasing | rho > 0 |

Where each score peaks is the interesting result. A score that rises monotonically when it should
peak in the middle tells you the model has one axis of "abnormality" rather than a lesion-type
distinction, which matters for how these features get used downstream.

3. **Normal versus primary AUC** for `invasive_carcinoma` and `malignancy`. This is the endpoint
   the 10-slide pilot could not compute, because it had 6 positives and 1 negative and returned
   AUC 0.50. With around 20 patients per class the standard error is about 0.06.

## Secondary endpoints

* `primary_site_mc` against `TissueorOrganofOrigin`, collapsed to organ. The pilot scored 8 of 8.
* `histologic_type_mc` against `PrimaryDiagnosis`, exact string match on the model's own values.
* `tumor_grade_mc` and `high_grade` against `TumorGrade`, mapped onto three levels. Both grade
  vocabularies come from the data model itself, not from centre drift.
* Open-ended answers scored as agreement categories (agree, broader, differs) by string rules,
  with disagreements listed rather than summarised.

## Exploratory, reported but never as accuracy

`lymphovascular_invasion`, `perineural_invasion`, `necrosis`, `tumor_infiltrating_lymphocytes`.
The first two have 11% and 7% coverage; the last two have **no** HTAN label at all for H&E
biospecimens. They are included because they cost nothing to ask and the scores are worth looking
at, not because they can be validated here.

## Two standing conditions

1. **fp32 scoring.** Every yes/no number must come from `--scoring_dtype fp32`. bf16 collapses 72
   pilot scores onto 32 distinct values, so any AUC computed from bf16 is partly measuring the
   dtype. The A100s on `p4d.24xlarge` have the 40 GB needed.
2. **Ranks, not thresholds.** In the pilot a true positive scored 0.095 and still outranked every
   negative. Never threshold at 0.5.
