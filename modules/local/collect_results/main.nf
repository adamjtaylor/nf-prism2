/*
 * Flatten the per-slide JSON into one wide table.
 *
 * Runs in a plain python image (stdlib only). Not `-slim`: AWS Batch task metrics need
 * `ps` from procps, which the slim image does not ship.
 *
 * Also reconciles what arrived against what was expected, because with
 * `--ignore_failed_slides` a dropped slide would otherwise just quietly not appear.
 */
process COLLECT_RESULTS {
    tag 'collect'
    label 'process_low'

    publishDir "${params.outdir}", mode: params.publish_dir_mode

    input:
    path json_files, stageAs: 'json/*'
    path expected

    output:
    path 'results.tsv'        , emit: tsv
    path 'results.json'       , emit: json
    path 'failed_samples.txt' , emit: failed

    script:
    """
    mkdir -p json
    collect_results.py \\
        --in-dir json \\
        --expected ${expected} \\
        --out-tsv results.tsv \\
        --out-json results.json \\
        --out-failed failed_samples.txt
    """
}
