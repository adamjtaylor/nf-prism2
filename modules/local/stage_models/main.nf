/*
 * Download the gated model weights once, then hand the HF cache to every task.
 *
 * Doing this in a single task means: gating/licence failures surface immediately and
 * cheaply (no GPU burned), no HF rate limiting from N parallel slides, and the ~10 GB
 * of weights is fetched once instead of once per slide.
 */
process STAGE_MODELS {
    tag 'hf_weights'
    label 'process_low'
    secret 'HF_TOKEN'

    output:
    path 'hf_cache', emit: cache

    script:
    def extra = params.hf_repos_extra ? params.hf_repos_extra.tokenize(',')*.trim().findAll() : []
    """
    export HF_HOME=\$PWD/hf_cache
    export HF_HUB_ENABLE_HF_TRANSFER=0
    mkdir -p hf_cache

    if [ -z "\${HF_TOKEN:-}" ]; then
        echo "ERROR: HF_TOKEN is empty. Both paige-ai/Virchow2 and paige-ai/Prism2 are gated." >&2
        echo "       Run: nextflow secrets set HF_TOKEN \\"hf_...\\"  (or add it as a Seqera secret)" >&2
        exit 1
    fi

    # Required, gated (CC-BY-NC-ND 4.0) - fail hard if the token lacks access
    hf download paige-ai/Virchow2
    hf download paige-ai/Prism2

    # Best-effort extras (e.g. TRIDENT segmenter weights); a miss is not fatal because
    # TRIDENT can still fetch them at runtime.
    ${extra.collect { "hf download ${it} || echo 'WARN: could not pre-stage ${it}' >&2" }.join('\n    ')}

    du -sh hf_cache
    """

    stub:
    """
    mkdir -p hf_cache/hub
    touch hf_cache/hub/.stub
    """
}
