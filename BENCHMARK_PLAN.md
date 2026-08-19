# Benchmark plan: choosing the H&E foundation model stack

This is the plan for the Epic 4 benchmark (Aim 1.4). It defines what we compare, on which
slides, with which metrics, and how a winner is chosen. It is written so the benchmark can be
re-run later when models change, which the Year 1 risk register lists as a real threat.

Plain technical English. Terms are defined on first use.

## 1. Purpose

Aim 1.4 promises a benchmark report comparing digital pathology foundation models (FMs), and
an embedding resource over the HTAN whole-slide image (WSI) collection, about 2,165 slides.
The study section asked for explicit model-selection logic. So this benchmark has two jobs:

1. **Pick a stack.** One segmentation method, one tile geometry, one tile encoder, one or two
   slide encoders, to run across all of HTAN.
2. **Show the reasoning.** Pre-specified metrics, simple baselines included, and a written
   decision rule. The report is the deliverable, not just the winning model.

A benchmark that only ranks models is not enough. We also need to know how stable the ranking
is when preprocessing changes, because the resource has to be regenerated over years.

## 2. What we vary: three axes

The pipeline is a chain. Each step depends only on the step above it.

```
segmentation  ->  tiling geometry  ->  tile encoder  ->  slide encoder
(3 options)       (set by encoder)     (4 to 6)          (1 to 12)
```

### 2.1 Segmentation

| Option | What it is |
|---|---|
| `hest` | Learned tissue segmentation, TRIDENT default |
| `grandqc` | Learned segmentation with quality control focus |
| `otsu` | Classical thresholding, no weights needed, our simple baseline |

Optional flags that change the tile inventory: `--remove_artifacts`, `--remove_penmarks`,
`--remove_holes`, and the confidence threshold `--seg_conf_thresh`.

Segmentation is cheap per slide but it is the most expensive **axis**, because everything
downstream forks from it.

### 2.2 Tile encoders

The four named in the proposal, plus Virchow2 for PRISM2, plus a simple baseline.

| Tile encoder | Dim | Tile size | Magnification | Licence |
|---|---|---|---|---|
| `hoptimus0` (H-Optimus-0) | 1536 | 224 | 20x | Apache-2.0 |
| `phikon_v2` (Phikon-v2) | 1024 | 224 | 20x | Owkin non-commercial |
| `conch_v15` (for TITAN) | 768 | 512 | 20x | CC-BY-NC-ND |
| `gigapath` (Prov-GigaPath) | 1536 | 256 | 20x | Apache-2.0 |
| `virchow2-cls` (for PRISM2) | 1280 | 224 | 20x | CC-BY-NC-ND |
| `resnet50` | 1024 | 256 | 20x | BSD-3, ImageNet baseline |

Tile size and magnification are properties of the encoder, not global settings. The benchmark
must carry them with the encoder.

### 2.3 Slide encoders

Slide encoders are pinned to a specific tile encoder and geometry. This is the key constraint.

| Slide encoder | Requires tile encoder | Geometry |
|---|---|---|
| `titan` | `conch_v15` | 512 px, 20x |
| `prism2` | `virchow2-cls` | 224 px, 20x |
| `gigapath` | `gigapath` | 256 px, 20x |
| `chief` | `ctranspath` | 256 px, 10x |
| `madeleine` | `conch_v1` | 256 px, 10x |
| mean pooling | any | any, our simple baseline |

So the benchmark varies the **pair**, not the slide encoder alone. Mean pooling of tile
embeddings is included deliberately as the trivial aggregator. If a learned slide encoder does
not beat mean pooling, that is an important negative result.

## 3. How the sweep is run

### 3.1 Split the pipeline into stages

TRIDENT supports `--task seg`, `--task coords` and `--task feat` separately. The benchmark
pipeline must use those separate stages rather than `--task all`, so that Nextflow caches each
level and reuses it:

```
TRIDENT_SEG        keyed by (slide, segmenter, seg flags)
TRIDENT_COORDS     keyed by (segmentation, mag, patch_size, overlap)
TRIDENT_PATCH_FEAT keyed by (coords, tile encoder)
TRIDENT_SLIDE_FEAT keyed by (patch features, slide encoder)
PRISM2_INFER       keyed by (patch features from virchow2-cls)  [text head only]
```

Reuse is where the money is. Tile embedding dominates GPU cost, so every slide encoder that
accepts a given tile encoder must share one tile pass.

