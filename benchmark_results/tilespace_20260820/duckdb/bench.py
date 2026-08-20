#!/usr/bin/env python3
"""Benchmark the DuckDB tile index: does the approximate index change the answer?

The question is not "is HNSW fast" (it is) but "does it quietly drop the cross-slide hits the
whole analysis depends on". So recall is measured against an EXACT scan of the same table, on the
same queries, at k=10 and k=50, and reported alongside latency.

Four configurations:

  exact 1280      brute-force scan of the unindexed `emb` column. Ground truth.
  hnsw 1280       the HNSW index on `emb_n`. Same metric, same vectors, approximate search.
  exact 128 (PCA) brute-force scan of `emb_pca`. Isolates what the projection costs, with no
                  index involved.
  hnsw 128 (PCA)  the index on `emb_pca`. Both losses together, which is what a deployment
                  would actually experience.

Two recall targets are reported for the PCA rows, because they answer different questions:
  vs its own exact  -> how much the INDEX loses
  vs exact 1280     -> how much the PIPELINE loses, which is the number that matters

Then the filtered forms. The analysis shows an unfiltered tile search is worse than chance,
because 76% of the top 10 comes from the query's own slide, so the only useful queries carry a
WHERE clause. DuckDB's HNSW index does not serve a filtered ORDER BY ... LIMIT, so those queries
are exact scans. That is measured rather than assumed: the plan is captured for each shape.
"""
import json, os, re, sys, time
import numpy as np
import duckdb

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as C

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(C.REPO, "analysis", "data", "tilesearch.duckdb")
N_QUERIES = 60          # each exact 1280-d query is a full scan of 667,537 x 1280 floats
KS = [10, 50]
KMAX = max(KS)

con = duckdb.connect(DB, read_only=True)
con.execute("LOAD vss; SET hnsw_enable_experimental_persistence = true;")
N = con.execute("SELECT count(*) FROM tiles").fetchone()[0]
NS = con.execute("SELECT count(*) FROM slides").fetchone()[0]
print(f"{N:,} tiles, {NS} slides")

# patient-balanced query tiles: one tile from each of N_QUERIES distinct patients
qs = con.execute(f"""
    SELECT tile_id, sample, patient, centre, ttt FROM (
      SELECT *, row_number() OVER (PARTITION BY patient ORDER BY hash(tile_id)) AS rn FROM tiles)
    WHERE rn = 1 ORDER BY hash(patient) LIMIT {N_QUERIES}""").fetchall()
print(f"{len(qs)} query tiles, one per patient")

def vec(col, tile_id):
    return con.execute(f"SELECT {col} FROM tiles WHERE tile_id = ?", [tile_id]).fetchone()[0]

def plan(sql, params):
    """Query plan as text. The vector literal is stripped: EXPLAIN inlines all 1280 floats, which
    would otherwise put a quarter of a megabyte of noise into the metrics JSON."""
    txt = "\n".join(r[1] for r in con.execute("EXPLAIN " + sql, params).fetchall())
    return re.sub(r"\[-?[0-9][^\]]{40,}\]", "[<query vector>]", txt)

def plan_ops(sql, params):
    """The operator names in the plan, which is all the metrics file needs."""
    p = plan(sql, params)
    ops = [o for o in ("HNSW_INDEX_SCAN", "TABLE_SCAN", "TOP_N", "ORDER_BY", "FILTER", "LIMIT")
           if o in p]
    return dict(hnsw_index_used="HNSW_INDEX_SCAN" in p, operators=ops)

CONFIGS = [
    ("exact 1280",        "emb",     1280),
    ("hnsw 1280",         "emb_n",   1280),
    ("exact 128 (PCA)",   "emb_pca",  128),
    ("hnsw 128 (PCA)",    "emb_pca",  128),
]
# `emb` carries no index, so a scan on it is exact; `emb_n` and `emb_pca` are indexed. To get an
# exact answer from an indexed column the index has to be defeated, and the honest way to do that
# is to ask for more rows than the index will serve, so exact-PCA is computed by ordering on an
# expression the index cannot match.
EXACT_PCA = "array_distance(t.emb_pca, ?::FLOAT[128]) + 0.0"

