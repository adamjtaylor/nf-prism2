/*
 * PRISM2: aggregate the Virchow2 tile embeddings into slide representations and answer
 * the whole question set in a single model load.
 */
process PRISM2_INFER {
    tag "${meta.id}"
    label 'gpu_large'
    secret 'HF_TOKEN'

    publishDir path: { "${params.outdir}/prism2/${meta.id}" }, mode: params.publish_dir_mode

    input:
    tuple val(meta), path(features)
    path hf_cache
    path questions

    output:
    tuple val(meta), path("${meta.id}.prism2.json"), emit: json
    tuple val(meta), path("${meta.id}.embeddings.npz"), emit: embeddings, optional: true
    path "${meta.id}.prism2.txt", emit: report

    script:
    def save_emb = params.save_embeddings ? '--save-embeddings' : ''
    """
    export HF_HOME=\$PWD/${hf_cache}
    export OMP_NUM_THREADS=${task.cpus}
    export TOKENIZERS_PARALLELISM=false

    prism2_infer.py \\
        --sample ${meta.id} \\
        --features ${features} \\
        --questions ${questions} \\
        --out-json ${meta.id}.prism2.json \\
        --out-report ${meta.id}.prism2.txt \\
        --out-npz ${meta.id}.embeddings.npz \\
        --max-tiles ${params.max_tiles} \\
        --max-new-tokens ${params.max_new_tokens} \\
        --seed ${params.seed} \\
        ${save_emb}
    """

    stub:
    // Mirrors the real JSON schema so -stub also exercises COLLECT_RESULTS
    """
    cat <<'JSON' > ${meta.id}.prism2.json
    {
      "sample": "${meta.id}",
      "model_id": "paige-ai/Prism2",
      "n_tiles_total": 0,
      "n_tiles_used": 0,
      "tile_embedding_dim": 1280,
      "base_embedding_dim": 2560,
      "diagnostic_embedding_dim": 3072,
      "yes_no": {"stub_question": {"question": "stub?", "score": 0.0}},
      "open_ended": {"stub_prompt": {"prompt": "stub?", "answer": "stub answer"}},
      "multiple_choice": {},
      "report_prompt": "Write a report",
      "report": "stub report"
    }
JSON
    echo 'stub report' > ${meta.id}.prism2.txt
    touch ${meta.id}.embeddings.npz
    """
}
