#!/usr/bin/env python3
"""Pre-registered scoring for the HTAN progression cohort.

Endpoints and their handling are fixed in assets/questions_htan_progression.md and are followed
here without deviation. Bootstrap resampling is over PATIENTS, never slides or tiles, because
32 patients contribute more than one specimen.
"""
import csv, json, os, itertools
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
AXIS = ["Normal", "Normal adjacent", "Atypia - hyperplasia", "Premalignant",
        "Premalignant - in situ", "Primary"]
RANK = {"Normal": 0, "Normal adjacent": 0, "Atypia - hyperplasia": 1, "Premalignant": 2,
        "Premalignant - in situ": 3, "Primary": 4}          # collapse per the pre-registration
STAGE_LETTER = {"A": "Normal", "B": "Atypia - hyperplasia", "C": "Premalignant",
                "D": "Premalignant - in situ", "E": "Primary", "F": "Metastatic",
                "G": "Post therapy neoadjuvant", "H": "Local recurrence"}
# F, G and H are real HTAN data model values that no slide in this cohort carries
OFF_COHORT = {"F", "G", "H"}
LADDER = ["negative_for_tumor", "benign", "hyperplasia_metaplasia", "dysplasia", "atypia",
          "precancerous_lesion", "carcinoma_in_situ", "invasive_carcinoma", "malignancy"]

lab = {r["sample"]: r for r in csv.DictReader(open(os.path.join(REPO, "assets/samplesheet_progression_labels.csv")))}
rec = {r["sample"]: r for r in json.load(open(os.path.join(HERE, "results.json")))}
rows = [dict(lab[s], **{"rec": rec[s]}) for s in rec if s in lab]
print(f"{len(rows)} slides scored, {len({r['pt'] for r in rows})} patients\n")

def yn(r, q):
    v = r["rec"].get("yes_no", {}).get(q, {}).get("score")
    return float(v) if v is not None else np.nan

def mc(r, q):
    a = r["rec"].get("multiple_choice", {}).get(q, {}).get("answer", "") or ""
    a = a.strip()
    return a[0].upper() if a and a[0].upper() in "ABCDEFGHIJKL" else None

# ---------------------------------------------------------------- 1. stage MCQ
print("=" * 78)
print("1. progression_stage_mc against TumorTissueType")
sub = [r for r in rows if r["ttt"] in RANK]
pred = {}
conf = {}
n_off = 0
for r in sub:
    L = mc(r, "progression_stage_mc")
    if L is None:
        continue
    if L in OFF_COHORT:
        n_off += 1
    p = STAGE_LETTER.get(L, "?")
    pred[r["sample"]] = p
    truth = "Normal" if r["ttt"] in ("Normal", "Normal adjacent") else r["ttt"]
    conf[(truth, p)] = conf.get((truth, p), 0) + 1
truths = [t for t in ["Normal", "Atypia - hyperplasia", "Premalignant", "Premalignant - in situ", "Primary"]]
preds = sorted({p for (_, p) in conf})
print(f"\n{'truth \\ predicted':<26}" + "".join(f"{p[:14]:>16}" for p in preds))
for t in truths:
    print(f"{t:<26}" + "".join(f"{conf.get((t,p),0):>16}" for p in preds))
scored = [(r, pred[r["sample"]]) for r in sub if r["sample"] in pred]
exact = sum(1 for r, p in scored if p == ("Normal" if r["ttt"] in ("Normal", "Normal adjacent") else r["ttt"]))
within1 = sum(1 for r, p in scored
              if p in RANK and abs(RANK[p] - RANK[r["ttt"]]) <= 1)
print(f"\nexact accuracy      {exact}/{len(scored)} = {exact/len(scored):.3f}   (chance 0.125)")
print(f"within one step     {within1}/{len(scored)} = {within1/len(scored):.3f}")
print(f"off-cohort answers  {n_off}/{len(scored)} = {n_off/len(scored):.3f}   "
      f"(Metastatic / Post therapy / Local recurrence, none present here)")
dist = {}
for r in sub:
    L = mc(r, "progression_stage_mc")
    if L:
        dist[STAGE_LETTER.get(L, L)] = dist.get(STAGE_LETTER.get(L, L), 0) + 1
print("answer distribution:", dict(sorted(dist.items(), key=lambda kv: -kv[1])))

# ---------------------------------------------------------------- 2. ladder
print("\n" + "=" * 78)
print("2. yes/no ladder against the ordinal axis (Spearman, bootstrap over patients)")
from scipy.stats import spearmanr
pts = sorted({r["pt"] for r in sub})
rng = np.random.default_rng(0)
print(f"\n{'question':<24}{'rho':>7}{'95% CI':>18}{'peak class':>26}")
ladder_out = {}
for q in LADDER:
    x = np.array([RANK[r["ttt"]] for r in sub])
    y = np.array([yn(r, q) for r in sub])
    m = ~np.isnan(y)
    rho = spearmanr(x[m], y[m]).statistic
    boots = []
    for _ in range(2000):
        take = rng.choice(pts, size=len(pts), replace=True)
        idx = [i for i, r in enumerate(sub) if r["pt"] in set(take) and m[i]]
        if len({x[i] for i in idx}) > 1:
            boots.append(spearmanr(x[idx], y[idx]).statistic)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    means = {c: np.nanmean([yn(r, q) for r in sub if r["ttt"] == c]) for c in AXIS}
    peak = max(means, key=lambda c: means[c])
    ladder_out[q] = dict(rho=round(float(rho), 3), ci=[round(float(lo), 3), round(float(hi), 3)],
                         peak=peak, means={k: round(float(v), 4) for k, v in means.items()})
    print(f"{q:<24}{rho:>7.2f}{f'[{lo:.2f}, {hi:.2f}]':>18}{peak:>26}")

# ---------------------------------------------------------------- 3. normal vs primary
print("\n" + "=" * 78)
print("3. normal versus primary AUC (the endpoint the pilot could not compute)")
def auc(pos, neg):
    if not pos or not neg:
        return None
    w = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p, n in itertools.product(pos, neg))
    return w / (len(pos) * len(neg))
for q in ["invasive_carcinoma", "malignancy", "negative_for_tumor", "carcinoma_in_situ"]:
    pos = [yn(r, q) for r in sub if r["ttt"] == "Primary"]
    neg = [yn(r, q) for r in sub if r["ttt"] in ("Normal", "Normal adjacent")]
    pos = [v for v in pos if not np.isnan(v)]; neg = [v for v in neg if not np.isnan(v)]
    a = auc(pos, neg)
    ties = sum(1 for p, n in itertools.product(pos, neg) if p == n)
    print(f"  {q:<22} AUC {a:.3f}  (n+={len(pos)} n-={len(neg)}, tied pairs "
          f"{ties}/{len(pos)*len(neg)} = {ties/(len(pos)*len(neg)):.3f})")

# ---------------------------------------------------------------- 4. quantisation
print("\n" + "=" * 78)
vals = [yn(r, q) for r in rows for q in LADDER if not np.isnan(yn(r, q))]
print(f"4. bf16 grid: {len(set(np.round(vals, 6)))} distinct values across {len(vals)} scores")

json.dump({"n_slides": len(rows), "n_patients": len({r['pt'] for r in rows}),
           "stage_mc": {"exact": exact / len(scored), "within_one": within1 / len(scored),
                        "off_cohort": n_off / len(scored), "n": len(scored)},
           "ladder": ladder_out}, open(os.path.join(HERE, "progression_metrics.json"), "w"), indent=2)
