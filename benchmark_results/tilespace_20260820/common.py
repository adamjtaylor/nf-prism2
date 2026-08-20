#!/usr/bin/env python3
"""Shared metadata, palette and sampling for the tile-embedding-space analysis.

Everything downstream depends on three decisions made here, so they live in one place:

  * ARM is normalised so that `A-pair` folds into `A`. The eight paired specimens were sampled
    to give within-patient pairs inside Arm A; they are Arm A slides.
  * SAMPLING IS PATIENT-BALANCED, then slide-balanced within patient. A patient with four
    slides must not contribute four times the tiles of a patient with one, or every
    "different patient" statistic inherits the sampling design.
  * BOOTSTRAP IS OVER PATIENTS. Resampling tiles treats 643 tiles from one slide as 643
    independent observations, which they are not.

Palette: dataviz reference instance (`references/palette.md`). Categorical slots are assigned
in fixed order and never cycled; magnitude always uses the one-hue blue ramp.
"""
import csv, glob, json, os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
FEAT = os.path.join(REPO, "analysis", "data", "tile_features")
SLIDE_EMB = os.path.join(REPO, "analysis", "data", "prism2")
FIG = os.path.join(HERE, "figures")

# ---------------------------------------------------------------- palette
SURFACE, INK, INK2, INK3 = "#fcfcfb", "#0b0b0b", "#52514e", "#898781"
GRID, AXIS = "#e1e0d9", "#c3c2b7"
# categorical slots, fixed order, never cycled
CAT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
BLUE, ORANGE, AQUA, YELLOW, MAGENTA, GREEN, VIOLET, RED = CAT
SEQ_STEPS = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
             "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]

def rcparams():
    import matplotlib as mpl
    mpl.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "text.color": INK, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
        "axes.edgecolor": AXIS, "axes.linewidth": 0.8, "font.size": 9,
        "font.family": "sans-serif",
        "axes.spines.top": False, "axes.spines.right": False,
        "legend.frameon": False, "figure.dpi": 110})

def seq_cmap():
    from matplotlib.colors import LinearSegmentedColormap
    return LinearSegmentedColormap.from_list("blue_seq", SEQ_STEPS)

# ---------------------------------------------------------------- cohort
# ordinal progression axis; Normal adjacent shares rank 0 with Normal (both tumour-absent)
RANK = {"Normal": 0, "Normal adjacent": 0, "Atypia - hyperplasia": 1, "Premalignant": 2,
        "Premalignant - in situ": 3, "Primary": 4, "Metastatic": 5}
CLASS_ORDER = ["Normal", "Normal adjacent", "Atypia - hyperplasia", "Premalignant",
               "Premalignant - in situ", "Primary", "Metastatic"]
CLASS_SHORT = {"Normal": "normal", "Normal adjacent": "normal adj.",
               "Atypia - hyperplasia": "atypia/hyperpl.", "Premalignant": "premalignant",
               "Premalignant - in situ": "in situ", "Primary": "primary", "Metastatic": "metastatic"}

# `organ` is empty for 53 of 188 samplesheet rows: HTAN BU records TissueorOrganofOrigin for only
# 90 of 116 Arm A slides, and HTAN Vanderbilt for none. Both atlases are single-organ by
# construction (BU is an airway precancer atlas, Vanderbilt colon), so organ is resolved from the
# atlas where the field is blank rather than dropping a third of the cohort. This is recorded as a
# metadata-completeness finding, not hidden: `organ_imputed` marks the affected rows.
ATLAS_ORGAN = {"HTAN BU": "Lung", "HTAN Vanderbilt": "Colon"}
ORGAN_FIX = {"Other and Ill-defined Sites": "Fallopian tube"}

def resolve_organ(r):
    o = (r.get("organ") or "").strip()
    o = ORGAN_FIX.get(o, o)
    if o:
        return o, False
    return ATLAS_ORGAN.get(r["atlas"], "unknown"), True

