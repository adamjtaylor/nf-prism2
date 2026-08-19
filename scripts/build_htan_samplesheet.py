#!/usr/bin/env python3
"""Build a stratified HTAN samplesheet for nf-prism2.

Design rules, all of them learned from the 10-slide pilot:

* **Several patients per organ, and more than one centre per organ where HTAN allows it.** The
  pilot could not separate slide identity from organ identity because only lung had more than one
  slide. Patient count, not file count, is the binding constraint.
* **One slide per patient by default**, plus a deliberate handful of same-patient pairs, so
  within-patient similarity can be compared against cross-patient same-organ similarity.
* **Supply `mpp` from BigQuery whenever the recorded value is plausible.** One pilot slide failed
  because an svs carried no MPP tag, and no retry can invent one. Values outside 0.08 to 1.2
  microns are treated as junk (one pilot slide records 16384).
* **Force `reader=image` for plain tif/tiff/qptiff.** TRIDENT picks its reader from the file
  extension with no fallback, and `.tiff` maps to OpenSlide, which cannot open some of them.
* **Cap file size.** The nf-synapse plugin stages sequentially on the head node, so total GB is
  the wall-clock driver, and one HTAN OME-TIFF is 13 GB.

Usage: uv run --with pandas python build_htan_samplesheet.py --n 100 --out ../assets/samplesheet_htan100.csv
"""
import argparse, json, os, subprocess, sys
from collections import defaultdict

# the htan CLI lives in the venv at the project root, two levels above this script
PROJECT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
# call the venv binary directly: nesting `uv run` inside `uv run` does not resolve the project venv
HTAN = os.path.join(PROJECT, ".venv", "bin", "htan")
CMD = [HTAN] if os.path.exists(HTAN) else ["uv", "run", "htan"]

MPP_MIN, MPP_MAX = 0.08, 1.2
# Only actual image containers. HTAN registers .ndpa alongside NDPI slides, which is Hamamatsu
# annotation XML and not an image at all, and it is indistinguishable from a slide in the portal
# metadata. Everything not on this list is dropped.
ALLOWED_FMT = {"svs", "ndpi", "tif", "tiff", "ome-tiff", "scn", "mrxs", "qptiff"}
# Formats we expect to be awkward, capped so one run cannot be dominated by them. qptiff is Akoya
# and is not an OpenSlide format, so it exercises the reader-escalation path deliberately.
RISKY_CAP = {"qptiff": 3}
IMAGE_READER_EXT = (".tif", ".tiff", ".qptiff")
OME_EXT = (".ome.tif", ".ome.tiff")

# organ -> target count. Sized to the patients HTAN actually has, not to a round number per organ.
TARGETS = {"Lung": 24, "Breast": 20, "Colorectal": 20,
           "Other and Ill-defined Sites": 14, "Skin": 12, "Pancreas": 10}


def portal(sql):
    r = subprocess.run(CMD + ["query", "portal", "sql", sql, "--output", "json"],
                       capture_output=True, text=True, cwd=PROJECT)
    if r.returncode:
        sys.exit(r.stderr[-2000:])
    return json.loads(r.stdout[r.stdout.index("["):])


