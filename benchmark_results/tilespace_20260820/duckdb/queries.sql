-- ===========================================================================================
-- The queries an HTAN image-search tool actually has to run, in DuckDB + vss.
--
-- Run against analysis/data/tilesearch.duckdb, built by build_db.py:
--     LOAD vss;
--     SET hnsw_enable_experimental_persistence = true;   -- needed to READ a persisted index too
--
-- $QUERY_TILE / $QUERY_SLIDE are id placeholders; $QUERY_VEC is the query vector, bound as a
-- parameter. run_examples.py substitutes real values.
--
-- THE QUERY VECTOR HAS TO BE A PARAMETER, NOT A COLUMN. The natural SQL for "find tiles like this
-- one" reads the query vector out of the table in a CTE and joins it in. That defeats the HNSW
-- index, because the index requires a constant: measured on this table, the CTE form of Q0 takes
-- 1,658 ms and the bound-parameter form 3 ms. So the tile is looked up first and its vector passed
-- in, which is also what an application does.
--
-- READ THIS BEFORE COPYING ANY QUERY BELOW.
--
-- The obvious way to write a filtered vector search,
--
--     SELECT ... FROM tiles WHERE sample <> ? ORDER BY array_cosine_distance(emb_n, ?) LIMIT 10
--
-- is WRONG in DuckDB and fails silently. The planner pushes the LIMIT into the HNSW index scan
-- and applies the WHERE clause to the ten rows that come back. Since 76% of an unfiltered tile
-- search is the query's own slide, the filter deletes most of the result: measured over 30
-- queries this returns 2.8 rows on average, sometimes zero, and recall against the true answer is
-- 0.18. No error, no warning, and on the centre-crossing filter recall is 0.003.
--
-- Two correct patterns, both used below:
--
--   OVER-FETCH.  Ask the index for the top F, then filter, then keep k. Recall against exact is
--                0.71 at F=100, 0.90 at F=1,000, 0.97 at F=10,000. F=1,000 costs about 37 ms on
--                667,537 tiles and is the operating point these queries use.
--   EXACT SCAN.  Order on the UNINDEXED `emb` column. Always right. About 470 ms here for an
--                unselective filter, and cheaper than a large over-fetch when the predicate is
--                selective, which makes it the better choice for the centre-crossing query.
--
-- Numbers from duckdb_benchmark.md, same machine, same table.
-- ===========================================================================================

-- -------------------------------------------------------------------------------------------
-- Q0. The one shape the index serves correctly: unfiltered k-NN. ~3 ms, recall@10 0.90.
--     Per section 4 of the analysis this is also the shape that is useless on its own, because
--     most of the page is the query's own slide. It is here as the speed reference.
--     The index only fires if nothing else in the same SELECT forces a scan, so the k-NN is its
--     own CTE and the same_slide flag is computed outside it.
-- -------------------------------------------------------------------------------------------
WITH knn AS (SELECT t.tile_id, t.sample, t.ttt, t.x, t.y,
                    array_cosine_distance(t.emb_n, $QUERY_VEC::FLOAT[1280]) AS dist
             FROM tiles t
             ORDER BY dist
             LIMIT 10)
SELECT tile_id, sample, ttt, x, y, round(1 - dist, 4) AS cosine,
       sample = (SELECT sample FROM tiles WHERE tile_id = $QUERY_TILE) AS same_slide
FROM knn
ORDER BY dist;

-- -------------------------------------------------------------------------------------------
-- Q1. k-NN excluding the query's own slide, over-fetch pattern.
--     The minimum a search tool can do. The inner query is the indexed shape; the filter and the
--     final LIMIT sit outside it.
-- -------------------------------------------------------------------------------------------
WITH cand AS (SELECT t.tile_id, t.sample, t.patient, t.ttt, t.x, t.y,
                     array_cosine_distance(t.emb_n, $QUERY_VEC::FLOAT[1280]) AS dist
              FROM tiles t
              ORDER BY dist
              LIMIT 1000)                                  -- over-fetch: recall@10 ~0.90
