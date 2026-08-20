#!/usr/bin/env python3
"""Figures for the HTAN progression cohort.

Form follows the job of each result:
  fig1  the ladder is trend plus peak location, so small multiples, one panel per question,
        never overlaid, with the pre-registered prediction stated on each panel
  fig2  the MCQ failure is identity confusion plus answer bias, so a confusion matrix beside a
        distribution split by in-cohort versus off-cohort distractor
  fig3  normal versus primary is separation of two groups, so strips with AUC direct-labelled

Palette: dataviz reference instance. Sequential blue for magnitude; categorical slots 1 and 2
(blue #2a78d6, orange #eb6834) validated at CVD dE 24.7 protan, 33.6 normal vision.
"""
import csv, json, os, itertools
import numpy as np
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from scipy.stats import spearmanr

SURFACE, INK, INK2, INK3 = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8880"
BLUE, ORANGE = "#2a78d6", "#eb6834"
SEQ = LinearSegmentedColormap.from_list("blue_seq", [
    "#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
    "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"])
mpl.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "text.color": INK, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "axes.edgecolor": "#dcdbd6", "axes.linewidth": 0.8, "font.size": 9,
    "axes.spines.top": False, "axes.spines.right": False})

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
FIG = os.path.join(HERE, "figures")
CLASSES = ["Normal", "Atypia - hyperplasia", "Premalignant", "Premalignant - in situ", "Primary"]
SHORT = ["normal", "atypia /\nhyperplasia", "premalignant", "premalignant\nin situ", "primary\ninvasive"]
RANK = {"Normal": 0, "Normal adjacent": 0, "Atypia - hyperplasia": 1, "Premalignant": 2,
        "Premalignant - in situ": 3, "Primary": 4}
LADDER = [("negative_for_tumor", "falls"), ("benign", "falls"),
          ("hyperplasia_metaplasia", "peaks early"), ("dysplasia", "peaks mid"),
          ("atypia", "peaks early"), ("precancerous_lesion", "peaks mid"),
          ("carcinoma_in_situ", "peaks at in situ"), ("invasive_carcinoma", "rises"),
          ("malignancy", "rises")]
STAGE_LETTER = {"A": "Normal", "B": "Atypia -\nhyperplasia", "C": "Premalignant",
                "D": "Premalignant\nin situ", "E": "Primary", "F": "Metastatic",
                "G": "Post therapy\nneoadjuvant", "H": "Local\nrecurrence"}
OFF = {"F", "G", "H"}

lab = {r["sample"]: r for r in csv.DictReader(open(f"{REPO}/assets/samplesheet_progression_labels.csv"))}
rec = {r["sample"]: r for r in json.load(open(f"{HERE}/results_merged.json" if os.path.exists(f"{HERE}/results_merged.json") else f"{HERE}/results.json"))}
rows_all = [dict(lab[s], rec=rec[s]) for s in rec if s in lab and lab[s]["ttt"] in RANK]
def _arm(r): return "A" if r["arm"].startswith("A") else r["arm"]
# Arm A only: the pre-registered primary endpoint, one centre and one organ. Pooling in Arm C
# would mix in Duke breast DCIS, labelled Primary but morphologically in situ.
rows = [r for r in rows_all if _arm(r) == "A"]
def yn(r, q):
    v = r["rec"].get("yes_no", {}).get(q, {}).get("score")
    return float(v) if v is not None else np.nan

# ------------------------------------------------------------------ fig 1
fig, axes = plt.subplots(3, 3, figsize=(12.6, 9.4), constrained_layout=True, sharex=True, sharey=True)
rng = np.random.default_rng(0)
for ax, (q, predicted) in zip(axes.ravel(), LADDER):
    xs, ys = [], []
    for r in rows:
        v = yn(r, q)
        if not np.isnan(v):
            xs.append(RANK[r["ttt"]]); ys.append(v)
    xs, ys = np.array(xs), np.array(ys)
    ax.scatter(xs + (rng.random(len(xs)) - .5) * 0.34, ys, s=11, alpha=0.35, linewidths=0,
               color=BLUE, rasterized=True)
    means = [np.nanmean(ys[xs == k]) if (xs == k).any() else np.nan for k in range(5)]
    ax.plot(range(5), means, "-o", color=INK, lw=1.8, ms=6, mfc=SURFACE, mew=1.8, zorder=4)
    peak = int(np.nanargmax(means))
    ax.plot([peak], [means[peak]], "o", ms=13, mfc="none", mec=ORANGE, mew=2.4, zorder=5)
    rho = spearmanr(xs, ys).statistic
    ax.set_title(q.replace("_", " "), fontsize=10, color=INK, pad=6)
    # put the label in the corner the curve is not using, so it cannot collide with the peak ring
    top_free = means[0] < 0.5
    ax.text(0.03, 0.96 if top_free else 0.04, f"rho {rho:+.2f}\npredicted: {predicted}",
            transform=ax.transAxes, va="top" if top_free else "bottom", ha="left",
            fontsize=7.8, color=INK2)
    ax.set_ylim(-0.05, 1.05)
    ax.grid(axis="y", color="#ecebe6", lw=0.7); ax.set_axisbelow(True)
