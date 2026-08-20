#!/usr/bin/env python3
"""Figure 4: retrieval scored the way a search tool would have to score it.

A pathologist using an image-search tool wants tiles that look like their query and come from
SOMEONE ELSE. A hit from the query's own slide is worthless and a hit from the same patient's
other block is nearly worthless, so relevance here is:

    same progression class AND a different patient

and the three policies differ in what the index is allowed to return:

    none                 the whole arm, minus the query tile itself   (the naive index)
    exclude same slide   the query's slide is removed from the pool
    exclude same patient the query's patient is removed entirely       (the honest test)

Under 'none' a same-slide neighbour cannot be a hit, because it is the same patient. That is the
point of including the policy: it measures how much of the result page a naive index wastes on the
query's own slide.

Chance is recomputed per query AND per policy, as the fraction of that query's own remaining
candidate pool which is same-class-different-patient. The pools differ by policy, so a single
chance line would flatter one policy over another.

Arms are never pooled. Arm C is excluded from this figure: it is Primary-only, so
"same class" is satisfied by every tile in the arm and the measure is degenerate. Arm C is also
one centre per organ, so "same organ, different centre" cannot be asked of it either.
"""
import os, sys, time
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C
import knn
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
C.rcparams()

STORE = os.path.join(C.REPO, "analysis", "data", "store")
KS = [1, 5, 10, 20, 50]
KMAX = max(KS)
QUERIES_PER_SLIDE = 120
POLICIES = ["none", "exclude same slide", "exclude same patient"]

tiles = pd.read_parquet(os.path.join(STORE, "tiles.parquet"))
meta = C.load_meta()
F = np.load(os.path.join(STORE, "features.f32.npy"), mmap_mode="r")

