# DuckDB tile-search prototype: benchmark

`667,537` tiles from 163 slides, Virchow2 class token, DuckDB 1.5.5 with the `vss` extension. Single laptop, no GPU.

## Build cost

| step | time | database size after |
|---|---|---|
| load 667,537 tiles x (1280 raw + 1280 normalised + 128 PCA) | 172 s | 12.73 GB |
| HNSW on 1280-d, cosine | 68 s | 14.78 GB |
| HNSW on 128-d PCA, cosine | 10 s | 15.22 GB |
| HNSW on the two slide-level embedding columns | 0 s | 15.23 GB |

The 1280-d HNSW index itself is 2.05 GB, the 128-d one 0.44 GB. `hnsw_enable_experimental_persistence` must be set both to create and to read a persisted index, and DuckDB warns that an unclean shutdown mid-write can corrupt it.

## Accuracy and latency

60 query tiles, one from each of 60 distinct patients. Recall is the overlap with the exact 1280-d top-k on the same query.

| configuration | median | p95 | index used | recall@10 vs exact 1280 | recall@50 vs exact 1280 |
|---|---|---|---|---|---|
| exact 1280 | 506 ms | 627 ms | no | 1.000 | 1.000 |
| hnsw 1280 | 17 ms | 57 ms | yes | 0.917 | 0.913 |
| exact 128 (PCA) | 32 ms | 48 ms | no | 0.785 | 0.805 |
| hnsw 128 (PCA) | 2 ms | 4 ms | yes | 0.785 | 0.816 |

Splitting the PCA row's two losses: against its OWN exact answer the 128-d index scores recall@10 0.953 and recall@50 0.952, so most of its disagreement with the 1280-d answer is the projection, not the index.

## Filtered search: the shapes the analysis actually needs

Written the obvious way, a filtered vector query in DuckDB pushes the `LIMIT` into the HNSW index scan and applies the `WHERE` clause afterwards. Because an unfiltered tile search returns the query's own slide 76% of the time, the filter then deletes most of the result and the query returns fewer than ten rows, often zero, without an error. `recall@10` below is the overlap with an exact scan of the same predicate.

| predicate | strategy | pool | median | rows returned | queries under 10 rows | recall@10 |
|---|---|---|---|---|---|---|
| exclude own slide | naive (LIMIT 10 through the index) | 99% | 6 ms | 2.8 | 93% | 0.183 |
| exclude own slide | over-fetch 100 then filter | 99% | 4 ms | 7.8 | 27% | 0.707 |
| exclude own slide | over-fetch 1,000 then filter | 99% | 27 ms | 9.4 | 7% | 0.900 |
| exclude own slide | over-fetch 10,000 then filter | 99% | 555 ms | 9.7 | 3% | 0.970 |
| exclude own slide | over-fetch 100,000 then filter | 99% | 526 ms | 10.0 | 0% | 1.000 |
| exclude own slide | exact scan (no index) | 99% | 528 ms | 10.0 | 0% | 1.000 |
| exclude own patient | naive (LIMIT 10 through the index) | 99% | 6 ms | 2.7 | 93% | 0.177 |
| exclude own patient | over-fetch 100 then filter | 99% | 4 ms | 7.4 | 30% | 0.673 |
| exclude own patient | over-fetch 1,000 then filter | 99% | 25 ms | 9.4 | 7% | 0.900 |
| exclude own patient | over-fetch 10,000 then filter | 99% | 544 ms | 9.7 | 3% | 0.970 |
| exclude own patient | over-fetch 100,000 then filter | 99% | 527 ms | 10.0 | 0% | 1.000 |
| exclude own patient | exact scan (no index) | 99% | 535 ms | 10.0 | 0% | 1.000 |
| different centre only | naive (LIMIT 10 through the index) | 45% | 11 ms | 0.7 | 93% | 0.003 |
| different centre only | over-fetch 100 then filter | 45% | 4 ms | 0.4 | 97% | 0.003 |
| different centre only | over-fetch 1,000 then filter | 45% | 31 ms | 1.2 | 90% | 0.120 |
| different centre only | over-fetch 10,000 then filter | 45% | 537 ms | 4.3 | 60% | 0.427 |
| different centre only | over-fetch 100,000 then filter | 45% | 522 ms | 9.2 | 10% | 0.917 |
| different centre only | exact scan (no index) | 45% | 283 ms | 10.0 | 0% | 1.000 |
| same class, other patient | naive (LIMIT 10 through the index) | 23% | 10 ms | 0.8 | 100% | 0.073 |
| same class, other patient | over-fetch 100 then filter | 23% | 4 ms | 6.4 | 43% | 0.637 |
| same class, other patient | over-fetch 1,000 then filter | 23% | 31 ms | 8.9 | 13% | 0.890 |
| same class, other patient | over-fetch 10,000 then filter | 23% | 525 ms | 9.7 | 3% | 0.967 |
| same class, other patient | over-fetch 100,000 then filter | 23% | 512 ms | 10.0 | 0% | 1.000 |
| same class, other patient | exact scan (no index) | 23% | 219 ms | 10.0 | 0% | 1.000 |

## Scaling to all of HTAN

This cohort is 163 slides of roughly 5,900 published H&E, about 1/36 of the images and, at this median tile count, order 24 million tiles. Linear in rows, that is 458.28 GB of table and 73.82 GB of 1280-d index, with an exact scan around 18 s per query. Storing only the 128-d projection cuts the table by 10x and the exact scan to about 1.1 s.