### 3.2 One gotcha to design around

TRIDENT's output layout encodes geometry and encoder name, for example
`20x_224px_0px_overlap/features_virchow2-cls/`, but it does **not** encode the segmenter.
Contours and `wsi_states/` sit at the top of `job_dir`. A segmenter sweep sharing one
`job_dir` will silently reuse or overwrite the first segmentation. The segmenter must
therefore be part of the task identity, which in Nextflow means a separate work directory per
segmenter.

### 3.3 PRISM2 needs both code paths

TRIDENT can produce a PRISM2 slide embedding through `--slide_encoder prism2`. It cannot
produce the language outputs, which need the HuggingFace model directly.

For the benchmark, take PRISM2's embedding from TRIDENT like every other slide encoder, so all
models go through identical preprocessing and batching. Run the language head as a separate
process. This costs one extra model load per slide, and it removes a confound that would
otherwise make the comparison arguable. For production runs over all of HTAN, use the single
combined load instead.

Open item to confirm before relying on it: which vector TRIDENT's `prism2` encoder returns,
the base embedding (2560) or the diagnostic embedding (3072).

## 4. Evaluation slide sets

The sweep runs on designed subsets, not on all 2,165 slides. Full-collection runs happen once,
with the winning stack.

| Set | Size, approximate | Purpose |
|---|---|---|
| **Contrast tissue set** | 40 to 60 slides | Different tissue types, for example lung and gastric, as named in the proposal. Tests whether embeddings separate the obvious. A model that fails here fails everything. |
| **TIL-annotated set** | 30 to 60 slides | Published slides with tumour infiltrating lymphocyte annotations. The only set with spatial ground truth, so the only set that supports Sørensen-Dice overlap. |
| **HTAN pilot set** | 100 slides | Stratified across HTAN atlases and tumour types, pinned to Release 7.0. Tests behaviour on our actual data, including scanner and staining variation between centres. |
| **Transform set** | 20 slides, many versions each | Same slides with shift, colour change, rotation and flip applied. Tests retrieval stability, which Aim 1.5 requires. |

Splits are by **patient**, not by slide. Several slides from one patient must never appear on
both sides of a train and test split.

## 5. Metrics

### 5.1 Tile level

| Question | Method | Metric |
|---|---|---|
| Do tile embeddings group by tissue or region type? | Cluster the embeddings, compare clusters to known labels | Adjusted Rand index, F-measure, Jaccard index |
| Is the structure real or just visible in a plot? | Train a simple classifier (logistic regression) on embeddings to predict the label | F1, AUC |
| Do TIL predictions land in the right place on the slide? | Tile-level prediction mapped back to slide coordinates, compared to published annotation | Sørensen-Dice |
| Is the embedding space stable to image changes? | Retrieve neighbours of transformed tiles | Rank stability, recall@k of the original tile |

### 5.2 Slide level

| Question | Method | Metric |
|---|---|---|
| Does the slide vector carry usable signal? | Linear probe on frozen embeddings, repeated k-fold cross-validation, patient-level splits | AUC, balanced accuracy, with confidence intervals |
| Does the learned aggregator beat trivial pooling? | Same probe on mean-pooled tile embeddings | Difference in AUC and whether it is inside the confidence interval |
| Is retrieval useful? | Nearest neighbours by cosine similarity | Precision@k on tissue type and, where available, diagnosis |

### 5.3 PRISM2 language outputs

These metrics apply only to PRISM2 and are the interpretability contribution.

| Question | Method | Metric |
|---|---|---|
| Are yes/no scores calibrated and discriminative? | Score against curated labels | AUC, plus calibration curve |
| Do open-ended answers match the recorded diagnosis? | Compare generated type against HTAN clinical fields | Agreement rate, with error categories written up by hand |
| Are generated reports usable for metadata enrichment? | Manual review of a sample by a pathologist collaborator | Structured rubric, error taxonomy |

The graded question bank in `PRISM2_question_bank.md` already marks each question as strong,
needs validation, or exploratory. Only the strong and validate categories enter the benchmark.
Exploratory questions are reported as observations, not as results.

### 5.4 Practical costs, recorded as results

Cost is a selection criterion, not an afterthought. From the Nextflow trace we record GPU
minutes per slide, tiles per slide, peak memory, and dollars per 1,000 slides for each stack.

