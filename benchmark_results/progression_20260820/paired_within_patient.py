#!/usr/bin/env python3
"""The paired within-patient analysis, pre-registered in STUDY_PLAN section 7 item 1.

Every number in `score_progression.py` compares slides from DIFFERENT patients, so a ladder score
that tracked something patient-level rather than lesion-level -- one hospital visit's staining, one
block's fixation, one person's tissue -- would look exactly like a working progression endpoint.
This is the analysis that can tell those apart, and it was specified before the run and not yet run.

**31 patients contribute more than one specimen and every one of them spans more than one
progression class.** All 31 are Arm A, which is convenient: the paired design lands entirely inside
the arm that is already one centre and one organ, so a within-patient contrast holds patient,
centre, organ and scanner constant simultaneously. What is left varying is the lesion.

Three statistics, in increasing order of how much they assume:

  1. WITHIN-PATIENT CONCORDANCE. Over the 51 slide pairs that share a patient and differ in
     ordinal rank, the fraction where the higher-rank slide scored higher. Ties count 0.5. This is
     a c-index stratified by patient; its null is 0.5 and it assumes nothing about the shape of
     the score. Reported beside the ordinary BETWEEN-patient concordance on the same slides, which
     is the quantity section 1 and 2 of the analysis are built from. If the two agree, the endpoint
     is not being carried by patient-level confounding.
  2. ADJACENT-ONLY CONCORDANCE. The same thing restricted to pairs one ordinal step apart, which
     is the hardest version of the question and the one a triage tool would actually face.
  3. A MIXED MODEL, score ~ rank + (1|patient), over all Arm A slides. This is the "patient as a
     random effect" half of the pre-registration. The fixed slope is the endpoint; the intraclass
     correlation is the more interesting output, because it says how much of each score is the
     patient rather than the lesion.

CIs bootstrap over PATIENTS throughout, resampling whole patients with all their pairs.
"""
import csv, itertools, json, os, warnings
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
RANK = {"Normal": 0, "Normal adjacent": 0, "Atypia - hyperplasia": 1, "Premalignant": 2,
        "Premalignant - in situ": 3, "Primary": 4}
AXIS = ["Normal", "Normal adjacent", "Atypia - hyperplasia", "Premalignant",
        "Premalignant - in situ", "Primary"]
SHORT = {"Normal": "normal", "Normal adjacent": "normal adj.", "Atypia - hyperplasia": "atypia /\nhyperplasia",
         "Premalignant": "premalignant", "Premalignant - in situ": "premalignant\nin situ",
         "Primary": "primary\ninvasive"}
LADDER = ["negative_for_tumor", "benign", "hyperplasia_metaplasia", "dysplasia", "atypia",
          "precancerous_lesion", "carcinoma_in_situ", "invasive_carcinoma", "malignancy"]
# The direction each question is predicted to move. Two readings are needed, not one.
#
# MONOTONE is the paired analogue of the pre-registered primary quantity, which is Spearman rho
# against the ordinal axis: it asks "does the score rise (or fall) with rank", for every pair.
#
# PROFILE is the pre-registered secondary: four of the nine are predicted to PEAK mid-axis, so for
# a pair sitting above the peak the predicted direction is DOWN, and scoring it as up penalises the
# question for behaving exactly as predicted. Pairs that straddle a peak carry no prediction at all
# and are dropped. PEAK is the rank each question should peak at, read off the pre-registration in
# assets/questions_htan_progression.md, not off these results.
FALLS = {"negative_for_tumor", "benign"}
PEAK = {"negative_for_tumor": 0, "benign": 0, "hyperplasia_metaplasia": 1, "dysplasia": 2,
        "atypia": 1, "precancerous_lesion": 2, "carcinoma_in_situ": 3,
        "invasive_carcinoma": 4, "malignancy": 4}

lab = {r["sample"]: r for r in csv.DictReader(
    open(os.path.join(REPO, "assets/samplesheet_progression_labels.csv")))}
