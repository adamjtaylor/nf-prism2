#!/usr/bin/env python3
"""Figure 1: what does the Virchow2 tile space actually organise by?

One shared UMAP, several labellings, equal aspect everywhere, so the eye compares the colouring
and never the geometry.

The layout is dictated by a palette constraint rather than by taste. A scatter plot puts every
pair of series on screen at once, and the validated categorical palette only clears the
colour-vision-deficiency floors for its first THREE slots under that all-pairs condition. Centre
has five values and organ has five, so neither can be a five-hue scatter. The skill's remedy is to
facet, so those two labellings become strips of small multiples: one panel per value, that value in
slot-1 blue, everything else in grey. One hue per panel means no pair to confuse.

  row 1   slide identity, THREE Arm A slides of the SAME class, so centre, organ and stage are
          all constant and anything separating them is slide identity alone
          progression class, which is ordinal, so it gets the one-hue blue ramp, not eight hues
  row 2   one panel per centre
  row 3   one panel per organ

Sampling is slide-balanced and patient-capped, so neither a 36,399-tile slide nor a patient with
four specimens can shape the projection.
"""
import os, sys, time
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
C.rcparams()

STORE = os.path.join(C.REPO, "analysis", "data", "store")
PER_SLIDE = 300
PATIENT_CAP = 2
GREY = "#dcdbd6"

import pandas as pd
tiles = pd.read_parquet(os.path.join(STORE, "tiles.parquet"))
meta = C.load_meta()
counts = tiles.groupby("sample").size().to_dict()
base = tiles.groupby("sample")["row"].min().to_dict()

picks = C.balanced_sample(meta, counts, per_slide=PER_SLIDE, seed=42, patient_cap=PATIENT_CAP)
rows = np.array([base[s] + i for s, i in picks])
o = np.argsort(rows); rows = rows[o]; picks = [picks[i] for i in o]
sel_samples = sorted({s for s, _ in picks})
print(f"{len(picks):,} tiles from {len(sel_samples)} slides "
      f"({len({meta[s]['patient'] for s in sel_samples})} patients)")

F = np.load(os.path.join(STORE, "features.f32.npy"), mmap_mode="r")
X = C.l2(np.asarray(F[rows]))
samp = np.array([s for s, _ in picks])
ttt = np.array([meta[s]["ttt"] for s in samp])
centre = np.array([meta[s]["centre"] for s in samp])
organ = np.array([meta[s]["organ_resolved"] for s in samp])

cache = os.path.join(C.HERE, "umap_coords.npz")
if os.path.exists(cache) and np.load(cache)["emb"].shape[0] == len(rows):
    emb = np.load(cache)["emb"]
    print("reusing cached UMAP")
else:
    import umap
    from sklearn.decomposition import PCA
    t0 = time.time()
    P = PCA(n_components=50, random_state=0).fit_transform(X)
    emb = umap.UMAP(n_neighbors=30, min_dist=0.1, metric="cosine", random_state=0).fit_transform(P)
    print(f"UMAP in {time.time()-t0:.0f}s")
    np.savez_compressed(cache, emb=emb, sample=samp, ttt=ttt, centre=centre, organ=organ)

CLASSES = [c for c in C.CLASS_ORDER if (ttt == c).any()]
CENTRES = sorted(set(centre))
ORGANS = sorted(set(organ))
seq = C.seq_cmap()
# ordinal ramp: start at step 250, the lightest step that still clears 2:1 on the light surface
class_colour = {c: seq(0.25 + 0.75 * i / max(1, len(CLASSES) - 1)) for i, c in enumerate(CLASSES)}

# three Arm A slides of one class: centre, organ and progression stage all held constant
byc = {}
for s in sel_samples:
    if meta[s]["arm"] == "A":
        byc.setdefault(meta[s]["ttt"], []).append(s)
focus_class = max(byc, key=lambda c: len(byc[c]))
cand = sorted(byc[focus_class], key=lambda s: -counts[s])
hi = cand[:3]
print(f"slide panel highlights {hi} (all Arm A / {focus_class})")

XL = (emb[:, 0].min() - 1, emb[:, 0].max() + 1)
YL = (emb[:, 1].min() - 1, emb[:, 1].max() + 1)

def frame(ax, title, sub=None, ts=10.5):
    ax.set_aspect("equal")
    ax.set_xlim(*XL); ax.set_ylim(*YL)
    ax.set_xticks([]); ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_visible(True); sp.set_color(C.GRID); sp.set_linewidth(0.8)
    ax.set_title(title if sub is None else f"{title}\n{sub}", fontsize=ts, color=C.INK,
                 loc="left", pad=5, linespacing=1.5)

