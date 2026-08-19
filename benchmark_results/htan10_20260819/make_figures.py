#!/usr/bin/env python3
"""Figures for the HTAN 10-slide pilot analysis.

Palette: dataviz reference instance. Sequential = single blue hue (magnitude);
categorical slots 1 and 2 (blue, orange) for the expected-label contrast, validated with
scripts/validate_palette.js (all checks pass, CVD dE 24.7 protan).
"""
import csv, glob, json, os
import numpy as np
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

SURFACE, INK, INK2, INK3 = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8880"
BLUE, ORANGE = "#2a78d6", "#eb6834"
# sequential blue ramp, steps 100 -> 700 from references/palette.md
BLUE_RAMP = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
             "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]
SEQ = LinearSegmentedColormap.from_list("blue_seq", BLUE_RAMP)

mpl.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "text.color": INK, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "axes.edgecolor": "#dcdbd6", "axes.linewidth": 0.8, "font.size": 9,
    "axes.spines.top": False, "axes.spines.right": False,
})

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
FIG = os.path.join(HERE, "figures")

records = {r["sample"]: r for r in json.load(open(os.path.join(HERE, "results.json")))}
gt = list(csv.DictReader(open(os.path.join(REPO, "assets/htan10_ground_truth.csv"))))
SHORT = {  # organ-first labels so grouping is visible on the axes
    "BU_lung_adenocarcinoma_svs": "lung adeno (BU, svs)",
    "BU_lung_squamous_ndpi": "lung squam (BU, ndpi)",
    "SRRS_lung_squamous_svs": "lung squam (SRRS, svs)",
    "DUKE_breast_dcis_svs": "breast DCIS (Duke, svs)",
    "HMS_colorectal_adenocarcinoma_ometiff": "colon adeno (HMS, ome)",
    "HMS_ovarian_hgserous_svs": "tube HGSC (HMS, svs)",
    "HMS_skin_melanoma_ometiff": "skin melanoma (HMS, ome)",
    "WUSTL_pancreas_pbcarcinoma_svs": "pancreas carc (WUSTL, svs)",
}
ORDER = ["BU_lung_adenocarcinoma_svs", "BU_lung_squamous_ndpi", "SRRS_lung_squamous_svs",
         "DUKE_breast_dcis_svs", "HMS_colorectal_adenocarcinoma_ometiff",
         "HMS_ovarian_hgserous_svs", "HMS_skin_melanoma_ometiff",
         "WUSTL_pancreas_pbcarcinoma_svs"]
labels = [SHORT[s] for s in ORDER]

# ---------------------------------------------------------------- figure 1
def cosine_matrix(key):
    V = []
    for s in ORDER:
        f = glob.glob(os.path.join(HERE, "embeddings", f"{s}.embeddings.npz"))[0]
        v = np.load(f)[key].astype(np.float64).reshape(-1)
        V.append(v / np.linalg.norm(v))
    V = np.array(V)
    return V @ V.T

fig, axes = plt.subplots(1, 2, figsize=(11.6, 5.1), constrained_layout=True)
for ax, (key, name, dim) in zip(axes, [("base", "Base embedding", 2560),
                                       ("diagnostic", "Diagnostic embedding", 3072)]):
    M = cosine_matrix(key)
    off = M[~np.eye(8, dtype=bool)]
    im = ax.imshow(M, cmap=SEQ, vmin=off.min(), vmax=1.0)
    ax.set_xticks(range(8), labels, rotation=45, ha="right", fontsize=8)
    ax.set_yticks(range(8), labels, fontsize=8)
    for i in range(8):
        for j in range(8):
            v = M[i, j]
            # label only off-diagonal cells; text ink, not series colour
            if i != j:
                shade = "#ffffff" if v > off.min() + 0.62 * (1 - off.min()) else INK2
                ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=7, color=shade)
    # the colour scales differ per panel by necessity; state each range so the
    # compression of the diagnostic space is readable as a number, not just a hue
    ax.set_title(f"{name}  ({dim}-d)\noff-diagonal range {off.min():.2f} to {off.max():.2f}",
                 fontsize=10, color=INK, pad=8)
    cb = fig.colorbar(im, ax=ax, fraction=0.046, shrink=0.85)
    cb.set_label("cosine similarity", fontsize=8)
    cb.outline.set_visible(False)
fig.suptitle("PRISM2 slide embeddings: do slides group by organ, or by scanner platform?",
             fontsize=12, color=INK)