out = {}
for arm in ["A", "B"]:
    sub = tiles[tiles["arm"] == arm].reset_index(drop=True)
    Xi = knn.l2(np.asarray(F[sub["row"].to_numpy()]))
    samp_i, pt_i, ttt_i = (sub[c].to_numpy() for c in ("sample", "patient", "ttt"))
    N = len(sub)
    rng = np.random.default_rng(42)
    qpos = np.sort(np.concatenate([
        rng.choice(np.asarray(g), size=min(QUERIES_PER_SLIDE, len(g)), replace=False)
        for _, g in sub.groupby("sample").indices.items()]))
    Xq = Xi[qpos]
    q_samp, q_pt, q_ttt = samp_i[qpos], pt_i[qpos], ttt_i[qpos]
    print(f"arm {arm}: {N:,} index tiles, {len(qpos):,} queries, {sub['sample'].nunique()} slides")
    n_by_s = pd.Series(samp_i).value_counts().to_dict()
    n_by_p = pd.Series(pt_i).value_counts().to_dict()
    n_by_c = pd.Series(ttt_i).value_counts().to_dict()
    n_by_cp = sub.groupby(["ttt", "patient"]).size().to_dict()

    arm_out = {}
    for policy in POLICIES:
        t0 = time.time()
        if policy == "none":
            blocks = [(np.arange(len(qpos)), None)]
        elif policy == "exclude same slide":
            blocks = knn.blocks_by_group(q_samp, samp_i, "group")
        else:
            blocks = knn.blocks_by_group(q_pt, pt_i, "group")
        I, _ = knn.topk_blocks(Xq, Xi, KMAX + 1, blocks)
        if policy == "none":  # drop the self hit, keeping order
            selfc = I == qpos[:, None]
            rank = np.where(~selfc, np.arange(KMAX + 1)[None, :], KMAX + 2)
            I = np.take_along_axis(I, np.sort(rank, axis=1)[:, :KMAX], axis=1)
        else:
            I = I[:, :KMAX]
        rel = (ttt_i[I] == q_ttt[:, None]) & (pt_i[I] != q_pt[:, None])

        # Per-query chance from the surviving pool, in closed form rather than by masking.
        # The relevant set is {ttt == c AND patient != p}. Every tile of the query's own slide has
        # patient p, so slide exclusion removes nothing from the numerator; only the denominator
        # changes between policies. That makes all three chances exact and O(queries).
        rel_pool = np.array([n_by_c[c] - n_by_cp.get((c, p), 0) for c, p in zip(q_ttt, q_pt)],
                            dtype=float)
        if policy == "none":
            pool_excl = np.full(len(qpos), N - 1.0)
        elif policy == "exclude same slide":
            pool_excl = np.array([N - n_by_s[s] for s in q_samp], dtype=float)
        else:
            pool_excl = np.array([N - n_by_p[p] for p in q_pt], dtype=float)
        chance = rel_pool / np.maximum(pool_excl, 1)

        pk = {}
        for k in KS:
            p = rel[:, :k].mean(1)
            m, lo, hi = C.patient_bootstrap(q_pt, p, n_boot=1000, seed=2)
            pk[str(k)] = dict(precision=round(m, 4), ci=[round(lo, 4), round(hi, 4)],
                              chance=round(float(chance.mean()), 4),
                              lift=round(float(m / max(chance.mean(), 1e-9)), 3))
        # p@10 by query class, for the composition of the result
        by_class = {}
        for c in sorted(set(q_ttt)):
            m = q_ttt == c
            mm, lo, hi = C.patient_bootstrap(q_pt[m], rel[m, :10].mean(1), n_boot=500, seed=3)
            cch = float(chance[m].mean())
            by_class[c] = dict(n_queries=int(m.sum()), precision_at_10=round(mm, 4),
                               ci=[round(lo, 4), round(hi, 4)], chance=round(cch, 4),
                               lift=round(mm / max(cch, 1e-9), 2),
                               index_tile_share=round(float((ttt_i == c).mean()), 4))
        # how much of the page a naive index spends on the query's own slide / patient
        arm_out[policy] = dict(
            precision_at_k=pk, by_query_class=by_class,
            frac_top10_same_slide=round(float((samp_i[I[:, :10]] == q_samp[:, None]).mean()), 4),
            frac_top10_same_patient=round(float((pt_i[I[:, :10]] == q_pt[:, None]).mean()), 4),
            mean_pool_size=int(pool_excl.mean()))
        print(f"  {policy:22s} p@10 {pk['10']['precision']:.3f} "
              f"(chance {pk['10']['chance']:.3f}, lift {pk['10']['lift']:.2f})  "
              f"own-slide share of top10 {arm_out[policy]['frac_top10_same_slide']:.3f}  "
              f"{time.time()-t0:.0f}s")
    out[arm] = dict(n_index_tiles=int(N), n_queries=int(len(qpos)),
                    n_slides=int(sub["sample"].nunique()),
                    n_patients=int(sub["patient"].nunique()), policies=arm_out)

C.dump(dict(ks=KS, queries_per_slide=QUERIES_PER_SLIDE,
            relevance="same TumorTissueType AND different patient",
            space="raw Virchow2 1280-d, cosine, exact",
            excluded_arm_C="Primary-only, so same-class is degenerate; also one centre per organ",
            by_arm=out), "retrieval_metrics.json")

