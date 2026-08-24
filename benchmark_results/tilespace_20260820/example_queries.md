# Example query output

Every statement in `duckdb/queries.sql`, run against the 859,342-tile prototype. Each statement is run twice and the second run timed, on a laptop, so read the timings as orders of magnitude.

**Query slide** `A_BU_insitu_14`: Arm A, patient `HTA3_50730`, HTAN BU, Premalignant - in situ, Lung, 15,899 tiles. Chosen as the Arm A carcinoma in situ slide with the median tile count.
**Query tile** `A_BU_insitu_14:1224`: the tile of that slide closest to the slide's own centroid.

## Q0. The one shape the index serves correctly: unfiltered k-NN. ~3 ms, recall@10 0.90.

Per section 4 of the analysis this is also the shape that is useless on its own, because most of the page is the query's own slide. It is here as the speed reference. The index only fires if nothing else in the same SELECT forces a scan, so the k-NN is its own CTE and the same_slide flag is computed outside it.

```sql
WITH knn AS (SELECT t.tile_id, t.sample, t.ttt, t.x, t.y,
                    array_cosine_distance(t.emb_n, $QUERY_VEC::FLOAT[1280]) AS dist
             FROM tiles t
             ORDER BY dist
             LIMIT 10)
SELECT tile_id, sample, ttt, x, y, round(1 - dist, 4) AS cosine,
       sample = (SELECT sample FROM tiles WHERE tile_id = $QUERY_TILE) AS same_slide
FROM knn
ORDER BY dist
```

`5 ms`, 10 rows

| tile_id | sample | ttt | x | y | cosine | same_slide |
|---|---|---|---|---|---|---|
| A_BU_insitu_14:1224 | A_BU_insitu_14 | Premalignant - in situ | 34,944 | 7,840 | 1 | True |
| A_BU_insitu_14:1076 | A_BU_insitu_14 | Premalignant - in situ | 34,720 | 7,392 | 0.8982 | True |
| A_BU_insitu_11:13610 | A_BU_insitu_11 | Premalignant - in situ | 40,320 | 26,880 | 0.8662 | False |
| A_BU_insitu_14:4939 | A_BU_insitu_14 | Premalignant - in situ | 36,288 | 15,456 | 0.8597 | True |
| A_BU_insitu_11:3068 | A_BU_insitu_11 | Premalignant - in situ | 17,696 | 9,408 | 0.8542 | False |
| A_BU_primary_12:7814 | A_BU_primary_12 | Primary | 42,336 | 18,144 | 0.8541 | False |
| A_BU_insitu_11:1324 | A_BU_insitu_11 | Premalignant - in situ | 28,896 | 6,496 | 0.8521 | False |
| A_BU_insitu_14:12939 | A_BU_insitu_14 | Premalignant - in situ | 32,928 | 30,240 | 0.8478 | True |
| A_BU_insitu_14:11479 | A_BU_insitu_14 | Premalignant - in situ | 28,896 | 27,104 | 0.8465 | True |
| A_BU_insitu_11:2745 | A_BU_insitu_11 | Premalignant - in situ | 14,112 | 8,960 | 0.8459 | False |


## Q1. k-NN excluding the query's own slide, over-fetch pattern.

The minimum a search tool can do. The inner query is the indexed shape; the filter and the final LIMIT sit outside it.

```sql
WITH cand AS (SELECT t.tile_id, t.sample, t.patient, t.ttt, t.x, t.y,
                     array_cosine_distance(t.emb_n, $QUERY_VEC::FLOAT[1280]) AS dist
              FROM tiles t
              ORDER BY dist
              LIMIT 1000)                                  -- over-fetch: recall@10 ~0.90
SELECT tile_id, sample, patient, ttt, x, y, round(1 - dist, 4) AS cosine
FROM cand
WHERE sample <> (SELECT sample FROM tiles WHERE tile_id = $QUERY_TILE)
ORDER BY dist
LIMIT 10
```

