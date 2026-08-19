#!/usr/bin/env python3
"""Build the three-arm HTAN progression cohort for nf-prism2.

Design, and why:

  Arm A  HTAN BU, lung, all six TumorTissueType classes. BU is the only atlas carrying the whole
         axis including Primary, so the progression endpoint can be measured WITHIN one centre and
         one organ. Without that, a normal-versus-primary difference would be partly BU versus
         Duke, because every non-primary specimen in HTAN comes from the precancer atlases.
  Arm B  HTAN Vanderbilt, colon, four classes. Independent replication of the axis in a different
         organ and centre. Vanderbilt records no organ or diagnosis, which is fine: this arm only
         needs TumorTissueType.
  Arm C  Primary only, spread across Duke, HMS and WUSTL. Supplies the several-centres-per-organ
         structure that the site and type questions and the tile-locking analysis need.

SiteofResectionorBiopsy is deliberately NOT a sampling axis. It is near-collinear with organ of
origin, so stratifying on both fragments the design, but its disagreements with origin occur
naturally inside Arm A (BU records origin "Lung NOS" with sites including "Trachea") and become a
free discrimination test: does the model track where the tissue came from or where disease started.

Other rules carried over from the pilot: one slide per patient plus a few deliberate same-patient
pairs, mpp supplied from BigQuery when plausible, reader forced for tif/tiff/qptiff, an extension
allowlist because HTAN registers .ndpa annotation XML as if it were a slide, and a file size cap.
"""
import argparse, csv, json, os, re, subprocess, sys
from collections import defaultdict

PROJECT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
HTAN = os.path.join(PROJECT, ".venv", "bin", "htan")
NULLS = ("", "Not Reported", "unknown", "Unknown", "Not Applicable", "NA", None)
MPP_MIN, MPP_MAX = 0.08, 1.2
ALLOWED_FMT = {"svs", "ndpi", "tif", "tiff", "ome-tiff", "scn", "mrxs", "qptiff"}
IMAGE_READER_EXT = (".tif", ".tiff", ".qptiff")
OME_EXT = (".ome.tif", ".ome.tiff")
AXIS = ["Normal", "Normal adjacent", "Atypia - hyperplasia", "Premalignant",
        "Premalignant - in situ", "Primary"]
SHORT = {"Normal": "normal", "Normal adjacent": "normadj", "Atypia - hyperplasia": "atypia",
         "Premalignant": "premal", "Premalignant - in situ": "insitu", "Primary": "primary"}


def portal(sql):
    r = subprocess.run([HTAN, "query", "portal", "sql", sql, "--output", "json"],
                       capture_output=True, text=True, cwd=PROJECT)
    if r.returncode:
        sys.exit(r.stderr[-1500:])
    return json.loads(r.stdout[r.stdout.index("["):])


def bq(sql):
    from google.cloud import bigquery
    return [dict(x) for x in bigquery.Client().query(sql).result()]


