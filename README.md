# nf-prism2

Lightweight Nextflow (DSL2) pipeline: OpenSlide-compatible whole-slide image →
**Virchow2** tile embeddings → **PRISM2** slide representation → inference against an
editable set of questions (zero-shot yes/no scores, open-ended answers, multiple choice,
and a generated report).

```
samplesheet.csv ──> STAGE_MODELS (CPU, once) ──┐ hf_cache/
        │                                      │
        └──> TRIDENT_EMBED (GPU, per slide) ───┤   224px @ 20x, virchow2-cls -> (N, 1280)
                     │                         │
                     └──> PRISM2_INFER (GPU, per slide) ──> <sample>.prism2.json + .npz
                                    │
                                    └──> COLLECT_RESULTS (CPU) ──> results.tsv / results.json
```

## Why `virchow2-cls`

PRISM2 consumes the **Virchow2 class token only**, `(N, 1280)` per slide — not the 2560-d
class+mean concatenation that most pipelines produce by default. TRIDENT exposes exactly
this as `--patch_encoder virchow2-cls`, which is what `TRIDENT_EMBED` uses. Tiles are
224 px at 20x (0.5 mpp), tissue-only. `bin/prism2_infer.py` asserts the 1280-d shape and
fails with an explanatory error rather than silently producing garbage.

## Quick start

```bash
# 1. gated model access (once): accept the licences on huggingface.co for
#    paige-ai/Virchow2 and paige-ai/Prism2, then
nextflow secrets set HF_TOKEN "hf_..."        # or add HF_TOKEN as a Seqera Platform secret

# 2. wiring check: no GPU, no containers, runs the real collector on stubbed slides
nextflow run . -profile test,local -stub

# 3. real run on AWS Batch via Seqera Platform / CLI
nextflow run . -profile awsbatch \
    --input samplesheet.csv \
    --outdir s3://my-bucket/prism2-results \
    --gpu_queue my-gpu-batch-queue
```

Samplesheet:

```csv
sample,slide,mpp
SLIDE_A,s3://my-bucket/wsis/SLIDE_A.svs,
SLIDE_C_no_metadata,s3://my-bucket/wsis/SLIDE_C.tif,0.2425
```

`mpp` is optional; set it only for slides whose OpenSlide metadata has no
microns-per-pixel (TRIDENT then cannot infer the tiling scale).

## Questions

`assets/questions.yaml` is the whole interface — edit it, or pass `--questions my.yaml`:

```yaml
yes_no:
  - id: lvi
    question: "Is lymphovascular invasion present?"
open_ended:
  - id: cancer_type
    prompt: "What type of cancer is this?"
multiple_choice:
  - id: specimen_adequacy
    prompt: |
      Is this specimen adequate for diagnostic assessment?
      Options:
      A. Adequate
      B. Suboptimal but interpretable
      C. Inadequate
report:
  prompt: "Write a report"
  max_new_tokens: 300
```

`id` values become `results.tsv` column names (`yes_no__lvi`, `open_ended__cancer_type`, …)
and must be unique. Every question for a slide is answered in a single model load.

## Outputs

```
outdir/
├── results.tsv                       # one row per slide, one column per question id
├── results.json                      # all per-slide records
├── prism2/<sample>/
│   ├── <sample>.prism2.json          # scores, answers, report, tile counts
│   ├── <sample>.prism2.txt           # report text
│   └── <sample>.embeddings.npz       # base (1,2560) + diagnostic (1,3072)
├── tiles/<sample>/qc/                # thumbnail, tissue contours, tile overlay, summary.md
└── pipeline_info/                    # timeline, report, trace, DAG
```

## Containers

Two images, built from `containers/`:

| Image | Base | Contents |
|---|---|---|
| `nf-prism2-trident` | `pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime` | OpenSlide + TRIDENT `[patch-encoders]` |
| `nf-prism2-prism2` | `pytorch/pytorch:2.6.0-cuda12.4-cudnn9-devel` | transformers ≥4.51, flash-attn (prebuilt wheel), h5py |

```bash
docker build -f containers/trident.Dockerfile -t nf-prism2-trident:1.0.0 containers
docker build -f containers/prism2.Dockerfile  -t nf-prism2-prism2:1.0.0  containers
```

Push to a registry the compute environment can pull from (ECR in the same region as the
Batch queue) and set `params.container_trident` / `params.container_prism2` in
`conf/base.config` — preferably by digest. Weights are **not** baked in: they are gated and
non-redistributable, and `STAGE_MODELS` fetches them once per run into a shared HF cache.

Profiles: `local` (caps label resources to a laptop/workstation), `docker`, `docker_gpu`
(adds `--gpus all`), `singularity` (adds `--nv`), `awsbatch`, `test`.

See `docs/usage.md` for AWS Batch/Seqera setup, GPU sizing and troubleshooting.

## Licence and intended use

`paige-ai/Virchow2` and `paige-ai/Prism2` are **CC-BY-NC-ND 4.0**: non-commercial academic
research only, explicitly **not for clinical or diagnostic use**. Model access is gated — the
HuggingFace account behind `HF_TOKEN` must have accepted both licences (institutional email
required). Outputs of this pipeline are research artefacts, not diagnoses.

References: PRISM2 — [arXiv:2506.13063](https://arxiv.org/abs/2506.13063);
[Virchow2](https://huggingface.co/paige-ai/Virchow2);
[TRIDENT](https://github.com/mahmoodlab/TRIDENT).
