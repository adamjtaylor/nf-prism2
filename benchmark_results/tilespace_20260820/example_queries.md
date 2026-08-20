# Example query output

Every statement in `duckdb/queries.sql`, run against the 667,537-tile prototype. Each statement is run twice and the second run timed, on a laptop, so read the timings as orders of magnitude.

**Query slide** `A_BU_insitu_06`: Arm A, patient `HTA3_50711`, HTAN BU, Premalignant - in situ, Lung, 15,583 tiles. Chosen as the Arm A carcinoma in situ slide with the median tile count.
**Query tile** `A_BU_insitu_06:7949`: the tile of that slide closest to the slide's own centroid.

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

`3 ms`, 10 rows

| tile_id | sample | ttt | x | y | cosine | same_slide |
|---|---|---|---|---|---|---|
| A_BU_insitu_06:7949 | A_BU_insitu_06 | Premalignant - in situ | 8,064 | 20,160 | 1 | True |
| A_BU_insitu_06:10544 | A_BU_insitu_06 | Premalignant - in situ | 11,872 | 23,744 | 0.9407 | True |
| A_BU_insitu_06:10067 | A_BU_insitu_06 | Premalignant - in situ | 8,064 | 23,072 | 0.9324 | True |
| A_BU_insitu_06:8580 | A_BU_insitu_06 | Premalignant - in situ | 7,168 | 21,056 | 0.9294 | True |
| A_BU_insitu_06:8102 | A_BU_insitu_06 | Premalignant - in situ | 6,944 | 20,384 | 0.9276 | True |
| A_BU_insitu_06:10238 | A_BU_insitu_06 | Premalignant - in situ | 11,872 | 23,296 | 0.924 | True |
| A_BU_insitu_06:8579 | A_BU_insitu_06 | Premalignant - in situ | 6,944 | 21,056 | 0.9238 | True |
| A_BU_insitu_06:8261 | A_BU_insitu_06 | Premalignant - in situ | 7,168 | 20,608 | 0.9236 | True |
| A_BU_insitu_06:10224 | A_BU_insitu_06 | Premalignant - in situ | 8,736 | 23,296 | 0.9235 | True |
| A_BU_insitu_06:7475 | A_BU_insitu_06 | Premalignant - in situ | 6,048 | 19,488 | 0.9223 | True |


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

`27 ms`, 10 rows

| tile_id | sample | patient | ttt | x | y | cosine |
|---|---|---|---|---|---|---|
| A_BU_primary_08:638 | A_BU_primary_08 | HTA3_50711 | Primary | 38,528 | 5,376 | 0.9185 |
| A_BU_primary_08:15844 | A_BU_primary_08 | HTA3_50711 | Primary | 19,264 | 28,000 | 0.912 |
| A_BU_primary_08:7044 | A_BU_primary_08 | HTA3_50711 | Primary | 17,024 | 17,024 | 0.9084 |
| A_BU_primary_08:1118 | A_BU_primary_08 | HTA3_50711 | Primary | 34,048 | 7,168 | 0.9081 |
| A_BU_primary_08:1212 | A_BU_primary_08 | HTA3_50711 | Primary | 37,408 | 7,392 | 0.9079 |
| A_BU_primary_08:1230 | A_BU_primary_08 | HTA3_50711 | Primary | 23,744 | 7,616 | 0.9078 |
| A_BU_primary_08:3693 | A_BU_primary_08 | HTA3_50711 | Primary | 39,872 | 12,544 | 0.9075 |
| A_BU_primary_08:1263 | A_BU_primary_08 | HTA3_50711 | Primary | 31,136 | 7,616 | 0.9075 |
| A_BU_primary_08:1423 | A_BU_primary_08 | HTA3_50711 | Primary | 29,792 | 8,064 | 0.907 |
| A_BU_primary_08:1707 | A_BU_primary_08 | HTA3_50711 | Primary | 31,808 | 8,736 | 0.9067 |


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

`21 ms`, 10 rows

| tile_id | sample | patient | ttt | cosine |
|---|---|---|---|---|
| A_BU_insitu_07:10682 | A_BU_insitu_07 | HTA3_50713 | Premalignant - in situ | 0.8974 |
| A_BU_insitu_07:5624 | A_BU_insitu_07 | HTA3_50713 | Premalignant - in situ | 0.8937 |
| A_BU_insitu_07:8751 | A_BU_insitu_07 | HTA3_50713 | Premalignant - in situ | 0.8924 |
| A_BU_insitu_07:12498 | A_BU_insitu_07 | HTA3_50713 | Premalignant - in situ | 0.8909 |
| A_BU_primary_13:12965 | A_BU_primary_13 | HTA3_50716 | Primary | 0.8883 |
| A_BU_insitu_07:8357 | A_BU_insitu_07 | HTA3_50713 | Premalignant - in situ | 0.8852 |
| A_BU_insitu_07:6783 | A_BU_insitu_07 | HTA3_50713 | Premalignant - in situ | 0.8847 |
| A_BU_insitu_12:3607 | A_BU_insitu_12 | HTA3_50728 | Premalignant - in situ | 0.8843 |
| A_BU_insitu_07:10678 | A_BU_insitu_07 | HTA3_50713 | Premalignant - in situ | 0.8841 |
| A_BU_insitu_07:8454 | A_BU_insitu_07 | HTA3_50713 | Premalignant - in situ | 0.8828 |


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

`694 ms`, 5 rows