fig.savefig(os.path.join(FIG, "fig1_embedding_similarity.png"), dpi=200)
plt.close(fig)

# ---------------------------------------------------------------- figure 2
QS = ["invasive_carcinoma", "carcinoma_in_situ", "adenocarcinoma", "squamous_cell_carcinoma",
      "melanoma", "lymphovascular_invasion", "perineural_invasion", "high_grade", "necrosis"]
S = np.full((len(ORDER), len(QS)), np.nan)
E = {}
for i, s in enumerate(ORDER):
    for j, q in enumerate(QS):
        v = records[s].get("yes_no", {}).get(q, {}).get("score")
        if v is not None:
            S[i, j] = v
        e = next((g["expected"] for g in gt if g["sample"] == s and g["question_id"] == q), None)
        if e:
            E[(i, j)] = e

fig, ax = plt.subplots(figsize=(9.4, 4.6), constrained_layout=True)
im = ax.imshow(S, cmap=SEQ, vmin=0, vmax=1)
ax.set_xticks(range(len(QS)), [q.replace("_", " ") for q in QS], rotation=35, ha="right", fontsize=8)
ax.set_yticks(range(len(ORDER)), labels, fontsize=8)
for (i, j), e in E.items():
    # secondary encoding: a ring marks cells with a case-level expectation, and the
    # expected answer is written as text, so identity is never colour-alone
    ax.add_patch(plt.Rectangle((j - .5, i - .5), 1, 1, fill=False,
                               edgecolor=INK if e == "yes" else ORANGE, lw=1.8))
for i in range(len(ORDER)):
    for j in range(len(QS)):
        if np.isnan(S[i, j]):
            continue
        shade = "#ffffff" if S[i, j] > 0.55 else INK2
        tag = {"yes": "\nexp Y", "no": "\nexp N"}.get(E.get((i, j), ""), "")
        ax.text(j, i, f"{S[i, j]:.2f}{tag}", ha="center", va="center", fontsize=6.4, color=shade)
cb = fig.colorbar(im, ax=ax, fraction=0.03, shrink=0.9)
cb.set_label("PRISM2 yes/no score", fontsize=8)
cb.outline.set_visible(False)
ax.set_title("Zero-shot yes/no scores against case-level HTAN labels\n"
             "black ring = case recorded positive, orange ring = case recorded negative",
             fontsize=10, color=INK, pad=10)
fig.savefig(os.path.join(FIG, "fig2_score_matrix.png"), dpi=200)
plt.close(fig)

# ---------------------------------------------------------------- figure 3
fig, ax = plt.subplots(figsize=(8.6, 4.4), constrained_layout=True)
rng = np.random.default_rng(0)
for j, q in enumerate(QS):
    for i, s in enumerate(ORDER):
        v = S[i, j]
        if np.isnan(v):
            continue
        e = E.get((i, j))
        y = j + (rng.random() - .5) * 0.26
        if e == "yes":
            ax.plot(v, y, "o", ms=8, color=BLUE, mec=SURFACE, mew=1.2, zorder=3)
        elif e == "no":
            ax.plot(v, y, "s", ms=7.5, color=ORANGE, mec=SURFACE, mew=1.2, zorder=3)
        else:
            ax.plot(v, y, "o", ms=5, color="#d8d7d2", mec=SURFACE, mew=1.0, zorder=2)
ax.set_yticks(range(len(QS)), [q.replace("_", " ") for q in QS], fontsize=8)
ax.set_xlim(-0.03, 1.03)
ax.set_xlabel("PRISM2 yes/no score")
ax.grid(axis="x", color="#ecebe6", lw=0.8)
ax.set_axisbelow(True)
ax.invert_yaxis()
h = [plt.Line2D([], [], marker="o", ls="", color=BLUE, ms=8, label="case recorded positive"),
     plt.Line2D([], [], marker="s", ls="", color=ORANGE, ms=7.5, label="case recorded negative"),
     plt.Line2D([], [], marker="o", ls="", color="#d8d7d2", ms=5, label="no case-level label")]
ax.legend(handles=h, frameon=False, fontsize=8, loc="lower right")
ax.set_title("Separation between recorded positives and negatives, per question",
             fontsize=10, color=INK, pad=8)
fig.savefig(os.path.join(FIG, "fig3_discrimination.png"), dpi=200)
plt.close(fig)
print("wrote", len(os.listdir(FIG)), "figures")