def load_meta():
    """Samplesheet rows for every sample that actually produced tile features."""
    rows = {}
    with open(os.path.join(REPO, "assets", "samplesheet_progression_labels.csv")) as fh:
        for r in csv.DictReader(fh):
            r["arm"] = "A" if r["arm"].startswith("A") else r["arm"]
            r["centre"] = r["atlas"]
            r["patient"] = r["pt"]
            r["organ_resolved"], r["organ_imputed"] = resolve_organ(r)
            rows[r["sample"]] = r
    # C_Duke_primary_21 produced a features file with zero rows: its segmentation found tissue but
    # no patch cleared min_tissue_proportion=0.65. It also has no PRISM2 slide embedding, which is
    # why the run reports 163 slides while 164 feature files exist. Excluded here so that every
    # downstream count refers to the same set of slides.
    import h5py
    out, empty = {}, []
    for p in sorted(glob.glob(os.path.join(FEAT, "*.features.h5"))):
        s = os.path.basename(p)[: -len(".features.h5")]
        if s not in rows:
            continue
        with h5py.File(p, "r") as h:
            if h["features"].shape[0] == 0:
                empty.append(s); continue
        r = dict(rows[s]); r["h5"] = p
        out[s] = r
    if empty:
        print(f"[common] excluded {len(empty)} slide(s) with zero tiles: {empty}")
    return out

def tile_counts(meta):
    import h5py
    n = {}
    for s, r in meta.items():
        with h5py.File(r["h5"], "r") as h:
            n[s] = int(h["features"].shape[0])
    return n

# ---------------------------------------------------------------- sampling
def balanced_sample(meta, counts, per_slide=250, arms=None, seed=42, patient_cap=None):
    """Equal tiles per slide (the projection requirement), with an optional cap on how many
    slides a single patient may contribute, so patients are balanced as well as slides.

    Returns a list of (sample, tile_index) in a stable order.
    """
    rng = np.random.default_rng(seed)
    samples = [s for s, r in meta.items() if arms is None or r["arm"] in arms]
    if patient_cap is not None:
        by_pt = {}
        for s in samples:
            by_pt.setdefault(meta[s]["patient"], []).append(s)
        keep = []
        for pt in sorted(by_pt):
            ss = sorted(by_pt[pt])
            if len(ss) > patient_cap:
                ss = list(rng.choice(ss, size=patient_cap, replace=False))
            keep += ss
        samples = sorted(keep)
    picks = []
    for s in sorted(samples):
        n = counts[s]
        k = min(per_slide, n)
        idx = np.sort(rng.choice(n, size=k, replace=False))
        picks += [(s, int(i)) for i in idx]
    return picks

def load_tiles(meta, picks):
    """Read the sampled tiles. Groups by slide so each h5 is opened once."""
    import h5py
    by_s = {}
    for s, i in picks:
        by_s.setdefault(s, []).append(i)
    X, coords, samp = [], [], []
    for s in sorted(by_s):
        idx = np.array(sorted(by_s[s]))
        with h5py.File(meta[s]["h5"], "r") as h:
            X.append(np.asarray(h["features"][idx], dtype=np.float32))
            coords.append(np.asarray(h["coords"][idx], dtype=np.int64))
        samp += [s] * len(idx)
    return np.vstack(X), np.vstack(coords), np.array(samp)

def l2(A):
    A = np.asarray(A, dtype=np.float32)
    return A / np.maximum(np.linalg.norm(A, axis=1, keepdims=True), 1e-12)

def patient_bootstrap(patients, values, n_boot=2000, seed=0, stat=np.mean):
    """95% CI by resampling PATIENTS with replacement, carrying all of a patient's values.

    Never resample tiles or slides: tiles inside a slide and slides inside a patient are not
    independent, and the pilot's slide-level CIs were correspondingly too tight.
    """
    patients = np.asarray(patients)
    values = np.asarray(values, dtype=float)
    uniq = np.unique(patients)
    groups = [values[patients == p] for p in uniq]
    rng = np.random.default_rng(seed)
    boots = np.empty(n_boot)
    for b in range(n_boot):
        pick = rng.integers(0, len(groups), size=len(groups))
        boots[b] = stat(np.concatenate([groups[i] for i in pick]))
    return float(stat(values)), float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))

def savefig(fig, name):
    os.makedirs(FIG, exist_ok=True)
    p = os.path.join(FIG, name)
    fig.savefig(p, dpi=200, bbox_inches="tight")
    print("wrote", p)
    return p

def dump(obj, name):
    p = os.path.join(HERE, name)
    with open(p, "w") as fh:
        json.dump(obj, fh, indent=2, default=lambda o: float(o) if isinstance(o, np.floating) else int(o))
    print("wrote", p)
