#!/usr/bin/env python3
"""Figure 2, the deconfound: Leiden inside Arm A, where centre and organ are held constant.

The pilot reported 99-100% slide-dominated communities and ARI 0.9 against slide, on 8 slides
drawn from 6 organs and 5 atlases. Two things were entangled in that number and both are separated
here.

  THE CONFOUND.  8 slides from 5 centres cannot distinguish "tiles cluster by slide" from
  "tiles cluster by scanner and stain". Arm A is 103 slides of HTAN BU lung: one centre, one
  protocol, one organ. A third stratum fixes progression stage as well.

  THE GRANULARITY ARTEFACT, which matters more.  Both ARI-against-slide and "% of tiles in a
  community where one slide supplies >=90%" are bounded by how many communities exist. With 8
  slides, a partition into 10 communities can put each slide in its own; with 103 slides it
  cannot, whatever the embeddings look like. So resolution is swept until the number of
  communities passes the number of slides, and every quantity is plotted against
  COMMUNITIES PER SLIDE rather than against the resolution parameter, which is not comparable
  across strata. The pilot's 8-slide regime is included as its own stratum, averaged over
  independent draws, so the artefact can be seen rather than argued about.

Tiles per slide is held at 300 in every stratum, so slide count is the only thing that varies.
The permutation null (same partition, slide labels shuffled) is drawn alongside, because with many
small communities some dominance arises by chance.
"""
import os, sys, time
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from sklearn.decomposition import PCA
from sklearn.neighbors import NearestNeighbors
from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score
import igraph as ig, leidenalg
import pandas as pd
C.rcparams()

STORE = os.path.join(C.REPO, "analysis", "data", "store")
PER_SLIDE = 300
K = 30
RESOLUTIONS = [0.25, 1.0, 4.0, 16.0, 64.0, 256.0]
DOM = 0.90

tiles = pd.read_parquet(os.path.join(STORE, "tiles.parquet"))
meta = C.load_meta()
counts = tiles.groupby("sample").size().to_dict()
base = tiles.groupby("sample")["row"].min().to_dict()
F = np.load(os.path.join(STORE, "features.f32.npy"), mmap_mode="r")

def fetch(samples, per_slide=PER_SLIDE, seed=42):
    sub = {s: meta[s] for s in samples}
    picks = C.balanced_sample(sub, counts, per_slide=per_slide, seed=seed)
    rows = np.sort(np.array([base[s] + i for s, i in picks]))
    lut = {base[s] + i: s for s, i in picks}
    return C.l2(np.asarray(F[rows])), np.array([lut[r] for r in rows])

def top_shares(cl, slide):
    out = []
    for c in range(cl.max() + 1):
        m = cl == c
        _, cnt = np.unique(slide[m], return_counts=True)
        out.append((int(m.sum()), float(cnt.max() / m.sum())))
    return out

def permuted(cl, slide, n=10, seed=0):
    rng = np.random.default_rng(seed)
    dom, shares = [], None
    for i in range(n):
        sh = slide.copy(); rng.shuffle(sh)
        t = top_shares(cl, sh)
        dom.append(sum(m for m, s in t if s >= DOM) / len(cl) * 100)
        if i == 0:
            shares = [s for _, s in t]
    return float(np.mean(dom)), shares

def sweep(X, samp, n_slides):
    P = PCA(n_components=50, random_state=0).fit_transform(X)
    nn = NearestNeighbors(n_neighbors=K + 1, metric="cosine").fit(P)
    _, ind = nn.kneighbors(P)
    ind = ind[:, 1:]
    edges = sorted({(min(i, j), max(i, j)) for i in range(len(P)) for j in ind[i]})
    g = ig.Graph(n=len(P), edges=edges, directed=False)
    ttt = np.array([meta[s]["ttt"] for s in samp])
    pt = np.array([meta[s]["patient"] for s in samp])
    out = []
    for r in RESOLUTIONS:
        part = leidenalg.find_partition(g, leidenalg.RBConfigurationVertexPartition,
                                        resolution_parameter=r, seed=0, n_iterations=2)
        cl = np.array(part.membership)
        t = top_shares(cl, samp)
        dom_t = sum(m for m, s in t if s >= DOM)
        pdom, pshares = permuted(cl, samp)
        out.append(dict(resolution=r, n_communities=int(cl.max() + 1),
                        communities_per_slide=round((cl.max() + 1) / n_slides, 3),
                        ari_slide=round(float(adjusted_rand_score(samp, cl)), 3),
                        ari_class=round(float(adjusted_rand_score(ttt, cl)), 3),
                        ari_patient=round(float(adjusted_rand_score(pt, cl)), 3),
                        nmi_slide=round(float(normalized_mutual_info_score(samp, cl)), 3),
                        nmi_class=round(float(normalized_mutual_info_score(ttt, cl)), 3),
                        pct_tiles_slide_dominated=round(100 * dom_t / len(cl), 1),
                        pct_tiles_slide_dominated_permuted=round(pdom, 1),
                        median_top_slide_share=round(float(np.median([s for _, s in t])), 3),
                        median_top_slide_share_permuted=round(float(np.median(pshares)), 3),
                        _shares=[s for _, s in t], _shares_permuted=pshares))
    return out

