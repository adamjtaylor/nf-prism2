#!/usr/bin/env python3
"""Figure 7: slide-level search, and whether it should be the primary index instead of tiles.

Three slide-level representations of the same 163 slides:

  PRISM2 base        2560-d, the Perceiver's slide embedding
  PRISM2 diagnostic  3072-d, the head trained for report generation
  mean-pooled tiles  1280-d, the arithmetic mean of the slide's Virchow2 tile vectors

Four things are measured, because a retrieval score alone would hide the failure mode the pilot
found:

  1. DYNAMIC RANGE. Cosine over all cross-patient slide pairs. A representation whose pairs all
     sit between 0.82 and 0.95 has almost no room to rank, whatever its precision looks like.
  2. p@5 for same progression class, different patient, WITHIN ARM A, so centre and organ are
     constant. Directly comparable to the tile-level number in figure 4.
  3. p@5 for same organ, different patient, across all arms. The other question a search tool
     gets asked, and the one Arm A cannot pose because it is entirely lung.
  4. RANK AGREEMENT between representations, on the same pairs. If base and diagnostic disagree
     about which slides are similar, at most one of them is describing morphology.

Chance is computed per query from that query's own candidate pool. CIs bootstrap over patients.
"""
import os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from scipy.stats import spearmanr
import pandas as pd
C.rcparams()

STORE = os.path.join(C.REPO, "analysis", "data", "store")
REPS = ["PRISM2 base\n2560-d", "PRISM2 diagnostic\n3072-d", "mean-pooled tiles\n1280-d"]
KS = [1, 5, 10]

tiles = pd.read_parquet(os.path.join(STORE, "tiles.parquet"))
meta = C.load_meta()
F = np.load(os.path.join(STORE, "features.f32.npy"), mmap_mode="r")
grp = tiles.groupby("sample")["row"]
lo_of, n_of = grp.min().to_dict(), grp.size().to_dict()

samples = sorted(meta)
V = {}
base, diag, mp = [], [], []
for s in samples:
    z = np.load(os.path.join(C.SLIDE_EMB, s, f"{s}.embeddings.npz"))
    base.append(np.asarray(z["base"], dtype=np.float32)[0])
    diag.append(np.asarray(z["diagnostic"], dtype=np.float32)[0])
    lo, n = int(lo_of[s]), int(n_of[s])
    mp.append(np.asarray(F[lo:lo + n]).mean(0))
V[REPS[0]] = C.l2(np.vstack(base))
V[REPS[1]] = C.l2(np.vstack(diag))
V[REPS[2]] = C.l2(np.vstack(mp))
arm = np.array([meta[s]["arm"] for s in samples])
pt = np.array([meta[s]["patient"] for s in samples])
ttt = np.array([meta[s]["ttt"] for s in samples])
organ = np.array([meta[s]["organ_resolved"] for s in samples])
print(f"{len(samples)} slides, {len(set(pt))} patients")

# ------------------------------------------------------------------ 1. dynamic range
pairs = np.triu_indices(len(samples), 1)
cross = pt[pairs[0]] != pt[pairs[1]]
COS = {r: (V[r] @ V[r].T)[pairs][cross] for r in REPS}
rng_stats = {r: dict(min=round(float(v.min()), 4), p5=round(float(np.percentile(v, 5)), 4),
                     median=round(float(np.median(v)), 4),
                     p95=round(float(np.percentile(v, 95)), 4), max=round(float(v.max()), 4),
                     span_5_95=round(float(np.percentile(v, 95) - np.percentile(v, 5)), 4))
             for r, v in COS.items()}
for r in REPS:
    print(f"  {r.splitlines()[0]:20s} cosine {rng_stats[r]['min']:.3f} to {rng_stats[r]['max']:.3f}"
          f"  5-95 span {rng_stats[r]['span_5_95']:.3f}")

# ------------------------------------------------------------------ 2 and 3. retrieval
def retrieve(rep, idx, label_arr, ks=KS):
    """p@k over the sub-cohort `idx`, relevance = same label AND different patient."""
    X = V[rep][idx]
    S = X @ X.T
    lab, p = label_arr[idx], pt[idx]
    n = len(idx)
    out = {}
    per_k = {k: [] for k in ks}
    chance = []
    for i in range(n):
        mask = p != p[i]
        if mask.sum() == 0:
            continue
        order = np.argsort(-np.where(mask, S[i], -np.inf))
        rel = (lab[order] == lab[i]) & mask[order]
        for k in ks:
            per_k[k].append(rel[:k].mean())
        chance.append(((lab == lab[i]) & mask).sum() / mask.sum())
    keep = np.array([i for i in range(n) if (p != p[i]).sum() > 0])
    for k in ks:
        m, lo, hi = C.patient_bootstrap(p[keep], np.array(per_k[k]), n_boot=2000, seed=8)
        out[str(k)] = dict(precision=round(m, 4), ci=[round(lo, 4), round(hi, 4)],
                           chance=round(float(np.mean(chance)), 4),
                           lift=round(m / max(float(np.mean(chance)), 1e-9), 3))
    return out

TASKS = {
    "same progression class, Arm A": (np.where(arm == "A")[0], ttt),
    "same organ, all arms": (np.arange(len(samples)), organ),
}
ret = {}
for task, (idx, lab) in TASKS.items():
    ret[task] = {r: retrieve(r, idx, lab) for r in REPS}
    for r in REPS:
        d = ret[task][r]["5"]
        print(f"  [{task}] {r.splitlines()[0]:20s} p@5 {d['precision']:.3f} "
              f"(chance {d['chance']:.3f}, lift {d['lift']:.2f})")

