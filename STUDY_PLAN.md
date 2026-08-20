# HTAN progression study: plan, launched run, and the analysis still to do

Single entry point for the current piece of work. The pipeline itself is described in
`README.md`; the Aim 1 rationale is in `INTRODUCTION.md`; the general Epic 4 sweep design is in
`BENCHMARK_PLAN.md`. This document is about one specific study and what happens next.

## 1. What this is for

Two artefacts from one body of work, deliberately **not** a diagnostic-performance paper:

1. **A proof of concept for Aim 1.4 and 1.5 of R01CA309386**, showing the embedding-resource path
   works end to end and that model selection is being done with measured criteria rather than
   assertion.
2. **A worked vignette for the HTAN documentation**, teaching HTAN data access from `syn://` URI
   to slide-level features, including the traps.

Under that framing the limits that would sink a claims paper (no comparator encoder, no
pathologist read, small per-question label counts) stop being fatal, because the value is the
reproducible path plus documented failure modes. The strongest content is negative and
methodological, which is exactly the content that survives modest sample size.

## 2. What the 10-slide pilot established

Full results in `benchmark_results/htan10_20260819/ANALYSIS.md`. Four findings carried forward:

* **Tile embeddings are slide-locked.** 0.5% of a tile's 30 nearest neighbours come from another
  slide, against an 87.5% ceiling. Leiden gives 99 to 100% slide-dominated communities at every
  resolution. Per-slide centering makes cross-slide retrieval worse; Harmony helps a little and is
  transductive, so unusable for an index that grows.
* **PRISM2's base embedding (2560-d) is a usable retrieval space, the diagnostic (3072-d) is not**,
  spanning cosine 0.00 to 0.59 against 0.82 to 0.95.
* **Mean pooling is not a substitute for the Perceiver.** Its top pairs are biologically
  meaningless, and pair orderings agree with PRISM2's at r = 0.05.
* **Zero-shot yes/no scores were bf16-quantised**: 72 scores collapsed onto 32 distinct values.
  Ranking still worked where absolute values did not, so they are rank features, never 0.5 calls.

The pilot also fixed the pipeline: `/dev/shm` for TRIDENT's DataLoader, `transformers==4.51.3`,
bf16 loading, spaces in filenames, retry-then-drop with the reader escalating on retry, the split
model cache, and `min_tissue_proportion` raised from TRIDENT's 0.0 to the paper's 0.65.

## 3. Why the cohort is built on TumorTissueType

An audit of every clinical column against the H&E cohort (`scripts/audit_htan_labels.py`,
results in `assets/htan_label_availability.json`) reordered the endpoints:

| Field | Coverage | Decision |
|---|---|---|
| `TumorTissueType` (specimen) | **92%** | primary endpoint, and an ordinal axis |
| `TissueorOrganofOrigin` | 95% | secondary |
| `PrimaryDiagnosis` | 85% | secondary |
| `TumorGrade` | 77% | secondary, two vocabularies in the model itself |
| `HistologicMorphologyCode` | 98% nominal | **unusable**: mixes ICD-10 diagnosis and topography, ICD-O-3, SNOMED, free text and placeholders. Real coverage under 40%. Reported as a harmonisation finding. |
| `LymphaticInvasionPresent` / `PerineuralInvasionPresent` | 11% / 7% | exploratory only |
| `TumorInfiltratingLymphocytes`, `PercentTumorCells`, `PercentNecrosis` | **0%** | not asked. The schema has them, the data does not. |

`TumorTissueType` also supplies the tumour-absent negatives whose absence made the pilot's
`invasive_carcinoma` endpoint return AUC 0.50 on 6 positives and 1 negative.

## 4. The cohort

`assets/samplesheet_progression.csv`, built by `scripts/build_progression_cohort.py`, labels in
`assets/samplesheet_progression_labels.csv`. **188 slides, 146 patients, 70.7 GB.**

| Arm | What | Slides | Purpose |
|---|---|---|---|
| A | HTAN BU, lung, 18 patients in each of 6 classes | 108 (+8 pairs) | primary endpoint **within one centre and one organ** |
| B | HTAN Vanderbilt, colon, 4 classes | 27 | replication in another organ and centre |
| C | Primary only: Duke breast, HMS, WUSTL pancreas | 45 | several centres per organ, for the site and type questions and the tile-locking analysis |

BU is the only atlas carrying the whole axis including Primary, which is what makes the
within-centre design possible: every non-primary specimen in HTAN comes from a precancer atlas, so
spreading the axis across centres would have confounded progression stage with scanner and stain.

