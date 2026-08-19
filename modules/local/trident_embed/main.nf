/*
 * Tissue segmentation + 224 px @ 20x tiling + Virchow2 CLASS-TOKEN embeddings, via TRIDENT.
 *
 * `virchow2-cls` is the encoder PRISM2 expects: (N, 1280). The plain `virchow2` encoder
 * returns the 2560-d class+mean concat and is NOT interchangeable here.
 */
process TRIDENT_EMBED {
    tag "${meta.id}"
    label 'gpu_small'
    secret params.hf_token_secret

    publishDir path: { "${params.outdir}/tiles/${meta.id}" }, mode: params.publish_dir_mode,
        pattern: 'qc/*'
    // Tile embeddings are an Aim 1.4 deliverable in their own right, but they are bulky
    // (about 630 MB per 10 slides), so publishing them is opt-in.
    publishDir path: { "${params.outdir}/tile_features" }, mode: params.publish_dir_mode,
        pattern: '*.features.h5', enabled: params.publish_tile_features

    input:
    tuple val(meta), path(slide, stageAs: 'wsi/*')
    path tile_cache

    output:
    tuple val(meta), path("${meta.id}.features.h5"), emit: features
    path 'qc/*'                                    , emit: qc, optional: true

    script:
    // Taken from Groovy, not from the shell: Nextflow escapes interpolated paths, so both
    // `basename "${slide}"` (keeps the backslashes) and `basename ${slide}` (relies on shell
    // unescaping) are fragile. HTAN filenames do contain spaces.
    def slide_name = slide.name.tokenize('/').last()   // stageAs puts it under wsi/
    def mpp_col    = meta.mpp ? 'wsi,mpp' : 'wsi'
    def mpp_row    = meta.mpp ? "${slide_name},${meta.mpp}" : slide_name
    def artifacts = params.remove_artifacts ? '--remove_artifacts' : ''
    def penmarks  = params.remove_penmarks  ? '--remove_penmarks'  : ''
    def holes     = params.remove_holes     ? '--remove_holes'     : ''
    // Reader selection, in precedence order:
    //   1. the samplesheet's per-slide `reader`
    //   2. the global --reader_type
    //   3. nothing on the first attempt, so TRIDENT auto-detects
    //   4. --retry_reader on a retry, because TRIDENT picks its reader from the file extension
    //      with no probing and no fallback. A plain .tiff matches OPENSLIDE_EXTENSIONS, so an
    //      OpenSlide-unreadable TIFF fails hard even though ImageWSI would have opened it.
    //      Escalating on retry self-heals that whole class without curating a column for
    //      thousands of files. It cannot fix a missing MPP, which has no value to infer.
    def reader_type = meta.reader ?: params.reader_type ?:
                      (task.attempt > 1 ? params.retry_reader : null)
    def reader    = reader_type             ? "--reader_type ${reader_type}" : ''
    """
    export HF_TOKEN="\${${params.hf_token_secret}:-}"
    export HF_HOME=\$PWD/${tile_cache}
    export TORCH_HOME=\$PWD/${tile_cache}/torch
    export TRIDENT_HOME=\${TRIDENT_HOME:-/opt/trident}
    export OMP_NUM_THREADS=${task.cpus}

    # One-row work list: this is also how a per-slide mpp override is supplied for
    # slides whose OpenSlide metadata has no MPP.
    printf '%s\\n%s\\n' '${mpp_col}' '${mpp_row}' > slide_list.csv

    python \$TRIDENT_HOME/run_batch_of_slides.py \\
        --task all \\
        --wsi_dir wsi \\
        --custom_list_of_wsis slide_list.csv \\
        --job_dir trident \\
        --patch_encoder virchow2-cls \\
        --mag ${params.mag} \\
        --patch_size ${params.patch_size} \\
        --overlap 0 \\
        --segmenter ${params.segmenter} \\
        --seg_conf_thresh ${params.seg_conf_thresh} \\
        --batch_size ${params.tile_batch_size} \\
        --gpus 0 ${artifacts} ${penmarks} ${holes} ${reader}

    # Locate the feature file without hard-coding TRIDENT's directory naming
    FEAT=\$(find trident -path '*features_*' -name '*.h5' -print -quit)
    if [ -z "\$FEAT" ]; then
        echo "ERROR: TRIDENT produced no feature h5 under trident/. See summary.md below." >&2
        cat trident/summary.md 2>/dev/null || true
        exit 1
    fi
    cp "\$FEAT" ${meta.id}.features.h5

    # QC artefacts worth keeping (thumbnail, tissue contours, tile overlay)
    mkdir -p qc
    find trident -type f \\( -name '*.jpg' -o -name '*.png' -o -name '*.geojson' -o -name 'summary.md' \\) \\
        -print0 | xargs -0 -r -I{} cp {} qc/ || true
    """

    stub:
    """
    mkdir -p qc
    touch ${meta.id}.features.h5 qc/${meta.id}_thumbnail.jpg
    """
}
