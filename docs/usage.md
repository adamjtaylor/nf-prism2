# Usage

## 1. Model access (do this first)

Both models are gated:

1. Log in to HuggingFace with an **institutional email** and accept the licences at
   <https://huggingface.co/paige-ai/Virchow2> and <https://huggingface.co/paige-ai/Prism2>.
2. Create a read token and register it as a Nextflow secret:

   ```bash
   nextflow secrets set HF_TOKEN "hf_..."
   ```

   On Seqera Platform: *Credentials → Secrets → Add secret* named `HF_TOKEN`, then attach it
   to the pipeline. Only `STAGE_MODELS` needs it in normal operation (`TRIDENT_EMBED` and
   `PRISM2_INFER` also declare it so an unexpected cache miss can still self-heal).

`STAGE_MODELS` downloads the weights once per run and hands them to every task as two separate
caches: `tile_cache` (Virchow2 plus the segmenter, about 2.5 GB) for `TRIDENT_EMBED`, and
`slide_cache` (PRISM2, about 17 GB) for `PRISM2_INFER`. The split matters because AWS Batch packs
several slides onto one instance, and each task stages its cache independently. Sharing one
directory meant every tile task pulled 20 GB it never opened.

**Use a persistent store so the download happens once, ever:**

```bash
--model_store s3://my-bucket/nf-prism2-models
```

On the first run the store is empty, so `STAGE_MODELS` downloads and publishes into it. Every
later run finds the `.complete` markers and skips the process entirely. The markers are written
after the downloads finish, so an interrupted run cannot leave a half-populated cache that a
later run mistakes for a good one. If either marker is missing, both caches are re-staged.

To bypass the check and point at directories directly, use `--hf_cache_tile` and
`--hf_cache_slide`. The legacy `--hf_cache` still works and supplies one directory to both
passes.

## 2. Build and publish the containers

```bash
docker build -f containers/trident.Dockerfile -t <registry>/nf-prism2-trident:1.0.0 containers
docker build -f containers/prism2.Dockerfile  -t <registry>/nf-prism2-prism2:1.0.0  containers
docker push <registry>/nf-prism2-trident:1.0.0
docker push <registry>/nf-prism2-prism2:1.0.0
```

Then edit `params.container_trident` / `params.container_prism2` in `conf/base.config`.
Pin by digest (`@sha256:...`) for reproducibility.

### flash-attn wheel

PRISM2 wants `flash-attn>=2.6.3`. The Dockerfile installs a prebuilt wheel because a source
build takes about an hour. If the build fails on the wheel, the tags did not match the base
image. Check them:

```bash
docker run --rm pytorch/pytorch:2.6.0-cuda12.4-cudnn9-devel python -c \
  "import sys, torch; print(sys.version_info[:2], torch.__version__, torch._C._GLIBCXX_USE_CXX11_ABI)"
```

Pick the matching asset from <https://github.com/Dao-AILab/flash-attention/releases> —
`cu12torch<VER>cxx11abi{TRUE,FALSE}-cp<PY>-cp<PY>-linux_x86_64.whl` — and pass it in:

```bash
docker build -f containers/prism2.Dockerfile --build-arg FLASH_ATTN_WHEEL=<url> -t ... containers
```

## 3. Compute environment (AWS Batch / Seqera Platform)

* Create a **GPU-enabled** Batch compute environment (ECS GPU-optimised AMI). Both GPU steps
  request `accelerator = 1`.
* GPU sizing:
  * `TRIDENT_EMBED` (`gpu_small`) — Virchow2 forward passes; a T4/A10G is fine.
  * `PRISM2_INFER` (`gpu_large`) — 4.4B params in bf16. Measured peak on CMU-1 with all 6,182
    tiles: 8.8 GB VRAM, 12.2 GB RSS. **24 GB VRAM is right-sized** (g5.2xlarge / A10G). Only
    `--scoring_dtype fp32` needs more, about 40 GB, because it loads the model in float32.