`32 ms`, 10 rows

| tile_id | sample | patient | ttt | x | y | cosine |
|---|---|---|---|---|---|---|
| A_BU_insitu_11:13610 | A_BU_insitu_11 | HTA3_50721 | Premalignant - in situ | 40,320 | 26,880 | 0.8662 |
| A_BU_insitu_11:3068 | A_BU_insitu_11 | HTA3_50721 | Premalignant - in situ | 17,696 | 9,408 | 0.8542 |
| A_BU_primary_12:7814 | A_BU_primary_12 | HTA3_50715 | Primary | 42,336 | 18,144 | 0.8541 |
| A_BU_insitu_11:1324 | A_BU_insitu_11 | HTA3_50721 | Premalignant - in situ | 28,896 | 6,496 | 0.8521 |
| A_BU_insitu_11:2745 | A_BU_insitu_11 | HTA3_50721 | Premalignant - in situ | 14,112 | 8,960 | 0.8459 |
| A_BU_insitu_11:5377 | A_BU_insitu_11 | HTA3_50721 | Premalignant - in situ | 29,344 | 12,992 | 0.8458 |
| A_BU_primary_18:12014 | A_BU_primary_18 | HTA3_50721 | Primary | 17,696 | 27,104 | 0.8391 |
| A_BU_insitu_01:906 | A_BU_insitu_01 | HTA3_50801 | Premalignant - in situ | 23,296 | 4,928 | 0.8309 |
| A_BU_insitu_11:6808 | A_BU_insitu_11 | HTA3_50721 | Premalignant - in situ | 32,928 | 15,232 | 0.8294 |
| A_BU_insitu_16:5355 | A_BU_insitu_16 | HTA3_50733 | Premalignant - in situ | 32,928 | 8,288 | 0.8294 |


## Q2. k-NN excluding the query's whole PATIENT.

32 patients here contribute more than one specimen, so excluding the slide is not enough: the next block from the same patient is the easiest possible hit and tells a user nothing. This is the policy every retrieval number in the analysis uses.

```sql
WITH cand AS (SELECT t.tile_id, t.sample, t.patient, t.ttt,
                     array_cosine_distance(t.emb_n, $QUERY_VEC::FLOAT[1280]) AS dist
              FROM tiles t
              ORDER BY dist
              LIMIT 1000)
SELECT tile_id, sample, patient, ttt, round(1 - dist, 4) AS cosine
FROM cand
WHERE patient <> (SELECT patient FROM tiles WHERE tile_id = $QUERY_TILE)
ORDER BY dist
LIMIT 10
```

`31 ms`, 10 rows

| tile_id | sample | patient | ttt | cosine |
|---|---|---|---|---|
| A_BU_insitu_11:13610 | A_BU_insitu_11 | HTA3_50721 | Premalignant - in situ | 0.8662 |
| A_BU_insitu_11:3068 | A_BU_insitu_11 | HTA3_50721 | Premalignant - in situ | 0.8542 |
| A_BU_primary_12:7814 | A_BU_primary_12 | HTA3_50715 | Primary | 0.8541 |
| A_BU_insitu_11:1324 | A_BU_insitu_11 | HTA3_50721 | Premalignant - in situ | 0.8521 |
| A_BU_insitu_11:2745 | A_BU_insitu_11 | HTA3_50721 | Premalignant - in situ | 0.8459 |
| A_BU_insitu_11:5377 | A_BU_insitu_11 | HTA3_50721 | Premalignant - in situ | 0.8458 |
| A_BU_primary_18:12014 | A_BU_primary_18 | HTA3_50721 | Primary | 0.8391 |
| A_BU_insitu_01:906 | A_BU_insitu_01 | HTA3_50801 | Premalignant - in situ | 0.8309 |
| A_BU_insitu_11:6808 | A_BU_insitu_11 | HTA3_50721 | Premalignant - in situ | 0.8294 |
| A_BU_insitu_16:5355 | A_BU_insitu_16 | HTA3_50733 | Premalignant - in situ | 0.8294 |