**32 patients contribute specimens at more than one class**, supporting a within-patient paired
analysis. Formats: 139 svs, 22 ome-tiff, 15 tif, 12 tiff. `mpp` supplied for 178 of 188 from
BigQuery, reader forced on 27.

`SiteofResectionorBiopsy` was considered as a third sampling axis and rejected: it is
near-collinear with organ of origin and the two disagree on 0 of 188 slides here.

## 5. Questions and pre-registered scoring

`assets/questions_htan_progression.yaml`, rationale and scoring in
`assets/questions_htan_progression.md`. 23 questions plus a report: 14 yes/no, 4 forced choice,
5 open ended.

Multiple-choice options are the HTAN data model's own valid values, so an answer compares to the
metadata string with no translation, and each list mixes in **off-cohort distractors** (real model
values no slide here carries) so the option set cannot leak the answer by elimination. Chance runs
from 0.083 to 0.167 depending on the question.

The yes/no ladder is designed so each question has a predicted **profile** across the axis, which
is a stronger test than any single binary split: `benign` should fall, `invasive_carcinoma` should
rise, and `carcinoma_in_situ` should peak in the middle. Where each score peaks is the result.

## 6. The runs, as executed

Four launches, three of which were needed only because of failures diagnosed along the way.

| Run | ID | Outcome |
|---|---|---|
| `nf-prism2-progression-188` | `5lCnAYecYQbYmt` | on-demand p4d, never placed a task in 25 minutes, cancelled |
| `nf-prism2-progression-188-spot` | `5D8jAR0CBnVYa4` | spot p4d, cancelled after every PRISM2 task failed on fp32 |
| `nf-prism2-progression-188-resume` | `20zEYRdNeMfjuM` | **SUCCEEDED, 163 slides**, resumed the above so 137 tile passes were reused |
| `nf-prism2-progression-retry25c` | `3HPLGdUhIgYACz` | **SUCCEEDED, 12 more slides recovered** |

Final state: **175 of 188 slides, 137 patients**, merged into
`benchmark_results/progression_20260820/results_merged.json`.

Settled parameters: `scoring_dtype=bf16`, `min_tissue_proportion=0.65`,
`publish_tile_features=true`, `shm_size=32g`,
`model_store=s3://mc2-project-tower-scratch/nf-prism2-models`, `hf_token_secret=AJT_HF_TOKEN`,
`maxForks=16`. Outputs under `s3://mc2-project-tower-scratch/nf-prism2-progression-spot/` and
`-retry-c/`, including 165 published tile-feature files totalling 3.5 GB.

### What the runs taught us, which the plan had wrong

* **fp32 scoring is not available for this model.** PRISM2's released code casts the Perceiver to
  bf16 whatever `torch_dtype` says, and flash attention supports only fp16 and bf16. Every
  `PRISM2_INFER` task failed with `expected mat1 and mat2 to have the same dtype` after burning
  about 33 minutes of GPU each. The score grid is a property of the released model, not a setting.
  At this cohort size it costs under 2.5% tied pairs, so it no longer threatens the conclusions.
* **Spot beat on-demand for p4d capacity**, placing in minutes where on-demand never placed at
  all, at roughly a third of the price. Racing the two and cancelling the loser cost nothing.
* **Staging was never the bottleneck.** The nf-synapse plugin staged 16 slides in 40 seconds.
  GPU capacity was.
* **`/dev/shm` does not scale with the memory request.** It was pinned at 8 GB, so escalating the
  cgroup limit to 48, then 96, then 144 GB could not fix DataLoader workers dying. Raising shm to
  32 GB recovered 10 of 12 slides in one go. A failure recurring *unchanged* across a swept
  parameter is evidence that parameter is not the cause.
* **TRIDENT's `--max_workers 0` is invalid** despite its help text, since it reaches
  `ThreadPoolExecutor(max_workers=0)`. The retry floor is 1.
* **Ten HMS OME-TIFFs are 3.7x overviews**, not 20x slides: `PhysicalSizeX = 2.72 um` at
  8064 x 9417, identical across all ten, while HTAN records `NominalMagnification: 20`. No reader
  or mpp setting could recover them, and the cohort builder now rejects anything coarser than
  1.0 um/px.

## 7. Downstream analysis

### Exists and reusable

