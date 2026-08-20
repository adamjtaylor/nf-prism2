#!/usr/bin/env python3
"""Figure 8: the slide embedding space projected, for all three slide representations.

163 points is a small number to hand to UMAP, so this figure is built to be checked rather than
admired:

  row 1  UMAP (cosine, n_neighbors=15) of all 163 slides, the projection people will want to look
         at. At this n it does what UMAP does with well-separated groups: it collapses each organ
         into a tight island and throws away everything inside them.
  row 2  PCA of the same vectors, with the explained variance printed. It is the more useful
         picture here, and it is also the control: a structure that appears in the UMAP and not in
         the first two components is a hint the UMAP is arranging noise, which at n=163 it can.
  row 3  the same two projections restricted to ARM A, 103 slides of HTAN BU lung. With organ and
         centre held constant there is no organ island to dominate the layout, so this is the row
         where progression class has a chance to show. It is the projection-space counterpart of
         the deconfound in section 2.

Encoding avoids the palette's series cap rather than working around it. Organ has five values and
progression class has seven, and a scatter puts every pair of series on screen at once, where the
validated categorical palette only clears the colour-vision-deficiency floors for three slots. So:

  hue    progression class, which is ORDINAL, on the one-hue blue ramp (no categorical cap applies)
  shape  organ, which is nominal, on marker glyphs (no hue involved, so nothing to confuse)

Centre is not drawn: in this cohort each atlas contributes exactly one organ, so the organ glyph
already carries it.

Below the projections, the check that decides whether the layout can be read at all: how much of a
slide's 5-nearest-neighbour composition survives the drop to two dimensions. Neighbours are taken
with the query's own patient excluded, as everywhere else.
"""
import os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from sklearn.decomposition import PCA
from scipy.stats import spearmanr
import pandas as pd
C.rcparams()

STORE = os.path.join(C.REPO, "analysis", "data", "store")
REPS = ["PRISM2 base\n2560-d", "PRISM2 diagnostic\n3072-d", "mean-pooled tiles\n1280-d"]
K = 5
N_NEIGHBORS = 15

tiles = pd.read_parquet(os.path.join(STORE, "tiles.parquet"))
meta = C.load_meta()
F = np.load(os.path.join(STORE, "features.f32.npy"), mmap_mode="r")
grp = tiles.groupby("sample")["row"]
lo_of, n_of = grp.min().to_dict(), grp.size().to_dict()

samples = sorted(meta)
base, diag, mp = [], [], []
for s in samples:
    z = np.load(os.path.join(C.SLIDE_EMB, s, f"{s}.embeddings.npz"))
    base.append(np.asarray(z["base"], dtype=np.float32)[0])
    diag.append(np.asarray(z["diagnostic"], dtype=np.float32)[0])
    lo, n = int(lo_of[s]), int(n_of[s])
    mp.append(np.asarray(F[lo:lo + n]).mean(0))
V = {REPS[0]: C.l2(np.vstack(base)), REPS[1]: C.l2(np.vstack(diag)), REPS[2]: C.l2(np.vstack(mp))}
pt = np.array([meta[s]["patient"] for s in samples])
ttt = np.array([meta[s]["ttt"] for s in samples])
organ = np.array([meta[s]["organ_resolved"] for s in samples])
arm = np.array([meta[s]["arm"] for s in samples])
ntile = np.array([n_of[s] for s in samples])
print(f"{len(samples)} slides, {len(set(pt))} patients")

CLASSES = [c for c in C.CLASS_ORDER if (ttt == c).any()]
ORGANS = sorted(set(organ))
MARK = dict(zip(ORGANS, ["o", "s", "^", "D", "v"]))
seq = C.seq_cmap()
# ordinal ramp starting at step 250, the lightest that clears 2:1 on the light surface
COL = {c: seq(0.25 + 0.75 * i / max(1, len(CLASSES) - 1)) for i, c in enumerate(CLASSES)}

