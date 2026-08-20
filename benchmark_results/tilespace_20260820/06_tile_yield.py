#!/usr/bin/env python3
"""Figure 6: did min_tissue_proportion = 0.65 quietly over-filter sparse normal lung?

The pipeline raised TRIDENT's default 0.0 to the Virchow2 paper's 0.65, so a 224 px patch is kept
only if at least 65% of it is inside the tissue mask. Alveolar lung is lacy: a patch of normal
parenchyma is mostly airspace. If normal and normal-adjacent slides lose a larger share of their
tissue than primaries do, then every per-class comparison in this cohort is standing on unequal
tile counts, and that would be a confound introduced by our own preprocessing.

The measurable quantity is RETENTION, which needs no assumption about magnification:

    retention = tiles kept / (segmented tissue area / patch area at level 0)

Both areas are level-0 pixels from the same slide, so mpp cancels. The denominator is how many
non-overlapping patches would fit inside the tissue mask; the numerator is how many survived the
threshold. Retention near 1 means the mask was tiled almost completely; retention near 0.3 means
two thirds of the tissue was too sparse to clear 0.65.

Tissue area comes from the segmentation GeoJSON the pipeline already publishes, with interior
rings subtracted, so holes in the mask are not counted as tissue.

Arm A is the stratum that matters: one centre, one organ, six classes, so a retention difference
between classes cannot be a scanner or an organ difference.
"""
import glob, json, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib.lines import Line2D
import pandas as pd
C.rcparams()

STORE = os.path.join(C.REPO, "analysis", "data", "store")
GEO = os.path.join(C.REPO, "analysis", "data", "tissue_geojson")

def ring_area(ring):
    a = np.asarray(ring, dtype=np.float64)
    if len(a) < 3:
        return 0.0
    x, y = a[:, 0], a[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))

def tissue_area_px(path):
    """Total segmented tissue area in level-0 px^2. Exterior rings minus interior rings."""
    d = json.load(open(path))
    feats = d["features"] if isinstance(d, dict) else d
    tot = 0.0
    for f in feats:
        g = f.get("geometry") or {}
        polys = ([g["coordinates"]] if g.get("type") == "Polygon"
                 else g.get("coordinates", []) if g.get("type") == "MultiPolygon" else [])
        for p in polys:
            if not p:
                continue
            tot += ring_area(p[0]) - sum(ring_area(r) for r in p[1:])
    return tot

census = json.load(open(os.path.join(C.HERE, "census.json")))
meta = C.load_meta()
geo = {os.path.basename(os.path.dirname(os.path.dirname(p))): p
       for p in glob.glob(os.path.join(GEO, "*", "qc", "*.geojson"))}

rows = []
missing = []
for s, r in sorted(meta.items()):
    if s not in geo:
        missing.append(s); continue
    g = census["geometry"][s]
    ps0 = float(g["ps0"])
    area = tissue_area_px(geo[s])
    grid = area / (ps0 ** 2)
    n = census["per_slide_tiles"][s]
    mpp0 = 112.0 / ps0                      # 224 px at 20x covers 112 um, whatever level 0 is
    rows.append(dict(sample=s, arm=r["arm"], patient=r["patient"], centre=r["centre"],
                     ttt=r["ttt"], organ=r["organ_resolved"], n_tiles=n,
                     tissue_mm2=area * mpp0 ** 2 / 1e6, patches_in_mask=grid,
                     retention=n / grid if grid > 0 else np.nan,
                     slide_mm2=g["w"] * g["h"] * mpp0 ** 2 / 1e6, mpp0=mpp0))
df = pd.DataFrame(rows)
print(f"{len(df)} slides with segmentation; {len(missing)} without: {missing}")

def summarise(sub, key):
    out = {}
    for v in sorted(set(sub[key]), key=lambda x: C.CLASS_ORDER.index(x) if x in C.CLASS_ORDER else 99):
        d = sub[sub[key] == v]
        m, lo, hi = C.patient_bootstrap(d["patient"].to_numpy(), d["retention"].to_numpy(),
                                        n_boot=2000, seed=6)
        tm, tlo, thi = C.patient_bootstrap(d["patient"].to_numpy(), d["n_tiles"].to_numpy(),
                                           n_boot=2000, seed=7, stat=np.median)
        out[v] = dict(n_slides=int(len(d)), n_patients=int(d["patient"].nunique()),
                      retention=round(m, 4), retention_ci=[round(lo, 4), round(hi, 4)],
                      median_tiles=round(tm, 1), median_tiles_ci=[round(tlo, 1), round(thi, 1)],
                      median_tissue_mm2=round(float(d["tissue_mm2"].median()), 1),
                      tiles_per_mm2=round(float((d["n_tiles"] / d["tissue_mm2"]).median()), 2))
    return out