def mean_of(list_of_sweeps):
    """Average the sweep across independent draws, keeping one draw's share distributions."""
    keys = [k for k in list_of_sweeps[0][0] if not k.startswith("_")]
    out = []
    for i in range(len(RESOLUTIONS)):
        rec = {k: (list_of_sweeps[0][i][k] if k == "resolution"
                   else round(float(np.mean([s[i][k] for s in list_of_sweeps])), 3)) for k in keys}
        rec["_shares"] = list_of_sweeps[0][i]["_shares"]
        rec["_shares_permuted"] = list_of_sweeps[0][i]["_shares_permuted"]
        rec["n_draws"] = len(list_of_sweeps)
        out.append(rec)
    return out

armA = sorted([s for s in meta if meta[s]["arm"] == "A"])
allsl = sorted(meta)
byc = {}
for s in armA:
    byc.setdefault(meta[s]["ttt"], []).append(s)
focus_class = max(byc, key=lambda c: len(byc[c]))
focus_slides = sorted(byc[focus_class])

results = {}
t0 = time.time()

# the pilot's regime, within one centre and organ, averaged over 5 independent draws of 8 slides
draws = []
for d in range(5):
    pick = sorted(np.random.default_rng(100 + d).choice(armA, size=8, replace=False))
    X, samp = fetch(list(pick), seed=200 + d)
    draws.append(sweep(X, samp, 8))
LAB_PILOT = "Arm A, 8 slides\nthe pilot's slide count"
results[LAB_PILOT] = mean_of(draws)
print(f"pilot regime done {time.time()-t0:.0f}s")

for lab, samples in [
        (f"Arm A, {focus_class.split(' - ')[0].lower()} only\n{len(focus_slides)} slides, "
         f"stage also constant", focus_slides),
        (f"Arm A\n{len(armA)} slides, 1 centre, 1 organ", armA),
        (f"all arms\n{len(allsl)} slides, 4 centres, 5 organs", allsl)]:
    t = time.time()
    X, samp = fetch(samples)
    results[lab] = sweep(X, samp, len(samples))
    print(f"[{lab.splitlines()[0]}] {len(X):,} tiles / {len(samples)} slides  {time.time()-t:.0f}s")

# literal reproduction of the pilot's configuration: 8 slides, 1500 tiles each, res 0.1 to 2.0
RES_SAVE = RESOLUTIONS
RESOLUTIONS = [0.1, 0.25, 0.5, 1.0, 2.0]
pilot_lit = []
for d in range(3):
    pick = sorted(np.random.default_rng(300 + d).choice(armA, size=8, replace=False))
    X, samp = fetch(list(pick), per_slide=1500, seed=400 + d)
    pilot_lit.append(sweep(X, samp, 8))
pilot_literal = [{k: v for k, v in r.items() if not k.startswith("_")} for r in mean_of(pilot_lit)]
RESOLUTIONS = RES_SAVE
print("literal pilot reproduction (8 Arm A slides, 1500 tiles each):")
for r in pilot_literal:
    print(f"  res {r['resolution']}: {r['n_communities']:.0f} communities, "
          f"ARI slide {r['ari_slide']}, {r['pct_tiles_slide_dominated']}% slide-dominated")

clean = {k: [{a: b for a, b in r.items() if not a.startswith("_")} for r in v]
         for k, v in results.items()}
C.dump(dict(per_slide=PER_SLIDE, k=K, dominance_threshold=DOM, resolutions=RESOLUTIONS,
            method=f"leiden (RBConfiguration) on cosine kNN k={K} over PCA-50",
            note="quantities are plotted against communities per slide, not resolution: both ARI "
                 "against slide and the dominance percentage are bounded by the community count, "
                 "so they are not comparable across strata at a fixed resolution",
            focus_class=focus_class, sweep=clean,
            pilot_literal_reproduction=dict(
                config="8 Arm A slides, 1500 tiles each, mean of 3 draws, resolutions 0.1 to 2.0",
                sweep=pilot_literal)), "leiden_metrics.json")

# ------------------------------------------------------------------ figure
LAB = list(results)
COL = {l: C.CAT[i] for i, l in enumerate(LAB)}
fig, axes = plt.subplots(2, 2, figsize=(12.6, 9.2))

def cps(v):
    return [x["communities_per_slide"] for x in v]

