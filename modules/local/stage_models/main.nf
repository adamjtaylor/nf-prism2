/*
 * Download the gated model weights once, then hand them to every task.
 *
 * Split into two caches on purpose. The tile pass needs Virchow2 (~2.5 GB) and the segmenter,
 * while the ~17 GB of PRISM2 weights are only needed by the slide pass. Sharing one directory
 * meant every TRIDENT_EMBED task staged 20 GB it never opened, which dominated wall clock when
 * several slides pack onto one instance.
 *
 * Doing this in a single task also means gating and licence failures surface immediately and
 * cheaply, with no GPU burned, and the weights are fetched once per run rather than per slide.
 */
process STAGE_MODELS {
    tag 'hf_weights'
    label 'process_low'
    secret params.hf_token_secret

    // Populate the persistent store on the way past, so the next run skips this process
    // entirely. Enabled only when --model_store is set.
    publishDir path: { params.model_store }, mode: 'copy', enabled: params.model_store as boolean

    output:
    path 'tile_cache' , emit: tile
    path 'slide_cache', emit: slide

    script:
    def extra = params.hf_repos_extra ? params.hf_repos_extra.tokenize(',')*.trim().findAll() : []
    """
    # the task env var is named after the secret, so normalise it to HF_TOKEN
    export HF_TOKEN="\${${params.hf_token_secret}:-}"
    export HF_HUB_ENABLE_HF_TRANSFER=0
    mkdir -p tile_cache slide_cache

    if [ -z "\${HF_TOKEN:-}" ]; then
        echo "ERROR: secret '${params.hf_token_secret}' is empty. paige-ai/Virchow2 and paige-ai/Prism2 are gated." >&2
        echo "       nextflow secrets set ${params.hf_token_secret} \\"hf_...\\"   (local)" >&2
        echo "       tw secrets add -w <WS_ID> -n ${params.hf_token_secret} -v hf_...  (Seqera workspace)" >&2
        echo "       or point --hf_token_secret at an existing secret name" >&2
        exit 1
    fi

    # --- tile cache: Virchow2 plus the segmenter weights -------------------
    export HF_HOME=\$PWD/tile_cache
    hf download paige-ai/Virchow2
    ${extra.collect { "hf download ${it} || echo 'WARN: could not pre-stage ${it}' >&2" }.join('\n    ')}

    # --- slide cache: PRISM2 only ------------------------------------------
    export HF_HOME=\$PWD/slide_cache
    hf download paige-ai/Prism2

    # Markers are what the next run checks for. Written last, so a half-finished download is
    # never mistaken for a usable cache.
    touch tile_cache/.complete slide_cache/.complete

    du -sh tile_cache slide_cache
    """

    stub:
    """
    mkdir -p tile_cache/hub slide_cache/hub
    touch tile_cache/hub/.stub slide_cache/hub/.stub
    touch tile_cache/.complete slide_cache/.complete
    """
}
