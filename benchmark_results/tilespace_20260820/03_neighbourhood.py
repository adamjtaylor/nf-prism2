#!/usr/bin/env python3
"""Figure 3: what is in a tile's neighbourhood, at k=30.

The pilot's headline number was "0.5% of a tile's 30 nearest neighbours come from another slide".
That was 8 slides from 6 organs and 5 atlases, so the number carried three explanations at once.
Here each arm is indexed on its own, so the composition is read within a fixed set of centres:

  Arm A  103 slides, ONE centre, ONE organ, six progression classes
  Arm B   26 slides, one centre, one organ
  Arm C   34 slides, three centres, three organs

Index = every tile in the arm, which is what a search tool would actually hold. Queries = 200
tiles per slide, slide-balanced, so a 36,399-tile slide does not supply a third of the queries.
The query's own tile is removed from its own result.

Chance is computed PER QUERY from the composition of that query's own candidate pool, because
each query has a different pool: a tile from a 36,399-tile slide has far fewer cross-slide
candidates available as a proportion than a tile from a 200-tile slide. A single cohort-wide
ceiling would be wrong for both.

CIs bootstrap over PATIENTS.
"""
import os, sys, time
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C
import knn
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import pandas as pd
C.rcparams()

STORE = os.path.join(C.REPO, "analysis", "data", "store")
K = 30
QUERIES_PER_SLIDE = 200

tiles = pd.read_parquet(os.path.join(STORE, "tiles.parquet"))
meta = C.load_meta()
F = np.load(os.path.join(STORE, "features.f32.npy"), mmap_mode="r")

ARMS = ["A", "B", "C"]
res, dists = {}, {}
for arm in ARMS:
    t0 = time.time()
    sub = tiles[tiles["arm"] == arm].reset_index(drop=True)
    rows = sub["row"].to_numpy()
    Xi = knn.l2(np.asarray(F[rows]))
    samp_i = sub["sample"].to_numpy()
    pt_i = sub["patient"].to_numpy()
    ttt_i = sub["ttt"].to_numpy()
    N = len(sub)

    rng = np.random.default_rng(42)
    qpos = []
    for s, g in sub.groupby("sample").indices.items():
        g = np.asarray(g)
        qpos.append(rng.choice(g, size=min(QUERIES_PER_SLIDE, len(g)), replace=False))
    qpos = np.sort(np.concatenate(qpos))
    Xq = Xi[qpos]
    print(f"arm {arm}: index {N:,} tiles / {sub['sample'].nunique()} slides, {len(qpos):,} queries")

    # policy 'none': only the query's own row is removed, so k+1 are taken and the self hit dropped
    blocks = [(np.arange(len(qpos)), None)]
    I, _ = knn.topk_blocks(Xq, Xi, K + 1, blocks)
    self_col = I == qpos[:, None]
    keep = np.where(~self_col, np.arange(K + 1)[None, :], K + 2)
    ordk = np.sort(keep, axis=1)[:, :K]
    I = np.take_along_axis(I, ordk, axis=1)

    q_samp, q_pt, q_ttt = samp_i[qpos], pt_i[qpos], ttt_i[qpos]
    diff_slide = (samp_i[I] != q_samp[:, None]).mean(1)
    diff_pt = (pt_i[I] != q_pt[:, None]).mean(1)
    diff_class = (ttt_i[I] != q_ttt[:, None]).mean(1)

    # per-query chance: proportion of the candidate pool (all index tiles bar the query itself)
    # that differs from the query on each label
    n_by_s = pd.Series(samp_i).value_counts().to_dict()
    n_by_p = pd.Series(pt_i).value_counts().to_dict()
    n_by_c = pd.Series(ttt_i).value_counts().to_dict()
    ch_slide = np.array([1 - (n_by_s[s] - 1) / (N - 1) for s in q_samp])
    ch_pt = np.array([1 - (n_by_p[p] - 1) / (N - 1) for p in q_pt])
    ch_class = np.array([1 - (n_by_c[c] - 1) / (N - 1) for c in q_ttt])

    r = {}
    for name, obs, ch in [("different slide", diff_slide, ch_slide),
                          ("different patient", diff_pt, ch_pt),
                          ("different class", diff_class, ch_class)]:
        m, lo, hi = C.patient_bootstrap(q_pt, obs, n_boot=1000, seed=1)
        r[name] = dict(observed=round(m, 4), ci=[round(lo, 4), round(hi, 4)],
                       chance=round(float(ch.mean()), 4),
                       ratio_to_chance=round(float(m / max(ch.mean(), 1e-9)), 4))
    r["n_index_tiles"] = int(N); r["n_queries"] = int(len(qpos))
    r["n_slides"] = int(sub["sample"].nunique()); r["n_patients"] = int(sub["patient"].nunique())
    r["n_centres"] = int(len({meta[s]["centre"] for s in set(samp_i)}))
    r["pct_queries_with_no_cross_slide_neighbour"] = round(100 * float((diff_slide == 0).mean()), 1)
    r["pct_queries_with_no_cross_patient_neighbour"] = round(100 * float((diff_pt == 0).mean()), 1)
    r["median_cross_slide_neighbours_of_30"] = float(np.median(diff_slide * K))
    res[arm] = r
    dists[arm] = diff_slide
    print("  ", {k: v for k, v in r.items() if isinstance(v, dict)})
    print(f"   {time.time()-t0:.0f}s")