* Set `--gpu_queue <queue>` so CPU steps (`STAGE_MODELS`, `COLLECT_RESULTS`) can run on a
  cheaper CPU queue; if omitted, everything goes to `--queue`.
* `conf/awsbatch.config` enables Fusion + Wave so WSIs stream from S3 instead of being fully
  copied into each task. On Seqera Platform the compute environment's own settings win.
* Use an S3 work directory (`-work-dir s3://...` or the compute environment default).

Launch from the CLI:

```bash
nextflow run . -profile awsbatch \
    --input samplesheet.csv \
    --outdir s3://my-bucket/prism2-results \
    --queue my-cpu-queue \
    --gpu_queue my-gpu-queue \
    -work-dir s3://my-bucket/work
```

On Seqera Platform, add the repo as a pipeline; `nextflow_schema.json` renders the launch
form, so `input`, `outdir`, `questions`, `gpu_queue` and the tiling/inference knobs are all
editable in the UI.

## 3b. Seqera Platform (tower.sagebionetworks.org)

The platform launches from a Git URL, so run from
`https://github.com/adamjtaylor/nf-prism2` (public - no Git credential needed in the
workspace). Images come from GHCR, built by `.github/workflows/containers.yml`.

**Stub run on a CPU queue** (no GPU, no weights, no token - proves the plumbing):

```bash
curl -s -X POST -H "Authorization: Bearer $TOWER_ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  "$TOWER_API_ENDPOINT/workflow/launch?workspaceId=<WS_ID>" -d '{
  "launch": {
    "computeEnvId": "<CE_ID>",
    "pipeline": "https://github.com/adamjtaylor/nf-prism2",
    "revision": "main",
    "stubRun": true,
    "pullLatest": true,
    "paramsText": "input: '"'"'https://raw.githubusercontent.com/adamjtaylor/nf-prism2/main/assets/samplesheet_stub.csv'"'"'\noutdir: '"'"'s3://<bucket>/nf-prism2-stub'"'"'\ncpu_only: true\ncontainer_trident: '"'"'python:3.12'"'"'\ncontainer_prism2: '"'"'python:3.12'"'"'\n"
  }}'
```

