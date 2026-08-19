#!/usr/bin/env python3
"""Does per-slide normalisation make tile embeddings searchable across slides?

Tile embeddings cluster by slide at ARI 0.9, so unconstrained cosine search returns same-slide
tiles. This compares four treatments on the same 10,059 tiles, with two metrics that pull in
opposite directions, because a correction that mixes slides by destroying biology is useless:

  mixing   = mean fraction of a tile's 30 nearest neighbours that come from a DIFFERENT slide
             (higher is better; 7/8 = 0.875 would be the value if slide identity were ignored)
  p@10     = precision at 10 for SAME ORGAN but DIFFERENT SLIDE, which is the Aim 1.5 search
             task. Only evaluable on the three lung slides, the one organ with more than one
             slide here, so it is a narrow test.
"""
import json, os
import h5py, numpy as np
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from sklearn.decomposition import PCA
from sklearn.neighbors import NearestNeighbors

SURFACE, INK, INK2 = "#fcfcfb", "#0b0b0b", "#52514e"
BLUE, ORANGE = "#2a78d6", "#eb6834"
mpl.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "text.color": INK, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "axes.edgecolor": "#dcdbd6", "axes.linewidth": 0.8, "font.size": 9,
    "axes.spines.top": False, "axes.spines.right": False})
HERE = os.path.dirname(os.path.abspath(__file__)); FIG = os.path.join(HERE, "figures")
FEAT = "/tmp/htan10/features"
META = {
    "BU_lung_adenocarcinoma_svs": "lung", "BU_lung_squamous_ndpi": "lung",
    "SRRS_lung_squamous_svs": "lung", "DUKE_breast_dcis_svs": "breast",
    "HMS_colorectal_adenocarcinoma_ometiff": "colon", "HMS_ovarian_hgserous_svs": "tube/ovary",
    "HMS_skin_melanoma_ometiff": "skin", "WUSTL_pancreas_pbcarcinoma_svs": "pancreas"}
rng = np.random.default_rng(42)
X, slide = [], []
for s in META:
    with h5py.File(os.path.join(FEAT, f"{s}.h5"), "r") as h:
        key = "features" if "features" in h else [k for k in h if h[k].ndim == 2][0]
        idx = np.sort(rng.choice(h[key].shape[0], size=min(1500, h[key].shape[0]), replace=False))
        X.append(np.asarray(h[key][idx], dtype=np.float32))
    slide += [s] * len(idx)
X = np.vstack(X).astype(np.float64); slide = np.array(slide)
organ = np.array([META[s] for s in slide])

def l2(A):
    return A / np.linalg.norm(A, axis=1, keepdims=True)

def per_slide(A, mode):
    B = A.copy()
    for s in set(slide):
        m = slide == s
        mu = B[m].mean(0)
        if mode == "center":
            B[m] -= mu
        elif mode == "standardise":
            sd = B[m].std(0); sd[sd == 0] = 1.0
            B[m] = (B[m] - mu) / sd
    return B

treatments = {
    "raw (L2 only)": l2(X),
    "per-slide centering": l2(per_slide(X, "center")),
    "per-slide standardise": l2(per_slide(X, "standardise")),
}
try:  # the single-cell standard: soft-cluster batch correction in PCA space
    import harmonypy
    P = PCA(n_components=50, random_state=0).fit_transform(l2(X))
    import pandas as pd
    ho = harmonypy.run_harmony(P, pd.DataFrame({"slide": slide}), ["slide"], max_iter_harmony=20)
    Zc = np.asarray(ho.Z_corr)
    if Zc.shape[0] != X.shape[0]:   # harmonypy versions differ in orientation
        Zc = Zc.T
    treatments["harmony (slide as batch)"] = l2(Zc)
except Exception as e:
    print("harmony unavailable:", e)

N_NEIGH = 30
rows = []
for name, A in treatments.items():
    Z = PCA(n_components=50, random_state=0).fit_transform(A) if A.shape[1] > 50 else A
    nn = NearestNeighbors(n_neighbors=N_NEIGH + 1, metric="cosine").fit(Z)
    _, ind = nn.kneighbors(Z)
    ind = ind[:, 1:]
    diff_slide = (slide[ind] != slide[:, None]).mean()

    # Cross-slide retrieval, done the way a search tool would have to: build the index from
    # every tile EXCEPT the query's own slide, then take the true top 10. Chance is computed
    # per query from the composition of that query's own candidate pool.
    hits, chance = [], []
    for s_q in sorted(set(slide[organ == "lung"])):
        q = np.where(slide == s_q)[0]
        pool = np.where(slide != s_q)[0]
        nn2 = NearestNeighbors(n_neighbors=10, metric="cosine").fit(Z[pool])
        _, ii = nn2.kneighbors(Z[q])
        hits.append((organ[pool[ii]] == "lung").mean(axis=1))
        chance += [ (organ[pool] == "lung").mean() ] * len(q)
    hits = np.concatenate(hits)
    rows.append(dict(treatment=name, mixing=round(float(diff_slide), 3),
                     p_at_10_cross_slide_lung=round(float(hits.mean()), 3),
                     chance=round(float(np.mean(chance)), 3)))
    print(rows[-1])

json.dump({"n_tiles": int(X.shape[0]), "n_neighbours": N_NEIGH,
           "note": "mixing ceiling if slide were ignored = 0.875; p@10 index excludes the query's own slide",
           "results": rows}, open(os.path.join(HERE, "normalisation_metrics.json"), "w"), indent=2)

fig, axes = plt.subplots(1, 2, figsize=(10.8, 4.3), constrained_layout=True)
names = [r["treatment"] for r in rows]
y = np.arange(len(names))
for ax, (key, title, ref, reflab) in zip(axes, [
        ("mixing", "Neighbourhood mixing across slides", 0.875, "ceiling if slide ignored (0.875)"),
        ("p_at_10_cross_slide_lung", "Cross-slide retrieval: p@10 for lung",
         rows[0]["chance"], f"chance ({rows[0]['chance']:.2f})")]):
    v = [r[key] for r in rows]
    ax.barh(y, v, height=0.52, color=BLUE)
    ax.axvline(ref, color=ORANGE, lw=2, ls="--")
    ax.annotate(reflab, (ref, len(names) - 0.35), color=ORANGE, fontsize=7.5,
                ha="right", va="bottom", rotation=90)
    for i, val in enumerate(v):
        ax.text(val + 0.012, i, f"{val:.3f}", va="center", fontsize=8.5, color=INK2)
    ax.set_yticks(y, names, fontsize=8.5)
    ax.set_xlim(0, 1.0); ax.invert_yaxis()
    ax.grid(axis="x", color="#ecebe6", lw=0.8); ax.set_axisbelow(True)
    ax.set_title(title, fontsize=10, color=INK)
fig.suptitle("Per-slide normalisation of Virchow2 tile embeddings, 10,059 tiles, 30 nearest neighbours",
             fontsize=11.5, color=INK)
fig.savefig(os.path.join(FIG, "fig6_normalisation.png"), dpi=200)
print("wrote fig6")