for ax in axes[-1]:
    ax.set_xticks(range(5), SHORT, fontsize=7.5)
for ax in axes[:, 0]:
    ax.set_ylabel("PRISM2 yes/no score")
fig.suptitle(f"Each question against the HTAN progression axis. Arm A only: "
             f"{len(rows)} slides, {len({r['pt'] for r in rows})} patients, HTAN BU lung\n"
             "black line = class mean, orange ring = observed peak, dots = individual slides",
             fontsize=12.5, color=INK)
fig.savefig(f"{FIG}/fig1_ladder.png", dpi=170); plt.close(fig)

# ------------------------------------------------------------------ fig 2
fig, axes = plt.subplots(1, 2, figsize=(13.2, 5.4), constrained_layout=True,
                         gridspec_kw={"width_ratios": [1.35, 1]})
order = ["Normal", "Atypia -\nhyperplasia", "Premalignant", "Premalignant\nin situ", "Primary",
         "Metastatic", "Post therapy\nneoadjuvant", "Local\nrecurrence"]
M = np.zeros((5, len(order)))
counts = {}
for r in rows:
    a = (r["rec"].get("multiple_choice", {}).get("progression_stage_mc", {}).get("answer") or "").strip()
    L = a[0].upper() if a and a[0].upper() in STAGE_LETTER else None
    if not L:
        continue
    p = STAGE_LETTER[L]
    counts[p] = counts.get(p, 0) + 1
    M[RANK[r["ttt"]], order.index(p)] += 1
ax = axes[0]
im = ax.imshow(M, cmap=SEQ, aspect="auto", vmin=0, vmax=M.max())
ax.set_xticks(range(len(order)), order, fontsize=7.5, rotation=35, ha="right")
ax.set_yticks(range(5), [c.replace(" - ", "\n") for c in CLASSES], fontsize=8)
for i in range(5):
    for j in range(len(order)):
        if M[i, j]:
            ax.text(j, i, int(M[i, j]), ha="center", va="center", fontsize=8,
                    color="#ffffff" if M[i, j] > M.max() * 0.55 else INK2)
# mark the diagonal (a correct answer) and the distractor block
for i in range(5):
    ax.add_patch(plt.Rectangle((i - .5, i - .5), 1, 1, fill=False, edgecolor=INK, lw=2.0))
ax.add_patch(plt.Rectangle((4.5, -.5), 3, 5, fill=False, edgecolor=ORANGE, lw=2.2, ls="--"))
ax.set_xlabel("PRISM2 answered"); ax.set_ylabel("HTAN recorded")
ax.set_title("Black outline = correct. Orange = options no slide here can be.",
             fontsize=9.5, color=INK)
ax = axes[1]
labels = [o for o in order if counts.get(o)]
vals = [counts[o] for o in labels]
cols = [ORANGE if o.replace("\n", " ") in ("Metastatic", "Post therapy neoadjuvant", "Local recurrence")
        else BLUE for o in labels]
y = np.arange(len(labels))
ax.barh(y, vals, color=cols, height=0.6)
for i, v in enumerate(vals):
    ax.text(v + 1, i, str(v), va="center", fontsize=9, color=INK2)
ax.set_yticks(y, labels, fontsize=8.5); ax.invert_yaxis()
ax.set_xlabel("slides answered this way (of 163)")
ax.grid(axis="x", color="#ecebe6", lw=0.7); ax.set_axisbelow(True)
h = [plt.Line2D([], [], marker="s", ls="", color=BLUE, ms=9, label="present in this cohort"),
     plt.Line2D([], [], marker="s", ls="", color=ORANGE, ms=9, label="off-cohort distractor")]
ax.legend(handles=h, frameon=False, fontsize=8.5, loc="lower right")
ax.set_title("43% of answers are one option; 25% are impossible here", fontsize=9.5, color=INK)
fig.suptitle(f"Forced choice using HTAN's own vocabulary, Arm A ({len(rows)} slides)",
             fontsize=12.5, color=INK)