A = df[df["arm"] == "A"]
metrics = dict(min_tissue_proportion=0.65,
               definition="retention = tiles kept / (segmented tissue area / level-0 patch area)",
               n_slides_with_segmentation=int(len(df)), slides_without=missing,
               arm_A_by_class=summarise(A, "ttt"),
               all_arms_by_organ=summarise(df, "organ"),
               all_arms_by_centre=summarise(df, "centre"))

# is the normal/normal-adjacent group different from the rest of Arm A? bootstrap the difference
norm = A[A["ttt"].isin(["Normal", "Normal adjacent"])]
rest = A[~A["ttt"].isin(["Normal", "Normal adjacent"])]
rng = np.random.default_rng(11)
def boot_mean(d, n=4000):
    pts = d["patient"].unique()
    groups = [d[d["patient"] == p]["retention"].to_numpy() for p in pts]
    return np.array([np.concatenate([groups[i] for i in rng.integers(0, len(groups), len(groups))]).mean()
                     for _ in range(n)])
bn, br = boot_mean(norm), boot_mean(rest)
diff = bn - br
metrics["arm_A_normal_vs_rest"] = dict(
    normal_retention=round(float(norm["retention"].mean()), 4),
    rest_retention=round(float(rest["retention"].mean()), 4),
    difference=round(float(norm["retention"].mean() - rest["retention"].mean()), 4),
    difference_ci=[round(float(np.percentile(diff, 2.5)), 4), round(float(np.percentile(diff, 97.5)), 4)],
    n_slides=[int(len(norm)), int(len(rest))],
    n_patients=[int(norm["patient"].nunique()), int(rest["patient"].nunique())])
print(metrics["arm_A_normal_vs_rest"])
C.dump(metrics, "tile_yield_metrics.json")
df.to_csv(os.path.join(C.HERE, "tile_yield_per_slide.csv"), index=False)

# ------------------------------------------------------------------ figure
CLS = [c for c in C.CLASS_ORDER if c in set(A["ttt"])]
seq = C.seq_cmap()
col = {c: seq(0.12 + 0.82 * i / max(1, len(CLS) - 1)) for i, c in enumerate(CLS)}

fig = plt.figure(figsize=(13.4, 4.6))
gs = fig.add_gridspec(1, 3, width_ratios=[1.15, 1, 1], wspace=0.32)

ax = fig.add_subplot(gs[0, 0])
lim = [A["patches_in_mask"].min() * 0.6, A["patches_in_mask"].max() * 1.6]
ax.plot(lim, lim, color=C.INK3, lw=1.1, ls="--", zorder=2)
ax.annotate("every patch in the mask kept", xy=(lim[1] * 0.35, lim[1] * 0.35),
            xytext=(-8, -14), textcoords="offset points", fontsize=7.2, color=C.INK3,
            ha="right", va="top")
for c in CLS:
    d = A[A["ttt"] == c]
    ax.scatter(d["patches_in_mask"], d["n_tiles"], s=34, color=col[c], alpha=0.92,
               linewidths=0.7, edgecolors=C.SURFACE, zorder=3)
ax.set_xscale("log"); ax.set_yscale("log")
ax.set_xlabel("patches that fit inside the tissue mask")
ax.set_ylabel("tiles kept at min_tissue_proportion 0.65")
ax.grid(color=C.GRID, lw=0.8); ax.set_axisbelow(True)
ax.set_title("Arm A: kept against available\nevery slide sits close to the diagonal",
             fontsize=10.5, color=C.INK, loc="left", linespacing=1.6)
ax.legend(handles=[Line2D([], [], marker="o", ls="", ms=6, mfc=col[c], mec="none",
                          label=C.CLASS_SHORT.get(c, c)) for c in CLS],
          fontsize=7, loc="lower right", ncol=1, handletextpad=0.3, columnspacing=0.7,
          labelcolor=C.INK2, borderaxespad=0.9)