ax = axes[0, 0]
for lab in LAB:
    v = results[lab]
    ax.plot(cps(v), [x["ari_slide"] for x in v], "-o", color=COL[lab], lw=2, ms=6.5,
            label=lab.replace("\n", " · "))
ax.axvline(1.0, color=C.INK3, lw=1, ls="--")
ax.text(1.12, 0.02, "one community\nper slide", fontsize=7.4, color=C.INK3, va="bottom")
ax.set_xscale("log"); ax.set_ylim(0, 1)
ax.set_xlabel("communities per slide"); ax.set_ylabel("ARI against slide")
ax.grid(color=C.GRID, lw=0.8); ax.set_axisbelow(True)
ax.set_title("Do communities recover slide identity?\nboth axes matter: the metric is bounded by "
             "the community count", fontsize=10.5, color=C.INK, loc="left", linespacing=1.6)
ax.legend(fontsize=7.4, loc="upper right", labelcolor=C.INK2)

ax = axes[0, 1]
for lab in LAB:
    v = results[lab]
    ax.plot(cps(v), [x["ari_class"] for x in v], "-o", color=COL[lab], lw=2, ms=6.5)
ax.axvline(1.0, color=C.INK3, lw=1, ls="--")
ax.set_xscale("log"); ax.set_ylim(0, 1)
ax.set_xlabel("communities per slide"); ax.set_ylabel("ARI against progression class")
ax.grid(color=C.GRID, lw=0.8); ax.set_axisbelow(True)
ax.set_title("Do they recover progression class?\nsame colours as on the left; the "
             "stage-constant stratum is 0 by construction", fontsize=10.5, color=C.INK,
             loc="left", linespacing=1.6)

ax = axes[1, 0]
for lab in LAB:
    v = results[lab]
    ax.plot(cps(v), [x["pct_tiles_slide_dominated"] for x in v], "-o", color=COL[lab], lw=2, ms=6.5)
    ax.plot(cps(v), [x["pct_tiles_slide_dominated_permuted"] for x in v], ":", color=COL[lab],
            lw=1.4, alpha=0.85)
ax.axvline(1.0, color=C.INK3, lw=1, ls="--")
ax.set_xscale("log"); ax.set_ylim(-2, 103)
ax.set_xlabel("communities per slide")
ax.set_ylabel(f"% of tiles in a ≥{DOM:.0%} single-slide community")
ax.grid(color=C.GRID, lw=0.8); ax.set_axisbelow(True)
ax.set_title("Slide dominance\ndotted = same partition with slide labels permuted",
             fontsize=10.5, color=C.INK, loc="left", linespacing=1.6)

# matched granularity: the resolution whose community count is closest to the slide count
ax = axes[1, 1]
rng = np.random.default_rng(3)
for i, lab in enumerate(LAB):
    v = results[lab]
    j = int(np.argmin([abs(np.log(x["communities_per_slide"])) for x in v]))
    for k, (vals, col) in enumerate([(np.array(v[j]["_shares"]), COL[lab]),
                                     (np.array(v[j]["_shares_permuted"]), "#dcdbd6")]):
        x = i + (k * 0.34 - 0.17)
        ax.scatter(np.full(len(vals), x) + rng.normal(0, 0.05, len(vals)), vals, s=15,
                   color=col, alpha=0.85, linewidths=0.5, edgecolors=C.SURFACE, zorder=3)
        ax.plot([x - 0.13, x + 0.13], [np.median(vals)] * 2, color=C.INK, lw=2, zorder=4)
    ax.annotate(f"{v[j]['n_communities']:.0f} communities", (i, 1.035), ha="center", fontsize=7.2,
                color=C.INK3)
ax.axhline(DOM, color=C.INK3, lw=1, ls="--")
ax.text(len(LAB) - 0.5, DOM + 0.015, f"{DOM:.0%}", fontsize=7.4, color=C.INK3, ha="right")
ax.set_xticks(range(len(LAB)), [l.splitlines()[0].replace(", ", ",\n") for l in LAB], fontsize=8)
ax.set_ylim(0, 1.1); ax.set_ylabel("top slide's share of the community")
ax.grid(axis="y", color=C.GRID, lw=0.8); ax.set_axisbelow(True)
ax.set_title("Community composition at matched granularity\ncoloured = observed   "
             "grey = slide labels permuted   bar = median", fontsize=10.5, color=C.INK,
             loc="left", linespacing=1.6)

fig.suptitle("Leiden on Virchow2 tile embeddings: the pilot's slide-locking was a slide-count "
             "artefact, not a stain artefact", fontsize=12.5, color=C.INK, y=0.99)
fig.subplots_adjust(hspace=0.42, wspace=0.24, top=0.885)
C.savefig(fig, "fig2_leiden_deconfound.png")
