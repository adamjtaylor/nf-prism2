#!/usr/bin/env python3
"""Build the DuckDB tile-search prototype: schema, load, HNSW indexes, and their cost.

Design decisions worth stating, because they are the ones a production index would inherit.

1. THREE VECTOR COLUMNS, not one. `emb` is the raw 1280-d Virchow2 class token. `emb_n` is the
   same vector L2-normalised, because DuckDB's `array_cosine_distance` normalises on every call;
   storing the normalised copy lets `array_inner_product` stand in for cosine at a fraction of the
   work. `emb_pca` is the 128-d PCA projection, for the speed-versus-accuracy question. Storing all
   three costs disk and answers the question honestly; a deployment would keep one.
2. HNSW PERSISTENCE IS EXPERIMENTAL. `hnsw_enable_experimental_persistence` has to be set for the
   index to survive a restart, and DuckDB warns that a crash mid-write can corrupt it. That is a
   real operational caveat for a public resource, so it is recorded rather than hidden.
3. SLIDES CARRY THEIR OWN VECTORS. PRISM2 emits a 2560-d `base` and a 3072-d `diagnostic`
   embedding per slide. Both are loaded, along with a mean-pooled tile vector, so slide-level
   search can be compared against tile-level search in the same engine.

Everything is timed and the resulting file sizes are recorded, because "is this viable" is a
question about cost as much as about accuracy.
"""
import json, os, sys, time
import numpy as np
import pyarrow as pa
import duckdb

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as C

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(C.REPO, "analysis", "data", "store")
DB = os.path.join(C.REPO, "analysis", "data", "tilesearch.duckdb")
CHUNK = 25000

if os.path.exists(DB):
    os.remove(DB)
for ext in (".wal",):
    if os.path.exists(DB + ext):
        os.remove(DB + ext)

import pandas as pd
tiles = pd.read_parquet(os.path.join(STORE, "tiles.parquet"))
meta = C.load_meta()
F = np.load(os.path.join(STORE, "features.f32.npy"), mmap_mode="r")
P = np.load(os.path.join(STORE, "pca128.f32.npy"), mmap_mode="r")
N, D = F.shape
PD = P.shape[1]
print(f"{N:,} tiles, {D}-d raw, {PD}-d PCA")

timings, sizes = {}, {}
con = duckdb.connect(DB)
con.execute("INSTALL vss; LOAD vss;")
con.execute("SET hnsw_enable_experimental_persistence = true;")
con.execute("PRAGMA memory_limit='12GB'")

schema = open(os.path.join(HERE, "schema.sql")).read()
con.execute(schema)

# ---------------------------------------------------------------- slides
rows, emb_rows, no_slide_emb = [], [], []
grp = tiles.groupby("sample")["row"]
lo_of, n_of = grp.min().to_dict(), grp.size().to_dict()
for s, r in sorted(meta.items()):
    n, lo = int(n_of[s]), int(lo_of[s])
    mp = C.l2(np.asarray(F[lo:lo + n]).mean(0, keepdims=True))[0]
    rows.append(dict(sample=s, arm=r["arm"], patient=r["patient"], centre=r["centre"],
                     ttt=r["ttt"], organ=r["organ_resolved"], organ_imputed=bool(r["organ_imputed"]),
                     n_tiles=n, fmt=r["fmt"], meanpool=mp.tolist()))
    npz = os.path.join(C.SLIDE_EMB, s, f"{s}.embeddings.npz")
    if os.path.exists(npz):
        z = np.load(npz)
        emb_rows.append(dict(sample=s,
                             base=C.l2(np.asarray(z["base"], dtype=np.float32))[0].tolist(),
                             diagnostic=C.l2(np.asarray(z["diagnostic"], dtype=np.float32))[0].tolist()))
    else:
        no_slide_emb.append(s)
sl = pa.Table.from_pylist(rows, schema=pa.schema([
    ("sample", pa.string()), ("arm", pa.string()), ("patient", pa.string()),
    ("centre", pa.string()), ("ttt", pa.string()), ("organ", pa.string()),
    ("organ_imputed", pa.bool_()), ("n_tiles", pa.int32()), ("fmt", pa.string()),
    ("meanpool", pa.list_(pa.float32(), 1280))]))
con.register("sl_arrow", sl)
con.execute("INSERT INTO slides SELECT * FROM sl_arrow")
con.unregister("sl_arrow")
se = pa.Table.from_pylist(emb_rows, schema=pa.schema([
    ("sample", pa.string()), ("base", pa.list_(pa.float32(), 2560)),
    ("diagnostic", pa.list_(pa.float32(), 3072))]))