C.dump(dict(k=K, queries_per_slide=QUERIES_PER_SLIDE, space="raw Virchow2 1280-d, cosine, exact",
            note="chance is per query from that query's own candidate pool; CI bootstraps patients",
            by_arm=res), "neighbourhood_metrics.json")

# ------------------------------------------------------------------ figure
LABELS = ["different slide", "different patient", "different class"]
NA = {("C", "different class"): "Primary only:\nnot applicable"}
fig = plt.figure(figsize=(13.0, 4.0))
gs = fig.add_gridspec(1, 4, width_ratios=[1, 1, 1, 1.15], wspace=0.44)

for j, lab in enumerate(LABELS):
    ax = fig.add_subplot(gs[0, j])
    y = np.arange(len(ARMS))
    for i, a in enumerate(ARMS):
        if (a, lab) in NA:
            ax.text(0.03, i, NA[(a, lab)], va="center", fontsize=7.6, color=C.INK3,
                    linespacing=1.4)
            continue
        d = res[a][lab]
        o, lo, hi = d["observed"], d["ci"][0], d["ci"][1]
        ax.barh(i, o, height=0.42, color=C.CAT[0], zorder=3)
        ax.errorbar(o, i, xerr=[[o - lo], [hi - o]], fmt="none", ecolor=C.INK2,
                    elinewidth=1.2, capsize=3, zorder=4)
        ax.plot([d["chance"]] * 2, [i - 0.24, i + 0.24], color=C.CAT[1], lw=2.6, zorder=5)
        ax.text(hi + 0.035, i, f"{o:.3f}", va="center", fontsize=8.5, color=C.INK2)
    ax.set_yticks(y, [f"Arm {a}" for a in ARMS], fontsize=9)
    ax.set_ylim(len(ARMS) - 0.45, -0.55)
    ax.set_xlim(0, 1.08)
    ax.set_xlabel(f"fraction of the {K} nearest neighbours")
    ax.grid(axis="x", color=C.GRID, lw=0.8); ax.set_axisbelow(True)
    ax.set_title(lab, fontsize=10.5, color=C.INK, loc="left")
    if j == 0:
        ax.legend(handles=[Line2D([], [], color=C.CAT[0], lw=7, label="observed"),
                           Line2D([], [], color=C.CAT[1], lw=2.6, label="chance, computed per query")],
                  fontsize=7.8, loc="upper left", bbox_to_anchor=(0.0, -0.30), ncol=2,
                  labelcolor=C.INK2, handletextpad=0.4, columnspacing=1.2)

ax = fig.add_subplot(gs[0, 3])
bins = np.arange(0, K + 2) - 0.5
for i, a in enumerate(ARMS):
    h, _ = np.histogram(dists[a] * K, bins=bins)
    ax.step(np.arange(0, K + 1), 100 * h / h.sum(), where="mid", color=C.CAT[i], lw=1.8,
            label=f"Arm {a}")
ax.set_yscale("log")
ax.set_xlabel(f"cross-slide neighbours among the top {K}")
ax.set_ylabel("% of query tiles")
ax.set_xlim(-0.5, K + 0.5)
ax.grid(axis="y", color=C.GRID, lw=0.8); ax.set_axisbelow(True)
ax.legend(fontsize=7.8, labelcolor=C.INK2, loc="upper center")
ax.set_title("Distribution, not just the mean\n" + "   ".join(
    f"Arm {a}: {res[a]['pct_queries_with_no_cross_slide_neighbour']:.0f}% have none" for a in ARMS),
    fontsize=10.5, color=C.INK, loc="left", linespacing=1.6)

fig.suptitle(f"Neighbourhood composition at k={K}, exact cosine in the raw 1280-d space, "
             f"each arm indexed separately", fontsize=12, color=C.INK, y=1.06)
C.savefig(fig, "fig3_neighbourhood.png")