`cpu_only: true` drops the `accelerator 1` request (implemented as a closure on the GPU
labels, since Nextflow's strict config parser forbids `if` statements in config files) and
`stubRun` skips the real work. Only `COLLECT_RESULTS` executes for real.

**Real run** additionally needs:

1. A queue with GPU instance types in the compute environment, and `cpu_only` left at
   `false` so `accelerator 1` is requested.
2. `HF_TOKEN` as a **workspace** secret - user-level secrets are only visible in your
   personal workspace, not in an org workspace:
   `tw secrets add -w <WS_ID> -n HF_TOKEN -v hf_...`
3. The GHCR packages made public once, after the first successful build:
   ```bash
   gh api -X PATCH /user/packages/container/nf-prism2-trident --field visibility=public
   gh api -X PATCH /user/packages/container/nf-prism2-prism2  --field visibility=public
   ```
   (Actions-created packages start private even in a public repo, and AWS Batch pulls
   anonymously.)

## 4. Verification ladder

```bash
# a. wiring, filenames, channel topology - no GPU and no container engine needed.
#    Every step is stubbed except COLLECT_RESULTS, which runs for real against the
#    stubbed per-slide JSON, so the merge logic is covered too.
nextflow run . -profile test,local -stub

# b. gated access + encoder registry, inside the trident image
docker run --rm -e HF_TOKEN=$HF_TOKEN <registry>/nf-prism2-trident:1.0.0 \
    trident-doctor --profile patch-encoders --check-gated

# c. real single slide (public OpenSlide CMU-1.svs) on one GPU box
#    add `local` if the box has less RAM than the gpu_large label requests (48 GB)
nextflow run . -profile test,docker_gpu,local --outdir results_test

# d. same, on Batch
nextflow run . -profile test,awsbatch --outdir s3://my-bucket/test --gpu_queue my-gpu-queue

# e. full cohort, then confirm -resume is a no-op
nextflow run . -profile awsbatch --input samplesheet.csv --outdir s3://... -resume
```

What to check after (c):

* `results_test/tiles/CMU-1/qc/` — tissue contours look sane, tile overlay covers tissue.
* `python -c "import h5py; f=h5py.File('<work>/CMU-1.features.h5'); print({k: f[k].shape for k in f})"`
  → features `(N, 1280)`.
* `results_test/prism2/CMU-1/CMU-1.prism2.json` → `base_embedding_dim: 2560`,
  `diagnostic_embedding_dim: 3072`, finite yes/no scores, non-empty `report`.
* `results_test/results.tsv` → one row per slide, one column per question id.

### Profiles

| Profile | Effect |
|---|---|
| `local` | Local executor with `resourceLimits` capped to 4 CPU / 8 GB, so label requests do not exceed a laptop |
| `docker` | Docker, no GPU flags (stub / CPU work) |
| `docker_gpu` | Docker with `--gpus all` |
| `singularity` | Singularity/Apptainer with `--nv` and auto-mounts |
| `awsbatch` | AWS Batch executor, Fusion + Wave, GPU queue routing |
| `test` | CMU-1.svs, tiny question set, all tiles, `segmenter=otsu` |

## 5. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `HF_TOKEN is empty` in `STAGE_MODELS` | Secret not set or not attached to the run. |
| `401/403` on `hf download` | Licence not accepted by the token's account, or non-institutional email. |
| `tile embeddings are 2560-d but PRISM2 requires 1280-d` | TRIDENT ran with `virchow2` instead of `virchow2-cls`. The encoder is deliberately hard-coded in `modules/local/trident_embed/main.nf`; check for local edits. |
| `zero tiles` | Segmentation found no foreground. Try `--segmenter otsu`, or check the slide is readable by OpenSlide. |
| TRIDENT errors about MPP / magnification | Slide metadata lacks microns-per-pixel — add the `mpp` column for that row in the samplesheet. |
| CUDA OOM in `PRISM2_INFER` | Set `--max_tiles` to cap the slide (default 0, meaning all tiles). This is purely an OOM guard: PRISM2 compresses any tile count to 256 image tokens, so capping does not make the run cheaper or faster. |
| Yes/no scores tie across slides | bf16 quantises them onto a ~0.03 logit grid. Use `--scoring_dtype fp32` (needs a 40 GB GPU) for AUC or calibration. |
| `ImportError: flash_attn` | Wheel/base-image mismatch — see the flash-attn section above. |
| `Process requirement exceeds available memory` | Local executor vs the `gpu_large` label - add `-profile ...,local`. |
| `Failed to initialize WSI with OpenSlide: Unsupported or missing image file` | The container is not OpenSlide-readable even though its extension suggests it is. TRIDENT picks a reader from the extension with no probing and no fallback, so `.tiff` always goes to OpenSlide. The retry escalates to `--retry_reader` (default `image`); to be explicit, set the `reader` column for that row. |
| `Unable to extract MPP from slide metadata` | The file has no microns-per-pixel tag. This happens with generic TIFF and OME-TIFF, and also with some cleaned or converted svs, so do not assume Aperio implies MPP. Set the `mpp` column for that row, e.g. from `imaging_level2_metadata_current.PhysicalSizeX`. No retry can fix this, since there is no value to infer. |
| Non-pyramidal or exotic formats | Convert first: `trident convert --input_dir ... --mpp_csv ...` inside the trident image, then point the samplesheet at the converted TIFFs. |

## 6. Deviations from a full nf-core template

Deliberately lightweight: no `nf-schema` plugin (validation is inline in `main.nf`;
`nextflow_schema.json` is kept purely so the Seqera launch form is usable), no
`versions.yml` collection, no MultiQC, four local modules only.
