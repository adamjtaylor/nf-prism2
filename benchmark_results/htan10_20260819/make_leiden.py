#!/usr/bin/env python3
"""Leiden community detection on the Virchow2 tile embeddings.

Replaces the k-means pass, which was methodologically weak: fixing k to the number of slides
builds "clusters equal slides" into the result, and Euclidean k-means on PCA space assumes
isotropic blobs while the projection used cosine. Leiden needs no k, runs on the cosine kNN
graph, and a resolution sweep shows how the structure behaves rather than asserting one
partition.

Adds a direct measure of the batch concern: what fraction of communities are dominated by a
single slide, and what fraction of tiles sit in those communities.
"""
import json, os
import h5py, numpy as np, scanpy as sc, anndata as ad
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score

SURFACE, INK, INK2 = "#fcfcfb", "#0b0b0b", "#52514e"
BLUE, ORANGE = "#2a78d6", "#eb6834"
mpl.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "text.color": INK, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "axes.edgecolor": "#dcdbd6", "axes.linewidth": 0.8, "font.size": 9,
    "axes.spines.top": False, "axes.spines.right": False,
})
HERE = os.path.dirname(os.path.abspath(__file__)); FIG = os.path.join(HERE, "figures")
FEAT = "/tmp/htan10/features"
META = {
    "BU_lung_adenocarcinoma_svs": ("lung adeno (BU)", "lung"),
    "BU_lung_squamous_ndpi": ("lung squam (BU)", "lung"),
    "SRRS_lung_squamous_svs": ("lung squam (SRRS)", "lung"),
    "DUKE_breast_dcis_svs": ("breast DCIS (Duke)", "breast"),
    "HMS_colorectal_adenocarcinoma_ometiff": ("colon adeno (HMS)", "colon"),
    "HMS_ovarian_hgserous_svs": ("tube HGSC (HMS)", "tube/ovary"),
    "HMS_skin_melanoma_ometiff": ("skin melanoma (HMS)", "skin"),
    "WUSTL_pancreas_pbcarcinoma_svs": ("pancreas carc (WUSTL)", "pancreas"),
}
PER_SLIDE = 1500
rng = np.random.default_rng(42)
X, slide = [], []
for s in META:
    with h5py.File(os.path.join(FEAT, f"{s}.h5"), "r") as h:
        key = "features" if "features" in h else [k for k in h if h[k].ndim == 2][0]
        idx = np.sort(rng.choice(h[key].shape[0], size=min(PER_SLIDE, h[key].shape[0]), replace=False))
        X.append(np.asarray(h[key][idx], dtype=np.float32))
    slide += [s] * len(idx)
X = np.vstack(X); slide = np.array(slide)
organ = np.array([META[s][1] for s in slide])

a = ad.AnnData(X)
a.obs["slide"] = slide; a.obs["organ"] = organ
sc.pp.pca(a, n_comps=50, random_state=0)
# cosine kNN graph, the same metric the UMAP projection uses
sc.pp.neighbors(a, n_neighbors=30, metric="cosine", use_rep="X_pca", random_state=0)

rows = []
for r in [0.1, 0.25, 0.5, 1.0, 2.0]:
    sc.tl.leiden(a, resolution=r, key_added=f"l{r}", flavor="igraph", n_iterations=2,
                 directed=False, random_state=0)
    cl = a.obs[f"l{r}"].to_numpy()
    k = len(set(cl))
    # a community is "slide-dominated" when one slide supplies >=90% of its tiles
    dom, dom_tiles = 0, 0
    for c in set(cl):
        m = cl == c
        top = max((slide[m] == s).sum() for s in set(slide[m])) / m.sum()
        if top >= 0.90:
            dom += 1; dom_tiles += m.sum()
    rows.append(dict(resolution=r, n_clusters=int(k),
                     ari_slide=round(float(adjusted_rand_score(slide, cl)), 3),
                     ari_organ=round(float(adjusted_rand_score(organ, cl)), 3),
                     nmi_slide=round(float(normalized_mutual_info_score(slide, cl)), 3),
                     nmi_organ=round(float(normalized_mutual_info_score(organ, cl)), 3),
                     slide_dominated_clusters=f"{dom}/{k}",
                     pct_tiles_in_slide_dominated=round(100 * dom_tiles / len(cl), 1)))
    print(rows[-1])
json.dump({"n_tiles": int(X.shape[0]), "per_slide": PER_SLIDE, "method": "leiden on cosine kNN (k=30) over PCA-50",
           "sweep": rows}, open(os.path.join(HERE, "tile_clustering_metrics.json"), "w"), indent=2)

# ---- figure: resolution sweep, two panels (no dual axis) ----
fig, axes = plt.subplots(1, 2, figsize=(10.6, 4.2), constrained_layout=True)
res = [r["resolution"] for r in rows]
ax = axes[0]
ax.plot(res, [r["ari_slide"] for r in rows], "-o", color=BLUE, lw=2, ms=8, label="vs slide")
ax.plot(res, [r["ari_organ"] for r in rows], "-s", color=ORANGE, lw=2, ms=7.5, label="vs organ")
for r in rows:
    ax.annotate(f"{r['n_clusters']}", (r["resolution"], r["ari_slide"]), textcoords="offset points",
                xytext=(0, 9), ha="center", fontsize=7.5, color=INK2)
ax.set_xscale("log"); ax.set_xticks(res, [str(x) for x in res])
ax.set_xlabel("Leiden resolution  (label = number of communities)")
ax.set_ylabel("adjusted Rand index"); ax.set_ylim(0, 1)
ax.grid(axis="y", color="#ecebe6", lw=0.8); ax.set_axisbelow(True)
ax.legend(frameon=False, fontsize=8.5)
ax.set_title("Community agreement with known labels", fontsize=10, color=INK)

ax = axes[1]
ax.plot(res, [r["pct_tiles_in_slide_dominated"] for r in rows], "-o", color=BLUE, lw=2, ms=8)
ax.set_xscale("log"); ax.set_xticks(res, [str(x) for x in res])
ax.set_xlabel("Leiden resolution")
ax.set_ylabel("% of tiles in a single-slide community"); ax.set_ylim(0, 105)
ax.grid(axis="y", color="#ecebe6", lw=0.8); ax.set_axisbelow(True)
ax.set_title("Slide dominance: communities where one slide\nsupplies at least 90% of tiles",
             fontsize=10, color=INK)
fig.suptitle("Leiden on Virchow2 tile embeddings, 10,059 tiles, cosine kNN graph",
             fontsize=12, color=INK)
fig.savefig(os.path.join(FIG, "fig5_leiden_sweep.png"), dpi=200)
print("wrote fig5")