## Q3. k-NN restricted to a DIFFERENT CENTRE, as an EXACT scan.

The generalisation test: does this tile have a match in tissue another institution cut, stained and scanned? Over-fetching does not work here. A tile's nearest neighbours are overwhelmingly from its own centre, so even F=100,000 only reaches recall 0.92 while costing more than the exact scan (495 ms against 277 ms). Ordering on `emb`, which carries no index, is both correct and faster.

```sql
WITH hits AS (SELECT t.centre, t.sample, t.ttt,
                     array_cosine_distance(t.emb, $QUERY_VEC::FLOAT[1280]) AS dist
              FROM tiles t
              WHERE t.centre <> (SELECT centre FROM tiles WHERE tile_id = $QUERY_TILE)
              ORDER BY dist
              LIMIT 50)
SELECT centre, sample, ttt, count(*) AS hits_in_top50,
       round(1 - min(dist), 4) AS best_cosine
FROM hits
GROUP BY centre, sample, ttt
ORDER BY hits_in_top50 DESC, best_cosine DESC
```

`974 ms`, 9 rows

| centre | sample | ttt | hits_in_top50 | best_cosine |
|---|---|---|---|---|
| HTAN WUSTL | C_WUSTL_primary_04 | Primary | 25 | 0.4029 |
| HTAN WUSTL | C_WUSTL_primary_06 | Primary | 11 | 0.3647 |
| HTAN Vanderbilt | B_Vanderbilt_primary_02 | Primary | 6 | 0.3779 |
| HTAN WUSTL | C_WUSTL_primary_08 | Primary | 2 | 0.3505 |
| HTAN WUSTL | C_WUSTL_primary_09 | Primary | 2 | 0.3466 |
| HTAN WUSTL | C_WUSTL_primary_11 | Primary | 1 | 0.3048 |
| HTAN WUSTL | C_WUSTL_primary_03 | Primary | 1 | 0.3005 |
| HTAN Vanderbilt | B_Vanderbilt_primary_11 | Primary | 1 | 0.2994 |
| HTAN Vanderbilt | B_Vanderbilt_primary_04 | Primary | 1 | 0.2918 |


## Q4. SLIDE-level search built from TILES: sample 10 of the query slide's tiles, find each one's

nearest neighbours in other patients, and let the slides vote. Needs no slide encoder, and returns evidence -- which tiles matched -- rather than one number.

```sql
WITH qs AS (SELECT tile_id, emb, patient FROM tiles
            WHERE sample = $QUERY_SLIDE
            ORDER BY hash(tile_id) LIMIT 10),      -- deterministic 10-tile sample of the slide
     -- One LATERAL top-10 per query tile. The query vector is a COLUMN here, not a parameter, so
     -- no lateral can use the HNSW index and each one is an exact scan: about 2 s per query tile
     -- on this table. An application would instead loop, issuing Q2 once per tile with the vector
     -- bound, and pay ~20 ms each. This single-statement form is the honest cost of doing it in
     -- one round trip.
     nn AS (SELECT qs.tile_id AS query_tile, h.sample, h.dist
            FROM qs, LATERAL (SELECT n.sample, array_cosine_distance(n.emb, qs.emb) AS dist
                              FROM tiles n
                              WHERE n.patient <> qs.patient
                              ORDER BY dist
                              LIMIT 10) h)
SELECT nn.sample, s.ttt, s.centre, s.organ, s.n_tiles,
       count(*) AS votes, round(1 - avg(dist), 4) AS mean_cosine
FROM nn JOIN slides s ON s.sample = nn.sample
GROUP BY nn.sample, s.ttt, s.centre, s.organ, s.n_tiles
ORDER BY votes DESC, mean_cosine DESC
LIMIT 10
```

`24337 ms`, 10 rows

