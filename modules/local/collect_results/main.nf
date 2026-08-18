/*
 * Flatten the per-slide JSON into one wide table.
 *
 * Runs in a plain python image (stdlib only). Not `-slim`: AWS Batch task metrics need
 * `ps` from procps, which the slim image does not ship.
 */
process COLLECT_RESULTS {
    tag 'collect'
    label 'process_low'

    publishDir "${params.outdir}", mode: params.publish_dir_mode

    input:
    path json_files, stageAs: 'json/*'

    output:
    path 'results.tsv' , emit: tsv
    path 'results.json', emit: json

    script:
    """
    collect_results.py --in-dir json --out-tsv results.tsv --out-json results.json
    """
}