fig.savefig(f"{FIG}/fig2_stage_mc.png", dpi=170); plt.close(fig)

# ------------------------------------------------------------------ fig 3
QS = ["malignancy", "carcinoma_in_situ", "invasive_carcinoma", "negative_for_tumor"]
def auc(pos, neg):
    w = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p, n in itertools.product(pos, neg))
    return w / (len(pos) * len(neg))
fig, axes = plt.subplots(1, 4, figsize=(12.4, 4.2), constrained_layout=True, sharey=True)
for ax, q in zip(axes, QS):
    pos = [yn(r, q) for r in rows if r["ttt"] == "Primary"]
    neg = [yn(r, q) for r in rows if r["ttt"] in ("Normal", "Normal adjacent")]
    pos = [v for v in pos if not np.isnan(v)]; neg = [v for v in neg if not np.isnan(v)]
    for i, (vals, col, name) in enumerate([(neg, ORANGE, "normal"), (pos, BLUE, "primary")]):
        ax.scatter(np.full(len(vals), i) + (rng.random(len(vals)) - .5) * 0.22, vals,
                   s=22, alpha=0.6, linewidths=0, color=col)
        ax.plot([i - .22, i + .22], [np.median(vals)] * 2, color=INK, lw=2.2, zorder=4)
    a = auc(pos, neg)
    ax.set_xticks([0, 1], [f"normal\n(n={len(neg)})", f"primary\n(n={len(pos)})"], fontsize=8.5)
    # a question expected to fall gives a small AUC in the primary>normal direction; show both
    # readings so 0.007 is not misread as failure when it is near-perfect separation
    extra = f"  ({1-a:.3f} inverted)" if a < 0.5 else ""
    ax.set_title(f"{q.replace('_',' ')}\nAUC {a:.3f}{extra}", fontsize=10, color=INK)
    ax.set_ylim(-0.05, 1.05)
    ax.grid(axis="y", color="#ecebe6", lw=0.7); ax.set_axisbelow(True)
axes[0].set_ylabel("PRISM2 yes/no score")
fig.suptitle("Normal versus invasive primary, Arm A only: one centre, one organ\n"
             "black bar = median; negative_for_tumor is expected to separate in the opposite direction",
             fontsize=12, color=INK)
fig.savefig(f"{FIG}/fig3_normal_vs_primary.png", dpi=170); plt.close(fig)
print("wrote", sorted(os.listdir(FIG)))


# ------------------------------------------------------------------ fig 4
QS4 = ["negative_for_tumor", "benign", "dysplasia", "carcinoma_in_situ", "invasive_carcinoma", "malignancy"]
arms = [("A", "Arm A\nBU lung, 6 classes"), ("B", "Arm B\nVanderbilt colon, 4 classes"),
        ("ALL", "pooled with Arm C\n(confounded)")]
fig, axes = plt.subplots(1, 2, figsize=(12.4, 4.6), constrained_layout=True)
for ax, (metric, title, ref) in zip(axes, [
        ("rho", "Spearman against the ordinal axis", 0.0),
        ("auc", "AUC, normal versus invasive primary", 0.5)]):
    for ai, (a, aname) in enumerate(arms):
        sub = rows_all if a == "ALL" else [r for r in rows_all if _arm(r) == a]
        vals = []
        for q in QS4:
            x = np.array([RANK[r["ttt"]] for r in sub]); y = np.array([yn(r, q) for r in sub])
            m = ~np.isnan(y)
            if metric == "rho":
                vals.append(spearmanr(x[m], y[m]).statistic)
            else:
                pos = [yn(r, q) for r in sub if r["ttt"] == "Primary"]
                neg = [yn(r, q) for r in sub if r["ttt"] in ("Normal", "Normal adjacent")]
                pos = [v for v in pos if not np.isnan(v)]; neg = [v for v in neg if not np.isnan(v)]
                vals.append(auc(pos, neg) if pos and neg else np.nan)
        off = (ai - 1) * 0.24
        ax.scatter(vals, np.arange(len(QS4)) + off, s=70, color=[BLUE, ORANGE, INK3][ai],
                   label=aname, zorder=3, linewidths=0)
    ax.axvline(ref, color=INK3, lw=1.4, ls="--")
    ax.set_yticks(range(len(QS4)), [q.replace("_", " ") for q in QS4], fontsize=9)
    ax.invert_yaxis(); ax.grid(axis="x", color="#ecebe6", lw=0.7); ax.set_axisbelow(True)
    ax.set_title(title, fontsize=10, color=INK)
    ax.set_xlim(-1.05, 1.05) if metric == "rho" else ax.set_xlim(-0.03, 1.03)