| sample | ttt | centre | organ | n_tiles | votes | mean_cosine |
|---|---|---|---|---|---|---|
| A_BU_insitu_11 | Premalignant - in situ | HTAN BU | Lung | 18,876 | 22 | 0.8685 |
| A_BU_insitu_16 | Premalignant - in situ | HTAN BU | Lung | 27,147 | 21 | 0.8608 |
| A_BU_primary_12 | Primary | HTAN BU | Lung | 26,715 | 16 | 0.8894 |
| A_BU_primary_18 | Primary | HTAN BU | Lung | 18,350 | 12 | 0.8653 |
| A_BU_primary_06 | Primary | HTAN BU | Lung | 25,498 | 5 | 0.8497 |
| A_BU_insitu_03 | Premalignant - in situ | HTAN BU | Lung | 26,121 | 4 | 0.8815 |
| A_BU_insitu_12 | Premalignant - in situ | HTAN BU | Lung | 12,773 | 4 | 0.8624 |
| A_BU_insitu_18 | Premalignant - in situ | HTAN BU | Lung | 36,399 | 4 | 0.8553 |
| A_BU_insitu_07 | Premalignant - in situ | HTAN BU | Lung | 13,970 | 4 | 0.7791 |
| A_BU_insitu_01 | Premalignant - in situ | HTAN BU | Lung | 26,449 | 2 | 0.8556 |


## Q5. SLIDE-level search on PRISM2's own slide embedding, 2560-d `base`.

163 rows, so this is sub-millisecond with or without an index. Patient excluded, because the same patient's other block would otherwise top every list.

```sql
WITH q AS (SELECT e.base AS v, s.patient AS p
           FROM slide_embeddings e JOIN slides s USING (sample)
           WHERE e.sample = $QUERY_SLIDE)
SELECT s.sample, s.ttt, s.organ, s.centre, s.n_tiles,
       round(1 - array_cosine_distance(e.base, q.v), 4) AS cosine
FROM slide_embeddings e JOIN slides s USING (sample), q
WHERE s.patient <> q.p
ORDER BY array_cosine_distance(e.base, q.v)
LIMIT 10
```

`4 ms`, 10 rows

| sample | ttt | organ | centre | n_tiles | cosine |
|---|---|---|---|---|---|
| A_BU_insitu_11 | Premalignant - in situ | Lung | HTAN BU | 18,876 | 0.9269 |
| A_BU_insitu_12 | Premalignant - in situ | Lung | HTAN BU | 12,773 | 0.9192 |
| A_BU_primary_08 | Primary | Lung | HTAN BU | 16,925 | 0.9049 |
| A_BU_insitu_16 | Premalignant - in situ | Lung | HTAN BU | 27,147 | 0.8921 |
| A_BU_insitu_06 | Premalignant - in situ | Lung | HTAN BU | 15,583 | 0.8905 |
| A_BU_insitu_09 | Premalignant - in situ | Lung | HTAN BU | 30,045 | 0.882 |
| A_BU_primary_18 | Primary | Lung | HTAN BU | 18,350 | 0.876 |
| A_BU_primary_16 | Primary | Lung | HTAN BU | 28,106 | 0.8654 |
| A_BU_primary_06 | Primary | Lung | HTAN BU | 25,498 | 0.8627 |
| A_BU_insitu_15 | Premalignant - in situ | Lung | HTAN BU | 12,767 | 0.8555 |


## Q6. The same query on the 3072-d `diagnostic` embedding, with both rankings side by side.

The pilot found the diagnostic embedding occupies a narrow cosine band while the base embedding spans a wide one, which makes the diagnostic space poorly conditioned for ranking. Putting both in one statement makes the disagreement visible per query instead of as an aggregate.

