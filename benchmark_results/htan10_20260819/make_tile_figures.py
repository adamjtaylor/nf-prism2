#!/usr/bin/env python3
"""Tile-level Virchow2 embedding structure for the HTAN 10-slide pilot.

This is the Aim 1.4 tile-level check: cluster the (N, 1280) class-token embeddings, project
with UMAP, and score cluster agreement with slide and organ labels using adjusted Rand,
F-measure and Jaccard, which are the metrics Epic 4 names.
"""
import glob, json, os
import h5py, numpy as np
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from sklearn.decomposition import PCA
from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score, f1_score, jaccard_score
from scipy.optimize import linear_sum_assignment
import umap

SURFACE, INK, INK2 = "#fcfcfb", "#0b0b0b", "#52514e"
# categorical slots 1-8 from the dataviz reference palette, fixed order, never cycled
CAT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#8a8880"]
mpl.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "text.color": INK, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "axes.edgecolor": "#dcdbd6", "axes.linewidth": 0.8, "font.size": 9,
    "axes.spines.top": False, "axes.spines.right": False,
})
HERE = os.path.dirname(os.path.abspath(__file__))
FIG = os.path.join(HERE, "figures")
FEAT = "/tmp/htan10/features"

META = {  # slide -> (short label, organ)
    "BU_lung_adenocarcinoma_svs":            ("lung adeno (BU)",        "lung"),
    "BU_lung_squamous_ndpi":                 ("lung squam (BU)",        "lung"),
    "SRRS_lung_squamous_svs":                ("lung squam (SRRS)",      "lung"),
    "DUKE_breast_dcis_svs":                  ("breast DCIS (Duke)",     "breast"),
    "HMS_colorectal_adenocarcinoma_ometiff": ("colon adeno (HMS)",      "colon"),
    "HMS_ovarian_hgserous_svs":              ("tube HGSC (HMS)",        "tube/ovary"),
    "HMS_skin_melanoma_ometiff":             ("skin melanoma (HMS)",    "skin"),
    "WUSTL_pancreas_pbcarcinoma_svs":        ("pancreas carc (WUSTL)",  "pancreas"),
}
ORDER = list(META)
PER_SLIDE = 1500          # equal sampling so a big slide cannot dominate the projection
rng = np.random.default_rng(42)

X, slide_lab, n_total = [], [], {}
for s in ORDER:
    with h5py.File(os.path.join(FEAT, f"{s}.h5"), "r") as h:
        key = "features" if "features" in h else [k for k in h if h[k].ndim == 2][0]
        n = h[key].shape[0]
        n_total[s] = n
        idx = np.sort(rng.choice(n, size=min(PER_SLIDE, n), replace=False))
        X.append(np.asarray(h[key][idx], dtype=np.float32))
    slide_lab += [s] * len(idx)
X = np.vstack(X)
slide_lab = np.array(slide_lab)
organ_lab = np.array([META[s][1] for s in slide_lab])
print(f"{X.shape[0]} tiles sampled from {len(ORDER)} slides, dim {X.shape[1]}")
assert X.shape[1] == 1280, X.shape

Xp = PCA(n_components=50, random_state=0).fit_transform(X)
emb = umap.UMAP(n_neighbors=30, min_dist=0.1, metric="cosine", random_state=0).fit_transform(Xp)

def purity(labels_true, k):
    km = KMeans(n_clusters=k, n_init=10, random_state=0).fit_predict(Xp)
    ari = adjusted_rand_score(labels_true, km)
    # map clusters to classes by best assignment, then macro F1 and Jaccard
    cls = sorted(set(labels_true)); C = np.zeros((k, len(cls)), int)
    for c, t in zip(km, labels_true):
        C[c, cls.index(t)] += 1
    r, c = linear_sum_assignment(-C)
    m = {ri: cls[ci] for ri, ci in zip(r, c)}
    pred = np.array([m.get(x, cls[0]) for x in km])
    return ari, f1_score(labels_true, pred, average="macro"), \
jaccard_score(labels_true, pred, average="macro"), km

res = {}
for name, lab in [("slide", slide_lab), ("organ", organ_lab)]:
    k = len(set(lab))
    ari, f1, jac, km = purity(lab, k)
    res[name] = dict(k=k, ari=round(float(ari), 3), f1=round(float(f1), 3), jaccard=round(float(jac), 3))
    print(f"k-means k={k} vs {name}: ARI {ari:.3f}  macro-F1 {f1:.3f}  macro-Jaccard {jac:.3f}")
json.dump({"n_sampled": int(X.shape[0]), "per_slide": PER_SLIDE,
           "tiles_total": {k: int(v) for k, v in n_total.items()}, "purity": res},
          open(os.path.join(HERE, "tile_clustering_metrics.json"), "w"), indent=2)

# ------------------------------------------------------------------ figure
fig, axes = plt.subplots(1, 2, figsize=(12.4, 5.6), constrained_layout=True)
for ax, (lab, title, keys) in zip(axes, [
        (slide_lab, "coloured by slide", ORDER),
        (organ_lab, "coloured by organ", ["lung", "breast", "colon", "tube/ovary", "skin", "pancreas"])]):
    for i, k in enumerate(keys):
        m = lab == k
        nm = META[k][0] if k in META else k
        ax.scatter(emb[m, 0], emb[m, 1], s=3.2, alpha=0.55, linewidths=0,
                   color=CAT[i % len(CAT)], label=f"{nm} ({m.sum()})", rasterized=True)
    ax.set_title(title, fontsize=10, color=INK)
    ax.set_xticks([]); ax.set_yticks([])
    ax.set_xlabel("UMAP 1"); ax.set_ylabel("UMAP 2")
    ax.set_aspect("equal")
    leg = ax.legend(frameon=False, fontsize=7.5, markerscale=3.2, loc="best")
fig.suptitle(f"Virchow2 tile embeddings, {X.shape[0]:,} tiles ({PER_SLIDE} sampled per slide)\n"
             "community metrics are in make_leiden.py, which supersedes the k-means pass here",
             fontsize=12, color=INK)
fig.savefig(os.path.join(FIG, "fig4_tile_umap.png"), dpi=180)
print("wrote fig4")
