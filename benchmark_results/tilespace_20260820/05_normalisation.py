#!/usr/bin/env python3
"""Figure 5: can normalisation make the tile space searchable across slides, at cohort scale?

Two metrics that pull against each other, so neither is reportable alone:

  mixing  mean fraction of a tile's 30 nearest neighbours that come from a DIFFERENT slide.
          Higher is better. The ceiling, if slide identity were ignored entirely, is the mean
          per-query fraction of the pool that is off-slide.
  p@10    precision at 10 for same progression class AND different patient, with the query's
          whole patient excluded from the index. This is the search task from figure 4.

A transformation can raise mixing simply by destroying the signal, which is why the pair is
plotted as a trade-off plane rather than as two independent bar charts.

Faceted by stratum, because the interesting question is whether normalisation earns its keep only
when centre varies:

  Arm A     one centre, one organ  -> any gain here is not a stain-batch correction
  all arms  four centres           -> the pilot's situation

Harmony is included because it is the single-cell standard, and excluded from the recommendation
for a structural reason, not a numerical one: it has no out-of-sample transform. Its correction is
a per-cell offset learned jointly over the whole dataset, so adding one slide to the index requires
recomputing every existing embedding. That disqualifies it from a growing index whatever it scores.
"""
import os, sys, time
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C
import knn
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from sklearn.decomposition import PCA
import pandas as pd
C.rcparams()

STORE = os.path.join(C.REPO, "analysis", "data", "store")
PER_SLIDE = 300
K = 30
NPC = 50

tiles = pd.read_parquet(os.path.join(STORE, "tiles.parquet"))
meta = C.load_meta()
counts = tiles.groupby("sample").size().to_dict()
base = tiles.groupby("sample")["row"].min().to_dict()
F = np.load(os.path.join(STORE, "features.f32.npy"), mmap_mode="r")

def per_slide_transform(A, slide, mode):
    B = A.astype(np.float32).copy()
    for s in np.unique(slide):
        m = slide == s
        mu = B[m].mean(0)
        if mode == "center":
            B[m] -= mu
        else:
            sd = B[m].std(0); sd[sd == 0] = 1.0
            B[m] = (B[m] - mu) / sd
    return B

def evaluate(Z, slide, patient, ttt):
    """mixing at k=30 and p@10 (same class, different patient, patient excluded), on the same set."""
    Z = knn.l2(Z)
    N = len(Z)
    # mixing: self removed, nothing else excluded
    I, _ = knn.topk_blocks(Z, Z, K + 1, [(np.arange(N), None)])
    selfc = I == np.arange(N)[:, None]
    rank = np.where(~selfc, np.arange(K + 1)[None, :], K + 2)
    I = np.take_along_axis(I, np.sort(rank, axis=1)[:, :K], axis=1)
    mixing = (slide[I] != slide[:, None]).mean(1)
    n_by_s = pd.Series(slide).value_counts().to_dict()
    mix_ceiling = float(np.mean([1 - (n_by_s[s] - 1) / (N - 1) for s in slide]))

    # p@10, whole patient excluded
    blocks = knn.blocks_by_group(patient, patient, "group")
    J, _ = knn.topk_blocks(Z, Z, 10, blocks)
    rel = (ttt[J] == ttt[:, None]) & (patient[J] != patient[:, None])
    p10 = rel.mean(1)
    # closed-form per-query chance: the relevant set is {same class, other patient}, and every
    # excluded tile belongs to the query's own patient, so only the denominator moves
    n_by_c = pd.Series(ttt).value_counts().to_dict()
    n_by_p = pd.Series(patient).value_counts().to_dict()
    n_by_cp = pd.Series(list(zip(ttt, patient))).value_counts().to_dict()
    rel_pool = np.array([n_by_c[c] - n_by_cp.get((c, p), 0) for c, p in zip(ttt, patient)], float)
    pool = np.array([N - n_by_p[p] for p in patient], float)
    ch = float(np.mean(rel_pool / np.maximum(pool, 1)))
    mm, mlo, mhi = C.patient_bootstrap(patient, mixing, n_boot=500, seed=4)
    pm, plo, phi = C.patient_bootstrap(patient, p10, n_boot=500, seed=5)
    return dict(mixing=round(mm, 4), mixing_ci=[round(mlo, 4), round(mhi, 4)],
                mixing_ceiling=round(mix_ceiling, 4),
                p_at_10=round(pm, 4), p_at_10_ci=[round(plo, 4), round(phi, 4)],
                p_at_10_chance=round(ch, 4), lift=round(pm / max(ch, 1e-9), 3))

