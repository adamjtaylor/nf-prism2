-- Tile-level vector search prototype for the HTAN image-search tool.
--
-- One table of tiles and one of slides. Every label a query might need to filter on lives on the
-- tile row, denormalised on purpose: DuckDB's HNSW index only accelerates an unfiltered
-- ORDER BY ... LIMIT, so any filter turns the query into a scan, and a scan should not also have
-- to join. sample -> slides is still declared so slide-level attributes have one home.

CREATE TABLE slides (
    sample         VARCHAR PRIMARY KEY,
    arm            VARCHAR,      -- A (BU lung, 6 classes), B (Vanderbilt colon), C (primary only)
    patient        VARCHAR,      -- HTAN participant ID; 32 patients contribute >1 specimen
    centre         VARCHAR,      -- HTAN atlas, i.e. scanner and stain protocol
    ttt            VARCHAR,      -- TumorTissueType, the progression class
    organ          VARCHAR,
    organ_imputed  BOOLEAN,      -- true where TissueorOrganofOrigin was blank and the atlas supplied it
    n_tiles        INTEGER,
    fmt            VARCHAR,
    meanpool       FLOAT[1280]   -- mean of the slide's Virchow2 tile vectors, L2-normalised
);

-- PRISM2's slide embeddings live in their own table because one slide (C_Duke_primary_21) produced
-- tile features but failed PRISM2 inference. Zero-filling its vectors would put a NaN cosine into
-- every slide-level ranking, and DuckDB's HNSW index rejects NULLs, so the 163 slides that have
-- embeddings are kept separate from the 164 that have tiles. A LEFT JOIN then makes the missing
-- one visible instead of silently dropping it.
CREATE TABLE slide_embeddings (
    sample      VARCHAR PRIMARY KEY,
    base        FLOAT[2560],  -- PRISM2 base embedding, L2-normalised
    diagnostic  FLOAT[3072]   -- PRISM2 diagnostic embedding, L2-normalised
);

CREATE TABLE tiles (
    row_id    BIGINT PRIMARY KEY,  -- position in the on-disk feature store, for exact cross-checks
    tile_id   VARCHAR,             -- '<sample>:<index within slide>'
    sample    VARCHAR,
    patient   VARCHAR,
    centre    VARCHAR,
    ttt       VARCHAR,
    x         BIGINT,              -- level-0 pixel coordinates of the patch's top-left corner
    y         BIGINT,
    emb       FLOAT[1280],         -- raw Virchow2 class token
    emb_n     FLOAT[1280],         -- the same vector L2-normalised: inner product == cosine
    emb_pca   FLOAT[128]           -- PCA of emb_n, fit slide-balanced
);