def bq(sql):
    # the BigQuery client rather than the CLI: the CLI has no JSON output mode
    from google.cloud import bigquery
    return [dict(r) for r in bigquery.Client().query(sql).result()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--max-gb", type=float, default=3.0)
    ap.add_argument("--same-patient-pairs", type=int, default=8)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    rows = portal("""SELECT synapseId, Filename, FileFormat, HTANParticipantID p, atlas_name,
        arrayStringConcat(organType,'|') organ, arrayStringConcat(PrimaryDiagnosis,'|') dx
        FROM files WHERE level='Level 2' AND assayName='H&E' AND synapseId <> '' LIMIT 4000""")
    print(f"candidates from the portal: {len(rows)}")

    meta = {r["entityId"]: r for r in bq("""SELECT entityId, File_Size, PhysicalSizeX
        FROM `isb-cgc-bq.HTAN.imaging_level2_metadata_current` WHERE entityId IS NOT NULL""")}
    print(f"rows with BigQuery pixel metadata: {len(meta)}")

    # enrich and filter
    cand = []
    for r in rows:
        m = meta.get(r["synapseId"], {})
        size = float(m.get("File_Size") or 0) / 1e9
        if size > a.max_gb:
            continue
        try:
            mpp = float(m.get("PhysicalSizeX"))
        except (TypeError, ValueError):
            mpp = None
        fn = r["Filename"].lower()
        fmt = ("ome-tiff" if fn.endswith(OME_EXT) else fn.rsplit(".", 1)[-1])
        if fmt not in ALLOWED_FMT:
            continue
        cand.append(dict(
            syn=r["synapseId"], patient=r["p"], atlas=r["atlas_name"],
            organ=r["organ"] or "unknown", dx=r["dx"] or "Not Reported",
            fmt=fmt,
            gb=round(size, 2),
            mpp=(round(mpp, 4) if mpp and MPP_MIN <= mpp <= MPP_MAX else ""),
            reader=("image" if fn.endswith(IMAGE_READER_EXT) and not fn.endswith(OME_EXT) else ""),
            name=r["Filename"].rsplit("/", 1)[-1]))
    print(f"after the {a.max_gb} GB cap and enrichment: {len(cand)}")

    # deterministic order, then round-robin across (atlas, format, dx) strata inside each organ so
    # no single large stratum such as Duke tif swamps the organ
    cand.sort(key=lambda c: c["syn"])
    picked, used_patients = [], set()
    for organ, target in TARGETS.items():
        pool = [c for c in cand if c["organ"] == organ]
        strata = defaultdict(list)
        for c in pool:
            strata[(c["atlas"], c["fmt"], c["dx"])].append(c)
        # prefer labelled diagnoses, then larger strata, so the round robin starts with real labels
        keys = sorted(strata, key=lambda k: (k[2] == "Not Reported", -len(strata[k]), k))
        n = 0
        risky = defaultdict(int)
        while n < target:
            progressed = False
            for k in keys:
                if n >= target:
                    break
                for c in strata[k]:
                    if c["patient"] in used_patients or c in picked:
                        continue
                    if c["fmt"] in RISKY_CAP and risky[c["fmt"]] >= RISKY_CAP[c["fmt"]]:
                        continue
                    picked.append(c); used_patients.add(c["patient"]); n += 1; progressed = True
                    risky[c["fmt"]] += 1
                    break
            if not progressed:
                print(f"  {organ}: only {n} of {target} available with one slide per patient")
                break

    # add same-patient pairs, drawn from patients who have another slide in a different stratum
    by_patient = defaultdict(list)
    for c in cand:
        by_patient[c["patient"]].append(c)
    pairs = 0
    for c in list(picked):
        if pairs >= a.same_patient_pairs:
            break
        sibs = [s for s in by_patient[c["patient"]] if s["syn"] != c["syn"] and s not in picked]
        if sibs:
            picked.append(sibs[0]); pairs += 1
    print(f"same-patient second slides added: {pairs}")

    import csv
    with open(a.out, "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["sample", "slide", "mpp", "reader"])
        seen = defaultdict(int)
        for c in picked:
            organ = c["organ"].split(" ")[0].lower().replace("/", "")
            stem = f"{c['atlas'].replace('HTAN ','').replace(' ','')}_{organ}_{c['fmt'].replace('-','')}"
            seen[stem] += 1
            w.writerow([f"{stem}_{seen[stem]:02d}", f"syn://{c['syn']}", c["mpp"], c["reader"]])
    print(f"\nwrote {len(picked)} slides to {a.out}, {sum(c['gb'] for c in picked):.1f} GB total")
    print(f"patients: {len({c['patient'] for c in picked})}, "
          f"with mpp supplied: {sum(1 for c in picked if c['mpp'])}, "
          f"reader forced: {sum(1 for c in picked if c['reader'])}")
    comp = defaultdict(int)
    for c in picked:
        comp[(c["organ"], c["atlas"], c["fmt"])] += 1
    print(f"\n{'organ':<28}{'centre':<18}{'format':<10}{'n':>4}")
    for k, v in sorted(comp.items()):
        print(f"{k[0][:27]:<28}{k[1]:<18}{k[2]:<10}{v:>4}")
    json.dump([{k: c[k] for k in ("syn", "patient", "atlas", "organ", "dx", "fmt", "gb", "mpp", "reader")}
               for c in picked], open(a.out.replace(".csv", "_provenance.json"), "w"), indent=2)


if __name__ == "__main__":
    main()
