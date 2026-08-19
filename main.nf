#!/usr/bin/env nextflow
/*
 * nf-prism2: OpenSlide WSI -> Virchow2 tile embeddings -> PRISM2 slide inference
 *
 * PRISM2 (paige-ai/Prism2) consumes Virchow2 CLASS-TOKEN embeddings only, (N, 1280),
 * from 224 px tiles at 20x (0.5 mpp). TRIDENT exposes exactly that encoder as
 * `virchow2-cls`, so tiling + tissue segmentation + tile embedding is one step.
 */

nextflow.enable.dsl = 2

include { STAGE_MODELS    } from './modules/local/stage_models'
include { TRIDENT_EMBED   } from './modules/local/trident_embed'
include { PRISM2_INFER    } from './modules/local/prism2_infer'
include { COLLECT_RESULTS } from './modules/local/collect_results'

def helpMessage() {
    log.info """
    nf-prism2 ${workflow.manifest.version}

    Usage:
      nextflow run . -profile awsbatch --input samplesheet.csv --outdir s3://bucket/results

    Required:
      --input         Samplesheet CSV with columns: sample,slide[,mpp]
      --outdir        Output directory (local path or s3://)

    Common options:
      --questions     Question set YAML (default: ${params.questions})
      --hf_cache      Pre-staged HuggingFace cache dir; skips STAGE_MODELS
      --max_tiles     OOM guard only; 0 (default) uses every tile. PRISM2 is constant-cost
                      in tile count, so subsampling saves nothing.
      --scoring_dtype bf16 (default) or fp32. Use fp32 for AUC or calibration work, see docs.
      --segmenter     TRIDENT tissue segmenter: hest | grandqc | otsu (default: ${params.segmenter})
      --gpu_queue     AWS Batch queue for GPU tasks (awsbatch profile)

    Model access:
      paige-ai/Virchow2 and paige-ai/Prism2 are gated, CC-BY-NC-ND 4.0 (non-commercial
      research only, no clinical use). Provide HF_TOKEN as a Nextflow secret for an
      account that has accepted both licences:  nextflow secrets set HF_TOKEN "hf_..."
    """.stripIndent()
}

workflow {

    if (params.help) {
        helpMessage()
        return
    }
    if (!params.input) {
        error "Missing --input. Provide a samplesheet CSV with columns sample,slide[,mpp]. See --help."
    }

    // --- samplesheet -------------------------------------------------------
    ch_slides = Channel.fromPath(params.input, checkIfExists: true)
        .splitCsv(header: true, strip: true)
        .map { row ->
            if (!row.sample || !row.slide) {
                error "Samplesheet rows need non-empty 'sample' and 'slide' columns; got: ${row}"
            }
            if (row.mpp && !(row.mpp ==~ /^[0-9]*\.?[0-9]+$/)) {
                error "Sample '${row.sample}': 'mpp' must be a number, got '${row.mpp}'"
            }
            def meta = [ id: row.sample.toString(), mpp: (row.mpp ?: '').toString() ]
            tuple(meta, file(row.slide, checkIfExists: true))
        }

    ch_slides
        .map { meta, slide -> meta.id }
        .toList()
        .map { ids ->
            def dupes = ids.countBy { it }.findAll { k, v -> v > 1 }.keySet()
            if (dupes) error "Duplicate sample ids in samplesheet: ${dupes.join(', ')}"
        }

    ch_questions = file(params.questions, checkIfExists: true)

    // --- model weights: staged once, reused by every task ------------------
    // .first() makes this a value channel so every slide task reuses the same cache
    // (on Nextflow <25 a single-item process output is a queue channel and would only
    // feed one downstream task; on >=26 it is already a value channel and the operator
    // is a harmless no-op).
    ch_cache = params.hf_cache
        ? Channel.fromPath(params.hf_cache, type: 'dir', checkIfExists: true).first()
        : STAGE_MODELS().cache.first()

    // --- tile + embed with Virchow2 (class token, 1280-d) ------------------
    TRIDENT_EMBED(ch_slides, ch_cache)

    // --- PRISM2 aggregation + question answering ---------------------------
    PRISM2_INFER(TRIDENT_EMBED.out.features, ch_cache, ch_questions)

    // --- merge per-slide JSON into one table -------------------------------
    COLLECT_RESULTS(PRISM2_INFER.out.json.map { meta, json -> json }.collect())
}