**Measured baseline, 2026-08-19.** First real GPU run of `nf-prism2`, on one on-demand
g5.2xlarge (1x A10G 24 GB, 8 vCPU, 32 GB RAM, $1.212/hr, us-east-1) in the `htan-dev` account.
Slide: Aperio CMU-1 (the public OpenSlide test slide, ~170 MB), `otsu` segmenter, 20x / 224 px,
6,182 tiles. Raw artefacts in `benchmark_results/gpu_smoke_20260819/`.

| Stage | Wall time | Peak VRAM | Peak host RSS |
|---|---|---|---|
| `STAGE_MODELS` (~22 GB of gated weights) | 3 m 10 s, once per run | n/a | 4.0 GB |
| `TRIDENT_EMBED` (segment + tile + Virchow2) | 1 m 37 s | 4.7 GB | 16.7 GB |
| `PRISM2_INFER`, 2,000 tiles | 1 m 13 s | 7.6 GB | 12.2 GB |
| `PRISM2_INFER`, all 6,182 tiles | 30.7 s | 8.8 GB | 12.2 GB |
| `COLLECT_RESULTS` | 1.4 s | n/a | 18 MB |

Inside `TRIDENT_EMBED`: otsu segmentation 4.3 s, patch-coordinate generation 0.9 s, Virchow2
class-token embedding 57.2 s. That is **108 tiles/s** on an A10G at batch size 32, and it is the
only part of the pipeline that scales with slide size.

Two results drive the cost model. First, PRISM2 is effectively **constant-cost in tile count**:
its processor sets `num_img_tokens = 256`, so the Perceiver resampler compresses any number of
tiles to 256 tokens before the Phi-3 decoder sees them. Running all 6,182 tiles was no more
expensive than running 2,000 - the 1 m 13 s figure in the table is the larger only because it
paid a cold read of the 17 GB checkpoint, and the extra 1.2 GB of VRAM is the Perceiver's
cross-attention over 3x as many tiles. There is no reason to subsample, and `--max_tiles` should
be treated as an out-of-memory guard rather than a cost lever. Second, the whole stack fits
comfortably on a 24 GB card once the model is loaded in bf16, so **g5.2xlarge is the right
instance and a larger GPU buys nothing**.

Cost per slide decomposes into a fixed part and a per-tile part:

```
cost(slide) ~= $0.024  +  $0.0031 per 1,000 tiles
```

The fixed $0.024 is slide download, two model loads, PRISM2 inference and result collection
(~72 s). The per-tile term is Virchow2 at 108 tiles/s. For CMU-1 that is 129 s and **$0.043 per
slide** in steady state, or $0.082 with cold page cache and no weight reuse.

Extrapolated to the 2,165-slide HTAN collection, on one on-demand g5.2xlarge:

| Mean tiles/slide | Per slide | 2,165 slides | Wall time, 1 GPU |
|---|---|---|---|
| 6,182 (as CMU-1) | $0.043 | $94 | 78 h |
| 20,000 | $0.086 | $186 | 154 h |
| 50,000 | $0.179 | $387 | 320 h |

These are small enough that GPU cost is not a constraint on the benchmark design. Even the
pessimistic row is under $400 for a full pass, and the fan-out is embarrassingly parallel, so
wall time is set by how many instances we run rather than by total spend. Spot g5.2xlarge is
typically 55-65% cheaper again, though the on-demand figures are the ones measured here. The
numbers above exclude S3 storage and egress, and assume one slide per GPU at a time; nothing in
the run suggested the 8 vCPU host was the bottleneck (`TRIDENT_EMBED` ran at 108% CPU).

Three caveats found while measuring, all recorded because they change results rather than only
cost:

* `transformers` must be pinned to exactly **4.51.3**. PRISM2's remote code calls
  `Phi3Model._prepare_4d_causal_attention_mask_with_cache_position` with a `device` argument;
  4.52 dropped that argument and 4.53 removed the helper entirely.
* The model must be loaded as **bf16**, not with `torch_dtype="auto"`. The published
  `config.json` declares `float32`, which loads 17 GB of weights and runs out of memory on a
  24 GB card before the first forward pass.
* Yes/no scores are **quantised by `autocast(bfloat16)`**. The CMU-1 logit came back as exactly
  -4.25, a bf16 grid point, and was bit-identical for the 2,000-tile and 6,182-tile runs even
  though their slide embeddings differed (cosine 0.9998). Near that magnitude the bf16 grid
  spacing is 0.03125 in logit space, so scores carry roughly two significant figures. Before
  computing AUC or calibration curves, the scoring head should be run in fp32, otherwise ties
  are an artefact of the dtype rather than of the model.