# ------------------------------------------------------------------ 4. rank agreement
agree = {}
for a, b in [(REPS[0], REPS[1]), (REPS[0], REPS[2]), (REPS[1], REPS[2])]:
    rho = spearmanr(COS[a], COS[b]).statistic
    agree[f"{a.splitlines()[0]} vs {b.splitlines()[0]}"] = round(float(rho), 4)
print("  rank agreement:", agree)

C.dump(dict(n_slides=len(samples), n_patients=int(len(set(pt))),
            n_cross_patient_pairs=int(cross.sum()),
            cosine_range=rng_stats, retrieval=ret, pair_rank_agreement_spearman=agree,
            tile_level_comparison="figure 4, Arm A, exclude same patient: p@10 0.381, "
                                  "chance 0.153, lift 2.50"),
        "slide_level_metrics.json")

# ------------------------------------------------------------------ figure
fig = plt.figure(figsize=(15.0, 4.7))
gs = fig.add_gridspec(1, 4, width_ratios=[1.3, 1, 1, 0.95], wspace=0.50)
SHORT = [r.splitlines()[0] for r in REPS]

ax = fig.add_subplot(gs[0, 0])
rng = np.random.default_rng(5)
for i, r in enumerate(REPS):
    v = COS[r]
    sub = v[rng.choice(len(v), size=min(3000, len(v)), replace=False)]
    ax.scatter(sub, np.full(len(sub), i) + rng.normal(0, 0.11, len(sub)), s=3, color=C.CAT[i],
               alpha=0.16, linewidths=0, rasterized=True)
    p5, p95 = np.percentile(v, [5, 95])
    ax.plot([p5, p95], [i - 0.32] * 2, color=C.INK, lw=2.4, solid_capstyle="butt", zorder=4)
    ax.plot([np.median(v)], [i - 0.32], marker="|", ms=11, color=C.INK, zorder=5)
    ax.text((p5 + p95) / 2, i - 0.44, f"5–95% span {p95 - p5:.2f}", ha="center", fontsize=7.4,
            color=C.INK2)
ax.set_yticks(range(len(REPS)), SHORT, fontsize=8.5)
ax.set_ylim(len(REPS) - 0.4, -0.62)
ax.set_xlim(0, 1.02); ax.set_xlabel("cosine between cross-patient slide pairs")
ax.grid(axis="x", color=C.GRID, lw=0.8); ax.set_axisbelow(True)
ax.set_title("Dynamic range of the cosine\nhow much room each space leaves to rank in",
             fontsize=9.6, color=C.INK, loc="left", linespacing=1.6)

for j, (task, (idx, _lab)) in enumerate(TASKS.items()):
    ax = fig.add_subplot(gs[0, 1 + j])
    y = np.arange(len(REPS))
    d = ret[task]
    v = [d[r]["5"]["precision"] for r in REPS]
    lo = [d[r]["5"]["ci"][0] for r in REPS]; hi = [d[r]["5"]["ci"][1] for r in REPS]
    ax.barh(y, v, height=0.42, color=C.CAT[0], zorder=3)
    ax.errorbar(v, y, xerr=[np.array(v) - np.array(lo), np.array(hi) - np.array(v)], fmt="none",
                ecolor=C.INK2, elinewidth=1.2, capsize=3, zorder=4)
    for i, r in enumerate(REPS):
        ax.plot([d[r]["5"]["chance"]] * 2, [i - 0.23, i + 0.23], color=C.CAT[1], lw=2.6, zorder=5)
        ax.text(1.04, i, f"{v[i]:.2f}   ×{d[r]['5']['lift']:.1f}", va="center", fontsize=8.2,
                color=C.INK2)
    # the second retrieval panel repeats the same three rows, so its labels are dropped rather
    # than printed twice a centimetre apart
    ax.set_yticks(y, SHORT if j == 0 else [""] * len(REPS), fontsize=8.5)
    ax.set_ylim(len(REPS) - 0.45, -0.55)
    ax.set_xlim(0, 1.42); ax.set_xticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.set_xlabel("precision@5, different patient")
    ax.grid(axis="x", color=C.GRID, lw=0.8); ax.set_axisbelow(True)
    ax.set_title(f"{task}\n{len(idx)} slides · ×N = lift over chance (orange)",
                 fontsize=9.6, color=C.INK, loc="left", linespacing=1.6)

ax = fig.add_subplot(gs[0, 3])
a, b = REPS[0], REPS[1]
sub = rng.choice(len(COS[a]), size=4000, replace=False)
ax.scatter(COS[a][sub], COS[b][sub], s=4, color=C.CAT[2], alpha=0.28, linewidths=0, rasterized=True)
ax.set_xlabel("cosine, PRISM2 base"); ax.set_ylabel("cosine, PRISM2 diagnostic")
ax.set_xlim(0, 1.02); ax.set_ylim(0, 1.02); ax.set_aspect("equal")
ax.grid(color=C.GRID, lw=0.8); ax.set_axisbelow(True)
rho = agree[f"{a.splitlines()[0]} vs {b.splitlines()[0]}"]
rho2 = agree[f"{a.splitlines()[0]} vs {REPS[2].splitlines()[0]}"]
ax.set_title(f"Do they agree on which pairs are close?\nbase–diagnostic ρ = {rho:.2f}, "
             f"base–mean-pooled ρ = {rho2:.2f}",
             fontsize=9.6, color=C.INK, loc="left", linespacing=1.6)

fig.suptitle("Slide-level search: three representations of the same 163 slides, "
             "all cross-patient", fontsize=12, color=C.INK, y=1.06)
C.savefig(fig, "fig7_slide_level.png")