def clean(v):
    return "" if v in NULLS else str(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-class-a", type=int, default=18)
    ap.add_argument("--per-class-b", type=int, default=12)
    ap.add_argument("--arm-c", type=int, default=45)
    ap.add_argument("--max-gb", type=float, default=1.5)
    ap.add_argument("--same-patient-pairs", type=int, default=8)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    rows = portal("""SELECT f.synapseId syn, f.Filename fn, f.HTANParticipantID pt, f.atlas_name atlas,
        s.TumorTissueType ttt, arrayStringConcat(f.organType,'|') organ,
        arrayStringConcat(f.PrimaryDiagnosis,'|') dx
        FROM files f ARRAY JOIN f.biospecimenIds AS bid
        INNER JOIN specimen s ON s.HTANBiospecimenID = bid
        WHERE f.level='Level 2' AND f.assayName='H&E' AND f.synapseId <> '' LIMIT 6000""")
    print(f"H&E slides joined to a biospecimen: {len(rows)}")

    dx = {r["p"]: r for r in portal("""SELECT HTANParticipantID p, TissueorOrganofOrigin origin,
        SiteofResectionorBiopsy site, TumorGrade grade, PrimaryDiagnosis pdx
        FROM diagnosis LIMIT 6000""")}
    meta = {r["entityId"]: r for r in bq("""SELECT entityId, File_Size, PhysicalSizeX
        FROM `isb-cgc-bq.HTAN.imaging_level2_metadata_current` WHERE entityId IS NOT NULL""")}

    cand = []
    for r in rows:
        fn = r["fn"].lower()
        fmt = "ome-tiff" if fn.endswith(OME_EXT) else fn.rsplit(".", 1)[-1]
        if fmt not in ALLOWED_FMT or clean(r["ttt"]) not in AXIS:
            continue
        m = meta.get(r["syn"], {})
        gb = float(m.get("File_Size") or 0) / 1e9
        if gb > a.max_gb:
            continue
        try:
            mpp = float(m.get("PhysicalSizeX"))
        except (TypeError, ValueError):
            mpp = None
        d = dx.get(r["pt"], {})
        cand.append(dict(syn=r["syn"], pt=r["pt"], atlas=r["atlas"], ttt=r["ttt"],
                         organ=clean(r["organ"]), dx=clean(r["dx"]), fmt=fmt, gb=round(gb, 2),
                         mpp=(round(mpp, 4) if mpp and MPP_MIN <= mpp <= MPP_MAX else ""),
                         reader=("image" if fn.endswith(IMAGE_READER_EXT)
                                 and not fn.endswith(OME_EXT) else ""),
                         origin=clean(d.get("origin")), site=clean(d.get("site")),
                         grade=clean(d.get("grade")), pdx=clean(d.get("pdx"))))
    cand.sort(key=lambda c: c["syn"])
    print(f"on the progression axis, under {a.max_gb} GB, image formats only: {len(cand)}")

    picked, used = [], set()

    def take(pool, n, arm, key=None):
        """key=None means one slide per patient overall; otherwise one per (patient, key)."""
        got = 0
        for c in pool:
            if got >= n:
                break
            tag = c["pt"] if key is None else (c["pt"], key)
            if tag in used:
                continue
            c["arm"] = arm
            picked.append(c); used.add(tag); used.add(("any", c["syn"])); got += 1
        return got

    # Arm A: BU, every class, one centre one organ
    for cls in AXIS:
        pool = [c for c in cand if c["atlas"] == "HTAN BU" and c["ttt"] == cls]
        n = take(pool, a.per_class_a, "A", key=cls)
        print(f"  arm A  {cls:<24} {n:>3} of {a.per_class_a} requested ({len(pool)} available)")
    # Arm B: Vanderbilt replication
    for cls in AXIS:
        pool = [c for c in cand if c["atlas"] == "HTAN Vanderbilt" and c["ttt"] == cls]
        if not pool:
            continue
        n = take(pool, a.per_class_b, "B", key=cls)
        print(f"  arm B  {cls:<24} {n:>3} of {a.per_class_b} requested ({len(pool)} available)")
    # Arm C: primary only, round robin over (centre, organ, format)
    poolC = [c for c in cand if c["ttt"] == "Primary" and c["atlas"] not in ("HTAN BU", "HTAN Vanderbilt")]
    strata = defaultdict(list)
    for c in poolC:
        strata[(c["atlas"], c["organ"], c["fmt"])].append(c)
    keys = sorted(strata, key=lambda k: (-len(strata[k]), k))
    n = 0
    while n < a.arm_c:
        progressed = False
        for k in keys:
            if n >= a.arm_c:
                break
            if take(strata[k], 1, "C"):
                n += 1; progressed = True
        if not progressed:
            break
    print(f"  arm C  {'cross-organ primary':<24} {n:>3} of {a.arm_c} requested")

    by_pt = defaultdict(list)
    for c in cand:
        by_pt[c["pt"]].append(c)
    pairs = 0
    for c in list(picked):
        if pairs >= a.same_patient_pairs:
            break
        sibs = [s for s in by_pt[c["pt"]] if s["syn"] != c["syn"] and s not in picked]
        if sibs:
            sibs[0]["arm"] = c["arm"] + "-pair"; picked.append(sibs[0]); pairs += 1

    seen = defaultdict(int)
    with open(a.out, "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["sample", "slide", "mpp", "reader"])
        for c in picked:
            stem = f"{c['arm'].replace('-pair','P')}_{c['atlas'].replace('HTAN ','')}_{SHORT[c['ttt']]}"
            seen[stem] += 1
            c["sample"] = f"{stem}_{seen[stem]:02d}"
            w.writerow([c["sample"], f"syn://{c['syn']}", c["mpp"], c["reader"]])
    lab = a.out.replace(".csv", "_labels.csv")
    with open(lab, "w", newline="") as fh:
        cols = ["sample", "arm", "syn", "pt", "atlas", "ttt", "organ", "origin", "site", "dx",
                "pdx", "grade", "fmt", "gb", "mpp", "reader"]
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader(); w.writerows(picked)

    spanning = sum(1 for pt, cs in
                   ((pt, {c["ttt"] for c in picked if c["pt"] == pt}) for pt in {c["pt"] for c in picked})
                   if len(cs) > 1)
    print(f"\n{len(picked)} slides, {len({c['pt'] for c in picked})} patients, "
          f"{sum(c['gb'] for c in picked):.1f} GB, {pairs} same-patient pairs")
    print(f"patients contributing specimens at more than one progression class: {spanning} "
          f"(these support a within-patient paired analysis)")
    print(f"labels written to {lab}")
    mismatch = sum(1 for c in picked if c["origin"] and c["site"]
                   and c["origin"].split()[0].lower() not in c["site"].lower())
    print(f"origin differs from site of resection on {mismatch} slides (the free discrimination test)")
    comp = defaultdict(int)
    for c in picked:
        comp[(c["arm"], c["atlas"], c["ttt"])] += 1
    print(f"\n{'arm':<8}{'centre':<18}{'TumorTissueType':<26}{'n':>4}")
    for k, v in sorted(comp.items()):
        print(f"{k[0]:<8}{k[1]:<18}{k[2]:<26}{v:>4}")


if __name__ == "__main__":
    main()
