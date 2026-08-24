# DuckDB tile-search prototype: benchmark

`859,342` tiles from 175 slides, Virchow2 class token, DuckDB 1.5.5 with the `vss` extension. Single laptop, no GPU.

## Build cost

| step | time | database size after |
|---|---|---|
| load 859,342 tiles x (1280 raw + 1280 normalised + 128 PCA) | 215 s | 16.35 GB |
| HNSW on 1280-d, cosine | 129 s | 19.03 GB |
| HNSW on 128-d PCA, cosine | 24 s | 19.59 GB |
| HNSW on the two slide-level embedding columns | 0 s | 19.60 GB |

The 1280-d HNSW index itself is 2.67 GB, the 128-d one 0.57 GB. `hnsw_enable_experimental_persistence` must be set both to create and to read a persisted index, and DuckDB warns that an unclean shutdown mid-write can corrupt it.

## Accuracy and latency

60 query tiles, one from each of 60 distinct patients. Recall is the overlap with the exact 1280-d top-k on the same query.

| configuration | median | p95 | index used | recall@10 vs exact 1280 | recall@50 vs exact 1280 |
|---|---|---|---|---|---|
| exact 1280 | 640 ms | 1138 ms | no | 1.000 | 1.000 |
| hnsw 1280 | 28 ms | 86 ms | yes | 0.947 | 0.944 |
| exact 128 (PCA) | 43 ms | 115 ms | no | 0.770 | 0.794 |
| hnsw 128 (PCA) | 3 ms | 5 ms | yes | 0.757 | 0.792 |

Splitting the PCA row's two losses: against its OWN exact answer the 128-d index scores recall@10 0.935 and recall@50 0.934, so most of its disagreement with the 1280-d answer is the projection, not the index.

## Filtered search: the shapes the analysis actually needs

Written the obvious way, a filtered vector query in DuckDB pushes the `LIMIT` into the HNSW index scan and applies the `WHERE` clause afterwards. Because an unfiltered tile search returns the query's own slide 76% of the time, the filter then deletes most of the result and the query returns fewer than ten rows, often zero, without an error. `recall@10` below is the overlap with an exact scan of the same predicate.

| predicate | strategy | pool | median | rows returned | queries under 10 rows | recall@10 |
|---|---|---|---|---|---|---|
| exclude own slide | naive (LIMIT 10 through the index) | 99% | 11 ms | 2.6 | 97% | 0.227 |
| exclude own slide | over-fetch 100 then filter | 99% | 43 ms | 7.4 | 30% | 0.703 |
| exclude own slide | over-fetch 1,000 then filter | 99% | 597 ms | 9.4 | 7% | 0.937 |
| exclude own slide | over-fetch 10,000 then filter | 99% | 656 ms | 9.7 | 3% | 0.970 |
| exclude own slide | over-fetch 100,000 then filter | 99% | 640 ms | 10.0 | 0% | 1.000 |
| exclude own slide | exact scan (no index) | 99% | 713 ms | 10.0 | 0% | 1.000 |
| exclude own patient | naive (LIMIT 10 through the index) | 99% | 18 ms | 2.4 | 97% | 0.207 |
| exclude own patient | over-fetch 100 then filter | 99% | 7 ms | 6.8 | 37% | 0.640 |
| exclude own patient | over-fetch 1,000 then filter | 99% | 66 ms | 9.4 | 7% | 0.937 |
| exclude own patient | over-fetch 10,000 then filter | 99% | 699 ms | 9.7 | 3% | 0.970 |
| exclude own patient | over-fetch 100,000 then filter | 99% | 670 ms | 10.0 | 0% | 1.000 |
| exclude own patient | exact scan (no index) | 99% | 734 ms | 10.0 | 0% | 1.000 |
| different centre only | naive (LIMIT 10 through the index) | 40% | 15 ms | 0.3 | 97% | 0.000 |
| different centre only | over-fetch 100 then filter | 40% | 4 ms | 0.4 | 97% | 0.003 |
| different centre only | over-fetch 1,000 then filter | 40% | 41 ms | 1.4 | 87% | 0.137 |
| different centre only | over-fetch 10,000 then filter | 40% | 649 ms | 4.5 | 57% | 0.447 |
| different centre only | over-fetch 100,000 then filter | 40% | 669 ms | 8.1 | 20% | 0.810 |
| different centre only | exact scan (no index) | 40% | 547 ms | 10.0 | 0% | 1.000 |
| same class, other patient | naive (LIMIT 10 through the index) | 24% | 17 ms | 0.9 | 100% | 0.073 |
| same class, other patient | over-fetch 100 then filter | 24% | 5 ms | 5.5 | 53% | 0.517 |
| same class, other patient | over-fetch 1,000 then filter | 24% | 50 ms | 8.9 | 13% | 0.870 |
| same class, other patient | over-fetch 10,000 then filter | 24% | 646 ms | 9.7 | 3% | 0.967 |
| same class, other patient | over-fetch 100,000 then filter | 24% | 689 ms | 10.0 | 0% | 1.000 |
| same class, other patient | exact scan (no index) | 24% | 617 ms | 10.0 | 0% | 1.000 |

## Scaling to all of HTAN

This cohort is 163 slides of roughly 5,900 published H&E, about 1/36 of the images and, at this median tile count, order 24 million tiles. Linear in rows, that is 588.71 GB of table and 96.24 GB of 1280-d index, with an exact scan around 23 s per query. Storing only the 128-d projection cuts the table by 10x and the exact scan to about 1.5 s.
