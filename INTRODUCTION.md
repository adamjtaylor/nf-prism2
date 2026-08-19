# Introduction: why nf-prism2 is a useful MVP for Aim 1

This document explains what this pipeline does, why it is a useful minimum viable product
for **Aim 1** of R01CA309386 ("Unveiling Cancer Risk Through AI-Powered Spatial Insights"),
how it maps onto **Epic 4** of the Year 1 plan, and why the models and the architecture were
chosen the way they were.

Written in plain technical English. Terms are defined on first use.

## 1. What the pipeline does

You give it a whole-slide image (WSI) that OpenSlide can read. It gives you back, per slide:

| Output | Shape or type | What it is for |
|---|---|---|
| Tile embeddings | `(N, 1280)` in HDF5 | One vector per 224 px tile. Input to the slide model. Also the tile-level resource for search. |
| Base embedding | `(1, 2560)` | Slide-level vector for retrieval, clustering, vector search. |
| Diagnostic embedding | `(1, 3072)` | Slide-level vector conditioned by the language decoder. Intended for linear probes and outcome models. |
| Yes/No scores | one float per question | Zero-shot probability that the answer is yes. Continuous, so it can be scored with ROC and AUC. |
| Open-ended and multiple-choice answers | text | Diagnosis, histologic type, descriptive features. |
| Generated report | text | Synoptic-style narrative for metadata enrichment. |

The work is split into four steps: stage the model weights once, tile the slide and embed the
tiles, run the slide model and answer the questions, then merge everything into one table.
It runs on Seqera Platform against AWS Batch, one slide per task.

## 2. Why this is useful for Aim 1

Aim 1.4 commits to producing foundation model (FM) embeddings over the HTAN WSI collection,
about 2,165 slides across several tumour types, and to storing tile-level and slide-level
embeddings in BigQuery so they can be searched with `VECTOR_SEARCH`. Aim 1.5 builds the image
search tool on top of that vector database.

The hard part of Aim 1.4 is not the science of the models. It is the engineering path from a
large, multi-resolution, proprietary-format image file to a numeric vector, repeated
thousands of times, reproducibly, on cloud GPUs, without manual steps. This MVP is that path,
end to end, already running on the infrastructure we will use at scale:

1. **It proves the shape of the resource.** Tile-level `(N, 1280)` plus slide-level `(1, 2560)`
   is exactly the two-tier structure Aim 1.4 promises to deliver into BigQuery.
2. **It is per-slide parallel.** One slide is one task, so 2,165 slides is a samplesheet
   change, not a redesign. Failed slides do not block the rest, and `-resume` re-runs only
   what is missing.
3. **It runs where the data and the budget are.** Seqera Platform on AWS Batch, S3 work
   directory, spot instances for the expensive tile pass.
4. **It produces measurements, not promises.** Measured on one A10G (g5.2xlarge), 19 Aug 2026,
   Aperio CMU-1 at 20x with 6,182 tiles: Virchow2 embedding runs at 108 tiles/s (1m37s), PRISM2
   inference over all tiles takes 31s, and peak GPU memory is 8.8 GB. That is about $0.027 of
   GPU time per slide, so the whole 2,165-slide HTAN collection is on the order of $94 to $387
   depending on slide size. **GPU spend is not a constraint on this work.** Raw artefacts are in
   `benchmark_results/gpu_smoke_20260819/`.
5. **It adds a text interface that the models named in the proposal do not have.** PRISM2
   returns human-readable statements about a slide, and a calibrated-looking score for
   yes/no questions. That gives Aim 1 a way to enrich HTAN metadata directly, and gives
   Epic 7 something interpretable to validate against annotations.

## 3. How it maps onto Epic 4

Epic 4 is "H&E foundation-model embeddings and benchmarking (Aim 1.4)", Q2 to Q4.