STRATA = {"Arm A  ·  one centre, one organ": ["A"], "all arms  ·  four centres": ["A", "B", "C"]}
results = {}
for label, arms in STRATA.items():
    sub = {s: r for s, r in meta.items() if r["arm"] in arms}
    picks = C.balanced_sample(sub, counts, per_slide=PER_SLIDE, seed=42)
    rows = np.sort(np.array([base[s] + i for s, i in picks]))
    lut = {base[s] + i: s for s, i in picks}
    X = knn.l2(np.asarray(F[rows]))
    slide = np.array([lut[r] for r in rows])
    patient = np.array([meta[s]["patient"] for s in slide])
    ttt = np.array([meta[s]["ttt"] for s in slide])
    print(f"[{label}] {len(X):,} tiles, {len(set(slide))} slides, {len(set(patient))} patients")

    treatments = {}
    treatments["raw L2"] = PCA(n_components=NPC, random_state=0).fit_transform(X)
    treatments["per-slide centering"] = PCA(n_components=NPC, random_state=0).fit_transform(
        knn.l2(per_slide_transform(X, slide, "center")))
    treatments["per-slide standardise"] = PCA(n_components=NPC, random_state=0).fit_transform(
        knn.l2(per_slide_transform(X, slide, "standardise")))
    try:
        import harmonypy
        t0 = time.time()
        ho = harmonypy.run_harmony(treatments["raw L2"], pd.DataFrame({"slide": slide}),
                                   ["slide"], max_iter_harmony=20)
        Zc = np.asarray(ho.Z_corr)
        if Zc.shape[0] != len(X):
            Zc = Zc.T
        treatments["Harmony (slide as batch)"] = Zc.astype(np.float32)
        print(f"  harmony {time.time()-t0:.0f}s")
    except Exception as e:
        print("  harmony unavailable:", e)

    res = {}
    for name, Z in treatments.items():
        t0 = time.time()
        res[name] = evaluate(np.asarray(Z, dtype=np.float32), slide, patient, ttt)
        print(f"  {name:26s} mixing {res[name]['mixing']:.3f} "
              f"(ceiling {res[name]['mixing_ceiling']:.3f})  "
              f"p@10 {res[name]['p_at_10']:.3f} (chance {res[name]['p_at_10_chance']:.3f})  "
              f"{time.time()-t0:.0f}s")
    results[label] = dict(n_tiles=int(len(X)), n_slides=int(len(set(slide))),
                          n_patients=int(len(set(patient))), treatments=res)

C.dump(dict(per_slide=PER_SLIDE, k=K, pca_dim=NPC,
            note="Harmony has no out-of-sample transform, so it cannot serve a growing index "
                 "regardless of score",
            by_stratum=results), "normalisation_metrics.json")

# ------------------------------------------------------------------ figure
names = list(next(iter(results.values()))["treatments"])
fig, axes = plt.subplots(1, 2, figsize=(12.4, 4.9))
for ax, (label, r) in zip(axes, results.items()):
    t = r["treatments"]
    for i, n in enumerate(names):
        if n not in t:
            continue
        x, y = t[n]["mixing"], t[n]["p_at_10"]
        ax.errorbar(x, y,
                    xerr=[[x - t[n]["mixing_ci"][0]], [t[n]["mixing_ci"][1] - x]],
                    yerr=[[y - t[n]["p_at_10_ci"][0]], [t[n]["p_at_10_ci"][1] - y]],
                    fmt="o", color=C.CAT[i], ms=11, mec=C.SURFACE, mew=1.6,
                    ecolor=C.CAT[i], elinewidth=1.2, capsize=2.5, zorder=4)
        ax.annotate(n, (x, y), textcoords="offset points", xytext=(13, 4), fontsize=8.4,
                    color=C.INK2, zorder=5)
    ref = t["raw L2"]
    ax.axvline(ref["mixing_ceiling"], color=C.INK3, lw=1.1, ls="--")
    ax.text(ref["mixing_ceiling"] - 0.012, 0.02, f"mixing ceiling {ref['mixing_ceiling']:.3f}",
            rotation=90, fontsize=7.4, color=C.INK3, ha="right", va="bottom")
    ax.axhline(ref["p_at_10_chance"], color=C.INK3, lw=1.1, ls=":")
    ax.text(0.005, ref["p_at_10_chance"] + 0.006, f"p@10 chance {ref['p_at_10_chance']:.3f}",
            fontsize=7.4, color=C.INK3, va="bottom")
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(0, max(0.45, max(t[n]["p_at_10"] for n in t) * 1.35))
    ax.set_xlabel(f"mixing: cross-slide share of the {K} nearest neighbours  →  better")
    ax.set_ylabel("p@10, same class & different patient  →  better")
    ax.grid(color=C.GRID, lw=0.8); ax.set_axisbelow(True)
    ax.set_title(f"{label}\n{r['n_tiles']:,} tiles, {r['n_slides']} slides, {r['n_patients']} patients",
                 fontsize=10, color=C.INK, loc="left")

fig.suptitle("Normalisation trade-off plane: mixing buys nothing if it costs retrieval",
             fontsize=12, color=C.INK, y=1.03)
fig.subplots_adjust(wspace=0.26)
C.savefig(fig, "fig5_normalisation.png")