rec = {r["sample"]: r for r in json.load(open(os.path.join(HERE, "results_merged.json")))}
rows = [dict(lab[s], rec=rec[s]) for s in rec if s in lab and lab[s]["ttt"] in RANK]
rows = [r for r in rows if r["arm"].startswith("A")]
print(f"Arm A: {len(rows)} slides, {len({r['pt'] for r in rows})} patients")

def yn(r, q):
    v = r["rec"].get("yes_no", {}).get(q, {}).get("score")
    return float(v) if v is not None else np.nan

by_pt = {}
for r in rows:
    by_pt.setdefault(r["pt"], []).append(r)
multi = {p: v for p, v in by_pt.items() if len(v) > 1}

# ---- pair inventory -------------------------------------------------------------------------
pairs = []          # (patient, low_rank_row, high_rank_row, step)
for p, v in sorted(multi.items()):
    for a, b in itertools.combinations(v, 2):
        ra, rb = RANK[a["ttt"]], RANK[b["ttt"]]
        if ra == rb:
            continue
        lo, hi = (a, b) if ra < rb else (b, a)
        pairs.append((p, lo, hi, abs(ra - rb)))
adj = [x for x in pairs if x[3] == 1]
print(f"{len(pairs)} within-patient rank-discordant pairs from {len({x[0] for x in pairs})} "
      f"patients; {len(adj)} of them one step apart")

def predicted_sign(q, r_lo, r_hi, profile):
    """+1 if the score should be higher on the higher-rank slide, -1 if lower, 0 if no prediction."""
    if not profile:
        return -1.0 if q in FALLS else 1.0
    pk = PEAK[q]
    if r_hi <= pk:
        return 1.0            # both below the peak: still climbing
    if r_lo >= pk:
        return -1.0           # both at or above the peak: coming down
    return 0.0                # straddles the peak, the profile predicts nothing about the order

def concordance(pair_list, q, profile=False):
    """Fraction of pairs ordered as predicted. Ties are 0.5. Returns (value, n_used, per-pair)."""
    out = []
    for _p, lo, hi, _s in pair_list:
        a, b = yn(lo, q), yn(hi, q)
        if np.isnan(a) or np.isnan(b):
            continue
        sgn = predicted_sign(q, RANK[lo["ttt"]], RANK[hi["ttt"]], profile)
        if sgn == 0:
            continue
        d = sgn * (b - a)
        out.append(1.0 if d > 0 else 0.0 if d < 0 else 0.5)
    return (float(np.mean(out)) if out else np.nan), len(out), out

def boot_over_patients(pair_list, q, n=4000, seed=0, profile=False):
    """Resample whole patients, carrying all of their pairs."""
    rng = np.random.default_rng(seed)
    pats = sorted({x[0] for x in pair_list})
    idx = {p: [x for x in pair_list if x[0] == p] for p in pats}
    vals = []
    for _ in range(n):
        take = rng.integers(0, len(pats), len(pats))
        sub = [x for i in take for x in idx[pats[i]]]
        v, k, _ = concordance(sub, q, profile)
        if k:
            vals.append(v)
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))

def between_concordance(q, strata=None, profile=False):
    """c-index over rank-discordant pairs of slides from DIFFERENT patients.

    With `strata`, the result is DIRECTLY STANDARDISED to a given mix of class contrasts. This
    matters more than it looks. The within-patient pairs are not a random sample of contrasts: 36
    of the 51 sit inside ranks 0 to 2, where every ladder question is flat by construction, while
    the unrestricted between-patient set is full of normal-versus-primary pairs that any of these
    questions separates trivially. Comparing the raw numbers would therefore compare two different
    questions and attribute the difference to the patient. Standardising to the within-patient
    contrast mix is what makes the comparison mean "same patient or not".
    """
    v = [(RANK[r["ttt"]], yn(r, q), r["pt"]) for r in rows]
    v = [x for x in v if not np.isnan(x[1])]
    per = {}
    for (ra, sa, pa), (rb, sb, pb) in itertools.combinations(v, 2):
        if ra == rb or pa == pb:
            continue
        key = (min(ra, rb), max(ra, rb))
        sgn = predicted_sign(q, key[0], key[1], profile)
        if sgn == 0:
            continue
        lo, hi = (sa, sb) if ra < rb else (sb, sa)
        d = sgn * (hi - lo)
        per.setdefault(key, []).append(1.0 if d > 0 else 0.0 if d < 0 else 0.5)
    if strata is None:
        allv = [x for vals in per.values() for x in vals]
        return float(np.mean(allv)), len(allv)
    num = den = 0.0
    for key, w in strata.items():
        if key in per:
            num += w * float(np.mean(per[key])); den += w
    return (num / den if den else np.nan), int(den)