| Epic 4 deliverable | What this MVP already does | What is still missing |
|---|---|---|
| FM benchmark report comparing H-optimus-0, Phikon-v2, TITAN, Prov-GigaPath, with cluster purity (Rand, F-measure, Jaccard), classifier separability (F1, AUC) and spatial overlap (Sørensen-Dice) | Provides the harness the benchmark runs inside. The tile encoder is a one-word parameter in TRIDENT, which ships about 33 tile encoders and 12 slide encoders, including all four named models. | The evaluation code itself: clustering, UMAP, purity metrics, probe training, Dice against TIL annotations. None of that is here yet. |
| Tile-level and whole-slide embeddings stored in BigQuery for vector search | Writes tile embeddings (HDF5) and slide embeddings (npz) per slide to S3, with stable per-sample naming. | The BigQuery load step, table schema, and the `ARRAY<FLOAT64>` conversion. |
| Initial exposure of embedding-derived features through the HTAN data portal | Produces `results.tsv`, one row per slide, one column per question. This is already a portal-shaped feature table. | Agreement on which columns to expose, and the portal ingestion route. |

Two further connections:

* **Epic 5 (image search tool)** consumes the base embedding. Cosine similarity over
  `(1, 2560)` vectors is the retrieval operation the tool needs, and the same run produces
  the tile vectors needed for region-of-interest search.
* **Epic 7 (interpretability and validation)** is where the yes/no scores matter. A vector
  cannot be argued with. A score for "Is lymphovascular invasion present?" can be compared
  against a curated label and reported as an AUC. That is a concrete response to the study
  section critique about model selection and interpretability, and it needs no training data
  to get started.

## 4. Design choices

### 4.1 Two-stage architecture: tile encoder, then slide aggregator

A WSI at 20x is far too large for one forward pass. So the pipeline does what every current
digital pathology FM does. It cuts tissue into small tiles, embeds each tile, then aggregates
the tile vectors into a slide vector.

This matters for planning, because the shape is the same for every slide-level model in the
field: tiles go in as an `(N, d)` matrix, one slide vector comes out. TITAN, Prov-GigaPath,
PRISM and PRISM2 all follow it. Choosing PRISM2 now therefore does not lock the project into
PRISM2 later. The expensive part, tiling and tile embedding, is reusable.

### 4.2 Tile encoder: Virchow2, class token only

PRISM2 does not read slides. It reads Virchow2 tile embeddings under fixed conditions:
224 px tiles, 20x magnification (0.5 microns per pixel), tissue tiles only, and the
**1280-dimensional class token alone**.

This is easy to get wrong. Most pipelines, including TRIDENT's default `virchow2` encoder,
return a 2560-dimensional vector made by concatenating the class token with the mean of the
patch tokens. That vector is not what PRISM2 expects. The pipeline therefore uses TRIDENT's
`virchow2-cls` encoder, hard-codes it rather than exposing it as a parameter, and asserts the
1280 dimension before inference with an error message that names the fix. Silent dimension
mismatches are the kind of bug that produces plausible numbers and wasted months.

### 4.3 Slide model: PRISM2

PRISM2 is a Perceiver slide encoder (0.6B parameters) joined to a Phi-3-mini language decoder
(3.8B parameters), 4.4B in total, trained on slides paired with clinical reports.

It was chosen for the MVP over the four models named in the proposal for three reasons.

1. **It returns embeddings and language from one model load.** The alternatives return
   vectors only. Aim 1 needs vectors for search, and text for metadata enrichment.
2. **Zero-shot yes/no scoring needs no training data and no class enumeration.** Ask a
   question, get a score. For a resource-building aim where labels are scarce and
   inconsistent across atlases, this is a fast way to produce comparable per-slide features
   for all 2,165 slides.
3. **It gives two different slide vectors.** The base embedding is a vision-side summary,
   suited to retrieval. The diagnostic embedding comes from the decoder state and is
   report-conditioned, which is a different and possibly stronger feature space for outcome
   models in Aim 2. Having both from one pass is cheap to test and easy to compare.

PRISM2 is **not** presented here as the winner of Epic 4's benchmark. It is the model that
makes the MVP show the most capability per GPU hour, and it is the only one that exercises
the text path.

### 4.4 Benchmarking is a parameter change, with one constraint

TRIDENT was chosen instead of writing our own segmentation and tiling because it already
implements tissue segmentation, tiling, and about 33 tile encoders plus 12 slide encoders,
including H-optimus-0, Phikon-v2, TITAN and Prov-GigaPath.