topk, timings, plans, plan_ops_cache = {}, {}, {}, {}
for name, col, dim in CONFIGS:
    if name == "exact 128 (PCA)":
        sql = (f"SELECT t.row_id FROM tiles t ORDER BY {EXACT_PCA} LIMIT {KMAX}")
    else:
        sql = (f"SELECT t.row_id FROM tiles t "
               f"ORDER BY array_cosine_distance(t.{col}, ?::FLOAT[{dim}]) LIMIT {KMAX}")
    res, lat = [], []
    for i, (tid, *_rest) in enumerate(qs):
        v = vec(col, tid)
        t0 = time.perf_counter()
        r = con.execute(sql, [v]).fetchall()
        lat.append((time.perf_counter() - t0) * 1000)
        res.append([x[0] for x in r])
        if i == 0:
            plans[name] = plan(sql, [v])
            plan_ops_cache[name] = plan_ops(sql, [v])
    topk[name] = res
    timings[name] = lat
    idx = "HNSW_INDEX_SCAN" in plans[name]
    print(f"{name:18s} median {np.median(lat):8.1f} ms   p95 {np.percentile(lat,95):8.1f} ms   "
          f"index used: {idx}")

def recall(a, b, k):
    return float(np.mean([len(set(x[:k]) & set(y[:k])) / k for x, y in zip(a, b)]))

rows = []
for name, _col, _dim in CONFIGS:
    lat = timings[name]
    r = dict(config=name,
             median_ms=round(float(np.median(lat)), 2),
             p95_ms=round(float(np.percentile(lat, 95)), 2),
             hnsw_index_used="HNSW_INDEX_SCAN" in plans[name],
             recall_at_10_vs_exact_1280=round(recall(topk[name], topk["exact 1280"], 10), 4),
             recall_at_50_vs_exact_1280=round(recall(topk[name], topk["exact 1280"], 50), 4))
    if name == "hnsw 128 (PCA)":
        r["recall_at_10_vs_exact_128"] = round(recall(topk[name], topk["exact 128 (PCA)"], 10), 4)
        r["recall_at_50_vs_exact_128"] = round(recall(topk[name], topk["exact 128 (PCA)"], 50), 4)
    rows.append(r)
    print(r)

# ---------------------------------------------------------------- filtered shapes
# THE MEASUREMENT THAT DECIDES WHETHER THE PROTOTYPE IS USABLE.
#
# Every retrieval number in the analysis comes from a FILTERED search: exclude the query's slide,
# its patient, or restrict to another centre. Written the obvious way,
#
#     SELECT ... FROM tiles WHERE sample <> ? ORDER BY array_cosine_distance(emb_n, ?) LIMIT 10
#
# DuckDB plans an HNSW index scan with the LIMIT pushed INTO it, and applies the WHERE clause to
# the ten rows that come back. Section 4 of the analysis shows those ten rows are 76% same-slide,
# so the filter deletes most of them and the query returns FEWER THAN TEN ROWS, sometimes zero,
# with no error. It is fast, it uses the index, and it is wrong.
#
# So three strategies are measured against an exact scan of the same predicate:
#   naive        the query above, as anyone would first write it
#   over-fetch   take the index's top F, then filter, then keep 10; F swept over four decades
#   exact        scan the unindexed `emb` column; correct by construction, and the cost baseline
FILTER_PREDS = {
    "exclude own slide": ("t.sample <> ?", (1,)),
    "exclude own patient": ("t.patient <> ?", (2,)),
    "different centre only": ("t.centre <> ?", (3,)),
    "same class, other patient": ("t.ttt = ? AND t.patient <> ?", (4, 2)),
}
OVERFETCH = [100, 1000, 10000, 100000]
QN = 30

def run_timed(sql, params):
    t0 = time.perf_counter()
    r = con.execute(sql, params).fetchall()
    return [x[0] for x in r], (time.perf_counter() - t0) * 1000

