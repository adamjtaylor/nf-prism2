#!/usr/bin/env python3
"""Consolidate 163 per-slide h5 files into one memmappable store, and take the cohort census.

Every later script needs random access to tiles across slides, which is exactly what 163
separate h5 files are bad at. This writes:

  features.f32.npy   (N, 1280) float32, raw Virchow2 class tokens in slide-then-coord order
  pca128.f32.npy     (N, 128)  float32, PCA of the L2-normalised features, fit on a
                     SLIDE-BALANCED subsample so a 36,399-tile slide does not define the axes
  tiles.parquet      one row per tile: ids, labels, level-0 xy
  census.json        slides, patients, tiles per arm / class / centre / organ

The PCA fit is slide-balanced on purpose. Fitting on all tiles would let the largest slides set
the principal axes, which is the same confound the analysis is trying to measure.

The raw h5 files stay under analysis/data/ (gitignored); only derived metrics reach the repo.
"""
import json, os, sys, time
import numpy as np, h5py

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C

STORE = os.path.join(C.REPO, "analysis", "data", "store")
os.makedirs(STORE, exist_ok=True)
D = 1280
PCA_DIM = 128
PCA_FIT_PER_SLIDE = 400

meta = C.load_meta()
print(f"{len(meta)} slides with tile features")

# ---- pass 1: shapes, so the output can be preallocated -------------------------------------
counts, geom = {}, {}
for s, r in sorted(meta.items()):
    with h5py.File(r["h5"], "r") as h:
        counts[s] = int(h["features"].shape[0])
        a = dict(h["coords"].attrs)
        geom[s] = dict(w=int(a["level0_width"]), h=int(a["level0_height"]),
                       ps0=float(a["patch_size_level0"]), mag=float(a.get("target_magnification", 20.0)),
                       trident_name=str(a.get("name", "")))
N = sum(counts.values())
print(f"{N:,} tiles total; median {int(np.median(list(counts.values())))}/slide; max {max(counts.values()):,}")

# ---- pass 2: copy features and build the tile table ----------------------------------------
feat_path = os.path.join(STORE, "features.f32.npy")
F = np.lib.format.open_memmap(feat_path, mode="w+", dtype=np.float32, shape=(N, D))
rows, off = [], 0
t0 = time.time()
for s, r in sorted(meta.items()):
    with h5py.File(r["h5"], "r") as h:
        n = counts[s]
        F[off:off + n] = np.asarray(h["features"][...], dtype=np.float32)
        xy = np.asarray(h["coords"][...], dtype=np.int64)
    for i in range(n):
        rows.append((off + i, f"{s}:{i}", s, r["arm"], r["patient"], r["centre"], r["ttt"],
                     r["organ_resolved"], int(xy[i, 0]), int(xy[i, 1])))
    off += n
F.flush(); del F
print(f"features written in {time.time()-t0:.0f}s -> {feat_path}")

import pandas as pd
tiles = pd.DataFrame(rows, columns=["row", "tile_id", "sample", "arm", "patient", "centre",
                                    "ttt", "organ", "x", "y"])
tiles.to_parquet(os.path.join(STORE, "tiles.parquet"), index=False)
print("wrote tiles.parquet", tiles.shape)

# ---- PCA-128 on L2-normalised features, fit slide-balanced ---------------------------------
from sklearn.decomposition import PCA
rng = np.random.default_rng(0)
fit_rows = []
start = 0
for s, _ in sorted(meta.items()):
    n = counts[s]
    k = min(PCA_FIT_PER_SLIDE, n)
    fit_rows.append(start + np.sort(rng.choice(n, size=k, replace=False)))
    start += n
fit_rows = np.concatenate(fit_rows)
Fm = np.load(feat_path, mmap_mode="r")
Xfit = C.l2(np.asarray(Fm[fit_rows]))
pca = PCA(n_components=PCA_DIM, random_state=0).fit(Xfit)
evr = float(pca.explained_variance_ratio_.sum())
print(f"PCA-{PCA_DIM} fit on {len(fit_rows):,} slide-balanced tiles, explains {evr:.3f} of variance")

P = np.lib.format.open_memmap(os.path.join(STORE, f"pca{PCA_DIM}.f32.npy"), mode="w+",
                              dtype=np.float32, shape=(N, PCA_DIM))
CH = 20000
for i in range(0, N, CH):
    P[i:i + CH] = pca.transform(C.l2(np.asarray(Fm[i:i + CH]))).astype(np.float32)
P.flush(); del P
print("wrote pca128.f32.npy")
np.save(os.path.join(STORE, "pca_components.npy"), pca.components_.astype(np.float32))
np.save(os.path.join(STORE, "pca_mean.npy"), pca.mean_.astype(np.float32))

# ---- census -------------------------------------------------------------------------------
def agg(key):
    out = {}
    for s, r in meta.items():
        k = r[key] if key != "arm" else r["arm"]
        d = out.setdefault(str(k), dict(slides=0, patients=set(), tiles=0))
        d["slides"] += 1; d["patients"].add(r["patient"]); d["tiles"] += counts[s]
    return {k: dict(slides=v["slides"], patients=len(v["patients"]), tiles=v["tiles"])
            for k, v in sorted(out.items())}

multi = {}
for s, r in meta.items():
    multi.setdefault(r["patient"], []).append(s)
census = dict(
    n_slides=len(meta), n_patients=len(multi), n_tiles=int(N),
    tiles_per_slide=dict(median=int(np.median(list(counts.values()))),
                         min=int(min(counts.values())), max=int(max(counts.values())),
                         mean=round(float(np.mean(list(counts.values()))), 1)),
    patients_with_multiple_slides=sum(1 for v in multi.values() if len(v) > 1),
    pca_dim=PCA_DIM, pca_explained_variance=round(evr, 4),
    pca_fit_tiles=int(len(fit_rows)), pca_fit_per_slide=PCA_FIT_PER_SLIDE,
    by_arm=agg("arm"), by_class=agg("ttt"), by_centre=agg("centre"), by_organ=agg("organ_resolved"),
    organ_imputed_slides=sum(1 for r in meta.values() if r["organ_imputed"]),
    per_slide_tiles=counts, geometry=geom)
C.dump(census, "census.json")
for k, v in census["by_arm"].items():
    print(f"  arm {k}: {v['slides']} slides, {v['patients']} patients, {v['tiles']:,} tiles")