The constraint to plan around is that slide models are paired to specific tile encoders and
tile geometries. TITAN needs CONCH v1.5 at 512 px. PRISM2 needs Virchow2 class tokens at
224 px. So an Epic 4 benchmark varies the **pair**, not the slide model alone. Since the tile
pass dominates cost, benchmark runs should be grouped by tile encoder so each tile pass is
paid for once and reused by every slide model that accepts it.

### 4.5 Nextflow on Seqera Platform, not a notebook

The unit of work is a slide, and slides are independent. Nextflow gives per-slide
parallelism, restart without recomputation, one container per step, and a work directory on
S3. Seqera Platform gives the launch record, the run log, the trace, and the compute
environment. A notebook gives none of that, and cannot be handed to a collaborator as a
reproducible resource-generation step.

### 4.6 Reproducibility against foundation model churn

The Year 1 risk register lists FM churn as a specific threat to Epic 4. The pipeline handles
it in three ways. Container images are built by CI and can be pinned by digest. The TRIDENT
commit is pinned in the image. Model weights are downloaded once per run into a shared cache,
so every task in a run uses the same weights, and a cached copy can be reused across runs.

### 4.7 One licensing issue to decide early

Virchow2 and PRISM2 are both released under CC-BY-NC-ND 4.0. Access is gated, the licence is
non-commercial, and it forbids clinical or diagnostic use. The "ND" (no derivatives) term
raises a question that Aim 1 has to answer before publishing a resource: can embeddings
derived from these models be redistributed openly through the HTAN portal and BigQuery?

This needs a decision, not an assumption. If open redistribution is required, the fallback is
to generate the shareable resource with permissively licensed encoders, for example
Prov-GigaPath or H-Optimus-0 (Apache-2.0), and keep the Virchow2 and PRISM2 outputs for
internal analysis and benchmarking. The pipeline supports that split because the tile encoder
is a parameter.

## 5. What this MVP does not do yet

Stated plainly, so nobody plans around capability that is not there.

* No BigQuery load. Outputs land on S3 as HDF5, npz, JSON and TSV.
* No tile-level embedding publication. Tile vectors exist in the work directory but are not
  yet exported as a searchable resource.
* No benchmark evaluation code. No clustering, UMAP, purity metrics, probes or Dice scoring.
* No multi-slide aggregation. PRISM2 can take several slides as one specimen. The pipeline
  currently treats one slide as one unit.
* No validation of the question bank. The questions in `assets/questions.yaml` are
  placeholders. The graded bank in `PRISM2_question_bank.md` still needs benchmarking against
  HTAN ground truth before any answer is trusted.
* Tested end to end on one public slide (Aperio CMU-1) on a real GPU, plus stub runs on AWS
  Batch. Not yet run across HTAN, and never on a slide with a known diagnosis, so no output has
  been checked against ground truth.
* Yes/no scores are quantised by bf16 to a grid of about 0.03 in logit space. Usable as a
  smoke test, not yet usable for AUC or calibration without `--scoring_dtype fp32`.

## 6. Suggested next steps for Epic 4

1. Generate the samplesheet from the HTAN WSI manifest and run 10 slides for real timings
   and cost per slide.
2. Add a BigQuery load step for slide embeddings, then tile embeddings, with a table schema
   agreed with the search tool work in Epic 5.
3. Add the evaluation module: clustering and purity metrics on tile embeddings, linear probes
   on slide embeddings, and AUC of the yes/no scores against curated labels.
4. Run the tile pass once per candidate tile encoder, then fan out to every compatible slide
   model, to produce the Epic 4 benchmark table at the lowest GPU cost.
5. Decide the licensing question in 4.7 and record it in the benchmark report.

## 7. References in this repository

* `README.md` for what the pipeline is and how to run it.
* `docs/usage.md` for Seqera Platform setup, GPU sizing and troubleshooting.
* `assets/questions.yaml` for the question set that drives inference.
* `PRISM2_question_bank.md` (project directory, not this repo) for the graded, aim-mapped
  question bank this pipeline is designed to execute.