axes[1].legend(frameon=False, fontsize=8, loc="center left")
fig.suptitle("Arm A is the endpoint. Pooling Arm C lowers the AUC because its Primary class\n"
             "includes Duke breast DCIS: a primary specimen that is morphologically in situ.",
             fontsize=11.5, color=INK)
fig.savefig(f"{FIG}/fig4_by_arm.png", dpi=170); plt.close(fig)
print("wrote fig4")

# ------------------------------------------------------------------ fig 5
# Two jobs: show that the free-text site description is stable while the forced choice slips,
# and contrast forced-choice accuracy between an everyday vocabulary (organ) and HTAN's
# curation vocabulary (progression stage).
def _mcsite(r):
    a = (r["rec"].get("multiple_choice", {}).get("primary_site_mc", {}).get("answer") or "").strip()
    return {"B": "Lung", "J": "Esophagus"}.get(a[:1].upper(), "other")
def _desc(r):
    d = (r["rec"].get("open_ended", {}).get("specimen_site", {}).get("answer") or "").lower()
    for k, v in [("bronch", "bronchial wall"), ("lung", "lung / parenchyma"), ("pleur", "pleura"),
                 ("sinonasal", "sinonasal"), ("tonsil", "tonsil")]:
        if k in d:
            return v
    return "other / unstated"

fig, axes = plt.subplots(1, 2, figsize=(12.6, 4.6), constrained_layout=True,
                         gridspec_kw={"width_ratios": [1.25, 1]})
ax = axes[0]
cats = ["bronchial wall", "lung / parenchyma", "pleura", "sinonasal", "tonsil", "other / unstated"]
tab = {}
for r in rows:
    tab[(_desc(r), _mcsite(r))] = tab.get((_desc(r), _mcsite(r)), 0) + 1
y = np.arange(len(cats))
lung = [tab.get((c, "Lung"), 0) for c in cats]
eso = [tab.get((c, "Esophagus"), 0) for c in cats]
ax.barh(y, lung, height=0.58, color=BLUE, label="answered Lung (correct)")
ax.barh(y, eso, left=lung, height=0.58, color=ORANGE, label="answered Esophagus (wrong)")
for i, (l, e) in enumerate(zip(lung, eso)):
    if l: ax.text(l / 2, i, str(l), ha="center", va="center", fontsize=8.5, color="#ffffff")
    if e: ax.text(l + e + 1.2, i, str(e), va="center", fontsize=8.5, color=ORANGE)
ax.set_yticks(y, cats, fontsize=9); ax.invert_yaxis()
ax.set_xlabel("Arm A slides (HTAN records only 'Lung NOS', and no site at all)")
ax.legend(frameon=False, fontsize=8.5, loc="lower right")
ax.grid(axis="x", color="#ecebe6", lw=0.7); ax.set_axisbelow(True)
ax.set_title("The free text is stable; the forced choice slips.\n"
             "All 83 bronchial-wall descriptions are verbatim identical.", fontsize=9.5, color=INK)

ax = axes[1]
bars = [("primary site\nArm A", 74 / 81, 0.10), ("primary site\nArm C", 31 / 34, 0.10),
        ("progression stage\nall arms", 0.209, 0.125)]
x = np.arange(len(bars))
ax.bar(x, [b[1] for b in bars], width=0.5, color=[BLUE, BLUE, ORANGE])
for i, b in enumerate(bars):
    ax.plot([i - .3, i + .3], [b[2]] * 2, color=INK, lw=2, ls="--")
    ax.text(i, b[1] + 0.03, f"{b[1]:.2f}", ha="center", fontsize=10, color=INK2)
ax.set_xticks(x, [b[0] for b in bars], fontsize=9)
ax.set_ylim(0, 1.05); ax.set_ylabel("forced-choice accuracy")
ax.grid(axis="y", color="#ecebe6", lw=0.7); ax.set_axisbelow(True)
ax.text(0.02, 0.97, "dashed line = chance", transform=ax.transAxes, va="top", fontsize=8, color=INK2)
ax.set_title("Same format, same distractors, different vocabulary", fontsize=9.5, color=INK)
fig.suptitle("Organ names are everyday language; HTAN's progression terms are not",
             fontsize=12, color=INK)
fig.savefig(f"{FIG}/fig5_site_vs_stage.png", dpi=170); plt.close(fig)
print("wrote fig5")