```sql
WITH q AS (SELECT e.base AS b, e.diagnostic AS d, s.patient AS p
           FROM slide_embeddings e JOIN slides s USING (sample)
           WHERE e.sample = $QUERY_SLIDE),
     scored AS (
        SELECT s.sample, s.ttt, s.organ, s.centre,
               1 - array_cosine_distance(e.base, q.b)       AS cos_base,
               1 - array_cosine_distance(e.diagnostic, q.d) AS cos_diag
        FROM slide_embeddings e JOIN slides s USING (sample), q
        WHERE s.patient <> q.p)
SELECT sample, ttt, organ, centre,
       round(cos_base, 4) AS cos_base,
       rank() OVER (ORDER BY cos_base DESC) AS rank_base,
       round(cos_diag, 4) AS cos_diag,
       rank() OVER (ORDER BY cos_diag DESC) AS rank_diagnostic
FROM scored
ORDER BY cos_base DESC
LIMIT 10
```

`7 ms`, 10 rows

| sample | ttt | organ | centre | cos_base | rank_base | cos_diag | rank_diagnostic |
|---|---|---|---|---|---|---|---|
| A_BU_insitu_11 | Premalignant - in situ | Lung | HTAN BU | 0.9269 | 1 | 0.9918 | 2 |
| A_BU_insitu_12 | Premalignant - in situ | Lung | HTAN BU | 0.9192 | 2 | 0.9879 | 9 |
| A_BU_primary_08 | Primary | Lung | HTAN BU | 0.9049 | 3 | 0.9911 | 3 |
| A_BU_insitu_16 | Premalignant - in situ | Lung | HTAN BU | 0.8921 | 4 | 0.9922 | 1 |
| A_BU_insitu_06 | Premalignant - in situ | Lung | HTAN BU | 0.8905 | 5 | 0.9903 | 4 |
| A_BU_insitu_09 | Premalignant - in situ | Lung | HTAN BU | 0.882 | 6 | 0.9888 | 7 |
| A_BU_primary_18 | Primary | Lung | HTAN BU | 0.876 | 7 | 0.9878 | 10 |
| A_BU_primary_16 | Primary | Lung | HTAN BU | 0.8654 | 8 | 0.9896 | 5 |
| A_BU_primary_06 | Primary | Lung | HTAN BU | 0.8627 | 9 | 0.9818 | 16 |
| A_BU_insitu_15 | Premalignant - in situ | Lung | HTAN BU | 0.8555 | 10 | 0.9894 | 6 |


## Q7. Mean-pooled tiles as a third slide representation, same query.

Cheap, no Perceiver required. The pilot found its pair orderings agree with PRISM2's at r = 0.05, so this is the control that says whether the slide encoder is earning its keep.

```sql
WITH q AS (SELECT meanpool AS v, patient AS p FROM slides WHERE sample = $QUERY_SLIDE)
SELECT s.sample, s.ttt, s.organ, s.centre,
       round(1 - array_cosine_distance(s.meanpool, q.v), 4) AS cosine
FROM slides s, q
WHERE s.patient <> q.p
ORDER BY array_cosine_distance(s.meanpool, q.v)
LIMIT 10
```

`1 ms`, 10 rows

| sample | ttt | organ | centre | cosine |
|---|---|---|---|---|
| A_BU_insitu_11 | Premalignant - in situ | Lung | HTAN BU | 0.8883 |
| A_BU_primary_06 | Primary | Lung | HTAN BU | 0.8663 |
| A_BU_insitu_03 | Premalignant - in situ | Lung | HTAN BU | 0.8447 |
| A_BU_primary_18 | Primary | Lung | HTAN BU | 0.816 |
| A_BU_insitu_15 | Premalignant - in situ | Lung | HTAN BU | 0.809 |
| A_BU_insitu_12 | Premalignant - in situ | Lung | HTAN BU | 0.785 |
| A_BU_insitu_16 | Premalignant - in situ | Lung | HTAN BU | 0.775 |
| A_BU_insitu_18 | Premalignant - in situ | Lung | HTAN BU | 0.7705 |
| A_BU_insitu_06 | Premalignant - in situ | Lung | HTAN BU | 0.7677 |
| A_BU_insitu_01 | Premalignant - in situ | Lung | HTAN BU | 0.7614 |