## 6. Statistics and controls

* Simple baselines are always included: `otsu` segmentation, `resnet50` tile embeddings, mean
  pooling aggregation. Complex methods must beat these to be chosen.
* Repeated k-fold cross-validation with patient-level splits. Report mean and confidence
  interval, never a single split.
* Probes are linear on frozen embeddings. No fine-tuning during the benchmark, so what is
  measured is the embedding and not our training loop.
* Metrics are fixed before the runs, in this document, to avoid choosing the metric that
  flatters the result.
* Any HTAN atlas contributing many slides is checked separately, so one centre cannot carry
  the result.

## 7. Staging and cost control

| Phase | What runs | Purpose |
|---|---|---|
| 0 | One slide, one stack | Plumbing. Already done. |
| 1 | Contrast set, 3 segmenters, `otsu` and 1 learned tile encoder | Measure the segmentation axis cheaply, decide whether it matters. Drop it if the effect is small. |
| 2 | Contrast and TIL sets, all tile encoders, all compatible slide encoders | The main comparison. |
| 3 | HTAN pilot set, top 2 stacks | Confirm on our data and on centre variation. |
| 4 | Full HTAN, winning stack | Resource generation, not benchmarking. |

Rough scale of the fan-out: tile passes cost `slides × segmenters × tile encoders`. Slide
encoders on top are close to free. Phase 2 on about 100 slides with 6 tile encoders is
therefore a few hundred tile passes, which is affordable. The same sweep across all 2,165
slides would be tens of thousands and is not affordable, which is why phases exist.

## 8. Outputs

* **Long-format results table.** One row per slide per configuration, with columns for
  segmenter, tile encoder, geometry, slide encoder, metric name and value. Wide tables become
  unmanageable with three axes.
* **Configuration slug** carried through file paths and tables, for example
  `hest__20x224__virchow2cls__prism2`, so nothing is ambiguous.
* **Run manifest** per configuration: container digests, TRIDENT commit, model revisions,
  parameters. This is what makes the benchmark re-runnable when models update.
* **Licence column** on every row, so that non-commercial outputs can be filtered out of
  anything published openly.
* **BigQuery tables**: tile embeddings and slide embeddings for the chosen stack, plus the
  metrics table, so results can be queried alongside the search tool work in Aim 1.5.

## 9. Decision rule

A stack is chosen on this order of criteria, decided in advance:

1. **Validity.** Beats the simple baselines on the tile and slide metrics, outside the
   confidence interval.
2. **Robustness.** Ranking does not flip between the contrast set and the HTAN pilot set, and
   retrieval survives the transform set.
3. **Cost.** GPU minutes per slide, and total cost to process 2,165 slides.
4. **Licence.** Whether the outputs can be redistributed through the HTAN portal. A
   permissively licensed model that is slightly worse may be chosen for the public resource,
   with the stronger non-commercial model kept for internal analysis.
5. **Capability.** Whether the model provides a language interface, which no other candidate
   currently does.

If criteria conflict, the report states the trade-off and names two stacks: one for the public
resource, one for internal analysis. That outcome is acceptable and should be planned for.

## 10. Known risks

| Risk | Handling |
|---|---|
| Models update during the project | Pin container digests, model revisions and the TRIDENT commit. The sweep is a re-runnable pipeline, not a one-off notebook. |
| Ground truth is scarce | Only the TIL set has spatial labels. Do not over-claim from tissue type separation alone. |
| Centre effects | Scanner and stain differences between HTAN atlases may dominate biology. Check per-atlas performance, and report it. |
| Licence restrictions on derived data | Decide before generating the public resource, not after. See criterion 4. |
| Text outputs look convincing and are wrong | Never report generated text as a result without the paired quantitative metric and human review. |

## 11. What needs building

The current pipeline provides the plumbing for one stack. Still required:

1. Stage-split TRIDENT processes with segmenter in the task identity.
2. Encoder specification file, with dimension, geometry, pairing and licence per model.
3. Generic slide-embedding process, plus mean pooling as a baseline aggregator.
4. Evaluation module: clustering and purity metrics, linear probes with patient-level
   cross-validation, Dice against TIL annotations, retrieval and rank stability.
5. Slide set definitions, generated from the HTAN manifest at Release 7.0.
6. Results loader for BigQuery, shared with the Aim 1.5 search tool.