# the contrast mix the within-patient pairs actually sample
STRATA = {}
for _p, lo, hi, _s in pairs:
    key = (RANK[lo["ttt"]], RANK[hi["ttt"]])
    STRATA[key] = STRATA.get(key, 0) + 1
print("within-patient contrast mix (rank_low, rank_high): "
      + ", ".join(f"{k}x{v}" for k, v in sorted(STRATA.items(), key=lambda kv: -kv[1])))

# ---- 1 and 2. concordance -------------------------------------------------------------------
print(f"\n{'question':<24}{'within':>9}{'95% CI':>16}{'matched':>9}{'raw':>7}"
      f"{'profile':>9}{'prof.mat':>10}{'n prof':>8}")
conc = {}
for q in LADDER:
    w, nw, _ = concordance(pairs, q)
    lo, hi = boot_over_patients(pairs, q)
    bm, _ = between_concordance(q, STRATA)
    braw, nb = between_concordance(q)
    a, na, _ = concordance(adj, q)
    wp, nwp, _ = concordance(pairs, q, profile=True)
    lop, hip = boot_over_patients(pairs, q, profile=True) if nwp else (np.nan, np.nan)
    bmp, _ = between_concordance(q, STRATA, profile=True)
    conc[q] = dict(within=round(w, 4), within_ci=[round(lo, 4), round(hi, 4)], n_within=nw,
                   between_matched=round(bm, 4), between_raw=round(braw, 4), n_between=nb,
                   adjacent=round(a, 4), n_adjacent=na,
                   within_minus_matched=round(w - bm, 4),
                   within_profile=round(wp, 4) if nwp else None,
                   within_profile_ci=[round(lop, 4), round(hip, 4)] if nwp else None,
                   n_within_profile=nwp,
                   between_matched_profile=round(bmp, 4) if not np.isnan(bmp) else None,
                   predicted_peak_rank=PEAK[q])
    print(f"{q:<24}{w:>9.3f}{f'[{lo:.2f}, {hi:.2f}]':>16}{bm:>9.3f}{braw:>7.3f}"
          f"{wp:>9.3f}{bmp:>10.3f}{nwp:>8}")

# ---- 3. mixed model -------------------------------------------------------------------------
import pandas as pd
import statsmodels.formula.api as smf
print(f"\n{'question':<24}{'slope':>9}{'95% CI':>18}{'ICC':>8}{'pooled slope':>14}")
mixed = {}
for q in LADDER:
    df = pd.DataFrame([{"score": yn(r, q), "rank": RANK[r["ttt"]], "pt": r["pt"]} for r in rows])
    df = df.dropna()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m = smf.mixedlm("score ~ rank", df, groups=df["pt"]).fit(reml=True)
        pooled = smf.ols("score ~ rank", df).fit()
    slope = float(m.params["rank"])
    ci = m.conf_int().loc["rank"].tolist()
    if not np.all(np.isfinite(ci)):
        # a singular random-effect fit (patient variance driven to ~0) leaves the Hessian
        # non-invertible; the OLS interval is the right fallback and is reported as such
        ci = pooled.conf_int().loc["rank"].tolist()
        singular = True
    else:
        singular = False
    # intraclass correlation: share of residual variance that is between-patient
    vg = float(m.cov_re.iloc[0, 0]); ve = float(m.scale)
    icc = vg / (vg + ve) if (vg + ve) > 0 else np.nan
    mixed[q] = dict(slope=round(slope, 4), ci=[round(float(ci[0]), 4), round(float(ci[1]), 4)],
                    ci_from_ols_singular_fit=singular,
                    icc=round(float(icc), 4), pooled_slope=round(float(pooled.params["rank"]), 4),
                    n=int(len(df)))
    print(f"{q:<24}{slope:>9.3f}{f'[{ci[0]:.3f}, {ci[1]:.3f}]':>18}{icc:>8.3f}"
          f"{pooled.params['rank']:>14.3f}")