| centre | sample | ttt | hits_in_top50 | best_cosine |
|---|---|---|---|---|
| HTAN WUSTL | C_WUSTL_primary_04 | Primary | 45 | 0.4783 |
| HTAN WUSTL | C_WUSTL_primary_09 | Primary | 2 | 0.4707 |
| HTAN WUSTL | C_WUSTL_primary_02 | Primary | 1 | 0.3131 |
| HTAN Vanderbilt | B_Vanderbilt_primary_09 | Primary | 1 | 0.3022 |
| HTAN WUSTL | C_WUSTL_primary_08 | Primary | 1 | 0.2981 |


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

`19660 ms`, 10 rows

| sample | ttt | centre | organ | n_tiles | votes | mean_cosine |
|---|---|---|---|---|---|---|
| A_BU_primary_13 | Primary | HTAN BU | Lung | 31,387 | 22 | 0.8809 |
| A_BU_insitu_18 | Premalignant - in situ | HTAN BU | Lung | 36,399 | 22 | 0.8564 |
| A_BU_primary_17 | Primary | HTAN BU | Lung | 9,415 | 16 | 0.8729 |
| A_BU_primary_16 | Primary | HTAN BU | Lung | 28,106 | 9 | 0.8032 |
| A_BU_primary_04 | Primary | HTAN BU | Lung | 12,933 | 8 | 0.8408 |
| A_BU_insitu_11 | Premalignant - in situ | HTAN BU | Lung | 18,876 | 7 | 0.8635 |
| A_BU_primary_14 | Primary | HTAN BU | Lung | 24,005 | 5 | 0.8201 |
| A_BU_insitu_07 | Premalignant - in situ | HTAN BU | Lung | 13,970 | 3 | 0.882 |
| A_BU_insitu_13 | Premalignant - in situ | HTAN BU | Lung | 20,625 | 3 | 0.8479 |
| A_BU_insitu_08 | Premalignant - in situ | HTAN BU | Lung | 22,150 | 1 | 0.8994 |


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

`3 ms`, 10 rows

| sample | ttt | organ | centre | n_tiles | cosine |
|---|---|---|---|---|---|
| A_BU_insitu_15 | Premalignant - in situ | Lung | HTAN BU | 12,767 | 0.946 |
| A_BU_primary_17 | Primary | Lung | HTAN BU | 9,415 | 0.9434 |
| A_BU_insitu_07 | Premalignant - in situ | Lung | HTAN BU | 13,970 | 0.9307 |
| A_BU_insitu_11 | Premalignant - in situ | Lung | HTAN BU | 18,876 | 0.9284 |
| A_BU_primary_05 | Primary | Lung | HTAN BU | 12,315 | 0.9085 |
| A_BU_primary_16 | Primary | Lung | HTAN BU | 28,106 | 0.9025 |
| A_BU_insitu_14 | Premalignant - in situ | Lung | HTAN BU | 15,899 | 0.8905 |
| A_BU_insitu_10 | Premalignant - in situ | Lung | HTAN BU | 8,143 | 0.8891 |
| A_BU_insitu_02 | Premalignant - in situ | Lung | HTAN BU | 23,255 | 0.8853 |
| A_BU_insitu_05 | Premalignant - in situ | Lung | HTAN BU | 12,739 | 0.8732 |


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

`5 ms`, 10 rows

| sample | ttt | organ | centre | cos_base | rank_base | cos_diag | rank_diagnostic |
|---|---|---|---|---|---|---|---|
| A_BU_insitu_15 | Premalignant - in situ | Lung | HTAN BU | 0.946 | 1 | 0.995 | 1 |
| A_BU_primary_17 | Primary | Lung | HTAN BU | 0.9434 | 2 | 0.9934 | 3 |
| A_BU_insitu_07 | Premalignant - in situ | Lung | HTAN BU | 0.9307 | 3 | 0.9874 | 7 |
| A_BU_insitu_11 | Premalignant - in situ | Lung | HTAN BU | 0.9284 | 4 | 0.9935 | 2 |
| A_BU_primary_05 | Primary | Lung | HTAN BU | 0.9085 | 5 | 0.9806 | 15 |
| A_BU_primary_16 | Primary | Lung | HTAN BU | 0.9025 | 6 | 0.9911 | 4 |
| A_BU_insitu_14 | Premalignant - in situ | Lung | HTAN BU | 0.8905 | 7 | 0.9903 | 5 |
| A_BU_insitu_10 | Premalignant - in situ | Lung | HTAN BU | 0.8891 | 8 | 0.9881 | 6 |
| A_BU_insitu_02 | Premalignant - in situ | Lung | HTAN BU | 0.8853 | 9 | 0.9874 | 8 |
| A_BU_insitu_05 | Premalignant - in situ | Lung | HTAN BU | 0.8732 | 10 | 0.9809 | 14 |


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
| A_BU_insitu_18 | Premalignant - in situ | Lung | HTAN BU | 0.862 |
| A_BU_insitu_07 | Premalignant - in situ | Lung | HTAN BU | 0.8579 |
| A_BU_insitu_11 | Premalignant - in situ | Lung | HTAN BU | 0.8541 |
| A_BU_insitu_15 | Premalignant - in situ | Lung | HTAN BU | 0.837 |
| A_BU_primary_13 | Primary | Lung | HTAN BU | 0.802 |
| A_BU_primary_09 | Primary | Lung | HTAN BU | 0.7717 |
| A_BU_insitu_14 | Premalignant - in situ | Lung | HTAN BU | 0.7677 |
| A_BU_primary_15 | Primary | Lung | HTAN BU | 0.7616 |
| A_BU_insitu_12 | Premalignant - in situ | Lung | HTAN BU | 0.7615 |
| A_BU_atypia_18 | Atypia - hyperplasia | Lung | HTAN BU | 0.752 |

