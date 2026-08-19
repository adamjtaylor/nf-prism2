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

    input:
    tuple val(meta), path(slide, stageAs: 'wsi/*')
    path tile_cache

    output:
    tuple val(meta), path("${meta.id}.features.h5"), emit: features
    path 'qc/*'                                    , emit: qc, optional: true

    script:
    def mpp_col   = meta.mpp ? 'wsi,mpp' : 'wsi'
    def mpp_row   = meta.mpp ? "\${SLIDE},${meta.mpp}" : "\${SLIDE}"
    def artifacts = params.remove_artifacts ? '--remove_artifacts' : ''
    def penmarks  = params.remove_penmarks  ? '--remove_penmarks'  : ''
    def holes     = params.remove_holes     ? '--remove_holes'     : ''
    def reader    = params.reader_type      ? "--reader_type ${params.reader_type}" : ''
    """
    export HF_TOKEN="\${${params.hf_token_secret}:-}"
    export HF_HOME=\$PWD/${tile_cache}
    export TORCH_HOME=\$PWD/${tile_cache}/torch
    export TRIDENT_HOME=\${TRIDENT_HOME:-/opt/trident}
    export OMP_NUM_THREADS=${task.cpus}

    SLIDE=\$(basename "${slide}")

    # One-row work list: this is also how a per-slide mpp override is supplied for
    # slides whose OpenSlide metadata has no MPP.
    printf '%s\\n%s\\n' '${mpp_col}' "${mpp_row}" > slide_list.csv

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