SELECT tile_id, sample, patient, ttt, x, y, round(1 - dist, 4) AS cosine
FROM cand
WHERE sample <> (SELECT sample FROM tiles WHERE tile_id = $QUERY_TILE)
ORDER BY dist
LIMIT 10;

-- -------------------------------------------------------------------------------------------
-- Q2. k-NN excluding the query's whole PATIENT.
--     32 patients here contribute more than one specimen, so excluding the slide is not enough:
--     the next block from the same patient is the easiest possible hit and tells a user nothing.
--     This is the policy every retrieval number in the analysis uses.
-- -------------------------------------------------------------------------------------------
WITH cand AS (SELECT t.tile_id, t.sample, t.patient, t.ttt,
                     array_cosine_distance(t.emb_n, $QUERY_VEC::FLOAT[1280]) AS dist
              FROM tiles t
              ORDER BY dist
              LIMIT 1000)
SELECT tile_id, sample, patient, ttt, round(1 - dist, 4) AS cosine
FROM cand
WHERE patient <> (SELECT patient FROM tiles WHERE tile_id = $QUERY_TILE)
ORDER BY dist
LIMIT 10;

-- -------------------------------------------------------------------------------------------
-- Q3. k-NN restricted to a DIFFERENT CENTRE, as an EXACT scan.
--     The generalisation test: does this tile have a match in tissue another institution cut,
--     stained and scanned? Over-fetching does not work here. A tile's nearest neighbours are
--     overwhelmingly from its own centre, so even F=100,000 only reaches recall 0.92 while
--     costing more than the exact scan (495 ms against 277 ms). Ordering on `emb`, which carries
--     no index, is both correct and faster.
-- -------------------------------------------------------------------------------------------
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
ORDER BY hits_in_top50 DESC, best_cosine DESC;

-- -------------------------------------------------------------------------------------------
-- Q4. SLIDE-level search built from TILES: sample 10 of the query slide's tiles, find each one's
--     nearest neighbours in other patients, and let the slides vote.
--     Needs no slide encoder, and returns evidence -- which tiles matched -- rather than one
--     number.
-- -------------------------------------------------------------------------------------------
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
LIMIT 10;

-- -------------------------------------------------------------------------------------------
-- Q5. SLIDE-level search on PRISM2's own slide embedding, 2560-d `base`.
--     163 rows, so this is sub-millisecond with or without an index. Patient excluded, because
--     the same patient's other block would otherwise top every list.
-- -------------------------------------------------------------------------------------------
WITH q AS (SELECT e.base AS v, s.patient AS p
           FROM slide_embeddings e JOIN slides s USING (sample)
           WHERE e.sample = $QUERY_SLIDE)
SELECT s.sample, s.ttt, s.organ, s.centre, s.n_tiles,
       round(1 - array_cosine_distance(e.base, q.v), 4) AS cosine
FROM slide_embeddings e JOIN slides s USING (sample), q
WHERE s.patient <> q.p
ORDER BY array_cosine_distance(e.base, q.v)
LIMIT 10;

-- -------------------------------------------------------------------------------------------
-- Q6. The same query on the 3072-d `diagnostic` embedding, with both rankings side by side.
--     The pilot found the diagnostic embedding occupies a narrow cosine band while the base
--     embedding spans a wide one, which makes the diagnostic space poorly conditioned for
--     ranking. Putting both in one statement makes the disagreement visible per query instead of
--     as an aggregate.
-- -------------------------------------------------------------------------------------------
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
LIMIT 10;

-- -------------------------------------------------------------------------------------------
-- Q7. Mean-pooled tiles as a third slide representation, same query.
--     Cheap, no Perceiver required. The pilot found its pair orderings agree with PRISM2's at
--     r = 0.05, so this is the control that says whether the slide encoder is earning its keep.
-- -------------------------------------------------------------------------------------------
WITH q AS (SELECT meanpool AS v, patient AS p FROM slides WHERE sample = $QUERY_SLIDE)
SELECT s.sample, s.ttt, s.organ, s.centre,
       round(1 - array_cosine_distance(s.meanpool, q.v), 4) AS cosine
FROM slides s, q
WHERE s.patient <> q.p
ORDER BY array_cosine_distance(s.meanpool, q.v)
LIMIT 10;