json.dump(dict(arm="A", n_slides=len(rows), n_patients=len({r["pt"] for r in rows}),
               n_multi_slide_patients=len(multi),
               n_pairs=len(pairs), n_pairs_adjacent=len(adj),
               n_patients_contributing_pairs=len({x[0] for x in pairs}),
               within_patient_contrast_mix={f"{k[0]}->{k[1]}": v for k, v in sorted(STRATA.items())},
               note="within-patient concordance is a c-index stratified by patient, null 0.5; "
                    "CIs resample whole patients with all their pairs; between_matched is the "
                    "between-patient c-index directly standardised to the within-patient mix of "
                    "class contrasts, which is the only fair comparator; the mixed model is "
                    "score ~ rank + (1|patient) by REML",
               concordance=conc, mixed_model=mixed),
          open(os.path.join(HERE, "paired_metrics.json"), "w"), indent=2)
print("\nwrote paired_metrics.json")

# ------------------------------------------------------------------ figure
# Three panels, because the headline number is uninterpretable without the third.
#   A  the raw paired data for the strongest question: one line per patient, across their own
#      specimens. Paired data should be shown paired.
#   B  the result: within-patient concordance against the between-patient comparator, both raw and
#      standardised to the within-patient contrast mix.
#   C  that contrast mix, which is why B needs standardising at all.
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

SURFACE, INK, INK2, INK3 = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8880"
BLUE, ORANGE, GRID = "#2a78d6", "#eb6834", "#e1e0d9"
mpl.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "text.color": INK, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "axes.edgecolor": "#dcdbd6", "axes.linewidth": 0.8, "font.size": 9, "legend.frameon": False,
    "axes.spines.top": False, "axes.spines.right": False})
FIG = os.path.join(HERE, "figures")
Q = "invasive_carcinoma"

fig = plt.figure(figsize=(14.6, 5.0))
gs = fig.add_gridspec(1, 3, width_ratios=[1.0, 1.25, 0.85], wspace=0.38)

ax = fig.add_subplot(gs[0, 0])
rng = np.random.default_rng(0)
for p, v in sorted(multi.items()):
    pts = sorted(((RANK[r["ttt"]], yn(r, Q)) for r in v if not np.isnan(yn(r, Q))))
    if len(pts) < 2:
        continue
    jx = rng.normal(0, 0.045)
    xs = [x + jx for x, _ in pts]; ys = [y for _, y in pts]
    ax.plot(xs, ys, "-o", color=INK3, lw=1.0, ms=4.0, alpha=0.55, mfc=BLUE, mec="none", zorder=3)
ranks = sorted({RANK[c] for c in AXIS})
means = [np.nanmean([yn(r, Q) for r in rows if RANK[r["ttt"]] == k]) for k in ranks]
ax.plot(ranks, means, "-", color=ORANGE, lw=2.6, zorder=4, label="Arm A class mean, all 115 slides")
ax.set_xticks(ranks, ["normal", "atypia /\nhyperpl.", "premal.", "in situ", "primary"],
              fontsize=8.5)
ax.set_ylabel(f"PRISM2 score, {Q.replace('_', ' ')}")
ax.set_ylim(-0.03, 1.0)
ax.grid(axis="y", color=GRID, lw=0.8); ax.set_axisbelow(True)
ax.legend(fontsize=7.6, loc="upper left", labelcolor=INK2)
ax.set_title(f"{len(multi)} patients with more than one specimen\n"
             f"one line per patient, across their own slides",
             fontsize=9.6, color=INK, loc="left", linespacing=1.6)
ax.set_xlim(-0.35, 4.35)