ax = fig.add_subplot(gs[0, 1])
y = np.arange(len(CLS))
m = [metrics["arm_A_by_class"][c]["retention"] for c in CLS]
lo = [metrics["arm_A_by_class"][c]["retention_ci"][0] for c in CLS]
hi = [metrics["arm_A_by_class"][c]["retention_ci"][1] for c in CLS]
ax.barh(y, m, height=0.55, color=C.CAT[0], zorder=3)
ax.errorbar(m, y, xerr=[np.array(m) - np.array(lo), np.array(hi) - np.array(m)], fmt="none",
            ecolor=C.INK2, elinewidth=1.2, capsize=3, zorder=4)
for i, c in enumerate(CLS):
    d = A[A["ttt"] == c]
    ax.scatter(d["retention"], np.full(len(d), i) + np.random.default_rng(i).normal(0, 0.075, len(d)),
               s=13, color=C.INK3, alpha=0.75, linewidths=0, zorder=5)
    ax.text(1.07, i, f"{m[i]:.2f}", va="center", fontsize=8.5, color=C.INK2)
ax.set_yticks(y, [f"{C.CLASS_SHORT.get(c, c)}  (n={metrics['arm_A_by_class'][c]['n_slides']})"
                  for c in CLS], fontsize=8.5)
ax.invert_yaxis(); ax.set_xlim(0, 1.20)
ax.set_xticks([0, 0.2, 0.4, 0.6, 0.8, 1.0])
ax.set_xlabel("retention  (tiles kept / patches available)")
ax.grid(axis="x", color=C.GRID, lw=0.8); ax.set_axisbelow(True)
ax.set_title("Retention by class, CI over patients\ngrey dots are individual slides",
             fontsize=10.5, color=C.INK, loc="left", linespacing=1.6)

ax = fig.add_subplot(gs[0, 2])
data = [A[A["ttt"] == c]["n_tiles"].to_numpy() for c in CLS]
for i, (c, v) in enumerate(zip(CLS, data)):
    ax.scatter(np.full(len(v), i) + np.random.default_rng(i + 40).normal(0, 0.06, len(v)), v,
               s=22, color=col[c], alpha=0.9, linewidths=0.6, edgecolors=C.SURFACE, zorder=3)
    ax.plot([i - 0.22, i + 0.22], [np.median(v)] * 2, color=C.INK, lw=2.2, zorder=4)
    # medians are labelled along the top of the axes rather than beside their own bar: the two
    # resection classes are 38x the others, so an in-place label lands on someone else's points
    ax.annotate(f"{int(np.median(v)):,}", (i, 0.975), xycoords=("data", "axes fraction"),
                ha="center", va="top", fontsize=7.8, color=C.INK2)
ax.set_yscale("log")
# headroom above the tallest column so the median labels have somewhere to sit
ax.set_ylim(top=float(A["n_tiles"].max()) * 4)
ax.set_xticks(range(len(CLS)), [C.CLASS_SHORT.get(c, c) for c in CLS], fontsize=8, rotation=28,
              ha="right")
ax.set_ylabel("tiles per slide")
ax.grid(axis="y", color=C.GRID, lw=0.8); ax.set_axisbelow(True)
fold = (np.median(A[A["ttt"] == "Premalignant - in situ"]["n_tiles"])
        / np.median(A[A["ttt"] == "Premalignant"]["n_tiles"]))
ax.set_xlim(-0.75, len(CLS) - 0.35)
ax.set_title(f"The quantity that does differ: tiles per slide\nslide-balanced by design, "
             f"but in situ carries {fold:.0f}× the tiles of premalignant  (medians labelled)",
             fontsize=10.5, color=C.INK, loc="left", linespacing=1.6)

d = metrics["arm_A_normal_vs_rest"]
fig.suptitle(f"min_tissue_proportion = 0.65 and tile yield in Arm A  ·  normal minus the rest = "
             f"{d['difference']:+.3f} retention "
             f"(95% CI {d['difference_ci'][0]:+.3f} to {d['difference_ci'][1]:+.3f})",
             fontsize=11.5, color=C.INK, y=1.04)
C.savefig(fig, "fig6_tile_yield.png")