con.register("se_arrow", se)
con.execute("INSERT INTO slide_embeddings SELECT * FROM se_arrow")
con.unregister("se_arrow")
print(f"loaded {len(rows)} slides, {len(emb_rows)} with PRISM2 slide embeddings; "
      f"missing: {no_slide_emb}")

# ---------------------------------------------------------------- tiles
t0 = time.time()
cols = tiles[["tile_id", "sample", "patient", "centre", "ttt", "x", "y"]]
for a in range(0, N, CHUNK):
    b = min(a + CHUNK, N)
    raw = np.ascontiguousarray(F[a:b])
    nrm = C.l2(raw)
    pc = np.ascontiguousarray(P[a:b])
    t = pa.Table.from_arrays([
        pa.array(np.arange(a, b, dtype=np.int64)),
        pa.array(cols["tile_id"].to_numpy()[a:b]),
        pa.array(cols["sample"].to_numpy()[a:b]),
        pa.array(cols["patient"].to_numpy()[a:b]),
        pa.array(cols["centre"].to_numpy()[a:b]),
        pa.array(cols["ttt"].to_numpy()[a:b]),
        pa.array(cols["x"].to_numpy()[a:b]),
        pa.array(cols["y"].to_numpy()[a:b]),
        pa.FixedSizeListArray.from_arrays(pa.array(raw.reshape(-1)), D),
        pa.FixedSizeListArray.from_arrays(pa.array(nrm.reshape(-1)), D),
        pa.FixedSizeListArray.from_arrays(pa.array(pc.reshape(-1)), PD)],
        names=["row_id", "tile_id", "sample", "patient", "centre", "ttt", "x", "y",
               "emb", "emb_n", "emb_pca"])
    con.register("t_arrow", t)
    con.execute("INSERT INTO tiles SELECT * FROM t_arrow")
    con.unregister("t_arrow")
    if (a // CHUNK) % 8 == 0:
        print(f"  {b:,}/{N:,}  {time.time()-t0:.0f}s")
timings["load_tiles_s"] = round(time.time() - t0, 1)
con.execute("CHECKPOINT")
sizes["db_after_load_bytes"] = os.path.getsize(DB)
print(f"load {timings['load_tiles_s']}s, db {sizes['db_after_load_bytes']/1e9:.2f} GB")

# ---------------------------------------------------------------- indexes
def build(name, sql):
    t = time.time()
    con.execute(sql)
    con.execute("CHECKPOINT")
    timings[f"build_{name}_s"] = round(time.time() - t, 1)
    sizes[f"db_after_{name}_bytes"] = os.path.getsize(DB)
    print(f"  {name}: {timings[f'build_{name}_s']}s, db now "
          f"{sizes[f'db_after_{name}_bytes']/1e9:.2f} GB")

build("hnsw_1280", "CREATE INDEX hnsw_1280 ON tiles USING HNSW (emb_n) WITH (metric = 'cosine')")
build("hnsw_pca128", "CREATE INDEX hnsw_pca ON tiles USING HNSW (emb_pca) WITH (metric = 'cosine')")
build("hnsw_slide_base",
      "CREATE INDEX hnsw_sl_base ON slide_embeddings USING HNSW (base) WITH (metric = 'cosine')")
build("hnsw_slide_diag",
      "CREATE INDEX hnsw_sl_diag ON slide_embeddings USING HNSW (diagnostic) WITH (metric = 'cosine')")

idx = con.execute("SELECT index_name, table_name FROM duckdb_indexes()").fetchall()
print("indexes:", idx)

sizes["bytes_per_tile_1280_float32"] = D * 4
sizes["bytes_per_tile_pca128_float32"] = PD * 4
sizes["hnsw_1280_index_bytes"] = sizes["db_after_hnsw_1280_bytes"] - sizes["db_after_load_bytes"]
sizes["hnsw_pca128_index_bytes"] = (sizes["db_after_hnsw_pca128_bytes"]
                                    - sizes["db_after_hnsw_1280_bytes"])
out = dict(n_tiles=int(N), n_slides=len(rows), n_slides_with_prism2=len(emb_rows),
           slides_without_prism2=no_slide_emb, dim_raw=int(D), dim_pca=int(PD),
           duckdb_version=duckdb.__version__, chunk_rows=CHUNK,
           hnsw_persistence="experimental (hnsw_enable_experimental_persistence=true required)",
           timings_s=timings, sizes_bytes=sizes, indexes=[i[0] for i in idx])
C.dump(out, "duckdb_build_metrics.json")
con.close()
print(json.dumps(out["timings_s"], indent=2))