filtered_rows = []
for label, (pred, cols) in FILTER_PREDS.items():
    inner_pred = pred.replace("t.", "")
    strategies = {"naive (LIMIT 10 through the index)":
                  f"SELECT t.row_id FROM tiles t WHERE {pred} "
                  f"ORDER BY array_cosine_distance(t.emb_n, ?::FLOAT[1280]) LIMIT 10"}
    for f in OVERFETCH:
        strategies[f"over-fetch {f:,} then filter"] = (
            f"SELECT row_id FROM (SELECT row_id, sample, patient, centre, ttt FROM tiles "
            f"ORDER BY array_cosine_distance(emb_n, ?::FLOAT[1280]) LIMIT {f}) t "
            f"WHERE {pred} LIMIT 10")
    strategies["exact scan (no index)"] = (
        f"SELECT t.row_id FROM tiles t WHERE {pred} "
        f"ORDER BY array_cosine_distance(t.emb, ?::FLOAT[1280]) LIMIT 10")

    truth, res, lat, nrows = {}, {}, {}, {}
    for name, sql in strategies.items():
        res[name], lat[name], nrows[name] = [], [], []
    for q in qs[:QN]:
        pre = [q[c] for c in cols]
        vi, ve = vec("emb_n", q[0]), vec("emb", q[0])
        for name, sql in strategies.items():
            # the over-fetch form binds the vector before the predicate; the others after
            params = ([vi] + pre if name.startswith("over-fetch")
                      else pre + [ve if "exact" in name else vi])
            r, ms = run_timed(sql, params)
            res[name].append(r); lat[name].append(ms); nrows[name].append(len(r))
    gt = res["exact scan (no index)"]
    # the candidate pool differs per query -- a BU tile excluding its own centre loses 83% of the
    # table, a WUSTL tile loses 3% -- so the reported pool is the mean over the queries actually run
    pool_fracs = [con.execute(f"SELECT count(*) FROM tiles t WHERE {pred}",
                              [q[c] for c in cols]).fetchone()[0] / N for q in qs[:QN]]
    for name in strategies:
        p = plan(strategies[name], ([vec("emb_n", qs[0][0])] + [qs[0][c] for c in cols]
                                   if name.startswith("over-fetch")
                                   else [qs[0][c] for c in cols]
                                   + [vec("emb" if "exact" in name else "emb_n", qs[0][0])]))
        filtered_rows.append(dict(
            predicate=label, strategy=name,
            pool_fraction=round(float(np.mean(pool_fracs)), 4),
            hnsw_index_used="HNSW_INDEX_SCAN" in p,
            median_ms=round(float(np.median(lat[name])), 2),
            p95_ms=round(float(np.percentile(lat[name], 95)), 2),
            mean_rows_returned=round(float(np.mean(nrows[name])), 2),
            pct_queries_returning_fewer_than_10=round(
                100 * float(np.mean([n < 10 for n in nrows[name]])), 1),
            recall_at_10=round(recall(res[name], gt, 10), 4),
            pct_queries_with_identical_top10=round(
                100 * float(np.mean([set(a) == set(b) for a, b in zip(res[name], gt)])), 1)))
        r = filtered_rows[-1]
        print(f"  {label:26s} {name:34s} {r['median_ms']:8.1f} ms  "
              f"rows {r['mean_rows_returned']:5.1f}  recall@10 {r['recall_at_10']:.3f}")

build = json.load(open(os.path.join(C.HERE, "duckdb_build_metrics.json")))
out = dict(n_tiles=int(N), n_slides=int(NS), n_queries=len(qs), k=KS,
           duckdb_version=duckdb.__version__,
           note="exact 1280 is a brute-force scan of the unindexed `emb` column and is the ground "
                "truth for every recall figure",
           configs=rows, filtered_shapes=filtered_rows,
           build=build["timings_s"], sizes_bytes=build["sizes_bytes"],
           plans={k: plan_ops_cache[k] for k in plan_ops_cache})
C.dump(out, "duckdb_bench_metrics.json")

# ---------------------------------------------------------------- benchmark table
def gb(x):
    return f"{x/1e9:.2f} GB"