| Script | What it does | Change needed |
|---|---|---|
| `bin/collect_results.py` | merges per-slide JSON, reconciles against the samplesheet, writes `failed_samples.txt` | none |
| `benchmark_results/htan10_20260819/make_tile_figures.py` | tile UMAP | point at the new features |
| `.../make_leiden.py` | Leiden resolution sweep, slide dominance | point at the new features |
| `.../make_leiden_map.py` | communities on UMAP plus composition matrix | point at the new features |
| `.../make_normalisation.py` | mixing and cross-slide p@10 for raw, centering, standardise, Harmony | point at the new features |
| `.../make_slide_reports.py` | per-slide report with streamed tiles | generalise from `htan10_clinical.csv` to `samplesheet_progression_labels.csv` |
| `.../make_figures.py` | embedding similarity, score matrix, discrimination dot plot | relabel for the progression axis |

### Done

* **Progression scoring** and its figures: `benchmark_results/progression_20260820/`, with
  `score_progression.py`, `make_figures.py`, five figures and `ANALYSIS.md`. Arm A is reported
  separately from Arms B and C throughout, after a first pass wrongly pooled them.
* **Secondary agreement** for site and histologic type, including the finding that the site
  question scores 0.91 while the progression-stage question scores 0.21 with the same format and
  the same distractors.

### Needs writing

1. ~~Progression scoring~~ **done**, see above. What remains of the original item: Join `results.tsv` to
   `samplesheet_progression_labels.csv` on `sample`, then:
   * confusion matrix for `progression_stage_mc`, overall accuracy, and accuracy within one
     ordinal step, since an adjacent-class error differs in kind from calling normal tissue
     invasive
   * **distractor rate** reported separately from accuracy
   * Spearman rho of each ladder score against ordinal rank, 95% CI bootstrapped **over patients**
   * peak location per ladder question against its predicted profile
   * normal versus primary AUC for `invasive_carcinoma` and `malignancy`, with CIs
   * the **paired within-patient** analysis over the 32 spanning patients, alongside the
     all-specimens analysis with patient as a random effect
   * Arm B replication reported separately, never pooled with Arm A
2. **Secondary agreement**: `primary_site_mc`, `histologic_type_mc`, `tumor_grade_mc` against their
   fields, with the grade vocabulary mapping applied; open-ended answers as agreement categories
   with disagreements listed rather than summarised.
3. **Tile-level re-analysis at scale.** Specified in full in `GOAL_tile_embeddings_duckdb.md`
   alongside a DuckDB vector-search prototype. The pilot's slide-locking result is the one that most
   needs this cohort: Arm A gives many slides within one centre and organ, so
   "slides separate even at fixed centre, organ and stage" becomes testable, and Arm C gives
   several centres per organ. Re-run mixing, Leiden dominance and cross-slide p@k with
   **patient-level** exclusion, not just slide-level.
4. **fp32 versus bf16 comparison.** Optional but cheap and directly useful: re-run a subset in
   bf16 and quantify how much AUC moves. That converts the quantisation finding from "the grid
   exists" to "here is what it costs you".

### Not in scope for this run, flagged deliberately

* **H-Optimus-0 as a second tile encoder.** Apache-2.0 and the same 224 px at 20x geometry, so it
  reuses this run's segmentation and coordinates and only the feature step re-runs. It is the
  cheapest comparator available and the natural next arm. PRISM2 cannot consume its features, so
  the comparison is tile-level.
* **Pathologist read of 20 to 30 slides.** The single highest-value addition, and the only thing
  that can adjudicate section-level questions or the unadjudicated pilot answers (the STIC call,
  the DCIS necrosis score, "atypical pneumocyte proliferation").
* **Pen mark and artefact removal.** `--remove_penmarks` and `--remove_artifacts` are off because
  the model store lacks their weights. The BU lung slide has blue marker writing and a green ink
  region that forms its own Leiden community, so this matters for BU specifically, which is Arm A.
* **The licence question.** Virchow2 and PRISM2 are CC-BY-NC-ND with a gated click-through. The
  operative document is that click-through, not the CC label, and whether it constrains publishing
  derived embeddings is a question for Sage counsel and the DCC. Worth resolving before the public
  tables are built.

## 8. Known risks for this run

| Risk | Detail |
|---|---|
| Staging, not GPU, is the bottleneck | 70.7 GB pulled sequentially by the nf-synapse plugin on the head node before the tile pass gets going. `SYNSTAGE` stages in parallel if this proves too slow. |
| fp32 is untested | First run. If `PRISM2_INFER` fails, bf16 still works and the run can be repeated. |
| 27 forced-`image`-reader slides | The reader escalates on retry, but qptiff through `ImageWSI` is unproven. |
| Ink on BU slides | Arm A is BU, and ink currently embeds as tissue. |
| Cost | About $150 to $250 at 16-wide on p4d. |