import umap
ARMA = arm == "A"
STRATA = {"all": np.ones(len(samples), bool), "armA": ARMA}
proj, evr = {}, {}
for st, sel in STRATA.items():
    for r in REPS:
        X = V[r][sel]
        # n_neighbors cannot exceed the sample, and on 103 points 15 is already generous
        nn = min(N_NEIGHBORS, max(5, sel.sum() // 8))
        proj[("UMAP", r, st)] = umap.UMAP(n_neighbors=nn, min_dist=0.15, metric="cosine",
                                          random_state=0).fit_transform(X)
        p = PCA(n_components=2, random_state=0).fit(X)
        proj[("PCA", r, st)] = p.transform(X)
        evr[(r, st)] = p.explained_variance_ratio_
        print(f"  [{st}] {r.splitlines()[0]:20s} n={sel.sum()} nn={nn} "
              f"PC1+PC2 explain {evr[(r, st)].sum():.3f}")

# ---- does the 2-D layout preserve who is next to whom? ------------------------------------
def knn_purity(X, labels, patients, k=K):
    """Mean fraction of a slide's k nearest neighbours sharing its label, own patient excluded.

    `patients` is passed explicitly rather than read from the enclosing scope: the class version of
    this measurement runs on the Arm A subset, and silently keeping the full-cohort patient vector
    is exactly the kind of mismatch that would go unnoticed.
    """
    high = X.shape[1] > 2
    S = C.l2(X) @ C.l2(X).T if high else -((X[:, None, :] - X[None, :, :]) ** 2).sum(-1)
    keep_p, out = [], []
    for i in range(len(X)):
        m = patients != patients[i]
        if m.sum() < k:
            continue
        order = np.argsort(-np.where(m, S[i], -np.inf))[:k]
        out.append((labels[order] == labels[i]).mean())
        keep_p.append(patients[i])
    return C.patient_bootstrap(np.array(keep_p), np.array(out), n_boot=2000, seed=9)

purity = {}
for r in REPS:
    # full cohort: does the projection keep organ neighbourhoods?
    for space, X in [("original", V[r]), ("UMAP 2-D", proj[("UMAP", r, "all")]),
                     ("PCA 2-D", proj[("PCA", r, "all")])]:
        purity[(r, space, "organ")] = knn_purity(X, organ, pt)
    # Arm A: does it keep progression-class neighbourhoods, with organ and centre constant?
    for space, X in [("original", V[r][ARMA]), ("UMAP 2-D", proj[("UMAP", r, "armA")]),
                     ("PCA 2-D", proj[("PCA", r, "armA")])]:
        purity[(r, space, "class")] = knn_purity(X, ttt[ARMA], pt[ARMA])
    print(f"  {r.splitlines()[0]:20s} organ@5 "
          f"orig {purity[(r,'original','organ')][0]:.2f} umap {purity[(r,'UMAP 2-D','organ')][0]:.2f} "
          f"pca {purity[(r,'PCA 2-D','organ')][0]:.2f}   |   Arm A class@5 "
          f"orig {purity[(r,'original','class')][0]:.2f} umap {purity[(r,'UMAP 2-D','class')][0]:.2f} "
          f"pca {purity[(r,'PCA 2-D','class')][0]:.2f}")

# ---- what is PC1 inside Arm A actually tracking? ------------------------------------------
# It carries 40 to 65% of the variance on its own, within one centre and one organ, which is a lot
# for 103 slides. Section 6 found the classes differ 38-fold in tile count, so the obvious rival
# explanation for any apparent progression axis is specimen SIZE. Both are tested against PC1, and
# the size correlation is the one that would invalidate a biological reading.
RANK = np.array([C.RANK[c] for c in ttt])
pc1 = {}
for r in REPS:
    v = proj[("PCA", r, "armA")][:, 0]
    rho_cls = spearmanr(v, RANK[ARMA]).statistic
    rho_size = spearmanr(v, np.log10(ntile[ARMA])).statistic
    # sign of PC1 is arbitrary, so orient it so that a positive rho means "rises with stage"
    if rho_cls < 0:
        rho_cls, rho_size = -rho_cls, -rho_size
    pc1[r] = dict(spearman_vs_ordinal_class=round(float(rho_cls), 4),
                  spearman_vs_log_tiles=round(float(rho_size), 4),
                  spearman_class_vs_log_tiles=round(
                      float(spearmanr(RANK[ARMA], np.log10(ntile[ARMA])).statistic), 4),
                  variance_explained=round(float(evr[(r, "armA")][0]), 4))
    print(f"  Arm A PC1, {r.splitlines()[0]:20s} vs class rho {rho_cls:+.2f}, "
          f"vs log tiles rho {rho_size:+.2f}, explains {evr[(r,'armA')][0]:.0%}")
print(f"  (class rank and log tiles are themselves correlated at rho "
      f"{spearmanr(RANK[ARMA], np.log10(ntile[ARMA])).statistic:+.2f}, so these cannot be "
      f"fully separated in this cohort)")

C.dump(dict(n_slides=len(samples), n_patients=int(len(set(pt))), k=K, umap_n_neighbors=N_NEIGHBORS,
            pca_explained_variance={f"{r.splitlines()[0]} | {st}":
                                    [round(float(x), 4) for x in evr[(r, st)]]
                                    for (r, st) in evr},
            knn_purity_at_5={f"{r.splitlines()[0]} | {sp} | {lb}":
                             dict(purity=round(v[0], 4), ci=[round(v[1], 4), round(v[2], 4)])
                             for (r, sp, lb), v in purity.items()},
            note="purity is the mean fraction of a slide's 5 nearest neighbours sharing its label, "
                 "query patient excluded; CI over patients. Comparing 'original' with the 2-D rows "
                 "says how much of the neighbourhood the projection kept.",
            arm_A_pc1=pc1,
            encoding="hue = ordinal progression class on the blue ramp; marker shape = organ"),
        "slide_umap_metrics.json")

# ------------------------------------------------------------------ figure
def square(ax, E, pad=0.10):
    """Equal aspect AND a common box shape: pad each panel's limits out to a square around its
    data, so the six panels are the same physical size and their titles line up. Padding the
    limits does not distort anything; letting matplotlib shrink the box to fit the data would."""
    cx, cy = (E[:, 0].max() + E[:, 0].min()) / 2, (E[:, 1].max() + E[:, 1].min()) / 2
    half = max(np.ptp(E[:, 0]), np.ptp(E[:, 1])) / 2 * (1 + pad) + 1e-9
    ax.set_xlim(cx - half, cx + half); ax.set_ylim(cy - half, cy + half)
    ax.set_aspect("equal")
    ax.set_xticks([]); ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_visible(True); sp.set_color(C.GRID); sp.set_linewidth(0.8)

ROWS = [("UMAP", "all", "All 163 slides, UMAP"),
        ("PCA", "all", "All 163 slides, PCA"),
        ("PCA", "armA", "Arm A only, 103 HTAN BU lung slides, PCA")]
fig, axes = plt.subplots(3, 3, figsize=(12.8, 13.6))
for i, (space, st, rowlab) in enumerate(ROWS):
    sel = STRATA[st]
    for j, r in enumerate(REPS):
        ax = axes[i, j]
        E = proj[(space, r, st)]
        t, o = ttt[sel], organ[sel]
        for c in CLASSES:
            for og in ORGANS:
                m = (t == c) & (o == og)
                if not m.any():
                    continue
                ax.scatter(E[m, 0], E[m, 1], s=46, color=COL[c], marker=MARK[og],
                           linewidths=0.9, edgecolors=C.SURFACE, zorder=3)
        square(ax, E)
        lab = "class" if st == "armA" else "organ"
        bits = [f"{lab} purity@5 {purity[(r, space + ' 2-D', lab)][0]:.2f}",
                f"original {purity[(r, 'original', lab)][0]:.2f}"]
        if space == "PCA":
            bits.insert(0, f"PC1 {evr[(r, st)][0]:.0%}, PC2 {evr[(r, st)][1]:.0%}")
        if st == "armA":
            bits.append(f"PC1 vs stage ρ {pc1[r]['spearman_vs_ordinal_class']:+.2f}, "
                        f"vs log tiles ρ {pc1[r]['spearman_vs_log_tiles']:+.2f}")
        head = "   ·   ".join(bits[:-1]) if st == "armA" else "   ·   ".join(bits)
        tail = f"\n{bits[-1]}" if st == "armA" else ""
        ax.set_title(f"{r.splitlines()[0]}\n{head}{tail}",
                     fontsize=9.2, color=C.INK, loc="left", linespacing=1.7)
    axes[i, 0].text(-0.055, 0.5, rowlab, transform=axes[i, 0].transAxes, rotation=90,
                    va="center", ha="right", fontsize=10, color=C.INK)

hcls = [Line2D([], [], marker="o", ls="", ms=7.5, mfc=COL[c], mec="none",
               label=C.CLASS_SHORT.get(c, c)) for c in CLASSES]
horg = [Line2D([], [], marker=MARK[o], ls="", ms=7.5, mfc=C.INK3, mec="none", label=o)
        for o in ORGANS]
leg1 = fig.legend(handles=hcls, loc="lower left", bbox_to_anchor=(0.055, 0.002), ncol=4,
                  fontsize=8.4, labelcolor=C.INK2, title="progression class  (hue, ordinal)",
                  title_fontsize=8.4, alignment="left")
fig.legend(handles=horg, loc="lower left", bbox_to_anchor=(0.615, 0.002), ncol=3, fontsize=8.4,
           labelcolor=C.INK2, title="organ  (marker shape)", title_fontsize=8.4, alignment="left")
fig.add_artist(leg1)

fig.suptitle("Slide embeddings projected: three representations, and the same view with organ "
             "and centre held constant", fontsize=12.5, color=C.INK, y=0.988)
fig.subplots_adjust(top=0.925, bottom=0.085, left=0.055, right=0.985, hspace=0.20, wspace=0.10)
C.savefig(fig, "fig8_slide_umap.png")