# ------------------------------------------------------------------ figure
# Arm B has exactly one slide per patient, so 'exclude same slide' and 'exclude same patient' are
# the same policy there; the two lines coincide and that is stated rather than left to be noticed.
SAME_POLICY = {a: (out[a]["n_slides"] == out[a]["n_patients"]) for a in out}
fig = plt.figure(figsize=(14.0, 4.7))
gs = fig.add_gridspec(1, 3, width_ratios=[1, 1, 1.35], wspace=0.50)
for j, arm in enumerate(["A", "B"]):
    ax = fig.add_subplot(gs[0, j])
    for i, policy in enumerate(POLICIES):
        pk = out[arm]["policies"][policy]["precision_at_k"]
        v = [pk[str(k)]["precision"] for k in KS]
        lo = [pk[str(k)]["ci"][0] for k in KS]
        hi = [pk[str(k)]["ci"][1] for k in KS]
        dash = (0, (1, 1.6)) if (SAME_POLICY[arm] and policy == "exclude same patient") else "-"
        ax.plot(KS, v, ls=dash, marker="o", color=C.CAT[i], lw=2, ms=6.5, label=policy, zorder=3 + i)
        ax.fill_between(KS, lo, hi, color=C.CAT[i], alpha=0.14, lw=0)
        ax.plot(KS, [pk[str(k)]["chance"] for k in KS], ":", color=C.CAT[i], lw=1.4, alpha=0.9)
        ax.annotate(f"×{pk['10']['lift']:.2f}", (10, pk["10"]["precision"]),
                    textcoords="offset points", xytext=(4, 7 if i else -13), fontsize=7.6,
                    color=C.CAT[i])
    ax.set_xscale("log"); ax.set_xticks(KS, [str(k) for k in KS])
    ax.set_ylim(0, 1.0); ax.set_xlabel("k")
    ax.set_ylabel("precision@k, same class & different patient")
    ax.grid(axis="y", color=C.GRID, lw=0.8); ax.set_axisbelow(True)
    ax.set_title(f"Arm {arm} · {out[arm]['n_slides']} slides, {out[arm]['n_patients']} patients\n"
                 f"{out[arm]['n_index_tiles']:,} index tiles",
                 fontsize=9.5, color=C.INK, loc="left", linespacing=1.6)
    if SAME_POLICY[arm]:
        ax.text(0.03, 0.955, "one slide per patient here,\nso the last two policies coincide",
                transform=ax.transAxes, fontsize=7.8, color=C.INK3, va="top", linespacing=1.5)
    if j == 0:
        ax.legend(fontsize=7.8, loc="upper left", labelcolor=C.INK2, title="index policy",
                  title_fontsize=7.8)

# Precision by class is dominated by how many tiles each class contributes to the index, which
# varies more than tenfold, so the lift over that class's own chance is direct-labelled.
ax = fig.add_subplot(gs[0, 2])
pol = "exclude same patient"
d = out["A"]["policies"][pol]["by_query_class"]
cls = [c for c in C.CLASS_ORDER if c in d]
y = np.arange(len(cls))
v = [d[c]["precision_at_10"] for c in cls]
lo = [d[c]["ci"][0] for c in cls]; hi = [d[c]["ci"][1] for c in cls]
ax.barh(y, v, height=0.5, color=C.CAT[0], zorder=3)
ax.errorbar(v, y, xerr=[np.array(v) - np.array(lo), np.array(hi) - np.array(v)], fmt="none",
            ecolor=C.INK2, elinewidth=1.2, capsize=3, zorder=4)
for i, c in enumerate(cls):
    ax.plot([d[c]["chance"]] * 2, [i - 0.27, i + 0.27], color=C.CAT[1], lw=2.6, zorder=5)
    ax.text(hi[i] + 0.02, i, f"{v[i]:.2f}   ×{d[c]['lift']:.1f}", va="center", fontsize=8.2,
            color=C.INK2)
ax.set_yticks(y, [f"{C.CLASS_SHORT.get(c, c)}\n{d[c]['index_tile_share']*100:.0f}% of index tiles"
                  for c in cls], fontsize=8)
ax.invert_yaxis(); ax.set_xlim(0, 1.0)
ax.set_xlabel("precision@10")
ax.grid(axis="x", color=C.GRID, lw=0.8); ax.set_axisbelow(True)
ax.set_title("Arm A by query class\nwhole patient excluded; ×N = lift over that class's chance",
             fontsize=9.5, color=C.INK, loc="left", linespacing=1.6)

fig.suptitle("Cross-patient tile retrieval: precision@k for same progression class, "
             "different patient", fontsize=12, color=C.INK, y=1.13)
fig.text(0.5, 1.055, "solid = observed   ·   band = 95% CI bootstrapped over patients   ·   "
                     "dotted = chance for that policy's own candidate pool",
         ha="center", fontsize=8.4, color=C.INK3)
C.savefig(fig, "fig4_retrieval.png")
