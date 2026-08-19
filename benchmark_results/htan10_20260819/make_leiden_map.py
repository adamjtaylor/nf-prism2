#!/usr/bin/env python3
"""Where the Leiden communities actually sit.

19 communities is more than the 8 categorical slots, and cycling hues is not allowed, so
identity is carried by numeric labels at each community centroid while colour carries slide
(8 slots, fixed order). The composition matrix on the right is the quantitative version of the
same claim: each community's tiles, broken down by slide.
"""
import json, os
import h5py, numpy as np, scanpy as sc, anndata as ad
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

SURFACE, INK, INK2 = "#fcfcfb", "#0b0b0b", "#52514e"
CAT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#8a8880"]
BLUE_RAMP = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
             "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]
SEQ = LinearSegmentedColormap.from_list("blue_seq", BLUE_RAMP)
mpl.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "text.color": INK, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "axes.edgecolor": "#dcdbd6", "axes.linewidth": 0.8, "font.size": 9,
    "axes.spines.top": False, "axes.spines.right": False})
HERE = os.path.dirname(os.path.abspath(__file__)); FIG = os.path.join(HERE, "figures")
FEAT = "/tmp/htan10/features"
META = {
    "BU_lung_adenocarcinoma_svs": "lung adeno (BU)", "BU_lung_squamous_ndpi": "lung squam (BU)",
    "SRRS_lung_squamous_svs": "lung squam (SRRS)", "DUKE_breast_dcis_svs": "breast DCIS (Duke)",
    "HMS_colorectal_adenocarcinoma_ometiff": "colon adeno (HMS)",
    "HMS_ovarian_hgserous_svs": "tube HGSC (HMS)", "HMS_skin_melanoma_ometiff": "skin melanoma (HMS)",
    "WUSTL_pancreas_pbcarcinoma_svs": "pancreas carc (WUSTL)"}
ORDER = list(META)
RES = 0.5
rng = np.random.default_rng(42)
X, slide = [], []
for s in ORDER:
    with h5py.File(os.path.join(FEAT, f"{s}.h5"), "r") as h:
        key = "features" if "features" in h else [k for k in h if h[k].ndim == 2][0]
        idx = np.sort(rng.choice(h[key].shape[0], size=min(1500, h[key].shape[0]), replace=False))
        X.append(np.asarray(h[key][idx], dtype=np.float32))
    slide += [s] * len(idx)
X = np.vstack(X); slide = np.array(slide)

a = ad.AnnData(X); a.obs["slide"] = slide
sc.pp.pca(a, n_comps=50, random_state=0)
sc.pp.neighbors(a, n_neighbors=30, metric="cosine", use_rep="X_pca", random_state=0)
sc.tl.leiden(a, resolution=RES, key_added="leiden", flavor="igraph", n_iterations=2,
             directed=False, random_state=0)
sc.tl.umap(a, random_state=0)
emb = a.obsm["X_umap"]
cl = a.obs["leiden"].to_numpy().astype(int)
K = cl.max() + 1

# composition matrix, communities ordered by their dominant slide then by size
comp = np.zeros((K, len(ORDER)))
for c in range(K):
    m = cl == c
    for j, s in enumerate(ORDER):
        comp[c, j] = (slide[m] == s).sum()
frac = comp / comp.sum(1, keepdims=True)
dom = frac.argmax(1)
order = sorted(range(K), key=lambda c: (dom[c], -comp[c].sum()))
pos = {c: i for i, c in enumerate(order)}

fig, axes = plt.subplots(1, 2, figsize=(13.2, 6.0), constrained_layout=True,
                         gridspec_kw={"width_ratios": [1.25, 1]})
ax = axes[0]
for j, s in enumerate(ORDER):
    m = slide == s
    ax.scatter(emb[m, 0], emb[m, 1], s=3.0, alpha=0.5, linewidths=0, color=CAT[j],
               label=META[s], rasterized=True)
for c in range(K):
    m = cl == c
    x, y = np.median(emb[m, 0]), np.median(emb[m, 1])
    ax.text(x, y, str(pos[c] + 1), fontsize=9, fontweight="bold", ha="center", va="center",
            color=INK, bbox=dict(boxstyle="circle,pad=0.16", fc=SURFACE, ec="#b9b8b2", lw=0.8))
ax.set_xticks([]); ax.set_yticks([]); ax.set_xlabel("UMAP 1"); ax.set_ylabel("UMAP 2")
    ax.set_aspect("equal")
ax.legend(frameon=False, fontsize=7.5, markerscale=3.2, loc="upper left")
ax.set_title(f"Colour = slide, numbered circles = the {K} Leiden communities at resolution {RES}",
             fontsize=10, color=INK)

ax = axes[1]
M = frac[order]
im = ax.imshow(M, cmap=SEQ, vmin=0, vmax=1, aspect="auto")
ax.set_xticks(range(len(ORDER)), [META[s] for s in ORDER], rotation=45, ha="right", fontsize=8)
ax.set_yticks(range(K), [f"{i+1}  (n={int(comp[c].sum())})" for i, c in enumerate(order)], fontsize=7.5)
for i, c in enumerate(order):
    for j in range(len(ORDER)):
        if M[i, j] >= 0.02:
            ax.text(j, i, f"{M[i, j]*100:.0f}", ha="center", va="center", fontsize=6.5,
                    color="#ffffff" if M[i, j] > 0.55 else INK2)
cb = fig.colorbar(im, ax=ax, fraction=0.035, shrink=0.9)
cb.set_label("share of the community's tiles (%)", fontsize=8); cb.outline.set_visible(False)
ax.set_title("Community composition by slide\n18 of 19 are at least 90% one slide", fontsize=10, color=INK)
fig.suptitle("Leiden communities nest inside slides, they do not span them", fontsize=12.5, color=INK)
fig.savefig(os.path.join(FIG, "fig7_leiden_communities.png"), dpi=180)

pure = int(sum(1 for c in range(K) if frac[c].max() >= 0.90))
json.dump({"resolution": RES, "n_communities": int(K), "slide_dominated_90pct": pure,
           "communities": [{"label": pos[c] + 1, "n_tiles": int(comp[c].sum()),
                            "dominant_slide": ORDER[dom[c]],
                            "dominant_share": round(float(frac[c].max()), 3)} for c in order]},
          open(os.path.join(HERE, "leiden_communities.json"), "w"), indent=2)
print(f"{K} communities, {pure} are >=90% one slide -> fig7")