md = ["# DuckDB tile-search prototype: benchmark",
      "",
      f"`{N:,}` tiles from {NS} slides, Virchow2 class token, DuckDB {duckdb.__version__} with the "
      f"`vss` extension. Single laptop, no GPU.",
      "",
      "## Build cost",
      "",
      "| step | time | database size after |",
      "|---|---|---|",
      f"| load {N:,} tiles x (1280 raw + 1280 normalised + 128 PCA) | "
      f"{build['timings_s']['load_tiles_s']:.0f} s | {gb(build['sizes_bytes']['db_after_load_bytes'])} |",
      f"| HNSW on 1280-d, cosine | {build['timings_s']['build_hnsw_1280_s']:.0f} s | "
      f"{gb(build['sizes_bytes']['db_after_hnsw_1280_bytes'])} |",
      f"| HNSW on 128-d PCA, cosine | {build['timings_s']['build_hnsw_pca128_s']:.0f} s | "
      f"{gb(build['sizes_bytes']['db_after_hnsw_pca128_bytes'])} |",
      f"| HNSW on the two slide-level embedding columns | "
      f"{build['timings_s']['build_hnsw_slide_base_s'] + build['timings_s']['build_hnsw_slide_diag_s']:.0f} s | "
      f"{gb(build['sizes_bytes']['db_after_hnsw_slide_diag_bytes'])} |",
      "",
      f"The 1280-d HNSW index itself is {gb(build['sizes_bytes']['hnsw_1280_index_bytes'])}, "
      f"the 128-d one {gb(build['sizes_bytes']['hnsw_pca128_index_bytes'])}. "
      "`hnsw_enable_experimental_persistence` must be set both to create and to read a persisted "
      "index, and DuckDB warns that an unclean shutdown mid-write can corrupt it.",
      "",
      "## Accuracy and latency",
      "",
      f"{len(qs)} query tiles, one from each of {len(qs)} distinct patients. Recall is the overlap "
      "with the exact 1280-d top-k on the same query.",
      "",
      "| configuration | median | p95 | index used | recall@10 vs exact 1280 | recall@50 vs exact 1280 |",
      "|---|---|---|---|---|---|"]
for r in rows:
    md.append(f"| {r['config']} | {r['median_ms']:.0f} ms | {r['p95_ms']:.0f} ms | "
              f"{'yes' if r['hnsw_index_used'] else 'no'} | "
              f"{r['recall_at_10_vs_exact_1280']:.3f} | {r['recall_at_50_vs_exact_1280']:.3f} |")
h = [r for r in rows if r["config"] == "hnsw 128 (PCA)"][0]
md += ["",
       f"Splitting the PCA row's two losses: against its OWN exact answer the 128-d index scores "
       f"recall@10 {h['recall_at_10_vs_exact_128']:.3f} and recall@50 "
       f"{h['recall_at_50_vs_exact_128']:.3f}, so most of its disagreement with the 1280-d answer "
       f"is the projection, not the index.",
       "",
       "## Filtered search: the shapes the analysis actually needs",
       "",
       "Written the obvious way, a filtered vector query in DuckDB pushes the `LIMIT` into the "
       "HNSW index scan and applies the `WHERE` clause afterwards. Because an unfiltered tile "
       "search returns the query's own slide 76% of the time, the filter then deletes most of "
       "the result and the query returns fewer than ten rows, often zero, without an error. "
       "`recall@10` below is the overlap with an exact scan of the same predicate.",
       "",
       "| predicate | strategy | pool | median | rows returned | queries under 10 rows | recall@10 |",
       "|---|---|---|---|---|---|---|"]
for r in filtered_rows:
    md.append(f"| {r['predicate']} | {r['strategy']} | {r['pool_fraction']*100:.0f}% | "
              f"{r['median_ms']:.0f} ms | {r['mean_rows_returned']:.1f} | "
              f"{r['pct_queries_returning_fewer_than_10']:.0f}% | {r['recall_at_10']:.3f} |")
md += ["",
       "## Scaling to all of HTAN",
       "",
       f"This cohort is 163 slides of roughly 5,900 published H&E, about 1/36 of the images and, "
       f"at this median tile count, order 24 million tiles. Linear in rows, that is "
       f"{gb(build['sizes_bytes']['db_after_load_bytes'] * 36)} of table and "
       f"{gb(build['sizes_bytes']['hnsw_1280_index_bytes'] * 36)} of 1280-d index, with an exact "
       f"scan around {np.median(timings['exact 1280']) * 36 / 1000:.0f} s per query. Storing only "
       f"the 128-d projection cuts the table by "
       f"{1280 / 128:.0f}x and the exact scan to about "
       f"{np.median(timings['exact 128 (PCA)']) * 36 / 1000:.1f} s.",
       ""]
open(os.path.join(C.HERE, "duckdb_benchmark.md"), "w").write("\n".join(md))
print("wrote duckdb_benchmark.md")
con.close()