fig = plt.figure(figsize=(12.6, 13.4))
gs = fig.add_gridspec(3, 6, height_ratios=[2.55, 1.0, 1.0], hspace=0.16, wspace=0.06)

# ---- row 1, panel 1: slide identity at fixed centre, organ and stage
ax = fig.add_subplot(gs[0, 0:3])
m_other = ~np.isin(samp, hi)
ax.scatter(emb[m_other, 0], emb[m_other, 1], s=1.5, c=GREY, alpha=0.7, linewidths=0, rasterized=True)
for i, s in enumerate(hi):
    m = samp == s
    ax.scatter(emb[m, 0], emb[m, 1], s=4.5, c=C.CAT[i], alpha=0.95, linewidths=0, rasterized=True)
frame(ax, "Slide identity",
      f"three Arm A slides, all {C.CLASS_SHORT.get(focus_class, focus_class)}: same centre, "
      f"same organ, same stage")
ax.legend(handles=[Line2D([], [], marker="o", ls="", ms=7, mfc=C.CAT[i], mec="none",
                          label=f"{s}  ({counts[s]:,} tiles)") for i, s in enumerate(hi)]
                  + [Line2D([], [], marker="o", ls="", ms=7, mfc=GREY, mec="none",
                            label=f"the other {len(sel_samples)-3} slides")],
          fontsize=7.6, loc="upper left", labelcolor=C.INK2, handletextpad=0.35)

# ---- row 1, panel 2: progression class on the ordinal ramp
ax = fig.add_subplot(gs[0, 3:6])
for c in CLASSES:
    m = ttt == c
    ax.scatter(emb[m, 0], emb[m, 1], s=1.6, color=class_colour[c], alpha=0.6, linewidths=0,
               rasterized=True)
frame(ax, "Progression class", "TumorTissueType, ordinal, so a single-hue ramp low to high")
ax.legend(handles=[Line2D([], [], marker="o", ls="", ms=7, mfc=class_colour[c], mec="none",
                          label=C.CLASS_SHORT.get(c, c)) for c in CLASSES],
          fontsize=7.6, loc="upper left", ncol=2, labelcolor=C.INK2, handletextpad=0.35,
          columnspacing=0.8)

# ---- rows 2 and 3: one panel per value, faceted because five hues cannot share a scatter
def strip(row, values, labels_arr, heading):
    for j, v in enumerate(values):
        ax = fig.add_subplot(gs[row, j])
        m = labels_arr == v
        ax.scatter(emb[~m, 0], emb[~m, 1], s=0.9, c=GREY, alpha=0.6, linewidths=0, rasterized=True)
        ax.scatter(emb[m, 0], emb[m, 1], s=1.5, c=C.CAT[0], alpha=0.75, linewidths=0, rasterized=True)
        n_sl = len({s for s in sel_samples if
                    (meta[s]["centre"] if heading == "Centre" else meta[s]["organ_resolved"]) == v})
        frame(ax, f"{v}", f"{n_sl} slide" + ("s" if n_sl != 1 else ""), ts=9)
    ax = fig.add_subplot(gs[row, len(values):]) if len(values) < 6 else None
    if ax is not None:
        ax.axis("off")
        ax.text(0.0, 0.62, heading, fontsize=11, color=C.INK, va="center")
        ax.text(0.0, 0.44, "highlighted in blue\nagainst the same projection\nin grey",
                fontsize=7.6, color=C.INK3, va="top", linespacing=1.5)

strip(1, CENTRES, centre, "Centre")
strip(2, ORGANS, organ, "Organ")

fig.suptitle(f"Virchow2 tile embeddings, one UMAP, four labellings          "
             f"{len(picks):,} tiles · {PER_SLIDE} per slide · {len(sel_samples)} slides · "
             f"max {PATIENT_CAP} slides per patient",
             fontsize=12, color=C.INK, y=0.995)
fig.subplots_adjust(top=0.905, bottom=0.02, left=0.03, right=0.985)
C.savefig(fig, "fig1_umap_small_multiples.png")

C.dump(dict(n_tiles=len(picks), per_slide=PER_SLIDE, patient_cap=PATIENT_CAP,
            n_slides=len(sel_samples),
            n_patients=len({meta[s]["patient"] for s in sel_samples}),
            slide_panel_class=focus_class, slide_panel_slides=hi,
            classes=CLASSES, centres=CENTRES, organs=ORGANS,
            palette_note="centre and organ are faceted, not hue-coded: the validated categorical "
                         "palette clears the all-pairs CVD floors for three slots only, and a "
                         "scatter puts every pair on screen at once"),
        "umap_meta.json")