ax = fig.add_subplot(gs[0, 1])
y = np.arange(len(LADDER))[::-1]
for i, q in enumerate(LADDER):
    c = conc[q]
    yy = y[i]
    # scored against the pre-registered profile, so a question predicted to peak mid-axis is not
    # penalised for coming down again afterwards
    w, ci, bm = c["within_profile"], c["within_profile_ci"], c["between_matched_profile"]
    ax.plot([bm, w], [yy, yy], color=GRID, lw=2.4, zorder=2)
    ax.plot([c["between_raw"]], [yy], marker="o", ms=6, color=INK3, mec="none", zorder=3)
    ax.plot([bm], [yy], marker="o", ms=8, color=ORANGE, mec=SURFACE, mew=1.2, zorder=4)
    ax.errorbar([w], [yy], xerr=[[w - ci[0]], [ci[1] - w]],
                fmt="o", ms=8, color=BLUE, mec=SURFACE, mew=1.2, ecolor=BLUE, elinewidth=1.3,
                capsize=2.5, zorder=5)
    ax.text(0.905, yy, f"{c['n_within_profile']}", va="center", ha="right", fontsize=7.4,
            color=INK3)
ax.axvline(0.5, color=INK, lw=1.1, ls="--", zorder=1)
ax.text(0.5, -0.75, " chance", fontsize=7.6, color=INK2, va="bottom")
ax.set_yticks(y, [q.replace("_", " ") for q in LADDER], fontsize=8.5)
ax.set_xlim(0.15, 0.93)
ax.set_xlabel("concordance: fraction of pairs ordered as predicted")
ax.grid(axis="x", color=GRID, lw=0.8); ax.set_axisbelow(True)
ax.legend(handles=[
    Line2D([], [], marker="o", ls="", ms=7, mfc=BLUE, mec="none", label="within patient (95% CI)"),
    Line2D([], [], marker="o", ls="", ms=7, mfc=ORANGE, mec="none",
           label="between patients, same contrast mix"),
    Line2D([], [], marker="o", ls="", ms=6, mfc=INK3, mec="none",
           label="between patients, all contrasts, monotone")],
    fontsize=7.6, loc="upper left", labelcolor=INK2)
ax.set_ylim(-1.0, len(LADDER) - 0.4)
ax.set_title(f"Scored against the pre-registered profile\n"
             f"up to {len(pairs)} within-patient pairs, {len({x[0] for x in pairs})} patients "
             f"(n at right)",
             fontsize=9.6, color=INK, loc="left", linespacing=1.6)

ax = fig.add_subplot(gs[0, 2])
labels = {0: "normal", 1: "atypia", 2: "premal.", 3: "in situ", 4: "primary"}
items = sorted(STRATA.items(), key=lambda kv: -kv[1])
yy = np.arange(len(items))[::-1]
flat = [i for i, (k, _) in enumerate(items) if k[1] <= 2]
cols = [INK3 if k[1] <= 2 else BLUE for k, _ in items]
ax.barh(yy, [v for _, v in items], height=0.6, color=cols, zorder=3)
for i, (k, v) in enumerate(items):
    ax.text(v + 0.4, yy[i], str(v), va="center", fontsize=8.5, color=INK2)
ax.set_yticks(yy, [f"{labels[k[0]]} → {labels[k[1]]}" for k, _ in items], fontsize=8.5)
ax.set_xlim(0, max(v for _, v in items) * 1.25)
ax.set_xlabel("within-patient pairs")
ax.grid(axis="x", color=GRID, lw=0.8); ax.set_axisbelow(True)
n_flat = sum(v for k, v in STRATA.items() if k[1] <= 2)
ax.set_title(f"Which contrasts a patient can supply\n"
             f"{n_flat} of {len(pairs)} stop at premalignant (grey)",
             fontsize=9.6, color=INK, loc="left", linespacing=1.6)

fig.suptitle("The paired within-patient analysis: is the progression endpoint carried by the "
             "patient or by the lesion?", fontsize=12, color=INK, y=1.06)
fig.savefig(os.path.join(FIG, "fig6_paired_within_patient.png"), dpi=200, bbox_inches="tight")
print("wrote figures/fig6_paired_within_patient.png")
